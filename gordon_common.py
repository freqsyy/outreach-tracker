#!/usr/bin/env python3
"""
gordon_common.py — общий модуль для всех агентов Гордона.

Один источник правды: база outreach.db (через track.py) + настройки из .env.
Только стандартная библиотека Python (как и в track.py).
"""

import os
import re
import sqlite3
from datetime import datetime, timedelta

# --- Пути ---
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "outreach.db")
ENV_PATH = os.path.join(HERE, ".env")
LOG_PATH = os.path.join(HERE, "gordon_run.log")
PITFALLS_PATH = os.path.join(HERE, "gordon_pitfalls.md")

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
TG_RE = re.compile(r"t\.me/([a-zA-Z0-9_]+)|@([a-zA-Z0-9_]{4,32})")


def log(msg, agent="GORDON"):
    """Пишет строку в gordon_run.log с меткой времени."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{agent}] {msg}\n"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass  # лог не должен ломать агента
    # принудительно utf-8, чтобы кириллица не плыла в cp1251-терминале
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(line, end="")


def load_env():
    """Читает .env вручную (без внешних библиотек).
    Возвращает dict. Поддерживает APP_PASSWORD_x=... построчно."""
    env = {}
    if not os.path.exists(ENV_PATH):
        return env
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            env[key.strip()] = val.strip()
    return env


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def record_pitfall(title, error, cause, solution):
    """Дописывает грабль в gordon_pitfalls.md (карта камней по методу Назара)."""
    header = "## " + title
    block = (
        f"\n{header}\n"
        f"- Ошибка: {error}\n"
        f"- Причина: {cause}\n"
        f"- Решение: {solution}\n"
        f"- Замечено: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    )
    try:
        existing = ""
        if os.path.exists(PITFALLS_PATH):
            with open(PITFALLS_PATH, "r", encoding="utf-8") as f:
                existing = f.read()
        if title not in existing:
            with open(PITFALLS_PATH, "a", encoding="utf-8") as f:
                f.write(block)
            log(f"Grabel zafiksirovan: {title}", "PITFALL")
    except Exception as e:
        log(f"Ne udalos zapisat pitfall: {e}", "PITFALL")


def extract_contacts(html):
    """Из HTML вытаскивает (emails, telegrams)."""
    emails = set()
    for m in EMAIL_RE.findall(html):
        emails.add(m.lower())
    tgs = set()
    for m in TG_RE.findall(html):
        handle = m[0] or m[1]
        if handle:
            tgs.add("@" + handle)
    return emails, tgs


# Путь к письму (UTF-8, нормальная кириллица — не транслит)
LETTER_PATH = os.path.join(HERE, "letter.txt")


def load_letter():
    """Читает письмо из letter.txt.
    Формат:
      Subject: <тема>
      <пустая строка>
      <тело>
    Возвращает (subject, body)."""
    subject = "Predlozhenie po testirovaniyu vashego sayta"
    body = ""
    try:
        with open(LETTER_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        if lines and lines[0].lower().startswith("subject:"):
            subject = lines[0].split(":", 1)[1].strip()
            body = "\n".join(lines[2:])  # пропускаем Subject и пустую строку
    except Exception as e:
        log(f"Ne udalos prochitat letter.txt: {e}", "COMMON")
    return subject, body
