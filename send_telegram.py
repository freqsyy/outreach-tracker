#!/usr/bin/env python3
"""
send_telegram.py - ОБРАТНЫЙ КАНАЛ моста: Claude -> Телеграм (от лица бота).

После выполнения /cmd агент/крон кидает результат Назару в Телеграм
тем же ботом. Токен и chat_id берутся из bridge.env:
  TG_BOT_TOKEN=8915477508:...
  TG_CHAT_ID=7433592364        (куда слать; если нет - берём из ALLOWED_CHAT)

Запуск: python send_telegram.py "текст сообщения"
или из кода: send_telegram("текст")
"""

import os
import sys
import urllib.request
import urllib.error
import json

HERE = os.path.dirname(os.path.abspath(__file__))
TELEGRAM_API = "https://api.telegram.org/bot"
MAX_CHUNK = 4000  # Telegram лимит 4096, берём с запасом


def load_env():
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


def send_telegram(text):
    env = load_env()
    token = env.get("TG_BOT_TOKEN")
    chat = env.get("TG_CHAT_ID") or env.get("ALLOWED_CHAT")
    if not token or not chat:
        print("[tg] Нет TG_BOT_TOKEN / TG_CHAT_ID в bridge.env. Не могу отправить.")
        return False
    url = f"{TELEGRAM_API}{token}/sendMessage"
    # дробим длинные сообщения
    chunks = []
    cur = ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > MAX_CHUNK:
            if cur:
                chunks.append(cur)
            cur = line
        else:
            cur = cur + "\n" + line if cur else line
    if cur:
        chunks.append(cur)
    ok = True
    for c in chunks:
        data = json.dumps({"chat_id": chat, "text": c}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                res = json.loads(r.read().decode("utf-8"))
            if not res.get("ok"):
                ok = False
                print("[tg] Telegram вернул ошибку:", res)
        except Exception as e:
            ok = False
            print(f"[tg] ошибка отправки: {e}")
    return ok


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python send_telegram.py \"текст\"")
        sys.exit(1)
    text = " ".join(sys.argv[1:])
    if send_telegram(text):
        print("[tg] отправлено.")
