#!/usr/bin/env python3
"""
send_now.py — РАЗОВЫЙ запуск отправки ВНЕ окна 9-21 (по прямой команде).

Переиспользует штатную логику agent_sender: send_one(), mark_sent(),
get_accounts(), load_letter(). Только снимает ночной гард (within_hours)
и лимит прогона, ставит "по 5 на аккаунт". .env НЕ меняется.

Запуск:  python send_now.py [per_account=5]
"""

import os
import random
import sys
import time
from datetime import datetime

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
    per_account = int(sys.argv[1]) if len(sys.argv) > 1 else 5

    env = gc.load_env()
    accounts = s.get_accounts(env)
    if not accounts:
        gc.log("Net akkauntov v .env. Ostanovka.", "SEND_NOW")
        return

    settings = s.load_settings()
    subject, body = gc.load_letter()
    if not body:
        gc.log("Pismo pustoe (letter.txt). Ostanovka.", "SEND_NOW")
        return

    total = per_account * len(accounts)
    gc.log(f"FORSNYJ ZAPUSK vne okna. Cel: {total} pisem ({per_account}/akk x{len(accounts)} akkauntov).", "SEND_NOW")

    # новые сайты — первыми (DESC по id), чтобы «только что добавленные» ушли вперёд
    # ФИКС БАГА #1: дедуп по email (LOWER) — один адрес получает ровно одно
    # письмо, даже если на него заведено несколько сайтов.
    conn = gc.get_conn()
    pending = conn.execute(
        "SELECT * FROM sites WHERE status='pending' AND email IS NOT NULL "
        "AND id IN ("
        "  SELECT MIN(id) FROM sites "
        "  WHERE status='pending' AND email IS NOT NULL "
        "  GROUP BY LOWER(email)"
        ") ORDER BY id DESC LIMIT 200"
    ).fetchall()
    conn.close()
    if not pending:
        gc.log("Net pending-saytov. Otpravka ne trebuetsya.", "SEND_NOW")
        return

    # случайный разброс — чтобы не пачкой (защита от спам-вида), но компактно
    min_gap = max(15, int(env.get("MIN_GAP_SEC", "20")))
    max_gap = 40  # укладываемся ~10 мин на 25 писем
    acc_sleep = int(settings["SLEEP_BETWEEN_ACCOUNTS_SEC"])

    sent = 0
    # ФИКС БАГА #2/#5: ротируем аккаунты из общего state (account_idx),
    # чтобы этот прогон продолжал очередь, а не бил один и тот же аккаунт с
    # нуля. И пишем прогресс в state после каждой отправки.
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("date") != today:
        # новый день — обнуляем счётчик, НО сохраняем account_idx (продолжаем ротацию)
        prev_idx = state.get("account_idx", 0)
        state = {"date": today, "sent_today": 0, "account_idx": prev_idx}
    acc_i = state.get("account_idx", 0)

    # кратковременная стартовая задержка
    start_delay = random.uniform(0, 30)
    gc.log(f"Start cherez {start_delay:.0f}s", "SEND_NOW")
    time.sleep(start_delay)

    for row in pending:
        if sent >= total:
            break
        acc = accounts[acc_i % len(accounts)]
        gc.log(f"Otpravka #{row['id']} -> {row['email']} cherez {acc[0]}", "SEND_NOW")
        try:
            s.send_one(acc, row["email"], settings, subject, body, row["url"], row["notes"] or "")
            s.mark_sent(row["id"])
            sent += 1
            acc_i = (acc_i + 1) % len(accounts)
            state["sent_today"] = state.get("sent_today", 0) + 1
            state["account_idx"] = acc_i
            save_state(state)
            gc.log(f"OK otpravleno #{row['id']} ({sent}/{total})", "SEND_NOW")
        except Exception as e:
            gc.log(f"OSHIBKA otpravki #{row['id']}: {e}", "SEND_NOW")
        if sent < total:
            gap = random.uniform(min_gap, max_gap)
            time.sleep(gap)
        time.sleep(acc_sleep)

    gc.log(f"SEND_NOW zavershen. Otpravleno: {sent}/{total}", "SEND_NOW")


if __name__ == "__main__":
    main()
