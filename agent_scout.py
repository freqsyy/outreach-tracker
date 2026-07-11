#!/usr/bin/env python3
"""
agent_scout.py - АГЕНТ 0 (Скаут). Ищет МОЛОДЫЕ сайты, а не мега-корпорации.

Источник: GitHub-дамп WebsiteLaunches/daily-website-launches - ежедневный топ-100
ТОЛЬКО ЧТО запущенных сайтов (2026/07/2026-07-09.md и т.д.). Это уже свежие,
маленькие проекты, которые только начали развиваться - именно то, что Назару нужно.

Фильтры (по просьбе Назара "искать по фильтрам создания и популярности"):
  1. ВОЗРАСТ домена через RDAP (registration event). Молодой = зарегистрирован
     недавно (< MAX_AGE_DAYS, по умолч. 730 дней = 2 года). Старые домены
     (переделанные/перезапущенные гиганты) отсекаем.
  2. ПОПУЛЯРНОСТЬ через Upvotes из дампа. Мега-популярные (> MAX_UPVOTES)
     отсекаем - там на нас не посмотрят.
  3. Контакт: как у парсера - curl страницы, вытаскиваем email + Telegram.

Прошедшие фильтр -> track.py add с тегами "auto-scout, fresh" и source "scout".

Запуск:
  python agent_scout.py                 # прогнать за последние 14 дней, залить в БД
  python agent_scout.py --days 30       # за месяц
  python agent_scout.py --dry-run       # только показать кандидатов, ничего не добавлять
  python agent_scout.py --max-age-days 365 --limit 15
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

import gordon_common as gc

HERE = os.path.dirname(os.path.abspath(__file__))
TRACK = os.path.join(HERE, "track.py")

# --- настраиваемые пороги ---
DEFAULT_DAYS = 14          # за сколько последних дней брать дампы
DEFAULT_MAX_AGE = 730      # домен моложе N дней = "молодой" (2 года)
DEFAULT_MAX_UPVOTES = 200  # upvotes выше = слишком раскрученный, пропускаем
DEFAULT_LIMIT = 40         # макс. новых сайтов за один прогон

DAILY_REPO = "WebsiteLaunches/daily-website-launches"
RANK_RE = re.compile(r"^###\s+#\d+\s+-\s+\[([^\]]+)\]\((https?://[^)]+)\)", re.M)
CAT_RE = re.compile(r"\*\*Category:\*\*\s*([^|]+)")
UP_RE = re.compile(r"\*\*Upvotes:\*\*\s*(\d+)")

VERISIGN_TLDS = {"com", "net", "org", "info", "biz", "name"}

# мусорные/placeholder email, которые парсер ловит из описаний и документации
JUNK_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "dev.io",
                      "test.com", "localhost", "invalid"}
JUNK_EMAIL_LOCAL = {"you", "your", "test", "noreply", "no-reply", "admin@example"}


def is_junk_email(email):
    e = email.lower()
    local, _, dom = e.partition("@")
    if dom in JUNK_EMAIL_DOMAINS:
        return True
    if local in JUNK_EMAIL_LOCAL:
        return True
    if e.endswith("@example.com") or "example" in dom:
        return True
    return False


def pick_contact(emails, site_domain):
    """Из набора email выбирает лучший: сначала на домене сайта (или субдомене),
    потом любой не-мусорный. Возвращает email или None."""
    clean = [e for e in emails if not is_junk_email(e)]
    same_domain = [e for e in clean
                   if e.lower().split("@")[-1] in (site_domain, "www." + site_domain)
                   or e.lower().endswith("." + site_domain)]
    if same_domain:
        return sorted(same_domain)[0]
    if clean:
        return sorted(clean)[0]
    return None


def fetch_text(url, timeout=25):
    """curl -> текст (как в парсере). Возвращает '' при ошибке."""
    try:
        out = subprocess.run(
            ["curl", "-s", "-m", str(timeout), "-L", url],
            capture_output=True, timeout=timeout + 5,
        )
        return out.stdout.decode("utf-8", errors="ignore") if out.stdout else ""
    except Exception as e:
        gc.log(f"curl fail {url}: {e}", "SCOUT")
        return ""


def fetch_json(url, timeout=20):
    txt = fetch_text(url, timeout)
    if not txt:
        return None
    try:
        return json.loads(txt)
    except Exception:
        return None


def list_daily_files(days):
    """Генерим список URL дневных дампов за последние N дней (новые -> старые)."""
    files = []
    today = datetime.now()
    for i in range(days):
        d = today - timedelta(days=i)
        ymd = d.strftime("%Y-%m-%d")
        y, m = d.strftime("%Y"), d.strftime("%m")
        url = f"https://raw.githubusercontent.com/{DAILY_REPO}/main/{y}/{m}/{ymd}.md"
        files.append((ymd, url))
    return files


def parse_daily(md):
    """Из текста дневного дампа -> список (domain, url, category, upvotes)."""
    out = []
    for block in RANK_RE.finditer(md):
        domain = block.group(1).strip().lower()
        url = block.group(2).strip()
        # категория и upvotes берём из куска вокруг совпадения
        snippet = md[block.end(): block.end() + 400]
        cat_m = CAT_RE.search(snippet)
        up_m = UP_RE.search(snippet)
        cat = cat_m.group(1).strip() if cat_m else ""
        up = int(up_m.group(1)) if up_m else 0
        out.append((domain, url, cat, up))
    return out


def domain_age_days(domain):
    """Возраст домена в днях через RDAP. None = не удалось узнать."""
    tld = domain.split(".")[-1].lower()
    urls = []
    if tld in VERISIGN_TLDS:
        urls.append(f"https://rdap.verisign.com/{tld}/v1/domain/{domain}")
    urls.append(f"https://rdap.org/domain/{domain}")  # bootstrap для остальных
    for u in urls:
        js = fetch_json(u)
        if not js:
            continue
        reg = None
        for ev in js.get("events", []):
            if ev.get("eventAction") == "registration":
                reg = ev.get("eventDate")
                break
        if not reg:
            continue
        try:
            s = reg.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo:
                dt = dt.replace(tzinfo=None)
            return (datetime.now() - dt).days
        except Exception:
            return None
    return None


def add_to_db(url, email, tg, notes):
    cmd = [sys.executable, TRACK, "add", url,
           "--email", email, "--tags", "auto-scout,fresh",
           "--source", "scout", "--notes", notes]
    if tg:
        cmd += ["--tg", tg]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=30)
        out = res.stdout.decode("utf-8", errors="ignore") if res.stdout else ""
        for line in out.splitlines():
            gc.log(line, "SCOUT")
    except Exception as e:
        gc.log(f"add fail {url}: {e}", "SCOUT")


def run(days, max_age, max_upvotes, limit, dry):
    gc.log(f"=== SCOUT start: days={days} max_age={max_age} max_up={max_upvotes} limit={limit} dry={dry} ===", "SCOUT")
    files = list_daily_files(days)
    seen = set()
    added = 0
    scanned = 0

    for ymd, url in files:
        md = fetch_text(url)
        if not md or "404" in md[:20]:
            continue
        sites = parse_daily(md)
        gc.log(f"[{ymd}] najdeno v dampe: {len(sites)} sajtov", "SCOUT")
        for domain, site_url, cat, up in sites:
            if domain in seen:
                continue
            seen.add(domain)
            if added >= limit:
                break
            scanned += 1

            # фильтр популярности
            if up > max_upvotes:
                gc.log(f"PROPUSCHEN (mega-popular {up} up): {domain}", "SCOUT")
                continue

            # фильтр возраста
            age = domain_age_days(domain)
            if age is not None and age > max_age:
                gc.log(f"PROPUSCHEN (old {age}d): {domain}", "SCOUT")
                continue

            # достаём контакты
            html = fetch_text(site_url, timeout=20)
            if not html:
                gc.log(f"NEDOSTUPNO: {domain}", "SCOUT")
                continue
            emails, tgs = gc.extract_contacts(html)
            if not emails:
                gc.log(f"KONTAKTOV NET: {domain}", "SCOUT")
                continue
            email = pick_contact(emails, domain)
            if not email:
                gc.log(f"TOLKO MUSOR-EMAIL: {domain} ({sorted(emails)[:2]})", "SCOUT")
                continue

            age_s = f"{age}d" if age is not None else "?"
            notes = f"scout: launched~{ymd}, cat={cat}, up={up}, age={age_s}"
            if dry:
                gc.log(f"[DRY] CANDIDATE: {domain} ({cat}, {up}up, age {age_s}) -> {email}", "SCOUT")
                added += 1
                continue

            tg = " ".join(tgs) if tgs else None
            add_to_db(site_url, email, tg, notes)
            added += 1
            if added >= limit:
                break
        if added >= limit:
            break

    gc.log(f"=== SCOUT done: proskanirovano={scanned} dobavleno={added} ===", "SCOUT")


def main():
    ap = argparse.ArgumentParser(description="Гордон-Скаут: ищет молодые сайты")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE)
    ap.add_argument("--max-upvotes", type=int, default=DEFAULT_MAX_UPVOTES)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--dry-run", action="store_true", help="только показать кандидатов")
    args = ap.parse_args()
    run(args.days, args.max_age_days, args.max_upvotes, args.limit, args.dry_run)


if __name__ == "__main__":
    main()
