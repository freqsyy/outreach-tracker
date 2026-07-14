#!/usr/bin/env python3
"""
dedup.py — дедупликатор лидов (защита от повторной рассылки).

Логика (спека scout-2026-07-14-03 / задача scout-2026-07-14-14):
  Агент НЕ должен слать повторно тем, кто уже sent / bounced / rejected / junk.
  Перед записью нового лида -> is_already_contacted(domain, email, tg) читает
  outreach.db (READ-ONLY) и пропускает дубль.

Ключи дедупа (нормализация):
  - domain  : norm_domain(url)  (без схемы/www/порта/пути)
  - apex    : apex_of(domain)   (семейство: blog.x.com == x.com)
  - email   : norm_email()      (lowercase, trim)
  - telegram: norm_tg()         (@ + lowercase, без пробелов)

ЗАПРЕТЫ: модуль НИКОГДА не пишет в БД, не меняет статусы существующих лидов.
Только read-only SELECT + skip новых дублей. Тире только "-".
"""

import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "outreach.db")

# Статусы = "уже в работе / закрыто" -> повторно НЕ шлём.
CLOSED_STATUSES = ("sent", "bounced", "rejected")


# ---------------------------------------------------------------------------
# Нормализация ключей (из спеки scout_dedup_spec.md)
# ---------------------------------------------------------------------------

def norm_domain(url_or_domain):
    s = (url_or_domain or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0]              # убрать путь/якорь/query
    s = s.split(":")[0]              # убрать порт
    if s.startswith("www."):
        s = s[4:]
    return s


def norm_email(e):
    return (e or "").strip().lower()


def norm_tg(t):
    s = (t or "").strip().lower()
    if not s:
        return ""
    if not s.startswith("@"):
        s = "@" + s
    return s.replace(" ", "")


def apex_of(domain):
    """apex (registrable) домена: 'app.resonella.app' -> 'resonella.app'."""
    parts = domain.split(".")
    if len(parts) <= 2:
        return domain
    return ".".join(parts[-2:])         # TLD+1


# ---------------------------------------------------------------------------
# Read-only сбор "истории" контактов (домены/apex/email/tg из CLOSED_STATUSES)
# ---------------------------------------------------------------------------

def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_contacted_keys(conn=None):
    """Возвращает dict с множествами доменов/apex/email/tg, которым УЖЕ что-то
    слали/отказали (статусы CLOSED_STATUSES). READ-ONLY."""
    own = conn is None
    c = conn if conn else _conn()
    try:
        rows = c.execute(
            "SELECT url, email, telegram, status FROM sites "
            "WHERE status IN (%s)" % ",".join(f"'{s}'" for s in CLOSED_STATUSES)
        ).fetchall()
    finally:
        if own:
            c.close()
    domains, apex, emails, tgs = set(), set(), set(), set()
    for r in rows:
        url = r["url"] or ""
        dom = norm_domain(url)
        if dom:
            domains.add(dom)
            ap = apex_of(dom)
            if ap:
                apex.add(ap)
        em = norm_email(r["email"])
        if em:
            emails.add(em)
        tg = norm_tg(r["telegram"])
        if tg:
            tgs.add(tg)
    return {"domains": domains, "apex": apex, "emails": emails, "tgs": tgs}


# ---------------------------------------------------------------------------
# Проверка дубля (главный интерфейс для агентов)
# ---------------------------------------------------------------------------

def is_already_contacted(domain, email=None, tg=None, conn=None):
    """True, если домен / apex-семья / email / tg УЖЕ в закрытых статусах.

    Возвращает (bool, reason). READ-ONLY, не меняет статусы.
    - domain: сырой url или домен (нормализуется внутри).
    - email:  сырой email (нормализуется).
    - tg:     сырой telegram (нормализуется).
    """
    dom = norm_domain(domain) if domain else ""
    ap = apex_of(dom) if dom else ""
    em = norm_email(email) if email else ""
    t = norm_tg(tg) if tg else ""

    keys = load_contacted_keys(conn)
    if dom and dom in keys["domains"]:
        return True, f"domain {dom} already contacted"
    if ap and ap in keys["apex"]:
        return True, f"apex {ap} already contacted"
    if em and em in keys["emails"]:
        return True, f"email {em} already contacted"
    if t and t in keys["tgs"]:
        return True, f"telegram {t} already contacted"
    return False, ""


# ---------------------------------------------------------------------------
# Dry-run: сколько текущих pending/review дублей ОТСЕЯЛОСЬ БЫ
# ---------------------------------------------------------------------------

def dry_run_report():
    """READ-ONLY симуляция: для каждого pending/review лида проверяет коллизию
    с закрытыми статусами (исключая самого себя). Возвращает список словарей
    отсеянных + сводку. НЕ пишет в БД."""
    c = _conn()
    try:
        keys = load_contacted_keys(c)
        rows = c.execute(
            "SELECT id, url, email, telegram, status FROM sites "
            "WHERE status IN ('pending','review')"
        ).fetchall()
    finally:
        c.close()

    skipped = []
    for r in rows:
        rid, url, email, tg, status = r["id"], r["url"], r["email"], r["telegram"], r["status"]
        dom = norm_domain(url)
        ap = apex_of(dom)
        em = norm_email(email)
        t = norm_tg(tg)
        # исключаем самого себя из "истории" (чтобы pending не отсекал сам себя)
        if dom in keys["domains"] - {dom}:
            reason = f"domain {dom}"
        elif ap in keys["apex"] - {ap}:
            reason = f"apex {ap}"
        elif em and em in keys["emails"] - {em}:
            reason = f"email {em}"
        elif t and t in keys["tgs"] - {t}:
            reason = f"telegram {t}"
        else:
            continue
        skipped.append({"id": rid, "url": url, "status": status, "reason": reason})

    summary = {
        "pending_review_total": len(rows),
        "contacted_keys": {
            "domains": len(keys["domains"]),
            "apex": len(keys["apex"]),
            "emails": len(keys["emails"]),
            "tgs": len(keys["tgs"]),
        },
        "would_skip": len(skipped),
        "skipped": skipped,
    }
    return summary


if __name__ == "__main__":
    s = dry_run_report()
    print(f"pending/review в БД: {s['pending_review_total']}")
    print(f"ключей в истории (closed): domains={s['contacted_keys']['domains']} "
          f"apex={s['contacted_keys']['apex']} email={s['contacted_keys']['emails']} "
          f"tg={s['contacted_keys']['tgs']}")
    print(f"ОТСЕЯЛОСЬ БЫ дублей: {s['would_skip']}")
    for k in s["skipped"]:
        print(f"  #{k['id']} {k['url']} [{k['status']}] -> {k['reason']}")
