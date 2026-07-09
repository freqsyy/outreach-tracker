#!/usr/bin/env python3
"""gordon_status.py - эмитит status.json для Telegram-бота.

Берёт сводку из track.py stats + хвост gordon_run.log, скребёт любые строки
с PASS/password (защита от утечки секретов) и пишет status.json рядом.

Запуск:  python gordon_status.py
Используется в конце GitHub Actions прогона.
"""
import os
import sys
import json
import re
import subprocess
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TRACK = os.path.join(HERE, "track.py")
LOG = os.path.join(HERE, "gordon_run.log")
OUT = os.path.join(HERE, "status.json")

# паттерны, которые НЕ должны попасть в публичный status.json
SCRUB_RE = re.compile(r"(PASS|PASSWORD|SECRET|TOKEN|APP.?PASS)", re.I)
# скрываем начало email-аккаунтов в логе (info@... оставляем, аккаунты - нет)
ACCT_RE = re.compile(r"([a-z0-9._%+\-]+@gmail\.com)", re.I)


def get_stats():
    try:
        out = subprocess.run([sys.executable, TRACK, "stats"],
                             capture_output=True, text=True, timeout=30).stdout
        return out.strip()
    except Exception as e:
        return f"stats error: {e}"


def scrub(text):
    """Вырезает опасные строки и маскирует gmail-аккаунты отправителя."""
    lines = []
    for line in text.splitlines():
        if SCRUB_RE.search(line):
            lines.append("[scrubbed: possible secret]")
            continue
        line = ACCT_RE.sub("[acct]@gmail.com", line)
        lines.append(line)
    return "\n".join(lines)


def tail_log(n=60):
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-n:]
        return scrub("".join(lines))
    except FileNotFoundError:
        return "(log not found)"


def main():
    stats = get_stats()
    log_tail = tail_log(60)
    status = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "stats": stats,
        "log_tail": log_tail,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    print(f"[STATUS] status.json written ({len(json.dumps(status, ensure_ascii=False))} bytes)")


if __name__ == "__main__":
    main()
