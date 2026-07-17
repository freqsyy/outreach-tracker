#!/usr/bin/env python3
"""
Outreach Tracker — track outreach contacts for QA testing gigs.

Usage:
  python track.py add https://example.com --email support@example.com --tg @example
  python track.py list
  python track.py send 1
  python track.py reply 1
  python track.py hired 1 --amount 80
  python track.py rejected 1
  python track.py stats
  python track.py note 1 "Called them, said email tomorrow"
  python track.py export
  python track.py edit 1 --email new@email.com --tags restaurant
  python track.py score resonella.app            # dry-скоринг домена (БЕЗ записи)
  python track.py score resonella.app --fetch    # +live RDAP/HTML через SSRF-гард
"""

import sqlite3
import argparse
import os
import sys
import re
import json
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outreach.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE")  # DELETE: весь журнал в одном файле outreach.db -> git-friendly sync между ПК и Actions
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            email TEXT,
            telegram TEXT,
            status TEXT DEFAULT 'pending'
                CHECK(status IN ('pending','sent','replied','hired','rejected','bounced','review')),
            tags TEXT,
            source TEXT DEFAULT 'manual',
            notes TEXT,
            amount_earned REAL DEFAULT 0,
            score REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def migrate_bounced_status():
    """Старые БД созданы с CHECK без 'bounced'/'review'. Добавляем статусы,
    пересоздав таблицу и перенеся данные. Идемпотентно.

    Детект старого CHECK - по тексту CREATE-запроса (надёжнее, чем UPDATE id=-1,
    который при 0 затронутых строк не проверяет constraint и ложно проходит)."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='sites'"
        ).fetchone()
        sql = (row[0] or "") if row else ""
        if "review" in sql and "bounced" in sql:
            return  # уже актуальный CHECK - миграция не нужна
        # CHECK старый — пересоздаём таблицу
        conn.execute("ALTER TABLE sites RENAME TO sites_old")
        conn.execute("""
            CREATE TABLE sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                email TEXT,
                telegram TEXT,
                status TEXT DEFAULT 'pending'
                    CHECK(status IN ('pending','sent','replied','hired','rejected','bounced','review')),
                tags TEXT,
                source TEXT DEFAULT 'manual',
                notes TEXT,
                amount_earned REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            INSERT INTO sites (id, url, email, telegram, status, tags, source, notes, amount_earned, created_at, updated_at)
            SELECT id, url, email, telegram,
                   CASE WHEN status NOT IN ('pending','sent','replied','hired','rejected','bounced','review') THEN 'pending' ELSE status END,
                   tags, source, notes, amount_earned, created_at, updated_at
            FROM sites_old
        """)
        conn.execute("DROP TABLE sites_old")
        conn.commit()
    finally:
        conn.close()


def migrate_score_column():
    """Блок 1 (скоринг): добавляем колонку score, если её нет.
    Идемпотентно — проверяем через PRAGMA table_info."""
    conn = get_conn()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sites)").fetchall()}
        if "score" not in cols:
            conn.execute("ALTER TABLE sites ADD COLUMN score REAL DEFAULT 0")
            conn.commit()
            print("[*] Migrated: added column 'score'")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# БЛОК 1 (СКОРИНГ) — score_lead(row) + команда `score` (dry, БЕЗ записи в БД)
# Формула: scout_filters_v2.md (взвешенная 0-100).
# score = 0.30*YOUNG + 0.25*REACH + 0.25*BUGGY + 0.10*NICHE + 0.10*FRESH
# Все внешние запросы (RDAP/HTML) идут ТОЛЬКО через SSRF-гард url_is_safe().
# ---------------------------------------------------------------------------

WEIGHTS = dict(young=0.30, reach=0.25, buggy=0.25, niche=0.10, fresh=0.10)
QA_NICHES = {"web app", "saas", "e-commerce", "ecommerce", "portfolio", "tool",
             "startup", "developer tools", "software", "consumer software"}

# Buggy-маркеры (бинарные +баллы, сумма capped 100). Без /nonexistent-запроса
# (тот требует доп. HTTP — делаем его опционально, см. _buggy_extra).
BUGGY_FORM_NO_VALIDATION = 25   # <form> без required/pattern/type
BUGGY_LOREM = 15                # lorem ipsum / placeholder
BUGGY_DEV_LEFT = 20             # TODO/FIXME/console.log/debug
BUGGY_NO_VIEWPORT = 20          # нет адаптивного viewport


def _domain_from_url(url):
    if not url:
        return ""
    m = re.match(r"https?://([^/]+)/?", url, re.I)
    return (m.group(1) if m else url).lower().strip()


def url_is_safe(url):
    """SSRF-гард для исходящих запросов движка скора.
    Блокирует не-http(s), userinfo, IP-литералы, loopback/private/link-local,
    IMDS 169.254.169.254. Доменный контакт (score) — только read-only GET."""
    if not url or not re.match(r"^https?://", url, re.I):
        return False, "scheme not http(s)"
    try:
        p = urllib.parse.urlparse(url)
    except Exception:
        return False, "bad url"
    if p.username or p.password:
        return False, "userinfo present"
    host = (p.hostname or "").lower()
    if not host:
        return False, "no host"
    # IP-литералы -> блок (исключаем loopback/private/link-local/IMDS)
    m = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$", host)
    if m:
        octs = [int(x) for x in m.groups()]
        if any(o > 255 for o in octs):
            return False, "bad ip"
        if octs[0] == 10 or octs[0] == 127:
            return False, "private/loopback ip"
        if octs[0] == 169 and octs[1] == 254:
            return False, "link-local / IMDS"
        if octs[0] == 172 and 16 <= octs[1] <= 31:
            return False, "private ip"
        if octs[0] == 192 and octs[1] == 168:
            return False, "private ip"
        return False, "ip literal not allowed"  # безопаснее не ходить по IP
    # хост должен быть доменом
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", host):
        return False, "bad host"
    return True, ""


def _fetch_text(url, timeout=8):
    """Read-only GET через SSRF-гард. Возвращает (text, error)."""
    ok, why = url_is_safe(url)
    if not ok:
        return None, f"SSRF blocked: {why}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GordonScout/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(200_000)  # лимит 200 КБ
        return raw.decode("utf-8", "replace"), None
    except Exception as e:
        return None, str(e)[:120]


def domain_age_days(domain):
    """Возраст домена через публичный RDAP (read-only, без записи). None при ошибке."""
    if not domain:
        return None
    for base in ["https://rdap.verisign.com", "https://rdap.org", "https://rdap.verisign-grs.com"]:
        url = f"{base}/com/domain/{domain}" if base == "https://rdap.verisign.com" else f"{base}/domain/{domain}"
        txt, err = _fetch_text(url, timeout=8)
        if err or not txt:
            continue
        try:
            data = json.loads(txt)
        except Exception:
            continue
        for ev in data.get("events", []):
            if ev.get("eventAction") == "registration":
                reg = ev.get("eventDate", "")[:10]
                try:
                    rd = datetime.strptime(reg, "%Y-%m-%d")
                    return (datetime.now() - rd).days
                except Exception:
                    return None
    return None


def _young(age):
    if age is None:
        return 40
    if age <= 30:
        return 100
    if age <= 90:
        return 80
    if age <= 180:
        return 60
    if age <= 365:
        return 40
    if age <= 730:
        return 20
    return 0


def _reach(emails, tgs, domain):
    if not emails and not tgs:
        return 0
    if not emails and tgs:
        return 35
    # pick_contact: email на домене сайта = идеал
    dom = domain.lower()
    on_domain = any(dom and em and dom in em.lower() for em in emails)
    if on_domain:
        return 100
    return 75


def _buggy(html):
    """Грубые маркеры сырости на HTML (без /nonexistent-запроса)."""
    if not html:
        return 0
    s = 0
    low = html.lower()
    if re.search(r"<form[^>]*>(?:(?!required|pattern|type=).)*?</form>", html, re.S | re.I):
        s += BUGGY_FORM_NO_VALIDATION
    if "lorem ipsum" in low or "your content here" in low or "placeholder text" in low:
        s += BUGGY_LOREM
    if re.search(r"\b(TODO|FIXME|console\.log|debug)\b", html):
        s += BUGGY_DEV_LEFT
    if not re.search(r'<meta[^>]+name\s*=\s*["\']?viewport', html, re.I):
        s += BUGGY_NO_VIEWPORT
    return min(100, s)


def _niche(cat):
    c = (cat or "").lower().strip()
    if c in QA_NICHES:
        return 100
    if c:
        return 50
    return 25


def _fresh(days_since_dump):
    if days_since_dump is None:
        return 50
    return max(0, 100 - min(100, days_since_dump * 5))


def score_lead(row, days_since_dump=None, fetch=False):
    """Считает score 0-100 из сигналов фильтра (scout_filters_v2.md).

    row: dict/sqlite3.Row с полями url, email, telegram, tags(категория в 'cat:'),
          created_at(дата дампа), score(если уже посчитан в БД).
    days_since_dump: свежесть лаунча (если None -> нейтрально 50).
    fetch: если True — делает read-only RDAP (возраст) + GET сайта (buggy/contact).
           По умолчанию False (использует только то что есть в строке БД).
           Никакой записи в БД не происходит.

    Возвращает dict: {score, young, reach, buggy, niche, fresh, age, signals, why}
    """
    url = row.get("url") or ""
    domain = _domain_from_url(url)
    emails = []
    em = (row.get("email") or "").strip()
    if em:
        emails = [e.strip() for e in em.split(",")]
    tgs = (row.get("telegram") or "").strip()

    # категория: храним в tags как 'cat:<Category>' (см. agent_scout.py ADD_TAGS fmt)
    cat = ""
    for t in (row.get("tags") or "").split(","):
        t = t.strip()
        if t.lower().startswith("cat:"):
            cat = t[4:].strip()

    age = domain_age_days(domain) if fetch else None
    html = None
    if fetch and domain:
        html, _ = _fetch_text(f"https://{domain}")
        # domain-email детект требует реального фетча; без фетча — по БД-контакту
    if fetch and html is not None:
        # дообогащаем emails из HTML только для reach-логики (не пишем в БД)
        found = re.findall(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", html, re.I)
        if found:
            emails = list({*emails, *found})

    young = _young(age)
    reach = _reach(emails, tgs, domain)
    buggy = _buggy(html) if fetch else 0
    niche = _niche(cat)
    fresh = _fresh(days_since_dump)

    score = round(100 * (
        WEIGHTS["young"] * young / 100
        + WEIGHTS["reach"] * reach / 100
        + WEIGHTS["buggy"] * buggy / 100
        + WEIGHTS["niche"] * niche / 100
        + WEIGHTS["fresh"] * fresh / 100
    ))
    score = max(0, min(100, score))

    signals = []
    if age is not None:
        signals.append(f"age={age}d")
    if emails:
        signals.append(f"email({len(emails)})")
    if tgs:
        signals.append("tg")
    if cat:
        signals.append(f"cat={cat}")
    why = (f"YOUNG {young}*.30 + REACH {reach}*.25 + BUGGY {buggy}*.25"
           f" + NICHE {niche}*.10 + FRESH {fresh}*.10")

    return {
        "domain": domain, "url": url, "score": score,
        "young": young, "reach": reach, "buggy": buggy, "niche": niche, "fresh": fresh,
        "age": age, "category": cat, "signals": signals, "why": why,
    }


def update_timestamp(conn, site_id):
    conn.execute(
        "UPDATE sites SET updated_at = datetime('now') WHERE id = ?",
        (site_id,)
    )
    conn.commit()


def cmd_add(args):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO sites (url, email, telegram, status, tags, source, notes, score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (args.url, args.email, args.tg, args.status, args.tags, args.source, args.notes, args.score)
        )
        conn.commit()
        site_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        print(f"[+] Added site #{site_id}: {args.url} (status={args.status}, score={args.score})")
    except sqlite3.IntegrityError:
        print(f"[!] Site already exists: {args.url}")
        existing = conn.execute("SELECT id FROM sites WHERE url = ?", (args.url,)).fetchone()
        if existing:
            print(f"   ID: {existing['id']}")
    finally:
        conn.close()


def cmd_list(args):
    conn = get_conn()
    query = "SELECT * FROM sites"
    params = []
    conditions = []

    if args.status:
        conditions.append("status = ?")
        params.append(args.status)
    if args.tags:
        conditions.append("tags LIKE ?")
        params.append(f"%{args.tags}%")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    if args.by_score:
        query += " ORDER BY score DESC, created_at DESC"
    else:
        query += " ORDER BY created_at DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        print("[-] No sites found.")
        return

    # БЛОК 1: если sort по score, а в БД score=0/None — считаем on-the-fly (БЕЗ записи).
    display = []
    for r in rows:
        r_score = r["score"]
        if args.by_score and (r_score is None or r_score == 0):
            days = _days_since_dump(r["created_at"])
            res = score_lead(dict(r), days_since_dump=days, fetch=False)
            r_score = res["score"]
        display.append((r, r_score))
    if args.by_score:
        display.sort(key=lambda x: (-x[1], x[0]["created_at"]))

    print(f"\n{'ID':<4} {'Status':<10} {'Score':<6} {'URL':<46} {'Email':<30} {'Telegram':<20}")
    print("-" * 130)
    for r, r_score in display:
        url = r['url'][:45] if r['url'] else ''
        email = (r['email'] or '')[:29]
        tg = (r['telegram'] or '')[:19]
        # помечаем on-the-fly расчёт звёздочкой, если в БД пусто
        db_score = r['score']
        if db_score is None or db_score == 0:
            score = f"{r_score:.0f}*" if r_score is not None else "-"
        else:
            score = f"{db_score:.0f}"
        icons = {'pending': '~', 'sent': '>', 'replied': '<', 'hired': '+', 'rejected': 'X', 'bounced': 'B', 'review': '?'}
        icon = icons.get(r['status'], '?')
        print(f"{r['id']:<4} {icon} {r['status']:<8} {score:<6} {url:<46} {email:<30} {tg:<20}")
    if args.by_score:
        print("  (*) = score посчитан on-the-fly (в БД пусто); не записан.")
    print()


def _days_since_dump(created_at):
    """Свежесть лаунча: дней от created_at (дата дампа) до сегодня. None если нет даты."""
    if not created_at:
        return None
    try:
        ds = created_at[:10]
        d = datetime.strptime(ds, "%Y-%m-%d")
        return (datetime.now() - d).days
    except Exception:
        return None


def cmd_score(args):
    """DRY: считает score_lead для домена, печатает таблицу. БЕЗ записи в БД.

    Ищет домен в БД (url LIKE %domain%); если не найден — строит синтетическую
    строку из самого домена. --fetch делает read-only RDAP (возраст) + GET сайта
    (buggy/contact) через SSRF-гард. Никакой записи/апдейта БД.
    """
    domain_in = args.domain.strip()
    domain = _domain_from_url(domain_in) or domain_in.lower()

    conn = get_conn()
    row = None
    try:
        rows = conn.execute("SELECT * FROM sites WHERE url LIKE ?", (f"%{domain}%",)).fetchall()
        if rows:
            row = dict(rows[0])
    finally:
        conn.close()

    if row is None:
        print(f"[*] '{domain}' нет в БД — считаю по синтетической строке (только домен).")
        row = {"url": f"https://{domain}", "email": "", "telegram": "", "tags": "", "created_at": ""}

    days = _days_since_dump(row.get("created_at"))
    res = score_lead(row, days_since_dump=days, fetch=args.fetch)

    sig = ", ".join(res["signals"]) if res["signals"] else "-"
    print()
    print(f"{'DOMAIN':<28} {'SCORE':<6} {'YOUNG':<6} {'REACH':<6} {'BUGGY':<6} {'NICHE':<6} {'FRESH':<6}")
    print("-" * 74)
    print(f"{res['domain']:<28} {res['score']:<6} {res['young']:<6} {res['reach']:<6} "
          f"{res['buggy']:<6} {res['niche']:<6} {res['fresh']:<6}")
    print()
    print(f"  SIGNALS: {sig}")
    if res["age"] is not None:
        print(f"  AGE_DAYS: {res['age']}")
    if args.fetch:
        print(f"  NOTE: buggy/reach считались по live-фетчу сайта (read-only, через SSRF-гард).")
    else:
        print(f"  NOTE: dry, без фетча (buggy=0, возраст из RDAP не брался). Добавь --fetch для live-сигналов.")
    print(f"  WHY: {res['why']}")
    print()
    print("  [DRY] В БД НЕ записано. Для сохранения нужен аппрув Назара (track.py edit --score).")
    print()


def cmd_send(args):
    conn = get_conn()
    conn.execute("UPDATE sites SET status = 'sent' WHERE id = ?", (args.id,))
    update_timestamp(conn, args.id)
    conn.close()
    print(f"[>] Site #{args.id} marked as SENT")


def cmd_reply(args):
    conn = get_conn()
    conn.execute("UPDATE sites SET status = 'replied' WHERE id = ?", (args.id,))
    update_timestamp(conn, args.id)
    conn.close()
    print(f"[<] Site #{args.id} marked as REPLIED")


def cmd_hired(args):
    conn = get_conn()
    conn.execute(
        "UPDATE sites SET status = 'hired', amount_earned = ? WHERE id = ?",
        (args.amount or 0, args.id)
    )
    update_timestamp(conn, args.id)
    conn.close()
    amount_str = f" (${args.amount})" if args.amount else ""
    print(f"[+] Site #{args.id} marked as HIRED{amount_str}")


def cmd_rejected(args):
    conn = get_conn()
    conn.execute("UPDATE sites SET status = 'rejected' WHERE id = ?", (args.id,))
    update_timestamp(conn, args.id)
    conn.close()
    print(f"[X] Site #{args.id} marked as REJECTED")


def cmd_bounce(args):
    conn = get_conn()
    conn.execute("UPDATE sites SET status = 'bounced' WHERE id = ?", (args.id,))
    update_timestamp(conn, args.id)
    conn.close()
    print(f"[B] Site #{args.id} marked as BOUNCED (dead address)")


def cmd_note(args):
    conn = get_conn()
    existing = conn.execute("SELECT notes FROM sites WHERE id = ?", (args.id,)).fetchone()
    if not existing:
        print(f"[!] Site #{args.id} not found.")
        conn.close()
        return
    old_notes = existing['notes'] or ''
    new_notes = old_notes + ("\n" if old_notes else "") + args.text
    conn.execute("UPDATE sites SET notes = ? WHERE id = ?", (new_notes, args.id))
    update_timestamp(conn, args.id)
    conn.close()
    print(f"[*] Note added to site #{args.id}")


def cmd_edit(args):
    conn = get_conn()
    existing = conn.execute("SELECT * FROM sites WHERE id = ?", (args.id,)).fetchone()
    if not existing:
        print(f"[!] Site #{args.id} not found.")
        conn.close()
        return

    updates = {}
    for field in ['url', 'email', 'telegram', 'tags', 'source', 'notes', 'status', 'score']:
        val = getattr(args, field, None)
        if val is not None:
            updates[field] = val

    if not updates:
        print("Nothing to update.")
        conn.close()
        return

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [args.id]
    conn.execute(f"UPDATE sites SET {set_clause}, updated_at = datetime('now') WHERE id = ?", values)
    conn.commit()
    conn.close()
    print(f"[E] Site #{args.id} updated.")


def cmd_stats(args):
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
    by_status = {
        row['status']: row['cnt']
        for row in conn.execute(
            "SELECT status, COUNT(*) as cnt FROM sites GROUP BY status"
        ).fetchall()
    }
    total_earned = conn.execute(
        "SELECT COALESCE(SUM(amount_earned), 0) FROM sites WHERE status = 'hired'"
    ).fetchone()[0]

    conn.close()

    print()
    print("=== Outreach Tracker Stats ===")
    print()
    print(f"  Total sites tracked:  {total}")
    print(f"  ~ Pending:           {by_status.get('pending', 0)}")
    print(f"  > Sent:              {by_status.get('sent', 0)}")
    print(f"  < Replied:           {by_status.get('replied', 0)}")
    print(f"  + Hired:             {by_status.get('hired', 0)}")
    print(f"  X Rejected:          {by_status.get('rejected', 0)}")
    print(f"  B Bounced:           {by_status.get('bounced', 0)}")

    sent_count = by_status.get('sent', 0) + by_status.get('replied', 0) + by_status.get('hired', 0) + by_status.get('rejected', 0)
    hired_count = by_status.get('hired', 0)
    if sent_count > 0:
        conv = (hired_count / sent_count) * 100
        print(f"\n  Conversion rate:    {conv:.1f}% ({hired_count}/{sent_count})")
    else:
        print("\n  Conversion rate:    N/A (no emails sent yet)")

    print(f"\n  Total earned:       BYN {total_earned:.2f}")
    print()


def cmd_export(args):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM sites ORDER BY created_at DESC").fetchall()
    conn.close()

    lines = []
    lines.append("---")
    lines.append(f"title: 'Outreach Tracker Export ({datetime.now().strftime('%Y-%m-%d %H:%M')})'")
    lines.append("tags: export/outreach")
    lines.append("---")
    lines.append("")
    lines.append("# Outreach Tracker - Full List")
    lines.append("")
    lines.append(f"**Total sites:** {len(rows)}")
    lines.append(f"**Earned:** BYN {sum(r['amount_earned'] or 0 for r in rows):.2f}")
    lines.append("")
    lines.append("## Table")
    lines.append("")
    lines.append("| # | URL | Email | Telegram | Status | Tags | Notes | Earned |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        url = r['url'] or ''
        email = r['email'] or ''
        tg = r['telegram'] or ''
        status = r['status'] or ''
        tags = r['tags'] or ''
        notes = (r['notes'] or '').replace('\n', ' ')[:50]
        amount = f"BYN {r['amount_earned']:.2f}" if r['amount_earned'] else ''
        lines.append(f"| {r['id']} | {url} | {email} | {tg} | {status} | {tags} | {notes} | {amount} |")

    lines.append("")
    lines.append("## By Status")
    lines.append("")
    for status in ['pending', 'sent', 'replied', 'hired', 'rejected', 'bounced']:
        status_rows = [r for r in rows if r['status'] == status]
        if status_rows:
            icons = {'pending': '~', 'sent': '>', 'replied': '<', 'hired': '+', 'rejected': 'X', 'bounced': 'B', 'review': '?'}
            lines.append(f"### {icons[status]} {status.capitalize()} ({len(status_rows)})")
            lines.append("")
            for r in status_rows:
                line = f"- {r['url']}"
                if r['email']:
                    line += f" -- {r['email']}"
                if r['telegram']:
                    line += f" -- {r['telegram']}"
                if r['amount_earned']:
                    line += f" -- BYN {r['amount_earned']:.2f}"
                lines.append(line)
            lines.append("")

    output = "\n".join(lines)
    print(output)


def main():
    parser = argparse.ArgumentParser(
        description="Outreach Tracker -- track QA testing outreach contacts"
    )
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # add
    p_add = subparsers.add_parser('add', help='Add a new site')
    p_add.add_argument('url', help='Site URL')
    p_add.add_argument('--email', help='Contact email')
    p_add.add_argument('--tg', help='Telegram handle')
    p_add.add_argument('--tags', help='Comma-separated tags (e.g. restaurant,belarus)')
    p_add.add_argument('--source', default='manual', help='Where this site was found')
    p_add.add_argument('--notes', help='Additional notes')
    p_add.add_argument('--score', type=float, default=0, help='Fit score 0-100 (Scout / Block 1)')
    p_add.add_argument('--status', choices=['pending', 'review'], default='pending',
                       help="pending = ready to send; review = auto-discovered, needs manual approval first")

    # list
    p_list = subparsers.add_parser('list', help='List sites')
    p_list.add_argument('--status', choices=['pending', 'sent', 'replied', 'hired', 'rejected', 'bounced', 'review'],
                        help='Filter by status')
    p_list.add_argument('--tags', help='Filter by tags (substring match)')
    p_list.add_argument('--by-score', action='store_true', help='Sort by fit score DESC (Block 1)')

    # send
    p_send = subparsers.add_parser('send', help='Mark site as emailed')
    p_send.add_argument('id', type=int, help='Site ID')

    # reply
    p_reply = subparsers.add_parser('reply', help='Mark site as replied')
    p_reply.add_argument('id', type=int, help='Site ID')

    # hired
    p_hired = subparsers.add_parser('hired', help='Mark site as hired')
    p_hired.add_argument('id', type=int, help='Site ID')
    p_hired.add_argument('--amount', type=float, help='Amount earned (BYN)')

    # rejected
    p_rejected = subparsers.add_parser('rejected', help='Mark site as rejected')
    p_rejected.add_argument('id', type=int, help='Site ID')

    # bounce
    p_bounce = subparsers.add_parser('bounce', help='Mark site as bounced (dead email)')
    p_bounce.add_argument('id', type=int, help='Site ID')

    # note
    p_note = subparsers.add_parser('note', help='Add a note to a site')
    p_note.add_argument('id', type=int, help='Site ID')
    p_note.add_argument('text', help='Note text')

    # edit
    p_edit = subparsers.add_parser('edit', help='Edit site fields')
    p_edit.add_argument('id', type=int, help='Site ID')
    p_edit.add_argument('--url', help='New URL')
    p_edit.add_argument('--email', help='New email')
    p_edit.add_argument('--telegram', help='New telegram')
    p_edit.add_argument('--tags', help='New tags')
    p_edit.add_argument('--source', help='New source')
    p_edit.add_argument('--notes', help='New notes')
    p_edit.add_argument('--score', type=float, help='New fit score 0-100')
    p_edit.add_argument('--status', choices=['pending', 'sent', 'replied', 'hired', 'rejected', 'bounced', 'review'],
                        help='New status')
    p_stats = subparsers.add_parser('stats', help='Show statistics')

    # export
    p_export = subparsers.add_parser('export', help='Export to markdown')

    # score (БЛОК 1: dry-скоринг домена, БЕЗ записи в БД)
    p_score = subparsers.add_parser('score', help='Score a lead by domain (dry, no DB write)')
    p_score.add_argument('domain', help='Domain or URL to score')
    p_score.add_argument('--fetch', action='store_true',
                         help='Live read-only RDAP (age) + GET site (buggy/contact) via SSRF-guard')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    # Map commands to functions
    cmd_map = {
        'add': cmd_add,
        'list': cmd_list,
        'send': cmd_send,
        'reply': cmd_reply,
        'hired': cmd_hired,
        'rejected': cmd_rejected,
        'bounce': cmd_bounce,
        'note': cmd_note,
        'edit': cmd_edit,
        'stats': cmd_stats,
        'export': cmd_export,
        'score': cmd_score,
    }

    init_db()
    migrate_bounced_status()
    migrate_score_column()
    cmd_map[args.command](args)


if __name__ == '__main__':
    main()
