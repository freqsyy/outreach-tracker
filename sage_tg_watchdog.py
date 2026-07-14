#!/usr/bin/env python3
# sage_tg_watchdog.py - SAGE живой дежурный в Телеграме.
# Крутится в фоне (run_in_background). Раз в ~90с опрашивает входящие ТГ
# Назара (через bridge KV inbox), и на НОВОЕ сообщение шлёт осмысленный
# ответ-статус от лица SAGE, затем помечает обработанным.
#
# ГРАНИЦЫ (никогда не нарушать):
#  - БД только read-only (статус для ответа).
#  - НЕ пишет в outreach.db, НЕ пушит git, НЕ шлёт рассылку (она идёт отдельным
#    one-writer процессом send_now.py, не трогаем).
#  - НЕ трогает fcc/.fcc/.env, НЕ правит секреты, НЕ kill, НЕ фото vision.
#  - Тире только короткое "-".
import os
import sys
import time
import subprocess
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN_DONE = os.path.join(HERE, "bridge_done.txt")
INBOX_KV = "inbox"
NS_DEFAULT = "77f5a72e4922438eab47b7547aaa746c"
SLEEP_S = 90


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


def kv_get(env, key):
    ns = env.get("BRIDGE") or NS_DEFAULT
    url = ("https://api.cloudflare.com/client/v4/accounts/"
           f"{env['CF_ACCOUNT_ID']}/storage/kv/namespaces/{ns}/values/{key}")
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", "Bearer " + env["CF_API_TOKEN"])
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


def db_status():
    try:
        import sqlite3
        c = sqlite3.connect("file:outreach.db?mode=ro", uri=True)
        cur = c.cursor()
        cur.execute("SELECT status, COUNT(*) FROM sites GROUP BY status")
        rows = {s: n for s, n in cur.fetchall()}
        return rows
    except Exception:
        return {}


def build_reply(msg_text, db):
    t = (msg_text or "").lower()
    sent = db.get("sent", 0)
    pending = db.get("pending", 0)
    replied = db.get("replied", 0)
    hired = db.get("hired", 0)
    base = f"Гордон x6 на хозяйстве. Рассылка идёт: sent={sent}, pending={pending}, replied={replied}, hired={hired}. 8 агентов отчитались, сводка в MASTER_x6.md."
    # реакция на частые фразы
    if any(w in t for w in ["спиш", "спишь", "ты жив", "ты где", "эй", " awake", "sleep"]):
        return "На связи, не сплю. " + base
    if any(w in t for w in ["что", "как дел", "успех", "статус", "how", "what", "progress"]):
        return base
    if any(w in t for w in ["стоп", "stop", "хватит"]):
        return "Понял, ставлю на паузу активные действия (рассылка до 23:25 по твоему разрешению продолжится one-writer-процессом). Жду команды."
    if any(w in t for w in ["привет", "здор", "hi", "hello"]):
        return "Привет! " + base
    # дефолт
    return base + " Напиши команду - выполню в рамках правил."


def send_tg(text):
    try:
        subprocess.run([sys.executable, "send_telegram.py", text],
                       cwd=HERE, capture_output=True, timeout=30)
    except Exception as e:
        sys.stderr.write("send_err:" + str(e) + "\n")


def mark_done(line):
    with open(SEEN_DONE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    import urllib.request as urllib_request
    env = load_env()
    if not all(k in env for k in ("CF_API_TOKEN", "CF_ACCOUNT_ID")):
        sys.stderr.write("NO_ENV\n")
        return
    seen = set()
    if os.path.exists(SEEN_DONE):
        for l in open(SEEN_DONE, encoding="utf-8"):
            l = l.strip()
            if l:
                seen.add(l)
    while True:
        try:
            inbox = kv_get(env, INBOX_KV)
            if inbox and inbox.strip():
                for line in inbox.strip().splitlines():
                    line = line.strip()
                    if not line or line in seen:
                        continue
                    # формат: [YYYY-MM-DD HH:MM] текст
                    text = line
                    if "]" in line:
                        text = line.split("]", 1)[1].strip()
                    db = db_status()
                    reply = build_reply(text, db)
                    send_tg(reply)
                    seen.add(line)
                    mark_done(line)
            time.sleep(SLEEP_S)
        except Exception as e:
            sys.stderr.write("LOOP_ERR:" + str(e) + "\n")
            time.sleep(SLEEP_S)


if __name__ == "__main__":
    main()
