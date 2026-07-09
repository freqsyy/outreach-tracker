#!/usr/bin/env python3
"""
agent_sender.py — АГЕНТ 2 (Отправитель).

Берёт из базы сайты со статусом pending и заполненным email,
ротирует spare-аккаунты из .env, шлёт письмо из шаблона через SMTP,
ставит статус sent через track.py.

БЕЗОПАСНОСТЬ (чтобы Gmail не забанил):
- MAX_PER_RUN — сколько писем за один прогон (по умолчанию 5)
- MAX_PER_DAY — потолок в сутки (по умолчанию 12)
- SEND_INTERVAL_SEC — пауза между письмами (по умолчанию 30)
- HOURS — окно отправки "9-21" (локальное время)

Запуск:  python agent_sender.py
"""

import os
import smtplib
import subprocess
import sys
import time
import random
from datetime import datetime
from email.mime.text import MIMEText

import gordon_common as gc

HERE = os.path.dirname(os.path.abspath(__file__))
TRACK = os.path.join(HERE, "track.py")
STATE_PATH = os.path.join(HERE, "gordon_send_state.json")

# Настройки по умолчанию (переопределяются из .env)
DEFAULTS = {
    "MAX_PER_RUN": "5",
    "MAX_PER_DAY": "12",
    "SEND_INTERVAL_SEC": "30",
    "HOURS": "9-21",
    "SMTP_HOST": "smtp.gmail.com",
    "SMTP_PORT": "587",
    "FROM_NAME": "Nazar",
    "SLEEP_BETWEEN_ACCOUNTS_SEC": "5",
}

# Письмо берём из letter.txt через gc.load_letter() (UTF-8, кириллица)


def load_settings():
    env = gc.load_env()
    s = dict(DEFAULTS)
    s.update({k: env[k] for k in DEFAULTS if k in env})
    return s


def effective_day_limit(settings, env):
    """Дневной лимит с учётом warm-up (плавный разогрев аккаунтов)."""
    try:
        target = int(settings["MAX_PER_DAY"])
    except Exception:
        target = 12
    warm = int(env.get("WARMUP_DAYS", "0"))
    if warm > 0:
        # каждый день +~ +50% от базы 12, но не выше target
        cap = min(target, 12 + warm * 6)
        return max(12, cap)
    return target


def effective_run_limit(settings, env, day_limit):
    """Лимит за прогон = сколько влезет за оставшееся время суток.
    Планировщик бегает раз в час (24 прогона). Берём чуть больше среднего
    на случай, если день начался не с первого часа."""
    try:
        runs_left = max(1, int(env.get("RUNS_PER_DAY", "24")))
    except Exception:
        runs_left = 24
    return max(1, int(day_limit / runs_left) + 1)


def get_accounts(env):
    """Список аккаунтов: ACCOUNT_1_EMAIL, ACCOUNT_1_PASS, ... до ACCOUNT_N_PASS."""
    accs = []
    i = 1
    while True:
        email = env.get(f"ACCOUNT_{i}_EMAIL")
        pwd = env.get(f"ACCOUNT_{i}_PASS")
        if not email or not pwd:
            break
        accs.append((email, pwd))
        i += 1
    return accs


def load_state():
    import json
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"date": "", "sent_today": 0, "account_idx": 0}


def save_state(state):
    import json
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f)


def within_hours(hours_str):
    try:
        start, end = hours_str.split("-")
        h = datetime.now().hour
        return int(start) <= h <= int(end)
    except Exception:
        return True


def get_pending():
    conn = gc.get_conn()
    rows = conn.execute(
        "SELECT * FROM sites WHERE status='pending' AND email IS NOT NULL ORDER BY id LIMIT 100"
    ).fetchall()
    conn.close()
    return rows


def send_one(account, to_email, settings, subject, body, site_url=""):
    # подставляем домен сайта вместо {site} — убирает "массовость"
    domain = ""
    try:
        from urllib.parse import urlparse
        domain = urlparse(site_url).netloc or site_url
    except Exception:
        domain = site_url
    subj = subject.replace("{site}", domain)
    bod = body.replace("{site}", domain)
    msg = MIMEText(bod, _charset="utf-8")
    msg["Subject"] = subj
    msg["From"] = f"{settings['FROM_NAME']} <{account[0]}>"
    msg["To"] = to_email

    server = smtplib.SMTP(settings["SMTP_HOST"], int(settings["SMTP_PORT"]), timeout=30)
    server.starttls()
    server.login(account[0], account[1])
    server.sendmail(account[0], [to_email], msg.as_string())
    server.quit()


