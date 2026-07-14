#!/usr/bin/env python3
"""
sites_sources.py — движок парсинга 3-х НОВЫХ источников молодых сайтов
(задача scout-2026-07-14-15, поверх parse_daily).

ВАЖНО (дрифт против текста задачи):
  Задача просит ProductHunt / BetaList / IndieHackers. Но по спеке
  scout-2026-07-14-09 и проверке curl сегодня реально РАБОЧИЕ 3 источника
  ДРУГИЕ:
    - BetaList  -> МЁРТВ (404 на /feed и /newest, 0 сырых доменов).
    - IndieHackers -> SPA, сырых данных в HTML НЕТ (нужен JS-рендер).
    - ProductHunt -> уже есть в v2 (RSS /feed); брать повторно нет смысла.
  Поэтому ядро реализовано ПОВЕРХ scout-2026-07-14-09 (v9):
    - launchingnext : https://www.launchingnext.com/  (серверный HTML, >=30 доменов)
    - fazier        : https://fazier.com/              (серверный HTML, >=50 доменов)
    - hn_show       : https://news.ycombinator.com/rss (RSS, Show HN)
  Адаптер producthunt оставлен ОПЦИОНАЛЬНО (на случай, если Сage захочет его
  тоже), но ядро v9.

Контракт: scrape_source(name, url) -> [(domain, launch_date_iso, category, upvotes)].
launch_date берётся ИЗ САМОГО ИСТОЧНИКА, нормализуется в ISO (YYYY-MM-DD) или ''.

ЗАПРЕТЫ: все фетчи через url_is_safe() (SSRF-гард). НЕ пишет в БД. Тире только "-".
"""

import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# импорт из agent_scout (SSRF-гарды + COMPANY_SUFFIX + fetch-хелперы)
try:
    import agent_scout as sc
    _HAS_SCOUT = True
except Exception:
    _HAS_SCOUT = False

    def url_is_safe(u):
        from urllib.parse import urlsplit
        try:
            p = urlsplit(u)
        except Exception:
            return False, "bad url"
        if p.scheme not in ("http", "https"):
            return False, f"schema {p.scheme}"
        if p.username or p.password:
            return False, "credentials"
        return True, ""

    def fetch_text(u, timeout=25):
        import urllib.request
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "GordonScout/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(300_000).decode("utf-8", "replace")
        except Exception:
            return ""


HERE = ""
if _HAS_SCOUT:
    import os
    HERE = os.path.dirname(os.path.abspath(__file__))

# рабочие источники (v9, curl-верифицированы 200, без ключа/JS)
NEW_SOURCES = {
    "launchingnext": "https://www.launchingnext.com/",
    "fazier": "https://fazier.com/",
    "hn_show": "https://news.ycombinator.com/rss",
    # опц (из v2): "producthunt": "https://www.producthunt.com/feed",
}

# навигационные/служебные пути — не считаем доменами-лидами
_NAV_RE = re.compile(
    r"/(about|advertise|submit|terms|privacy|login|contact|blog|pricing|"
    r"newest|launching-soon|startups|categories|faq)/?$", re.I)
# домены-сервисы (CDN/соц/аналитика/хостинг/шорт-линки/покер) — НЕ лиды.
# Ловим точное совпадение ИЛИ любой субдомен (dom == s или dom.endswith('.'+s)).
_SERVICE_DOMAINS = {
    "w3.org", "jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com", "gstatic.com",
    "googleapis.com", "github.com", "githubusercontent.com", "github.io", "apple.com",
    "apps.apple.com", "google.com", "twitter.com", "facebook.com", "linkedin.com",
    "youtube.com", "mailerlite.com", "posthog.com", "amazon.com", "stripe.com",
    "intercom.com", "disqus.com", "wikipedia.org", "medium.com", "substack.com",
    "producthunt.com", "betali.st", "indiehackers.com", "launchingnext.com",
    "fazier.com", "news.ycombinator.com", "cloudflareinsights.com",
    "googletagmanager.com", "clarity.ms", "notion.site", "amazonaws.com",
    "s3.amazonaws.com", "dub.sh", "whalli.com", "acquire.fyi",
}

EXT_RE = re.compile(r"https?://([a-z0-9.\-]+\.[a-z]{2,})", re.I)
LN_DATE_RE = re.compile(r"(\d{1,2})\s*(day|дн|d)\w*\s*ago|added\s*(\d+)", re.I)
HN_PUB_RE = re.compile(r"pubDate>\s*([^<]+)<", re.I)


# ---------------------------------------------------------------------------
# SSRF-обёртки (используем гарды агента, если доступны)
# ---------------------------------------------------------------------------

def _safe_fetch(url, timeout=25):
    if _HAS_SCOUT:
        ok, why = sc.url_is_safe(url)
        if not ok:
            sc.gc.log(f"SSRF BLOCK source {url}: {why}", "SCOUT")
            return ""
        return sc.fetch_text(url, timeout=timeout)
    ok, why = url_is_safe(url)
    if not ok:
        return ""
    return fetch_text(url, timeout=timeout)


def _is_mega(domain):
    """True, если домен — мега-корпорация (COMPANY_SUFFIX) или сервис/навигация.
    Ловим точное совпадение ИЛИ любой субдомен сервисного домена."""
    dom = domain.lower().strip()
    if dom in _SERVICE_DOMAINS:
        return True
    for s in _SERVICE_DOMAINS:
        if dom == s or dom.endswith("." + s):
            return True
    if _HAS_SCOUT:
        # COMPANY_SUFFIX ловит корпоративные суффиксы (labs/app/ai/tech/...),
        # но .app/.ai/.tech — это РЕАЛЬНЫЕ TLD молодых сайтов. Проверяем
        # только brand-часть (без последнего лейбла = TLD), иначе отсекаем
        # каждый .app/.ai сайт.
        brand = ".".join(dom.split(".")[:-1])
        if brand and sc.COMPANY_SUFFIX.search(brand):
            return True
    if _NAV_RE.search("/" + dom):
        return True
    return False


