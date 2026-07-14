#!/usr/bin/env python3
# sage_status_report.py - регулярный отчёт SAGE в Телеграм (раз в N часов).
# ТОЛЬКО read-only: статус БД, send-state, непрочитанные ТГ-сообщения.
# НЕ пишет в БД, НЕ шлёт письма, НЕ пушит. Запуск: python sage_status_report.py
import os
import sys
import json
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from send_telegram import send_telegram  # обратный канал бот -> ТГ

# пути к файлам скилла (ридер непрочитанных ТГ)
SKILL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     ".claude", "skills", "sage-oncall", "scripts")
if not os.path.isdir(SKILL):
    SKILL = r"C:\Users\nazar\.claude\skills\sage-oncall\scripts"
TG_UNHANDLED = os.path.join(SKILL, "tg_unhandled.json")


def db_status():
    try:
        c = sqlite3.connect("file:outreach.db?mode=ro", uri=True)
        rows = c.execute("SELECT status, COUNT(*) FROM sites GROUP BY status").fetchall()
        c.close()
        return {s: n for s, n in rows}
    except Exception as e:
        return {"ERR": str(e)}


def send_state():
    try:
        return json.load(open(os.path.join(HERE, "gordon_send_state.json"), encoding="utf-8"))
    except Exception:
        return {}


def tg_unread():
    try:
        if os.path.exists(TG_UNHANDLED):
            data = json.load(open(TG_UNHANDLED, encoding="utf-8"))
            return len(data), data[-3:]
    except Exception:
        pass
    return 0, []


def main():
    db = db_status()
    st = send_state()
    n_unread, last = tg_unread()

    sent = db.get("sent", 0)
    pending = db.get("pending", 0)
    replied = db.get("replied", 0)
    hired = db.get("hired", 0)
    bounced = db.get("bounced", 0)
    review = db.get("review", 0)
    sent_today = st.get("sent_today", "?")
    lim = "лимит" if sent_today != "?" and int(str(sent_today)) >= 96 else "в норме"

    lines = [
        "Гордон x6 - отчёт по расписанию",
        "Рассылка: sent=%s, pending=%s, replied=%s, hired=%s, bounced=%s, review=%s" % (
            sent, pending, replied, hired, bounced, review),
        "Сегодня отправлено: %s/96 (%s)" % (sent_today, lim),
        "Непрочитанных ТГ от тебя: %d" % n_unread,
    ]
    if n_unread:
        lines.append("Последние непрочитанные:")
        for l in last:
            lines.append("  - " + l[:120])
    lines.append("Красная зона на удержании (без твоей команды не делаю): пуш/рассылка/аппрув hired/правка цены.")
    if n_unread:
        lines.append("Есть непрочитанные - отвечу как в живой сессии.")
    msg = "\n".join(lines)
    ok = send_telegram(msg)
    print("[report] sent=%s" % ok)


if __name__ == "__main__":
    main()
