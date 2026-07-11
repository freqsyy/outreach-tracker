#!/usr/bin/env python3
"""
bridge_me.py - МОСТ ПК <-> телефон (вариант 1: передача задач Claude).

Что делает:
- Опрашивает Cloudflare KV (namespace gordon-bridge) раз в BRIDGE_POLL_SEC.
- Твои команды из Телеграма (/cmd ...) копятся в KV "inbox".
  Агент вытаскивает ТОЛЬКО НОВЫЕ строки (без дублей) и кладёт их в
  bridge_pending.txt - это очередь задач, которую Claude разбирает.
- Полная история задач пишется в bridge_inbox.txt (для тебя, справочно).
- Пишет текущий статус Claude (чем занят) в KV "status" -> бот отдаёт по /me.

КАК CLAUDE ЭТО ВИДИТ:
- Claude по расписанию (cron) сам будит себя, читает bridge_pending.txt,
  выполняет задачи и очищает очередь. Тебе НЕ нужно ничего пересылать.

Запуск: python bridge_me.py   (держать открытым рядом с Claude Code)
Останов: Ctrl+C

Секреты: CF_API_TOKEN и CF_ACCOUNT_ID берутся из bridge.env
или из переменных окружения. KV namespace id - в начале файла (KV_NS).
"""

import os
import sys
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))

# KV namespace "gordon-bridge" (создан через API)
KV_NS = "77f5a72e4922438eab47b7547aaa746c"

BRIDGE_POLL_SEC = 5      # как часто опрашивать KV
STATUS_FILE = os.path.join(HERE, "bridge_status.txt")      # куда Claude пишет свой статус
INBOX_FILE = os.path.join(HERE, "bridge_inbox.txt")        # полная история твоих /cmd
PENDING_FILE = os.path.join(HERE, "bridge_pending.txt")    # ОЧЕРЕДЬ невыполненных задач (Claude чистит)
SEEN_FILE = os.path.join(HERE, "bridge_seen.txt")          # сколько строк inbox уже обработано (маркер)

# статус по умолчанию, пока Claude не написал свой
DEFAULT_STATUS = "Мост запущен, жду задач от Назара."


def load_env():
    """Читаем bridge.env (отдельно от .env Гордона)."""
    env = {}
    p = os.path.join(HERE, "bridge.env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def kv_get(token, acct, key):
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/storage/kv/namespaces/{KV_NS}/values/{key}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        return None
    except Exception:
        return None


def kv_put(token, acct, key, value):
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/storage/kv/namespaces/{KV_NS}/values/{key}"
    req = urllib.request.Request(url, data=value.encode("utf-8"), method="PUT")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "text/plain")
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception:
        return False


def read_local(path, default=""):
    if not os.path.exists(path):
        return default
    try:
        return open(path, encoding="utf-8").read()
    except Exception:
        return default


def append_local(path, text):
    with open(path, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def read_seen():
    """Сколько строк inbox уже разобрали (чтобы не дублировать)."""
    raw = read_local(SEEN_FILE, "0").strip()
    try:
        return int(raw)
    except Exception:
        return 0


def write_seen(n):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        f.write(str(n))


def main():
    env = load_env()
    token = os.environ.get("CF_API_TOKEN") or env.get("CF_API_TOKEN")
    acct = os.environ.get("CF_ACCOUNT_ID") or env.get("CF_ACCOUNT_ID")
    if not token or not acct:
        print("[bridge] Нет CF_API_TOKEN / CF_ACCOUNT_ID (в bridge.env или env). Выход.")
        sys.exit(1)

    print(f"[bridge] старт. KV={KV_NS}, опрос каждые {BRIDGE_POLL_SEC}с")
    print(f"[bridge] новые /cmd -> очередь {os.path.basename(PENDING_FILE)} (Claude сам разбирает)")
    print(f"[bridge] полная история -> {os.path.basename(INBOX_FILE)}")
    print(f"[bridge] статус Claude <- {os.path.basename(STATUS_FILE)} -> /me")

    seen = read_seen()
    while True:
        try:
            # 1. читаем inbox из KV (твои команды с телефона, копятся строками)
            inbox = kv_get(token, acct, "inbox")
            if inbox is not None:
                lines = [l for l in inbox.strip().splitlines() if l.strip()]
                if len(lines) > seen:
                    new_lines = lines[seen:]
                    for line in new_lines:
                        append_local(INBOX_FILE, line)    # полная история
                        append_local(PENDING_FILE, line)  # очередь для Claude
                    seen = len(lines)
                    write_seen(seen)
                    print(f"[bridge] +{len(new_lines)} новых задач -> {os.path.basename(PENDING_FILE)}")
                    for nl in new_lines:
                        print(f"[bridge]   • {nl}")

            # 2. пишем статус Claude в KV (для /me)
            status = read_local(STATUS_FILE, DEFAULT_STATUS).strip() or DEFAULT_STATUS
            kv_put(token, acct, "status", status)

        except Exception as e:
            print(f"[bridge] ошибка цикла: {e}")
        time.sleep(BRIDGE_POLL_SEC)


if __name__ == "__main__":
    main()
