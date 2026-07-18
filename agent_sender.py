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
import hashlib
from datetime import datetime
from email.mime.text import MIMEText

import gordon_common as gc

HERE = os.path.dirname(os.path.abspath(__file__))
TRACK = os.path.join(HERE, "track.py")
STATE_PATH = os.path.join(HERE, "gordon_send_state.json")

# Настройки по умолчанию (переопределяются из .env)
# ЖЁСТКИЙ ОКОННЫЙ РЕЖИМ (send_window):
# - между письмами с ОДНОГО аккаунта — СЛУЧАЙНАЯ задержка 15-30 минут
#   (имитация живого человека, защита от спам-вида Gmail)
# - общий лимит на систему — 4-5 писем в час (PER_ACCOUNT_PER_HOUR учитывает
#   число аккаунтов, но жёстко не выше HARD_HOURLY_CAP)
# - MAX_PER_DAY — потолок писем с ОДНОГО аккаунта за сутки (~20)
# - рассылка растягивается плавно в течение окна HOURS (9-21)
DEFAULTS = {
    "MAX_PER_RUN": "5",
    "MAX_PER_DAY": "12",
    "SEND_INTERVAL_SEC": "30",
    "HOURS": "9-21",
    "SMTP_HOST": "smtp.gmail.com",
    "SMTP_PORT": "587",
    "FROM_NAME": "Nazar",
    "SLEEP_BETWEEN_ACCOUNTS_SEC": "5",
    # --- жёсткий оконный режим ---
    "PER_ACCOUNT_PER_HOUR": "5",   # сколько писем/час с ОДНОГО аккаунта
    "HARD_HOURLY_CAP": "5",       # абсолютный потолок писем/час на систему (4-5)
    "MAX_PER_ACCOUNT_DAY": "20",  # максимум писем/сутки с ОДНОГО аккаунта
    "GAP_MIN_MIN": "15",           # мин задержка между письмами с аккаунта (минуты)
    "GAP_MAX_MIN": "30",           # макс задержка между письмами с аккаунта (минуты)
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


def effective_hourly_cap(settings, env, n_accounts):
    """Жёсткий лимит писем в ЧАС на систему (4-5).
    per_account_per_hour даёт ориентир (с одного аккаунта), но общий потолок
    НЕ выше HARD_HOURLY_CAP независимо от числа аккаунтов.
    То есть: с 5 акков шлём ~1/час каждый (в сумме до 5), а не 5 с каждого."""
    try:
        per_acc = int(settings["PER_ACCOUNT_PER_HOUR"])
    except Exception:
        per_acc = 5
    per_acc = max(1, per_acc)
    try:
        cap = int(settings["HARD_HOURLY_CAP"])
    except Exception:
        cap = 5
    cap = max(1, cap)
    # суммарно с аккаунтов не больше cap; но и не меньше per_acc (если акков мало)
    return min(cap, max(per_acc, per_acc * n_accounts)) if n_accounts <= cap else cap


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
    return {"date": "", "hour": -1, "sent_today": 0, "sent_this_hour": 0,
            "account_idx": 0, "per_acct_today": {}}


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


def send_one(account, to_email, settings, subject, body, site_url="", notes=""):
    # v0.5: текст уже собран в agent_sender.main() через gc.compose_unique_letter()
    # (рандом шаблон + человеческий баг + ниша). Здесь только НЕПРОЙДЕННЫЕ
    # плейсхолдеры: {site} (если compose не сработал и прилетел старый шаблон).
    domain = ""
    try:
        from urllib.parse import urlparse
        domain = urlparse(site_url).netloc or site_url
    except Exception:
        domain = site_url
    subj = subject.replace("{site}", domain)
    bod = body.replace("{site}", domain)
    # страховка: если compose не подставил {bug} (старый letter.txt фоллбэк) —
    # не оставляем пустой плейсхолдер в письме.
    if "{bug}" in subj or "{bug}" in bod:
        bug = gc.extract_audit_bug(notes) or ""
        subj = subj.replace("{bug}", bug)
        bod = bod.replace("{bug}", bug)
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
    this_hour = datetime.now().hour
    if state.get("date") != today:
        state = {"date": today, "hour": this_hour, "sent_today": 0,
                 "sent_this_hour": 0, "account_idx": 0, "per_acct_today": {}}
    elif state.get("hour") != this_hour:
        # новый час — сбрасываем почасовой счётчик (дневной оставляем)
        state["hour"] = this_hour
        state["sent_this_hour"] = 0

    # v0.5: базовый фоллбэк-шаблон (если letters/templates.txt пуст/
    # сломан — используем старый letter.txt как запасной). compose_unique_letter
    # сам проверит наличие AUDIT:: бага и вернёт None, если бага нет.
    fallback_subj, fallback_body = gc.load_letter()
    if not fallback_body:
        fallback_subj, fallback_body = "Предложение по тестированию вашего сайта", ""

    # дневной лимит с учётом warm-up, и лимит за прогон — считаем сами
    max_day = effective_day_limit(settings, env)
    # жёсткий часовой потолок системы (4-5 писем/час)
    hourly_cap = effective_hourly_cap(settings, env, len(accounts))
    # лимит за прогон НЕ выше часового потолка системы (4-5 писем/час)
    max_run = effective_run_limit(settings, env, max_day)
    max_run = min(max_run, hourly_cap)
    # потолок писем/сутки с ОДНОГО аккаунта (~20)
    try:
        max_per_acct_day = int(settings["MAX_PER_ACCOUNT_DAY"])
    except Exception:
        max_per_acct_day = 20
    max_per_acct_day = max(1, max_per_acct_day)
    acc_sleep = int(settings["SLEEP_BETWEEN_ACCOUNTS_SEC"])
    gc.log(
        f"Limity: dnevnoj={max_day} (warmup={env.get('WARMUP_DAYS','0')}), "
        f"za progon={max_run}, V CHAS={hourly_cap}, s akkaunta/den={max_per_acct_day}",
        "SENDER",
    )

    # --- ЖЁСТКИЙ ОКОННЫЙ РЕЖИМ: случайная задержка 15-30 мин МЕЖДУ письмами ---
    # с одного аккаунта. Имитация живого человека, защита от спам-вида Gmail.
    try:
        gap_min = int(settings["GAP_MIN_MIN"])
    except Exception:
        gap_min = 15
    try:
        gap_max = int(settings["GAP_MAX_MIN"])
    except Exception:
        gap_max = 30
    if gap_max < gap_min:
        gap_max = gap_min
    start_jitter = random.uniform(gap_min * 60, gap_max * 60) / 2.0  # старт в пределах окна
    safe_max = gap_max * 60  # верхний зазор = максимум окна (30 мин в сек)

    pending = get_pending()
    sent_this_run = 0
    # v0.5: окно хэшей ПОСЛЕДНИХ (до 10) отправленных тел — чтобы бот
    # не чередовал 2 шаблона по кругу (А->Б->А->Б). compose_unique_letter
    # сам выберет другой шаблон, если сгенерённый попал в окно.
    recent_hashes = []

    # случайная задержка старта: первое письмо тоже в случайную минуту часа
    if pending:
        start_delay = random.uniform(0, start_jitter)
        gc.log(f"Sluchaynyy start cherez {start_delay:.0f}s", "SENDER")
        time.sleep(start_delay)

    for row in pending:
        if state["sent_today"] >= max_day or sent_this_run >= max_run:
            gc.log(f"Limit: dnevnoj {state['sent_today']}/{max_day} ili za progon {sent_this_run}/{max_run}", "SENDER")
            break
        if state.get("sent_this_hour", 0) >= hourly_cap:
            gc.log(f"Limit V CHAS dostignut: {state['sent_this_hour']}/{hourly_cap}. Stop etot progon.", "SENDER")
            break
        acc = accounts[state["account_idx"] % len(accounts)]
        # v0.5: динамическая генерация УНИКАЛЬНОГО письма по реальному аудиту.
        # compose_unique_letter берёт AUDIT:: баг из notes, переводит его на
        # человеческий язык, подставляет нишу из tags, рандомит шаблон/
        # приветствие/CTA. ЕСЛИ бага НЕТ (нет AUDIT::) — возвращает None:
        # НЕ шлём шаблонный спам, сайт остаётся pending (аудитор дойдёт до
        # него в след. прогоне). Это защита от слива базы в спам.
        composed = gc.compose_unique_letter(
            row["url"], row["notes"] or "", row["tags"] or "",
            recent_hashes=recent_hashes[-10:])
        if composed is None:
            gc.log(f"NET BUGA -> propusk #{row['id']} (ostayotsya pending, zhdym audita)", "SENDER")
            continue
        subject, body = composed
        # лимит писем/сутки с ОДНОГО аккаунта (~20)
        acc_sent_today = state.get("per_acct_today", {}).get(acc[0], 0)
        if acc_sent_today >= max_per_acct_day:
            gc.log(f"Akkount {acc[0]} dostig dnevnogo potolka {max_per_acct_day}. Rotaciya dal'she.", "SENDER")
            state["account_idx"] = (state["account_idx"] + 1) % len(accounts)
            save_state(state)
            # если все аккаунты упёрлись в потолок — выходим
            if all(state.get("per_acct_today", {}).get(a[0], 0) >= max_per_acct_day for a in accounts):
                gc.log("Vse akkaunty dostigli dnevnogo potolka. Stop.", "SENDER")
                break
            continue
        gc.log(f"Otpravka #{row['id']} -> {row['email']} cherez {acc[0]} (unikal'noe pismo)", "SENDER")
        try:
            send_one(acc, row["email"], settings, subject, body, row["url"], row["notes"] or "")
            # фиксируем хэш тела в окне последних 10 (защита от А->Б->А->Б)
            recent_hashes.append(hashlib.sha256(body.encode("utf-8")).hexdigest())
            if len(recent_hashes) > 10:
                recent_hashes = recent_hashes[-10:]
            mark_sent(row["id"])
            state["sent_today"] += 1
            state["sent_this_hour"] = state.get("sent_this_hour", 0) + 1
            sent_this_run += 1
            state.setdefault("per_acct_today", {})[acc[0]] = acc_sent_today + 1
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
        # ЖЁСТКИЙ разброс 15-30 мин между письмами (имитация человека)
        if sent_this_run < max_run and state.get("sent_this_hour", 0) < hourly_cap:
            gap = random.uniform(gap_min * 60, gap_max * 60)
            gc.log(f"Pauza do sleduyuschego pisma: {gap/60:.1f} min (sluchayno 15-30)", "SENDER")
            time.sleep(gap)
        time.sleep(acc_sleep)

    save_state(state)
    gc.log(f"Sender zavershen. Otpravleno v etom progone: {sent_this_run}", "SENDER")


if __name__ == "__main__":
    main()
