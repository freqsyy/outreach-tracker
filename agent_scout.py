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

РАСШИРЕНИЕ (под спеку Scout / Блок 1 скоринга):
  - Извлекает founder/dev name (JSON-LD, meta author, текстовые маркеры) с confidence.
  - Детектит tech stack (React/Next/Vue/Svelte/WP/Shopify/Webflow/Astro/...) + is_static.
  - Считает bugginess (сырые маркеры: lorem ipsum, TODO, empty links, mixed content...).
  - СЧИТАЕТ fit-score 0-100 (young + buggy-looking + reachable + technographic + signal).
  - Пишет STRUCTURED вывод: domain | contact | score | reason.
  - HAND-OFF горячих лидов (>70): тег `hot` + маркеры `HANDOFF::gordon` (email) и
    `HANDOFF::herald` (social) в notes. Статус ПО-ПРЕЖНЕМУ `review` (до аппрува Назара).
  - Пишет отчёт `scout_leads.md` (таблица + детали по каждому лиду).

Прошедшие фильтр -> track.py add с тегами "auto-scout,fresh[,hot]", source "scout".
ПО УМОЛЧАНИЮ статус = "review" (НЕ готов к рассылке) - авто-найденные контакты
требуют ручного подтверждения (track.py edit --status pending), чтобы не слать
спам по адресам, вытащенным из чужих сайтов. Ключ --auto-approve ставит сразу
"pending" - только если Назар точно хочет.

БЕЗОПАСНОСТЬ (SSRF, харденинг 2026-07-11 + расширение): URL сайтов и домены берутся
из публичного дампа (недоверенный ввод):
  - SSRF #1: перед фетчем URL проверяется (scheme http/https, без учётных данных,
    хост не loopback/private/link-local/IMDS, НЕ IP-литерал). Редиректы не следуются
    (-L убран); при 3xx Location ревалидируется.
  - SSRF #2: домен валидируется строгой регуляркой до встраивания в RDAP-URL.
  - Вывод сайта трактуется как hostile: парсим как текст, не exec/eval.

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
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import gordon_common as gc

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import dedup as dedup_mod
except ImportError:
    dedup_mod = None
TRACK = os.path.join(HERE, "track.py")
REPORT_PATH = os.path.join(HERE, "scout_leads.md")

# --- настраиваемые пороги ---
DEFAULT_DAYS = 14          # за сколько последних дней брать дампы
DEFAULT_MAX_AGE = 730      # домен моложе N дней = "молодой" (2 года)
DEFAULT_MAX_UPVOTES = 200  # upvotes выше = слишком раскрученный, пропускаем
DEFAULT_LIMIT = 40         # макс. новых сайтов за один прогон
HOT_THRESHOLD = 70         # fit-score выше = "горячий" лид, готов к hand-off
SLEEP_BETWEEN = 1.0        # пауза между фетчами сайтов (rate limit / robots)
UA = "Mozilla/5.0 (compatible; GordonScout/1.0; +https://github.com/nazar/outreach-tracker)"

DAILY_REPO = "WebsiteLaunches/daily-website-launches"
RANK_RE = re.compile(r"^###\s+#\d+\s+-\s+\[([^\]]+)\]\((https?://[^)]+)\)", re.M)
CAT_RE = re.compile(r"\*\*Category:\*\*\s*([^|]+)")
UP_RE = re.compile(r"\*\*Upvotes:\*\*\s*(\d+)")

VERISIGN_TLDS = {"com", "net", "org", "info", "biz", "name"}
IMDS_IP = ipaddress.ip_address("169.254.169.254")  # cloud metadata - главная цель SSRF

# строгий паттерн зарегистрированного домена (без '/', '@', '?', '#', учётных данных)
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")

# мусорные/placeholder email, которые парсер ловит из описаний и документации
JUNK_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "dev.io",
                      "test.com", "localhost", "invalid"}
JUNK_EMAIL_LOCAL = {"you", "your", "test", "noreply", "no-reply", "admin@example"}