def mark_sent(site_id):
    subprocess.run([sys.executable, TRACK, "send", str(site_id)],
                   capture_output=True, text=True, timeout=30)


def main():
    settings = load_settings()
    env = gc.load_env()
    accounts = get_accounts(env)

    if not accounts:
        gc.log("Net akkauntov v .env (ACCOUNT_1_EMAIL / ACCOUNT_1_PASS ...). Otpravka ostanovlena.", "SENDER")
        gc.record_pitfall(
            "Sender: pusto v .env",
            "agenty ne mogut otpravit pisma",
            "net zaponennyh ACCOUNT_x v .env",
            "zapolnit .env po .env.example, dobavit spare-gmail akkaunty"
        )
        return

    if not within_hours(settings["HOURS"]):
        gc.log(f"Vne okna otpravki ({settings['HOURS']}). Propuskaem.", "SENDER")
        return

    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("date") != today:
        state = {"date": today, "sent_today": 0, "account_idx": 0}

    subject, body = gc.load_letter()
    if not body:
        gc.log("Pismo pustoe (letter.txt). Otpravka ostanovlena.", "SENDER")
        return

    # дневной лимит с учётом warm-up, и лимит за прогон — считаем сами
    max_day = effective_day_limit(settings, env)
    max_run = effective_run_limit(settings, env, max_day)
    acc_sleep = int(settings["SLEEP_BETWEEN_ACCOUNTS_SEC"])
    gc.log(f"Limity: dnevnoj={max_day} (warmup={env.get('WARMUP_DAYS','0')}), za progon={max_run}", "SENDER")

    # --- случайный разброс отправки внутри часа (защита от спам-вида) ---
    # письма уходят НЕ пачкой в начале часа, а в случайные минуты часа
    min_gap = int(env.get("MIN_GAP_SEC", "20"))
    max_gap = int(env.get("MAX_GAP_SEC", "900"))
    start_jitter = int(env.get("RANDOM_START_SEC", "600"))
    # верхняя граница зазора, чтобы весь прогон уложился в ~55 мин (1 час минус запас).
    # учитываем стартовую задержку, чтобы прогон точно не вылез за час.
    safe_max = min(max_gap, int((3300 - min(start_jitter, 3300)) / max(1, max_run - 1)))

    pending = get_pending()
    sent_this_run = 0

    # случайная задержка старта: первое письмо тоже в случайную минуту часа
    if pending:
        start_delay = random.uniform(0, start_jitter)
        gc.log(f"Sluchaynyy start cherez {start_delay:.0f}s", "SENDER")
        time.sleep(start_delay)

    for row in pending:
        if state["sent_today"] >= max_day or sent_this_run >= max_run:
            gc.log(f"Limit: dnevnoj {state['sent_today']}/{max_day} ili za progon {sent_this_run}/{max_run}", "SENDER")
            break
        acc = accounts[state["account_idx"] % len(accounts)]
        gc.log(f"Otpravka #{row['id']} -> {row['email']} cherez {acc[0]}", "SENDER")
        try:
            send_one(acc, row["email"], settings, subject, body, row["url"])
            mark_sent(row["id"])
            state["sent_today"] += 1
            sent_this_run += 1
            state["account_idx"] = (state["account_idx"] + 1) % len(accounts)
            save_state(state)
            gc.log(f"OK otpravleno #{row['id']}", "SENDER")
        except Exception as e:
            gc.log(f"OSHIBKA otpravki #{row['id']}: {e}", "SENDER")
            gc.record_pitfall(
                "Sender: oshibka SMTP",
                str(e),
                "blok/nekorrektnyy parol/limit akkaunta",
                "proverit APP_PASSWORD, rotirovat akkaunt, umenshit MAX_PER_DAY"
            )
        # случайный разброс между письмами — следующее уйдёт в другую минуту часа
        if sent_this_run < max_run:
            gap = random.uniform(min_gap, safe_max)
            gc.log(f"Pauza do sleduyuschego pisma: {gap:.0f}s (sluchayno)", "SENDER")
            time.sleep(gap)
        time.sleep(acc_sleep)

    save_state(state)
    gc.log(f"Sender zavershen. Otpravleno v etom progone: {sent_this_run}", "SENDER")


if __name__ == "__main__":
    main()
