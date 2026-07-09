#!/usr/bin/env python3
"""
test_smtp.py — ТЕСТ подключения. Шлёт ОДНО письмо самому себе
(на ACCOUNT_1_EMAIL). Не трогает базу и не рассылает контактам.

Запуск:  python test_smtp.py
"""

import smtplib
import sys
from email.mime.text import MIMEText

import gordon_common as gc

env = gc.load_env()


def main():
    email = env.get("ACCOUNT_1_EMAIL")
    pwd = env.get("ACCOUNT_1_PASS")
    host = env.get("SMTP_HOST", "smtp.gmail.com")
    port = int(env.get("SMTP_PORT", "587"))

    if not email or not pwd or "ZAMENI" in email.upper() or "XXXX" in pwd.upper():
        print("[!] .env ne zapolnen. Otkroy .env v bloknote i vpischi dannye.")
        return

    subject, body = gc.load_letter()
    if not body:
        body = "Test Gordona: esli ty eto vidis, SMTP rabotaet."
        subject = "Gordon SMTP test"
    subj = subject.replace("{site}", "burvin.by")
    bod = body.replace("{site}", "burvin.by")
    msg = MIMEText(bod, _charset="utf-8")
    msg["Subject"] = subj
    msg["From"] = email
    msg["To"] = email

    try:
        s = smtplib.SMTP(host, port, timeout=30)
        s.starttls()
        s.login(email, pwd)
        s.sendmail(email, [email], msg.as_string())
        s.quit()
        print(f"[+] TEST OK: pismo otpravleno na {email}. Prover pochtu.")
        gc.log(f"SMTP test OK dlya {email}", "TEST")
    except Exception as e:
        print(f"[-] TEST FAIL: {e}")
        gc.log(f"SMTP test FAIL: {e}", "TEST")


if __name__ == "__main__":
    main()
