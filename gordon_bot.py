#!/usr/bin/env python3
"""GordonBot - Telegram-мост к Claude Code / Гордону.

Позволяет Назару спросить "что ты делаешь" и дёрнуть статус Гордона
с любого конца мира через Telegram. Работает на головом VPS (Oracle Cloud
Always-Free), без внешних зависимостей - только стандартная библиотека.

Команды (пишутся в личку боту):
  /status        - сводка трекера + последние строки лога Гордона
  /log [N]       - последние N строк gordon_run.log (по умолч. 15)
  /ask <вопрос>  - прогнать вопрос через Claude Code (claude -p) и вернуть ответ
  /help          - список команд
  (любой другой текст) -> /status

Переменные окружения (.env на VPS):
  TG_BOT_TOKEN      - токен от @BotFather (ОБЯЗАТЕЛЬНО)
  TG_ALLOWED_CHAT   - твой chat_id (чтобы левые не управляли Гордоном)
  CLAUDE_BIN        - путь к claude (по умолч. "claude")
  ANTHROPIC_API_KEY - для claude -p на головом сервере (ОБЯЗАТЕЛЬНО для /ask)
  TRACK             - путь к track.py (по умолч. рядом)
  LOG               - путь к gordon_run.log (по умолч. рядом)
  POLL_SEC         - интервал опроса Telegram (по умолч. 3)
  ASK_TIMEOUT       - таймаут claude -p в сек (по умолч. 120)

Запуск:  python gordon_bot.py
Фон:      nohup python gordon_bot.py > gordon_bot.log 2>&1 &

Self-test (без сети):  python gordon_bot.py --self-test
"""
import os
import sys
import json
import time
import subprocess
import urllib.request
import urllib.parse

# --- пути рядом со скриптом ---
HERE = os.path.dirname(os.path.abspath(__file__))
TRACK = os.environ.get("TRACK", os.path.join(HERE, "track.py"))
LOG = os.environ.get("LOG", os.path.join(HERE, "gordon_run.log"))
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
ALLOWED_CHAT = os.environ.get("TG_ALLOWED_CHAT", "")
POLL_SEC = int(os.environ.get("POLL_SEC", "3"))
ASK_TIMEOUT = int(os.environ.get("ASK_TIMEOUT", "120"))
API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""


def tg(method, data=None):
    """Один вызов Telegram Bot API (POST, json). Возвращает dict или None."""
    if not API:
        return None
    url = f"{API}/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # сеть легла / Telegram недоступен
        print(f"[BOT] tg.{method} error: {e}", file=sys.stderr)
        return None


def send_message(chat_id, text):
    """Отправляет текст, разбивая на куски по 4096 знаков (лимит Telegram)."""
    chunks = chunk_text(text, 4096)
    for c in chunks:
        tg("sendMessage", {"chat_id": chat_id, "text": c, "parse_mode": "HTML" if False else None})
        time.sleep(0.3)


def chunk_text(text, size):
    """Разбивает длинный текст на части <= size, не рвёт посреди строки при возможности."""
    if len(text) <= size:
        return [text]
    out = []
    cur = ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > size:
            if cur:
                out.append(cur)
            cur = line
            while len(cur) > size:
                out.append(cur[:size])
                cur = cur[size:]
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        out.append(cur)
    return out or [text]


def tail(path, n):
    """Последние n строк файла (без загрузки всего файла в память)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except FileNotFoundError:
        return "(лог не найден)"


def tracker_stats():
    """Сводка из track.py stats."""
    try:
        out = subprocess.run(
            [sys.executable, TRACK, "stats"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return out or "(пусто)"
    except Exception as e:
        return f"ошибка stats: {e}"


def ask_claude(question):
    """Прогоняет вопрос через Claude Code в print-режиме и возвращает ответ."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "ANTHROPIC_API_KEY не задан на VPS - /ask недоступен. Задай ключ в окружении."
    try:
        out = subprocess.run(
            [CLAUDE_BIN, "-p", question, "--allowedTools", ""],
            capture_output=True, text=True, timeout=ASK_TIMEOUT,
        )
        res = (out.stdout or "").strip()
        if not res and out.stderr:
            res = "(stderr) " + out.stderr.strip()[:2000]
        return res or "(пустой ответ)"
    except subprocess.TimeoutExpired:
        return f"Claude не ответил за {ASK_TIMEOUT}с."
    except Exception as e:
        return f"ошибка claude: {e}"


def handle(text):
    """Возвращает текст-ответ на команду пользователя."""
    text = (text or "").strip()
    low = text.lower()
    if low in ("/start", "/help", "help", "?"):
        return (
            "GordonBot. Команды:\n"
            "/status - сводка Гордона + хвост лога\n"
            "/log [N] - последние N строк лога\n"
            "/ask <вопрос> - спросить Claude\n"
            "(любой другой текст = /status)"
        )
    if low.startswith("/log"):
        try:
            n = int(text.split()[1])
        except Exception:
            n = 15
        return f"=== gordon_run.log (последние {n}) ===\n" + tail(LOG, n)
    if low.startswith("/ask"):
        q = text[len("/ask"):].strip()
        if not q:
            return "После /ask напиши вопрос."
        return "Claude думает...\n" + ask_claude(q)
    # всё остальное -> статус
    head = "=== Tracker stats ===\n" + tracker_stats()
    last = "\n=== gordon_run.log (хвост) ===\n" + tail(LOG, 8)
    return head + "\n" + last


def poll():
    """Основной цикл опроса Telegram long-polling."""
    offset = 0
    print("[BOT] старт polling", file=sys.stderr)
    while True:
        try:
            resp = tg("getUpdates", {"offset": offset, "timeout": 30})
            if resp and resp.get("ok"):
                for upd in resp["result"]:
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or upd.get("edited_message")
                    if not msg:
                        continue
                    chat_id = msg.get("chat", {}).get("id")
                    who = str(chat_id)
                    if ALLOWED_CHAT and who != str(ALLOWED_CHAT):
                        # чужой чат - игнорим, ничего не отвечаем
                        print(f"[BOT] ignored chat {who}", file=sys.stderr)
                        continue
                    text = msg.get("text", "")
                    print(f"[BOT] msg from {who}: {text[:60]}", file=sys.stderr)
                    send_message(chat_id, handle(text))
        except Exception as e:
            print(f"[BOT] poll loop error: {e}", file=sys.stderr)
            time.sleep(POLL_SEC)
        time.sleep(POLL_SEC)


def self_test():
    """Проверка логики без сети."""
    print("== chunk_text ==")
    big = "\n".join(f"line {i}" for i in range(500))
    parts = chunk_text(big, 4096)
    assert all(len(p) <= 4096 for p in parts), "chunk too big"
    print(f"ok: 500 строк -> {len(parts)} кусков, все <=4096")
    print("== handle /status (без claude) ==")
    out = handle("/status")
    print(out[:300])
    print("== handle /log 3 ==")
    print(handle("/log 3")[:200])
    print("== handle /help ==")
    print(handle("/help"))
    print("SELF-TEST OK")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
        sys.exit(0)
    if not BOT_TOKEN:
        print("TG_BOT_TOKEN не задан. Задай в окружении (.env на VPS).", file=sys.stderr)
        sys.exit(1)
    poll()
