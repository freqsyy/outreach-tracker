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
"""

import sqlite3
import argparse
import os
import sys
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
            """INSERT INTO sites (url, email, telegram, status, tags, source, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (args.url, args.email, args.tg, args.status, args.tags, args.source, args.notes)
        )
        conn.commit()
        site_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        print(f"[+] Added site #{site_id}: {args.url} (status={args.status})")
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

    query += " ORDER BY created_at DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        print("[-] No sites found.")
        return

    print(f"\n{'ID':<4} {'Status':<10} {'URL':<50} {'Email':<30} {'Telegram':<20}")
    print("-" * 120)
    for r in rows:
        url = r['url'][:49] if r['url'] else ''
        email = (r['email'] or '')[:29]
        tg = (r['telegram'] or '')[:19]
        icons = {'pending': '~', 'sent': '>', 'replied': '<', 'hired': '+', 'rejected': 'X'}
        icon = icons.get(r['status'], '?')
        print(f"{r['id']:<4} {icon} {r['status']:<8} {url:<50} {email:<30} {tg:<20}")
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
    for field in ['url', 'email', 'telegram', 'tags', 'source', 'notes', 'status']:
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
            icons = {'pending': '~', 'sent': '>', 'replied': '<', 'hired': '+', 'rejected': 'X'}
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
    p_add.add_argument('--status', choices=['pending', 'review'], default='pending',
                       help="pending = ready to send; review = auto-discovered, needs manual approval first")

    # list
    p_list = subparsers.add_parser('list', help='List sites')
    p_list.add_argument('--status', choices=['pending', 'sent', 'replied', 'hired', 'rejected', 'bounced', 'review'],
                        help='Filter by status')
    p_list.add_argument('--tags', help='Filter by tags (substring match)')

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
    p_edit.add_argument('--status', choices=['pending', 'sent', 'replied', 'hired', 'rejected', 'bounced', 'review'],
                        help='New status')

    # stats
    p_stats = subparsers.add_parser('stats', help='Show statistics')

    # export
    p_export = subparsers.add_parser('export', help='Export to markdown')

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
    }

    init_db()
    migrate_bounced_status()
    cmd_map[args.command](args)


if __name__ == '__main__':
    main()