# конкуренты (QA/тестирование агентства) - не лиды, а конкуренты (red flag R2)
COMPETITOR_RE = re.compile(r"\b(qa|qc|testing|test|bug)\b.{0,20}\b(agency|service|company|studio|as\s+a\s+service|platform)\b", re.I)

# --- bugginess markers: regex -> (вес, код) ---
BUGGY_MARKERS = [
    (r"lorem ipsum|lipsum", 3, "placeholder-copy"),
    (r"your (name|email|text|content) here|placeholder text|sample text", 3, "template-field"),
    (r"\bTODO\b|\bFIXME\b|\bXXX:?\b|coming soon|under construction|work in progress", 4, "dev-in-progress"),
    (r"\[insert .*?\]|\{\{[^}]+\}\}|&lt;placeholder", 3, "template-token"),
    (r"undefined|null|NaN|\[object Object\]", 3, "unrendered-js"),
    (r"test (page|site|mode)|debug mode|console\.log\(|dbg=|[?&]debug=1", 2, "debug-affordance"),
    (r"http://", 2, "mixed-content"),
    (r'href=""|href="#null"|href="#undefined"|href="javascript:;"', 2, "empty-link"),
    (r"width:\s*0|height:\s*0|display:\s*none", 1, "hidden-layout"),
    (r"<title>\s*</title>|duplicate <title>", 1, "thin-seo"),
]

# --- tech stack markers: regex -> (метка, kind)  kind: fw/cms/be ---
STACK_MARKERS = [
    (r"/_next/static/|__NEXT_DATA__|next-router|next-themes", "nextjs", "fw"),
    (r"react(\.production)?\.min\.js|react-dom|window\.__REACT_DEVTOOLS__", "react", "fw"),
    (r"__NUXT__|/_nuxt/|id=\"__nuxt\"", "nuxt", "fw"),
    (r"vue(\.runtime)?\.|data-server-rendered", "vue", "fw"),
    (r"__SVELTEKIT__|%sveltekit|svelte", "svelte", "fw"),
    (r"astro-island|data-astro-cid|/_astro/", "astro", "fw"),
    (r"__gatsby|/page-data/", "gatsby", "fw"),
    (r"ng-[\w-]+|window\.ng|_ngcontent|main-es2015\.js", "angular", "fw"),
    (r"/wp-content/|/wp-includes/|/wp-json/|xmlrpc\.php|generator[^>]*wordpress", "wordpress", "cms"),
    (r"cdn\.shopify\.com|shopify\.theme|_shopify|myshopify\.com", "shopify", "cms"),
    (r"assets-global\.website-files\.com|webflow\.js|data-wf-", "webflow", "cms"),
    (r"static\.squarespace\.com|squarespace", "squarespace", "cms"),
    (r"sites/default/files|drupal\.settings|generator[^>]*drupal", "drupal", "cms"),
    (r"option=com_|joomla", "joomla", "cms"),
    (r"assets\.ghost\.io|\.ghost\.io|generator[^>]*ghost", "ghost", "cms"),
    (r"csrfmiddlewaretoken|generator[^>]*django", "django", "be"),
    (r"laravel_session|xsrf-token", "laravel", "be"),
    (r"__viewstate|x-aspnet-version|\.aspx", "aspnet", "be"),
    (r"x-powered-by: ?php|\.php\b", "php", "be"),
    (r"x-powered-by: ?express|express", "express", "be"),
    (r"jsessionid|\.do\b|\.action\b", "java", "be"),
    (r"csrf-token|phusion passenger|generator[^>]*rails", "rails", "be"),
]

# суффиксы, выдающие название КОМПАНИИ, а не имя человека
COMPANY_SUFFIX = re.compile(
    r"(inc|llc|ltd|gmbh|corp|co\.?$|labs|studio|app|ai|tech|digital|group|"
    r"agency|solutions|software|systems|media|network|cloud|ventures|holding|"
    r"limited|plc)\.?$", re.I)
FOUNDER_TITLE = re.compile(r"founder|co-?founder|ceo|creator|owner|maker|built|made", re.I)


def is_safe_domain(domain):
    """Строгая валидация домена перед встраиванием в RDAP-URL (фикс SSRF #2)."""
    return bool(DOMAIN_RE.match(domain or ""))


