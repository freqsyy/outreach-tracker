#!/usr/bin/env python3
"""
agent_parser.py — АГЕНТ 1 (Парсер).

Читает список URL из gordon_sources.txt (по одному на строку),
для каждого делает curl, вытаскивает email + Telegram,
заливает в track.py через `add`.

Запуск:  python agent_parser.py
"""

import os
import subprocess
import sys

import gordon_common as gc

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import dedup as dedup_mod
except ImportError:
    dedup_mod = None

SOURCES = os.path.join(HERE, "gordon_sources.txt")
TRACK = os.path.join(HERE, "track.py")


def fetch_url(url):
    """curl с таймаутом. Возвращает HTML или ''. WebFetch не берёт .by.
    Берём байты и декодируем с errors=ignore — HTML бывает в UTF-8,
    а терминал Windows в cp1251 (иначе UnicodeDecodeError)."""
    try:
        out = subprocess.run(
            ["curl", "-s", "-m", "20", "-L", url],
            capture_output=True, timeout=25  # без text=True -> байты
        )
        return out.stdout.decode("utf-8", errors="ignore") if out.stdout else ""
    except Exception as e:
        gc.log(f"curl fail {url}: {e}", "PARSER")
        return ""


def parse_and_add(url):
    """Парсит URL и добавляет контакты в БД.
    Возвращает: 'added' (добавлен новый), 'exists' (уже в БД),
    'skip' (недоступно / нет контактов)."""
    html = fetch_url(url)
    if not html:
        gc.log(f"Pusto / nedostupno: {url}", "PARSER")
        return "skip"
    emails, tgs = gc.extract_contacts(html)
    if not emails:
        gc.log(f"Kontaktov net: {url}", "PARSER")
        return "skip"
    # --- ДЕДУП (задача scout-2026-07-14-14): НЕ слать тому, кто уже
    # sent/bounced/rejected. READ-ONLY проверка; статусы не меняем. ---
    if dedup_mod is not None:
        for e in emails:
            dup, why = dedup_mod.is_already_contacted(url, e, " ".join(tgs) if tgs else None)
            if dup:
                gc.log(f"DEDUP: uzhe kontaktirovan {url} ({e}) -> {why}", "PARSER")
                return "skip"
    added = False
    for email in emails:
        tg = " ".join(tgs) if tgs else None
        cmd = [sys.executable, TRACK, "add", url,
               "--email", email, "--tags", "auto-parsed"]
        if tg:
            cmd += ["--tg", tg]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=30)
            out = res.stdout.decode("utf-8", errors="ignore") if res.stdout else ""
            for line in out.splitlines():
                gc.log(line, "PARSER")
            if "[!] Site already exists" in out:
                return "exists"
            if "[+] Added site" in out:
                added = True
        except Exception as e:
            gc.log(f"add fail {url}: {e}", "PARSER")
    return "added" if added else "skip"


def main():
    if not os.path.exists(SOURCES):
        gc.log("Net fayla gordon_sources.txt — sozday i dobav URL postrochno", "PARSER")
        return
    with open(SOURCES, "r", encoding="utf-8") as f:
        urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    if not urls:
        gc.log("Spisok istochnikov pust (gordon_sources.txt). Dobav URL cherez "
               "meniu «Dobavit URL v istochniki».", "PARSER")
        return
    gc.log(f"Start parser. Url v spiske: {len(urls)}", "PARSER")
    added = skipped = exists = 0
    for url in urls:
        res = parse_and_add(url)
        if res == "added":
            added += 1
        elif res == "exists":
            exists += 1
        else:
            skipped += 1
    gc.log(f"Parser zavershen. Novyh dobavleno: {added}. Uzhe v BD: {exists}. "
           f"Propushcheno (net kontaktov): {skipped}.", "PARSER")
    if added == 0 and exists:
        gc.log("Vse istochniki uzhe v baze — parsit nechego. Dobav novye URL "
               "cherez meniu «Dobavit URL v istochniki».", "PARSER")


if __name__ == "__main__":
    main()
