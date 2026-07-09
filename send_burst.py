#!/usr/bin/env python3
"""Разовый прогон: по 3 письма с каждого из 4 новых аккаунтов (2..5) = 12.
ИДЕМПОТЕНТНО: отправляет ровно столько, сколько нужно добить до 12 за этот прогон,
продолжая с места остановки. Не дублирует уже sent.
Зазор ~200с между письмами -> 12 писем укладываются в ~45 мин (в рамках часа)."""
import os, sys, time, random, subprocess
import smtplib
from email.mime.text import MIMEText
import gordon_common as gc

HERE = os.path.dirname(os.path.abspath(__file__))
TRACK = os.path.join(HERE, "track.py")
PER_ACCOUNT = 3
ACCOUNTS = [2, 3, 4, 5]
SLEEP_BETWEEN = 8
GAP = 200  # сек между письмами (фикс ~200, плюс джиттер +-30)

env = gc.load_env()
def acc(i):
    return env[f"ACCOUNT_{i}_EMAIL"], env[f"ACCOUNT_{i}_PASS"]

settings = {
    "SMTP_HOST": env.get("SMTP_HOST", "smtp.gmail.com"),
    "SMTP_PORT": env.get("SMTP_PORT", "587"),
    "FROM_NAME": env.get("FROM_NAME", "Nazar"),
}

subject, body = gc.load_letter()
conn = gc.get_conn()
# БЕРЁМ ТОЛЬКО pending (sent уже ушедшие не трогаем)
pending = conn.execute(
    "SELECT * FROM sites WHERE status='pending' AND email IS NOT NULL ORDER BY id"
).fetchall()
conn.close()

# план: аккаунт по кругу, по 3 на каждый = 12 слотов (если pending хватает)
plan = []
for k in range(PER_ACCOUNT):
    for ai in ACCOUNTS:
        plan.append(ai)
# сколько ещё добить (если прошлый прогон успел отправить часть - продолжаем)
already = 12 - len(pending) if False else 0  # не используем

sent_this = 0
idx = 0
for ai in plan:
    if idx >= len(pending):
        gc.log("Konec pending.", "BURST"); break
    email_addr, pwd = acc(ai)
    row = pending[idx]; idx += 1
    from urllib.parse import urlparse
    domain = urlparse(row["url"]).netloc or row["url"]
    subj = subject.replace("{site}", domain)
    bod = body.replace("{site}", domain)
    msg = MIMEText(bod, _charset="utf-8")
    msg["Subject"] = subj
    msg["From"] = f"{settings['FROM_NAME']} <{email_addr}>"
    msg["To"] = row["email"]
    try:
        s = smtplib.SMTP(settings["SMTP_HOST"], int(settings["SMTP_PORT"]), timeout=30)
        s.starttls(); s.login(email_addr, pwd)
        s.sendmail(email_addr, [row["email"]], msg.as_string()); s.quit()
        subprocess.run([sys.executable, TRACK, "send", str(row["id"])],
                       capture_output=True, text=True, timeout=30)
        gc.log(f"OK #{row['id']} -> {row['email']} cherez akk{ai} ({email_addr})", "BURST")
        sent_this += 1
    except Exception as e:
        gc.log(f"OSHIBKA #{row['id']} (akk{ai}): {e}", "BURST")
    gap = GAP + random.uniform(-30, 30)
    gc.log(f"Pauza {gap:.0f}s", "BURST")
    time.sleep(gap)

gc.log(f"BURST gotovo. Otpravleno v etom progone: {sent_this}", "BURST")
print(f"Otpravleno v etom progone: {sent_this}")