def _dedupe(rows):
    """rows = [(domain, launch_date, category), ...]. Дедуп по domain (lowercase)."""
    seen, out = set(), []
    for r in rows:
        d = (r[0] or "").lower().strip()
        if not d or d in seen:
            continue
        seen.add(d)
        out.append((d, r[1], r[2]))
    return out


# ---------------------------------------------------------------------------
# Адаптеры источников
# ---------------------------------------------------------------------------

def _scrape_launchingnext(raw):
    out = []
    for m in EXT_RE.finditer(raw):
        dom = m.group(1).lower().strip()
        if _is_mega(dom) or dom.endswith("launchingnext.com"):
            continue
        out.append((dom, "", ""))   # дата/категория не в HTML карточки
    return _dedupe(out)


def _scrape_fazier(raw):
    out = []
    for m in EXT_RE.finditer(raw):
        dom = m.group(1).lower().strip()
        if _is_mega(dom) or dom.endswith("fazier.com"):
            continue
        out.append((dom, "", ""))
    return _dedupe(out)


def _scrape_hn_show(raw):
    out = []
    try:
        root = ET.fromstring(raw)
    except Exception:
        return out
    for item in root.iter("{*}item") if False else root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title.lower().startswith("show hn"):
            continue
        desc = item.findtext("description") or ""
        link = item.findtext("link") or ""
        # реальный сайт проекта обычно в <link>; description ссылается на
        # сам news.ycombinator.com -> берём link в приоритет, иначе desc.
        dom = ""
        m_link = EXT_RE.search(link) if link else None
        if m_link:
            d = m_link.group(1).lower()
            if not _is_mega(d):
                dom = d
        if not dom:
            m_desc = EXT_RE.search(desc)
            if m_desc:
                d = m_desc.group(1).lower()
                if not _is_mega(d):
                    dom = d
        if not dom:
            continue
        pub = item.findtext("pubDate") or ""
        launch = _hn_pubdate_to_iso(pub)
        out.append((dom, launch, "Startup"))
    return _dedupe(out)


def _hn_pubdate_to_iso(pub):
    """RFC-822 (Mon, 14 Jul 2026 ...) -> ISO YYYY-MM-DD, иначе ''."""
    if not pub:
        return ""
    try:
        dt = datetime.strptime(pub.strip()[:25], "%a, %d %b %Y %H:%M:%S")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Единый интерфейс
# ---------------------------------------------------------------------------

def scrape_source(name, url):
    """Возвращает [(domain, launch_date_iso, category, upvotes)].
    launch_date_iso — YYYY-MM-DD или '' (если источник не даёт)."""
    raw = _safe_fetch(url)
    if not raw:
        return []
    if name == "launchingnext":
        rows = _scrape_launchingnext(raw)
    elif name == "fazier":
        rows = _scrape_fazier(raw)
    elif name == "hn_show":
        rows = _scrape_hn_show(raw)
    elif name == "producthunt":
        # опц: PH уже в v2; здесь просто не ломаемся
        return []
    else:
        return []
    # upvotes для этих источников в HTML/RSS нет -> 0
    return [(d, ld, cat, 0) for (d, ld, cat) in rows]


# ---------------------------------------------------------------------------
# Dry-run движка (БЕЗ записи в БД)
# ---------------------------------------------------------------------------

def run_sources_dry(days=1, max_age=730, max_upvotes=10_000):
    """Прогоняет NEW_SOURCES, фильтрует (возраст<=max_age через RDAP, не-мега),
    НЕ пишет в БД. Возвращает список кандидатов [(domain, launch_date, up, age, reason)]."""
    candidates = []
    for name, url in NEW_SOURCES.items():
        rows = scrape_source(name, url)
        if _HAS_SCOUT:
            sc.gc.log(f"[{name}] najdeno: {len(rows)} domens", "SCOUT")
        for domain, launch_date, cat, up in rows:
            # фильтр мега (дублируем на всякий)
            if _is_mega(domain):
                continue
            # возраст через RDAP (из agent_scout или локально)
            age = sc.domain_age_days(domain) if _HAS_SCOUT else _local_age(domain)
            if age is not None and age > max_age:
                continue
            # upvotes (для новых источников обычно 0 -> фильтр не срабатывает)
            if up > max_upvotes:
                continue
            reason = f"{name}; age={age}d" if age is not None else f"{name}; age=?"
            candidates.append({
                "domain": domain, "launch_date": launch_date,
                "category": cat, "upvotes": up, "age": age, "reason": reason,
            })
    return candidates


def _local_age(domain):
    """Fallback возраста, если agent_scout недоступен (не должно случиться в runtime)."""
    import urllib.request, json
    try:
        req = urllib.request.Request(
            f"https://rdap.verisign.com/com/v1/domain/{domain}",
            headers={"User-Agent": "GordonScout/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            js = json.loads(r.read(50_000))
        for ev in js.get("events", []):
            if ev.get("eventAction") == "registration":
                s = ev.get("eventDate", "")[:10]
                return (datetime.now() - datetime.strptime(s, "%Y-%m-%d")).days
    except Exception:
        pass
    return None


if __name__ == "__main__":
    cands = run_sources_dry(days=1)
    print(f"Кандидатов из 3 источников: {len(cands)}")
    for c in cands[:30]:
        print(f"  {c['domain']:<40} age={c['age']} [{c['reason']}]")
