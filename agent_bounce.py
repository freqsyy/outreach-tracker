#!/usr/bin/env python3
"""
agent_bounce.py - АГЕНТ 4 (Чистильщик мёртвых адресов).

Автоматически находит баунсы (возвраты писем) в ящике отправителя
и помечает соответствующие сайты статусом 'bounced' через track.py.
Мёртвые адреса выпадают из рассылки сами (sender берёт только pending).

Как детектить баунс (без внешних библиотек):
- Отправитель письма = mailer-daemon / postmaster / *-daemon / *@*.bounce
- В заголовках есть Return-Path: <> (пустой) или содержится
  "Final-Recipient:" / "Action: failed" / "Status: 5." (перманентный фейл)
- В теле/заголовках фигурирует адрес, кому мы отправляли (recipient)

Сопоставление recipient -> site_id строим по обратному индексу
(email.lower() -> [id]), только для сайтов со статусом 'sent'
(idempotent: уже bounced/replied/hired не трогаем).

ВКЛ/ВЫКЛ: BOUNCE_ENABLED=true в .env (по умолчанию true, т.к. IMAP уже включён).

Запуск:  python agent_bounce.py
"""

import os
import re
import imaplib
import email as email_lib
import socket
import subprocess
import sys
from datetime import datetime, timedelta
from email.utils import parseaddr, getaddresses

import gordon_common as gc

HERE = os.path.dirname(os.path.abspath(__file__))
TRACK = os.path.join(HERE, "track.py")

# паттерны "это точно баунс-уведомление"
BOUNCE_FROM_RE = re.compile(r"(mailer-daemon|postmaster|.*-daemon|.*@.*\.bounce|.*@.*bounce\.)", re.I)
BOUNCE_HEADER_RE = re.compile(r"(final-recipient|action:\s*failed|status:\s*5\.|return-path:\s*<>)", re.I)
BOUNCE_SUBJECT_RE = re.compile(r"(returned mail|undeliverable|delivery (status notification|failure)|mail delivery (failed|subsytem)|did not reach|could not be delivered)", re.I)
# адрес получателя внутри баунса
RECIPIENT_RE = re.compile(r"final-recipient:\s*(?:rfc822;\s*)?([a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,})", re.I)
ORIG_TO_RE = re.compile(r"(?:original[-\s]?recipient|failed recipient|undelivered to|was not delivered to)\s*[:<]\s*\"?([a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,})", re.I)
PERMANENT_RE = re.compile(r"(status:\s*5\.|user unknown|no such user|does not exist|mailbox unavailable|address not found|permanent failure|550 |551 |552 |553 )", re.I)


def get_accounts(env):
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


def build_recipient_map():
    """email(lower) -> [id сайтов] для сайтов со статусом 'sent' (ещё не отвечали)."""
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


def _collect_addrs_from_message(msg):
    """Все email-адреса, упомянутые в заголовках/теле баунса."""
    addrs = set()
    # 1) явные Final-Recipient / Original-Recipient
    for hdr in ("Final-Recipient", "Original-Recipient", "Original-Recipient", "X-Failed-Recipients"):
        val = msg.get(hdr, "")
        for m in RECIPIENT_RE.findall(val) + ORIG_TO_RE.findall(val):
            addrs.add(m.lower())
    # 2) адреса в теле письма (ищем все email-подобные)
    try:
        payload = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() in ("text/plain", "text/html"):
                    try:
                        payload += part.get_payload(decode=True).decode("utf-8", "ignore") + "\n"
                    except Exception:
                        pass
        else:
            try:
                payload = msg.get_payload(decode=True).decode("utf-8", "ignore")
            except Exception:
                payload = ""
        for a in gc.EMAIL_RE.findall(payload):
            addrs.add(a.lower())
    except Exception:
        pass
    # 3) адреса в To/Cc (иногда баунс дублирует получателя)
    for name, addr in getaddresses(msg.get_all("To", []) + msg.get_all("Cc", [])):
        if "@" in addr:
            addrs.add(addr.strip().lower())
    return addrs


