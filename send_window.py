#!/usr/bin/env python3
"""
send_window.py — ФОРСИРОВАННАЯ рассылка на окно времени (по прямой команде).

Переиспользует штатную логику agent_sender: send_one(), mark_sent(),
get_accounts(), load_letter(). Растягивает отправку равномерно на WINDOW_SEC
секунд, ротируя аккаунты по кругу (по per_account с каждого).

Запуск:  python send_window.py [per_account=7] [window_sec=7200]
"""
import os
import random
import sys
import time

import gordon_common as gc
import agent_sender as s

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "gordon_send_state.json")


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


def main():
    per_account = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    window_sec = int(sys.argv[2]) if len(sys.argv) > 2 else 7200

    env = gc.load_env()
    accounts = s.get_accounts(env)
    if not accounts:
        gc.log("Net akkauntov v .env. Ostanovka.", "WINDOW")
        return

    settings = s.load_settings()
    subject, body = gc.load_letter()
    if not body:
        gc.log("Pismo pustoe (letter.txt). Ostanovka.", "WINDOW")
        return

    total = per_account * len(accounts)
    # равномерный зазор: делим окно на число писем, минус запас на SMTP-коннект
    base_gap = max(15, int((window_sec - 60) / max(1, total)))
    jitter = int(base_gap * 0.2)
    acc_sleep = int(settings["SLEEP_BETWEEN_ACCOUNTS_SEC"])

    gc.log(
        f"FORSNYJ ZAPUSK NA OKNO {window_sec}s. Cel: {total} pisem "
        f"({per_account}/akk x{len(accounts)} akkauntov). Bazovyy zazor ~{base_gap}s.",
        "WINDOW",
    )

    pending = s.get_pending()
    if len(pending) < total:
        gc.log(
            f"Vnimanie: pending s email tolko {len(pending)}, otpravim stolko, skolko est.",
            "WINDOW",
        )
        total = len(pending)

    start_delay = random.uniform(0, min(30, base_gap))
    gc.log(f"Start cherez {start_delay:.0f}s", "WINDOW")
    time.sleep(start_delay)

    sent = 0
    acc_i = 0
    for row in pending:
        if sent >= total:
            break
        acc = accounts[acc_i % len(accounts)]
        gc.log(f"Otpravka #{row['id']} -> {row['email']} cherez {acc[0]}", "WINDOW")
        try:
            s.send_one(acc, row["email"], settings, subject, body, row["url"])
            s.mark_sent(row["id"])
            sent += 1
            acc_i += 1
            gc.log(f"OK otpravleno #{row['id']} ({sent}/{total})", "WINDOW")
        except Exception as e:
            gc.log(f"OSHIBKA otpravki #{row['id']}: {e}", "WINDOW")
        if sent < total:
            gap = base_gap + random.uniform(-jitter, jitter)
            gc.log(f"Pauza {gap:.0f}s do sleduyuschego pisma", "WINDOW")
            time.sleep(gap)
        time.sleep(acc_sleep)

    # обновляем state (чтобы штатный лимит 9-21 знал про отправку)
    from datetime import datetime
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("date") != today:
        # ФИКС БАГА #2: НЕ сбрасываем account_idx при старте — иначе
        # параллельный прогон бьёт тот же аккаунт. Продолжаем ротацию.
        prev_idx = state.get("account_idx", 0)
        state = {"date": today, "sent_today": 0, "account_idx": prev_idx}
    state["sent_today"] = state.get("sent_today", 0) + sent
    save_state(state)

    gc.log(f"WINDOW zavershen. Otpravleno: {sent}/{total}", "WINDOW")


if __name__ == "__main__":
    main()
