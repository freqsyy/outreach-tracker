#!/usr/bin/env python3
"""
agent_recorder.py — АГЕНТ 3 (Летописец).

Фиксирует результаты откликов:
1. РУЧНОЙ режим (по умолчанию): читает gordon_responses.txt,
   где каждая строка: <id> <replied|hired|rejected> [сумма BYN]
   и обновляет статус в track.py.
2. IMAP-поллинг (опционально, ВЫКЛ по умолчанию): проверяет папку "Sent"
   аккаунтов на ответы — помечает replied. ВКЛ только если IMAP_ENABLED=true в .env.

Запуск:  python agent_recorder.py
"""

import os
import re
import imaplib
import email as email_lib
import socket
import subprocess
import sys
from datetime import datetime, timedelta
from email.utils import parseaddr

import gordon_common as gc

HERE = os.path.dirname(os.path.abspath(__file__))
RESPONSES = os.path.join(HERE, "gordon_responses.txt")
TRACK = os.path.join(HERE, "track.py")


def get_accounts(env):
    """Список аккаунтов из .env: ACCOUNT_1_EMAIL/PASS ... (тот же App Password, что и для SMTP)."""
    accs = []
    i = 1
    while True:
        e = env.get(f"ACCOUNT_{i}_EMAIL")
        p = env.get(f"ACCOUNT_{i}_PASS")
        if not e or not p:
            break
        accs.append((e, p))
        i += 1
    return accs


def build_email_map():
    """email(lower) -> [id сайтов], только те, кому уже писали (status = sent).
    Отвеченные (replied/hired/rejected) не трогаем — idempotent."""
    conn = gc.get_conn()
    rows = conn.execute(
        "SELECT id, email FROM sites WHERE status='sent' AND email IS NOT NULL"
    ).fetchall()
    conn.close()
    m = {}
    for r in rows:
        key = (r["email"] or "").strip().lower()
        if key:
            m.setdefault(key, []).append(r["id"])
    return m


def check_account_inbox(email_addr, password, email_map, lookback_days=14):
    """Заходит в INBOX аккаунта, ищет письма от владельцев сайтов.
    Возвращает список (site_id, subject, preview) совпадений."""
    matched = []
    try:
        socket.setdefaulttimeout(30)
        m = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        m.login(email_addr, password)
        m.select("INBOX")
        since = (datetime.now() - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
        typ, data = m.search(None, "SINCE", since)
        if typ != "OK" or not data or not data[0]:
            m.logout()
            return matched
        for num in data[0].split():
            typ, msg_data = m.fetch(num, "(RFC822)")
            if typ != "OK":
                continue
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)
            from_email = parseaddr(msg.get("From", ""))[1].strip().lower()
            if from_email in email_map:
                subject = msg.get("Subject", "") or ""
                preview = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            try:
                                preview = part.get_payload(decode=True).decode("utf-8", "ignore")
                            except Exception:
                                preview = ""
                            break
                else:
                    try:
                        preview = msg.get_payload(decode=True).decode("utf-8", "ignore")
                    except Exception:
                        preview = ""
                preview = " ".join(preview.split())[:200]
                for sid in email_map[from_email]:
                    matched.append((sid, subject, preview))
        m.logout()
    except Exception as e:
        gc.log(f"IMAP oshibka dlya {email_addr}: {e}", "RECORDER")
        gc.record_pitfall(
            "Recorder: oshibka IMAP",
            str(e),
            "IMAP vyklyuchen v akkaunte / nevernyy app-password / blok",
            "vklyuchit IMAP v nastroykah Gmail, proverit APP_PASSWORD"
        )
    return matched


def apply(id_, status, amount=None, note=None):
    if status == "replied":
        subprocess.run([sys.executable, TRACK, "reply", str(id_)],
                       capture_output=True, text=True, timeout=30)
    elif status == "hired":
        cmd = [sys.executable, TRACK, "hired", str(id_)]
        if amount:
            cmd += ["--amount", str(amount)]
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    elif status == "rejected":
        subprocess.run([sys.executable, TRACK, "rejected", str(id_)],
                       capture_output=True, text=True, timeout=30)
    if note:
        subprocess.run([sys.executable, TRACK, "note", str(id_), note],
                       capture_output=True, text=True, timeout=30)
    gc.log(f"Reshenie po #{id_}: {status}" + (f" (+{amount} BYN)" if amount else ""), "RECORDER")


def main():
    env = gc.load_env()

    # --- РУЧНОЙ режим: gordon_responses.txt ---
    if os.path.exists(RESPONSES):
        with open(RESPONSES, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # формат: 12 hired 80  ИЛИ  12 replied
                m = re.match(r"^(\d+)\s+(replied|hired|rejected)\s*(\d+(?:\.\d+)?)?", line)
                if m:
                    id_ = int(m.group(1))
                    status = m.group(2)
                    amount = float(m.group(3)) if m.group(3) else None
                    apply(id_, status, amount)
                else:
                    gc.log(f"Ne raspoznal stroku: {line}", "RECORDER")
        # очищаем обработанный файл, чтобы не дублировать
        open(RESPONSES, "w", encoding="utf-8").close()
        gc.log("gordon_responses.txt obrabotan i ochishen.", "RECORDER")
    else:
        gc.log("Net ruchnyh otvetov (gordon_responses.txt pust).", "RECORDER")

    # --- АВТО-режим: IMAP-поллинг (включается IMAP_ENABLED=true) ---
    if env.get("IMAP_ENABLED", "false").lower() == "true":
        gc.log("IMAP- pollling VKLYUCHEN. Proveryaem vhodyaschie otvetov...", "RECORDER")
        accounts = get_accounts(env)
        email_map = build_email_map()
        if not email_map:
            gc.log("Net otpravlennyh saitov dlya proverki otvetov.", "RECORDER")
            return
        found = set()
        for email_addr, password in accounts:
            lookback = int(env.get("IMAP_LOOKBACK_DAYS", "14"))
            results = check_account_inbox(email_addr, password, email_map, lookback)
            for sid, subject, preview in results:
                if sid in found:
                    continue  # один сайт — один replied за прогон
                found.add(sid)
                gc.log(f"NAYDEN otvet po #{sid}: '{subject}'", "RECORDER")
                apply(sid, "replied", note=f"Avto-otvet: {subject} | {preview}")
        if found:
            gc.log(f"IMAP: otmecheno replied: {sorted(found)}", "RECORDER")
        else:
            gc.log("IMAP: novyh otvetov net.", "RECORDER")
    else:
        gc.log("IMAP vyklyuchen (IMAP_ENABLED=false). Avto-proverka propuschena.", "RECORDER")


if __name__ == "__main__":
    main()
