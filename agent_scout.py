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

Прошедшие фильтр -> track.py add с тегами "auto-scout, fresh", source "scout".
ПО УМОЛЧАНИЮ статус = "review" (НЕ готов к рассылке) - авто-найденные контакты
требуют ручного подтверждения (track.py edit --status pending), чтобы не слать
спам по адресам, вытащенным из чужих сайтов. Ключ --auto-approve ставит сразу
"pending" - только если Назар точно хочет.

БЕЗОПАСНОСТЬ: URL сайтов и домены берутся из публичного дампа (недоверенный ввод).
  - SSRF: перед фетчем URL проверяется (scheme http/https, без учётных данных,
    хост не loopback/private/link-local). Редиректы не следуются (-L убран).
  - домен валидируется строгой регуляркой до встраивания в RDAP-URL.

Запуск:
  python agent_scout.py                 # прогнать за последние 14 дней, залить в БД
  python agent_scout.py --days 30       # за месяц
  python agent_scout.py --dry-run       # только показать кандидатов, ничего не добавлять
  python agent_scout.py --max-age-days 365 --limit 15
  python agent_scout.py --auto-approve  # сразу pending (ГОТОВО К РАССЫЛКЕ, осторожно!)
"""

import argparse
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timedelta
from urllib.parse import urlsplit

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

# строгий паттерн зарегистрированного домена (без '/', '@', '?', '#', учётных данных)
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")

# мусорные/placeholder email, которые парсер ловит из описаний и документации
JUNK_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "dev.io",
                      "test.com", "localhost", "invalid"}
JUNK_EMAIL_LOCAL = {"you", "your", "test", "noreply", "no-reply", "admin@example"}


def is_safe_domain(domain):
    """Строгая валидация домена перед встраиванием в RDAP-URL (фикс SSRF #2)."""
    return bool(DOMAIN_RE.match(domain or ""))


def url_is_safe(url):
    """Проверяет, что URL можно фетчить: только http/https, публичный хост,
    без учётных данных, не loopback/private/link-local (фикс SSRF #1)."""
    try:
        p = urlsplit(url)
    except Exception:
        return False, "bad url"
    if p.scheme not in ("http", "https"):
        return False, f"schema {p.scheme}"
    if p.username or p.password:
        return False, "credentials in url"
    host = (p.hostname or "").strip().lower()
    if not host:
        return False, "no host"
    # домен (не IP) — ок
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host) and ":" not in host:
        return True, ""
    # IP — отбиваем приватные/loopback/link-local
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # хост с портом или IPv6 без скобок — перестраховка
        try:
            ip = ipaddress.ip_address(host.split(":")[0])
        except ValueError:
            return True, ""  # не смогли разобрать как IP, пусть доменная ветка решает
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return False, f"blocked ip {ip}"
    return True, ""


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
    """curl -> текст (как в парсере). БЕЗ -L: редиректы не следуем (фикс SSRF #1,
    чтобы финальный URL не уводил на внутренний адрес). Возвращает '' при ошибке."""
    try:
        out = subprocess.run(
            ["curl", "-s", "-m", str(timeout), url],
            capture_output=True, timeout=timeout + 5,
        )
        return out.stdout.decode("utf-8", errors="ignore") if out.stdout else ""
    except Exception as e:
        gc.log(f"curl fail {url}: {e}", "SCOUT")
        return ""


def fetch_site(url, timeout=20):
    """Фетч страницы сайта ТОЛЬКО после SSRF-проверки (фикс SSRF #1)."""
    ok, why = url_is_safe(url)
    if not ok:
        gc.log(f"SSRF BLOCK: {url} ({why})", "SCOUT")
        return ""
    return fetch_text(url, timeout)


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
    """Возраст домена в днях через RDAP. None = не удалось узнать.
    Фикс SSRF #2: домен строго валидируем до встраивания в URL."""
    if not is_safe_domain(domain):
        gc.log(f"SSRF BLOCK domain: {domain!r}", "SCOUT")
        return None
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


def add_to_db(url, email, tg, notes, status):
    """status='review' по умолчанию (фикс #3): авто-найденные контакты НЕ готовы
    к рассылке, требуют ручного подтверждения. Перевод в 'pending' - только вручную
    через track.py edit --status pending ИЛИ ключом --auto-approve."""
    if status == "pending":  # явный аппрув от пользователя
        st = "pending"
    else:
        st = "review"
    cmd = [sys.executable, TRACK, "add", url,
           "--email", email, "--tags", "auto-scout,fresh",
           "--source", "scout", "--status", st, "--notes", notes]
    if tg:
        cmd += ["--tg", tg]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=30)
        out = res.stdout.decode("utf-8", errors="ignore") if res.stdout else ""
        for line in out.splitlines():
            gc.log(line, "SCOUT")
    except Exception as e:
        gc.log(f"add fail {url}: {e}", "SCOUT")


def run(days, max_age, max_upvotes, limit, dry, auto_approve):
    gc.log(f"=== SCOUT start: days={days} max_age={max_age} max_up={max_upvotes} limit={limit} dry={dry} auto_approve={auto_approve} ===", "SCOUT")
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

            # достаём контакты (только после SSRF-проверки URL)
            html = fetch_site(site_url, timeout=20)
            if not html:
                gc.log(f"NEDOSTUPNO/BLOCK: {domain}", "SCOUT")
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
            # по умолчанию status='review' (защита от спама), см. fix #3
            add_to_db(site_url, email, tg, notes, "pending" if auto_approve else "review")
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
    ap.add_argument("--auto-approve", action="store_true",
                    help="добавлять сразу как pending (ГОТОВО К РАССЫЛКЕ). "
                         "БЕЗ ключа скаут ставит статус 'review' - нужно ручное подтверждение")
    args = ap.parse_args()
    run(args.days, args.max_age_days, args.max_upvotes, args.limit, args.dry_run, args.auto_approve)


if __name__ == "__main__":
    main()