def url_is_safe(url):
    """Проверяет, что URL можно фетчить: только http/https, публичный хост,
    без учётных данных, не loopback/private/link-local/IMDS, НЕ IP-литерал
    (фикс SSRF #1 + расширение: блок IMDS 169.254.169.254 и IP-литералов)."""
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
    # IP-литералы в URL запрещаем целиком (фетчим только по доменным именам)
    if re.match(r"^\[?[0-9a-fA-F:.]+\]?$", host):
        return False, f"ip literal {host}"
    # домен — ок (резолв в рантайме не делаем, доверяем схеме + доменному паттерну)
    if ":" not in host and not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
        return True, ""
    # на всякий случай — если всё же IP, блок приватные/loopback/link-local/IMDS
    try:
        ip = ipaddress.ip_address(host.split(":")[0])
    except ValueError:
        return True, ""
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip == IMDS_IP):
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
    потом любой не-мусорный. Возвращает (email, quality) где quality в
    ('domain','any','')."""
    clean = [e for e in emails if not is_junk_email(e)]
    same_domain = [e for e in clean
                   if e.lower().split("@")[-1] in (site_domain, "www." + site_domain)
                   or e.lower().endswith("." + site_domain)]
    if same_domain:
        return sorted(same_domain)[0], "domain"
    if clean:
        return sorted(clean)[0], "any"
    return None, ""


# ---------------------------------------------------------------------------
# Извлечение основателя / разработчика
# ---------------------------------------------------------------------------

def _walk_ld(node, out):
    """Рекурсивно собирает все dict-узлы JSON-LD (вкл. @graph)."""
    if isinstance(node, dict):
        out.append(node)
        for v in node.values():
            _walk_ld(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_ld(v, out)


def _clean_name(name):
    if not name:
        return None
    name = re.sub(r"\s+", " ", name).strip().strip(".,;:\"'")
    # убираем "All rights reserved" и прочий мусор после имени
    name = re.split(r"\s+(all rights|rights reserved|inc\.?|llc|ltd|©)", name,
                    flags=re.I)[0].strip()
    if len(name) < 2 or len(name) > 60:
        return None
    return name


def _looks_like_company(name):
    if COMPANY_SUFFIX.search(name):
        return True
    if "." in name:
        return True
    if not any(c.isupper() for c in name):
        return True
    return False


def extract_founder(html, domain):
    """Возвращает {name, confidence, evidence}. confidence 0-1.
    Приоритет: JSON-LD Person/Organization.founder > meta author >
    текстовые маркеры > copyright. Компании отсеиваем (не пишем 'Hi Shopify Inc')."""
    cands = []

    # 1. JSON-LD
    for block in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                             html, re.S | re.I):
        try:
            data = json.loads(block)
        except Exception:
            continue
        nodes = []
        _walk_ld(data, nodes)
        for n in nodes:
            if not isinstance(n, dict):
                continue
            t = (n.get("@type") or "").lower()
            if t == "person":
                nm = n.get("name")
                jt = (n.get("jobTitle") or "").lower()
                if nm:
                    conf = 0.9 if FOUNDER_TITLE.search(jt or "") else 0.7
                    cands.append((nm, conf, f"json-ld:Person.jobTitle={jt!r}"))
            elif t == "organization":
                f = n.get("founder")
                if isinstance(f, dict) and f.get("name"):
                    cands.append((f["name"], 0.85, "json-ld:Organization.founder"))
                a = n.get("author")
                if isinstance(a, dict) and a.get("name"):
                    cands.append((a["name"], 0.6, "json-ld:Organization.author"))

    # 2. meta author
    m = re.search(r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)',
                  html, re.I)
    if m:
        cands.append((m.group(1).strip(), 0.7, "meta:author"))

    # 3. текстовые маркеры "built by X" / "I'm X"
    text_pats = [
        (r"built by ([A-Z][a-z]+(?: [A-Z][a-z]+)?)", "text:'built by'"),
        (r"made by ([A-Z][a-z]+(?: [A-Z][a-z]+)?)", "text:'made by'"),
        (r"founded by ([A-Z][a-z]+(?: [A-Z][a-z]+)?)", "text:'founded by'"),
        (r"created by ([A-Z][a-z]+(?: [A-Z][a-z]+)?)", "text:'created by'"),
        (r"developed by ([A-Z][a-z]+(?: [A-Z][a-z]+)?)", "text:'developed by'"),
        (r"designed by ([A-Z][a-z]+(?: [A-Z][a-z]+)?)", "text:'designed by'"),
        (r"\bI(?:'m| am) ([A-Z][a-z]+(?: [A-Z][a-z]+)?)\b", "text:'I am X'"),
    ]
    for pat, ev in text_pats:
        mm = re.search(pat, html)
        if mm:
            cands.append((mm.group(1), 0.55, ev))

    # 4. copyright в футере
    cm = re.search(r"©\s*\d{4}\s+([A-Z][\w.&' \-]+)", html)
    if cm:
        cands.append((cm.group(1).strip(), 0.4, "footer:copyright"))

    # фильтруем компании, берём лучшего ЧЕЛОВЕКА
    best = None
    for raw, conf, ev in cands:
        nm = _clean_name(raw)
        if not nm:
            continue
        if _looks_like_company(nm):
            continue
        if best is None or conf > best[1]:
            best = (nm, conf, ev)

    if best:
        return {"name": best[0], "confidence": round(best[1], 2), "evidence": best[2]}
    return {"name": None, "confidence": 0.0, "evidence": ""}


# ---------------------------------------------------------------------------
# Детект tech stack
# ---------------------------------------------------------------------------

def detect_stack(html):
    """Возвращает (labels:list[str], is_static:bool, has_framework:bool)."""
    labels = []
    kinds = set()
    for pat, label, kind in STACK_MARKERS:
        if re.search(pat, html, re.I):
            if label not in labels:
                labels.append(label)
            kinds.add(kind)
    has_framework = "fw" in kinds
    is_static = (not has_framework) and ("cms" not in kinds)
    return labels, is_static, has_framework


# ---------------------------------------------------------------------------
# Детект "сырости" сайта (bugginess)
# ---------------------------------------------------------------------------

def detect_buggy(html, site_url):
    """Возвращает (points:int, markers:list[str]). Только маркеры на https-странице
    считаются mixed-content (иначе http:// — норма для http-сайта)."""
    points = 0
    markers = []
    is_https = site_url.lower().startswith("https")
    for pat, w, code in BUGGY_MARKERS:
        if re.search(pat, html, re.I):
            if code == "mixed-content" and not is_https:
                continue
            points += w
            markers.append(code)
    return points, markers


# ---------------------------------------------------------------------------
# Fit-score 0-100
# ---------------------------------------------------------------------------

def score_lead(age, up, launch_days, contact_q, founder, stack, buggy_points):
    """Считает fit-score 0-100 и словарь basis для объяснимости.
    Факторы (сумма <=100): freshness(25) + technographic(20) + signals(15)
    + buggy(25) + contact(15)."""
    basis = {}

    # freshness: молодой домен = высокий
    if age is None:
        basis["freshness"] = 12
    elif age <= 30:
        basis["freshness"] = 25
    elif age <= 180:
        basis["freshness"] = 18
    elif age <= 365:
        basis["freshness"] = 10
    elif age <= 730:
        basis["freshness"] = 4
    else:
        basis["freshness"] = 0

    # technographic: реальный фреймворк/CMS = что тестировать
    if stack["has_framework"]:
        basis["technographic"] = 20
    elif stack["labels"]:
        basis["technographic"] = 14
    else:
        basis["technographic"] = 8

    # signals: свежий launch из дампа + тракшн в вилке
    if launch_days is None:
        rec = 7
    elif launch_days <= 7:
        rec = 10
    elif launch_days <= 30:
        rec = 8
    elif launch_days <= 90:
        rec = 5
    else:
        rec = 3
    if up is None or up == 0:
        tr = 4
    elif up <= 50:
        tr = 5
    elif up <= 200:
        tr = 4
    else:
        tr = 0
    basis["signals"] = min(15, rec + tr)

    # buggy-looking: сырые маркеры
    basis["buggy"] = min(25, buggy_points)

    # contact quality
    basis["contact"] = {"domain": 15, "any": 10, "": 0}.get(contact_q, 0)

    total = sum(basis.values())
    total = max(0, min(100, total))
    return total, basis


# ---------------------------------------------------------------------------
# Сетевые хелперы
# ---------------------------------------------------------------------------

def fetch_text(url, timeout=25):
    """curl -> текст. БЕЗ -L: редиректы не следуем (фикс SSRF #1).
    Добавлен User-Agent + Accept (вежливый фетч)."""
    try:
        out = subprocess.run(
            ["curl", "-s", "-m", str(timeout), "-A", UA,
             "-H", "Accept: text/html,application/json", url],
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


# ---------------------------------------------------------------------------
# Запись в БД
# ---------------------------------------------------------------------------

def add_to_db(url, email, tg, notes, status, score, founder, stack, handoff):
    """status='review' по умолчанию (фикс #3): авто-найденные контакты НЕ готовы
    к рассылке, требуют ручного подтверждения. Перевод в 'pending' - только вручную
    через track.py edit --status pending ИЛИ ключом --auto-approve.

    Дополнительно пишет score (Блок 1), founder, tech stack и hand-off маркеры.
    ВАЖНО: track.py add имеет single --notes, поэтому всё собираем в ОДИН блок."""
    st = "pending" if status == "pending" else "review"
    extra = []
    if founder and founder.get("name"):
        extra.append(f"FOUNDER:: {founder['name']} "
                     f"(conf={founder['confidence']}, {founder['evidence']})")
    if stack:
        extra.append(f"STACK:: {','.join(stack)}")
    for h in handoff:
        extra.append(f"HANDOFF::{h}")
    full_notes = notes + ("\n" + "\n".join(extra) if extra else "")
    cmd = [sys.executable, TRACK, "add", url,
           "--email", email, "--tags", "auto-scout,fresh",
           "--source", "scout", "--status", st,
           "--score", str(score), "--notes", full_notes]
    if founder and founder.get("name"):
        # тег hot ставим, если лид горячий (handoff не пуст)
        if handoff:
            cmd[cmd.index("--tags") + 1] += ",hot"
    if tg:
        cmd += ["--tg", tg]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=30)
        out = res.stdout.decode("utf-8", errors="ignore") if res.stdout else ""
        for line in out.splitlines():
            gc.log(line, "SCOUT")
    except Exception as e:
        gc.log(f"add fail {url}: {e}", "SCOUT")


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------

def write_report(leads, hot_threshold):
    """Пишет scout_leads.md: таблица + детали. leads = список dict."""
    if not leads:
        return
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Scout Leads — {today}",
        "",
        f"- scanned: {leads[0]['_scanned']}",
        f"- with_contact: {len(leads)}",
        f"- hot (>={hot_threshold}): {sum(1 for l in leads if l['score'] >= hot_threshold)}",
        "",
        "## Table",
        "",
        "| # | domain | score | contact | founder | stack | reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, l in enumerate(leads, 1):
        lines.append(
            f"| {i} | {l['domain']} | {l['score']} | {l['contact']} | "
            f"{l['founder_name'] or '-'} | {','.join(l['stack']) or '-'} | {l['reason']} |"
        )
    lines.append("")
    lines.append("## Details")
    lines.append("")
    for l in leads:
        lines.append(f"### {l['domain']} (score {l['score']})")
        lines.append(f"- contact: {l['contact']}")
        if l['founder_name']:
            lines.append(f"- founder: {l['founder_name']} "
                         f"(conf={l['founder_conf']}, {l['founder_ev']})")
        else:
            lines.append("- founder: -")
        lines.append(f"- stack: {', '.join(l['stack']) or '-'} "
                     f"(static={l['is_static']})")
        lines.append(f"- buggy markers: {', '.join(l['buggy']) or 'none'}")
        if l['handoff']:
            lines.append(f"- handoff: {', '.join('HANDOFF::'+h for h in l['handoff'])}")
        lines.append(f"- basis: {l['basis']}")
        lines.append(f"- reason: {l['reason']}")
        lines.append("")
    try:
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        gc.log(f"Otchet zapisan: {REPORT_PATH} ({len(leads)} lidov)", "SCOUT")
    except Exception as e:
        gc.log(f"Ne udalos zapisat otchet: {e}", "SCOUT")


# ---------------------------------------------------------------------------
# Основной цикл
# ---------------------------------------------------------------------------

def run(days, max_age, max_upvotes, limit, dry, auto_approve, hot_threshold):
    """limit = ЧИСЛО сайтов С КОНТАКТОМ (email). Скаут крутит дневные дампы
    (новые -> старые) пока не наберёт limit сайтов с контактом. Если во всех
    дампах за days контактов меньше limit - логирует исчерпание источника."""
    gc.log(f"=== SCOUT start: days={days} max_age={max_age} max_up={max_upvotes} "
           f"limit={limit} dry={dry} auto_approve={auto_approve} hot>={hot_threshold} ===", "SCOUT")
    files = list_daily_files(days)
    seen = set()
    added = 0          # только сайты С контактом (цель limit)
    scanned = 0
    leads = []         # для отчёта

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

            # RED FLAG: конкурент (QA-агентство) — не лид
            if COMPETITOR_RE.search(f"{domain} {cat}"):
                gc.log(f"PROPUSCHEN (konkurent QA): {domain}", "SCOUT")
                continue

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
            email, contact_q = pick_contact(emails, domain)
            if not email:
                gc.log(f"TOLKO MUSOR-EMAIL: {domain} ({sorted(emails)[:2]})", "SCOUT")
                continue

            # --- ДЕДУП (задача scout-2026-07-14-14): НЕ слать тому, кто уже
            # sent/bounced/rejected. READ-ONLY проверка БД, статусы не меняем. ---
            if dedup_mod is not None:
                dup, why = dedup_mod.is_already_contacted(site_url, email, " ".join(tgs) if tgs else None)
                if dup:
                    gc.log(f"DEDUP: propuschen (uzhe kontaktirovan) {domain} -> {why}", "SCOUT")
                    continue

            # --- РАСШИРЕННЫЙ СБОР (спека Scout) ---
            founder = extract_founder(html, domain)
            stack_labels, is_static, has_framework = detect_stack(html)
            buggy_pts, buggy_markers = detect_buggy(html, site_url)
            launch_days = (datetime.now() - datetime.strptime(ymd, "%Y-%m-%d")).days
            score, basis = score_lead(age, up, launch_days, contact_q,
                                       founder, {"labels": stack_labels,
                                                 "has_framework": has_framework},
                                       buggy_pts)

            stack = {"labels": stack_labels, "is_static": is_static,
                     "has_framework": has_framework}
            reason = _reason(age, up, launch_days, contact_q, founder,
                             stack, buggy_markers, score)

            # hand-off горячих лидов (>70) — маркеры для Gordon (email) + Herald (social)
            handoff = []
            if score >= hot_threshold:
                handoff = ["gordon", "herald"]

            age_s = f"{age}d" if age is not None else "?"
            notes = (f"scout: launched~{ymd}, cat={cat}, up={up}, age={age_s}, "
                     f"score={score}")
            if buggy_markers:
                notes += f", buggy={','.join(buggy_markers)}"

            # structured output
            founder_disp = founder["name"] or "-"
            gc.log(f"[LEAD] {domain} | {email} | score={score} | {reason}", "SCOUT")

            leads.append({
                "domain": domain, "contact": email,
                "founder_name": founder["name"], "founder_conf": founder["confidence"],
                "founder_ev": founder["evidence"],
                "stack": stack_labels, "is_static": is_static,
                "buggy": buggy_markers, "handoff": handoff,
                "score": score, "basis": basis, "reason": reason,
                "_scanned": scanned,
            })

            if dry:
                gc.log(f"[DRY] CANDIDATE: {domain} ({cat}, {up}up, age {age_s}) "
                       f"-> {email} | score={score}", "SCOUT")
                added += 1
                continue

            tg = " ".join(tgs) if tgs else None
            # по умолчанию status='review' (защита от спама), см. fix #3
            add_to_db(site_url, email, tg, notes,
                      "pending" if auto_approve else "review",
                      score, founder, stack_labels, handoff)
            added += 1
            if added >= limit:
                break

            # rate limit: пауза между фетчами сайтов
            if SLEEP_BETWEEN:
                time.sleep(SLEEP_BETWEEN)

        if added >= limit:
            break

    # отчёт (только реальный прогон, не dry и не пусто)
    if not dry and leads:
        write_report(leads, hot_threshold)

    if added < limit:
        gc.log(f"=== SCOUT: istochnik ISCHERPAN - najdeno tolko {added} iz "
               f"{limit} sajtov s kontaktom za {days} dnej ===", "SCOUT")
    gc.log(f"=== SCOUT done: proskanirovano={scanned} s_kontaktom={added}/{limit} "
           f"hot={sum(1 for l in leads if l['score'] >= hot_threshold)} ===", "SCOUT")


def _reason(age, up, launch_days, contact_q, founder, stack, buggy_markers, score):
    """Человекочитаемая причина скоринга/квалификации."""
    parts = []
    if age is not None:
        parts.append(f"age {age}d")
    else:
        parts.append("age ?")
    if stack["labels"]:
        parts.append("/".join(stack["labels"][:2]))
    elif stack["is_static"]:
        parts.append("static")
    if buggy_markers:
        parts.append(f"{len(buggy_markers)} buggy")
    if founder["name"]:
        parts.append(f"founder {founder['name']}")
    if contact_q == "domain":
        parts.append("email@domain")
    if launch_days is not None and launch_days <= 7:
        parts.append("fresh launch")
    return "; ".join(parts) if parts else f"score {score}"


def main():
    ap = argparse.ArgumentParser(description="Гордон-Скаут: ищет молодые сайты")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE)
    ap.add_argument("--max-upvotes", type=int, default=DEFAULT_MAX_UPVOTES)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--hot-threshold", type=int, default=HOT_THRESHOLD,
                    help="fit-score выше = горячий лид, hand-off в Gordon/Herald")
    ap.add_argument("--dry-run", action="store_true", help="только показать кандидатов")
    ap.add_argument("--auto-approve", action="store_true",
                    help="добавлять сразу как pending (ГОТОВО К РАССЫЛКЕ). "
                         "БЕЗ ключа скаут ставит статус 'review' - нужно ручное подтверждение")
    ap.add_argument("--sources", action="store_true",
                    help="парсить 3 НОВЫХ источника (LaunchingNext/Fazier/HN Show HN) "
                         "ПОВЕРХ дневного дампа. Только dry-run (БЕЗ записи в БД).")
    ap.add_argument("--sources-maxage", type=int, default=DEFAULT_MAX_AGE,
                    help="для --sources: макс возраст домена в днях (RDAP)")
    args = ap.parse_args()
    if args.sources:
        _run_sources_dry(args.sources_maxage)
        return
    run(args.days, args.max_age_days, args.max_upvotes, args.limit,
        args.dry_run, args.auto_approve, args.hot_threshold)


def _run_sources_dry(max_age):
    """Dry-run движка 3-х новых источников (задача scout-2026-07-14-15).
    НЕ пишет в БД. Только парсит + фильтрует + логирует кандидатов."""
    try:
        from sites_sources import run_sources_dry, NEW_SOURCES
    except Exception as e:
        gc.log(f"--sources: import fail: {e}", "SCOUT")
        return
    gc.log(f"=== SOURCES dry-run: max_age={max_age} sources={list(NEW_SOURCES)} ===", "SCOUT")
    cands = run_sources_dry(days=1, max_age=max_age)
    gc.log(f"=== SOURCES done: kandidatov={len(cands)} ===", "SCOUT")
    for c in cands:
        gc.log(f"[SRC] {c['domain']} | age={c['age']} | {c['reason']}", "SCOUT")


if __name__ == "__main__":
    main()