def is_bounce(msg):
    """Вернёт (True/False, причина)."""
    from_addr = parseaddr(msg.get("From", ""))[1].strip().lower()
    subject = msg.get("Subject", "") or ""
    return_path = msg.get("Return-Path", "") or ""
    headers_blob = f"{from_addr}\n{return_path}\n" + "\n".join(
        f"{k}: {v}" for k, v in msg.items() if k.lower() in ("final-recipient", "action", "status", "return-path", "x-failed-recipients", "original-recipient")
    )

    if BOUNCE_FROM_RE.search(from_addr):
        return True, f"from={from_addr}"
    if BOUNCE_SUBJECT_RE.search(subject):
        return True, f"subject='{subject[:40]}'"
    if BOUNCE_HEADER_RE.search(headers_blob):
        return True, "bounce-headers"
    return False, ""


def check_account_bounces(email_addr, password, recipient_map, lookback_days=21):
    """Ищет баунсы в ящике. Возвращает set(site_id), помеченных мёртвыми."""
    dead = set()
    try:
        socket.setdefaulttimeout(30)
        m = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        m.login(email_addr, password)
        # баунсы могут лежать в INBOX, в папке "[Gmail]/All Mail" или "Spam"/"Trash"
        for box in ("INBOX", '"[Gmail]/All Mail"', '"[Gmail]/Spam"'):
            try:
                typ, _ = m.select(box)
            except Exception:
                continue
            if typ != "OK":
                continue
            since = (datetime.now() - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
            try:
                typ, data = m.search(None, "SINCE", since)
            except Exception:
                continue
            if typ != "OK" or not data or not data[0]:
                continue
            for num in data[0].split():
                try:
                    typ, msg_data = m.fetch(num, "(RFC822)")
                except Exception:
                    continue
                if typ != "OK":
                    continue
                raw = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw)
                bounce, why = is_bounce(msg)
                if not bounce:
                    continue
                addrs = _collect_addrs_from_message(msg)
                # сопоставляем только с нашими получателями (status=sent)
                hit_ids = []
                for a in addrs:
                    if a in recipient_map:
                        hit_ids.extend(recipient_map[a])
                if hit_ids:
                    for sid in hit_ids:
                        if sid in dead:
                            continue
                        dead.add(sid)
                        gc.log(f"BAUNS po #{sid} (v {email_addr}): {why} | addr={list(addrs & set(recipient_map.keys()))}", "BOUNCE")
                else:
                    gc.log(f"Bauns bez sovpadeniya (v {email_addr}): {why} | addr={sorted(addrs)[:3]}", "BOUNCE")
        m.logout()
    except Exception as e:
        gc.log(f"IMAP oshibka (bounce-check) dlya {email_addr}: {e}", "BOUNCE")
        gc.record_pitfall(
            "Bounce: oshibka IMAP",
            str(e),
            "IMAP vyklyuchen / nevernyy app-password / blok",
            "vklyuchit IMAP v Gmail, proverit APP_PASSWORD"
        )
    return dead


def apply_bounce(site_id, note):
    subprocess.run([sys.executable, TRACK, "bounce", str(site_id)],
                   capture_output=True, text=True, timeout=30)
    subprocess.run([sys.executable, TRACK, "note", str(site_id), note],
                   capture_output=True, text=True, timeout=30)
    gc.log(f"Site #{site_id} -> BOUNCED (mertvyy adres)", "BOUNCE")


def main():
    env = gc.load_env()
    if env.get("BOUNCE_ENABLED", "true").lower() != "true":
        gc.log("Bounce-check vyklyuchen (BOUNCE_ENABLED=false). Propuskaem.", "BOUNCE")
        return

    accounts = get_accounts(env)
    if not accounts:
        gc.log("Net akkauntov v .env. Bounce-check ostanovlen.", "BOUNCE")
        return

    recipient_map = build_recipient_map()
    if not recipient_map:
        gc.log("Net otpravlennyh saitov (status=sent) dlya proverki baunsov.", "BOUNCE")
        return

    lookback = int(env.get("BOUNCE_LOOKBACK_DAYS", "21"))
    all_dead = set()
    for email_addr, password in accounts:
        dead = check_account_bounces(email_addr, password, recipient_map, lookback)
        all_dead |= dead

    if all_dead:
        for sid in sorted(all_dead):
            apply_bounce(sid, f"Avto-bauns: mertvyy adres, ubran iz rasylki {datetime.now().strftime('%Y-%m-%d')}")
        gc.log(f"Bounce-check zavershen. Pomacheno mertvyh: {sorted(all_dead)}", "BOUNCE")
    else:
        gc.log("Bounce-check: novyh baunsov net. Vse adresa zhivy.", "BOUNCE")


if __name__ == "__main__":
    main()
