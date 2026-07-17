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

# --- Грязные контакты: парсер часто хватает CSS-селекторы/плейсхолдеры вместо
# реальных email (sprite@x2.png, example@mail.ru, %20don.alfa-k@yandex.ru).
# Этот фильтр централизован: питает И parser, И scout (оба зовут extract_contacts).
# После фикса баунс-воронка чистая, deliverability растёт.
_PLACEHOLDER_DOMAINS = {
    "example.com", "example.net", "example.org",
    "test-new-site-12345.by", "localhost", "test.com", "abc.com",
}
_PLACEHOLDER_LOCAL = {
    "example", "test", "demo", "user", "foo", "bar",
    "noreply", "no-reply", "noreply", "postmaster", "root",
}
# мусорные хвосты в домене (картинки/спрайты вместо TLD)
_IMG_DOMAIN_RE = re.compile(
    r"\.(png|jpe?g|webp|gif|svg|bmp|ico)$", re.I
)
# мусорные куски в локальной части (x2 / 2x / px / .png прямо в ящике)
_IMG_LOCAL_RE = re.compile(r"(^|[\w.\-+])+(2x|x\d|\dx|\d+px|\.png|\.jpg|\.webp|\.svg)", re.I)


def is_valid_email(email):
    """True только для похожего на реальный контакт email.
    Отсекает: плейсхолдеры, картинки/спрайты, %20, пробелы, домены-заглушки.
    Сохраняет легит admin@ / info@ / sales@ и т.п."""
    e = (email or "").strip().replace("%20", "").strip().lower()
    if not e or "%20" in e or " " in e:
        return False
    if not EMAIL_RE.fullmatch(e):
        return False
    local, _, dom = e.rpartition("@")
    if not local or not dom:
        return False
    if dom in _PLACEHOLDER_DOMAINS:
        return False
    if local in _PLACEHOLDER_LOCAL:
        return False
    if _IMG_DOMAIN_RE.search(dom):
        return False
    if _IMG_LOCAL_RE.search(local):
        return False
    return True
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
    """Из HTML вытаскивает (emails, telegrams).
    Email фильтруются через is_valid_email — отсекаем CSS-мусор/плейсхолдеры."""
    emails = set()
    for m in EMAIL_RE.findall(html):
        if is_valid_email(m):
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


# --- Извлечение бага из notes для персонализации письма (v0.4 core-funnel) ---
# Формат строки аудита в notes: AUDIT::<severity>::<type>::<description>[::N]
# Пишется agent_auditor.py через audit_engine.bug_to_note().
_SEVERITY_RANK = {"critical": 3, "high": 3, "medium": 2, "low": 1, "info": 0}


def extract_audit_bug(notes):
    """Извлекает самый серьёзный баг из notes сайта и форматирует его для письма.

    Возвращает короткую человекочитаемую строку (без служебных маркеров/severity)
    или пустую строку "", если багов нет. Никогда не бросает исключение.

    Приоритет: critical/high > medium > low > info. При равенстве — первый по порядку.
    """
    if not notes:
        return ""
    best = None
    best_rank = -1
    for raw in str(notes).splitlines():
        line = raw.strip()
        if not line.startswith("AUDIT::"):
            continue
        parts = line.split("::")
        # parts[0]='AUDIT', [1]=severity, [2]=type, [3]=description, [4]=опц. счётчик
        if len(parts) < 4:
            continue
        severity = (parts[1] or "").strip().lower()
        btype = (parts[2] or "").strip()
        desc = (parts[3] or "").strip()
        if not desc:
            continue
        rank = _SEVERITY_RANK.get(severity, 0)
        if rank > best_rank:
            best_rank = rank
            # Тип полезен как контекст ("Форма", "Console"), но не обязателен.
            if btype and btype.lower() not in desc.lower():
                best = f"{btype}: {desc}"
            else:
                best = desc
    if not best:
        return ""
    # Обрезаем слишком длинные описания — письмо должно быть коротким.
    best = " ".join(best.split())  # схлопываем переводы строк/пробелы
    if len(best) > 220:
        best = best[:217].rstrip() + "..."
    return best
