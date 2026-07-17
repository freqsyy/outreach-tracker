#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
letter_proof.py - АГЕНТ ДОКАЗАТЕЛЬСТВА персонализации письма (v0.4 core-funnel).

РЕШАЕТ критический баг v0.4: письма уходили БЕЗ персонального бага из notes
(AUDIT::), потому что letter.txt не содержал {bug}, а agent_sender только
replace("{site}"). Этот агент показывает, какое письмо РЕАЛЬНО уйдёт каждому
лиду — с подставленным доменом и багом — БЕЗ отправки.

Режимы:
  python letter_proof.py            # proof для первых 10 лидов (pending+sent)
  python letter_proof.py --all      # proof для всех лидов с email
  python letter_proof.py --id 58    # proof только для сайта #58
  python letter_proof.py --with-bug # только те, у кого есть AUDIT:: баг

БЕЗОПАСНО: ничего не пишет в БД, не отправляет письма. Read-only.
"""
import os
import sys
import sqlite3
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gordon_common as gc


def _conn():
    return gc.get_conn()


def render(site):
    """Возвращает (subject, body, bug) для сайта — как уйдёт в письме."""
    subject, body = gc.load_letter()
    domain = ""
    try:
        from urllib.parse import urlparse
        domain = urlparse(site.get("url") or "").netloc or (site.get("url") or "")
    except Exception:
        domain = site.get("url") or ""
    bug = gc.extract_audit_bug(site.get("notes", ""))
    subj = subject.replace("{site}", domain).replace("{bug}", bug or "")
    if not bug:
        bug_fill = "пара мелких недочётов в вёрстке и формах, которые проще показать на живом примере"
    else:
        bug_fill = bug
    bod = body.replace("{site}", domain).replace("{bug}", bug_fill)
    return subj, bod, bug


def proof_one(site):
    subj, bod, bug = render(site)
    has_audit = "AUDIT::" in (site.get("notes") or "")
    print("=" * 64)
    print("Сайт #{id} | {url}".format(id=site.get("id"), url=site.get("url")))
    print("email: {e} | status: {st} | баг в notes: {a}".format(
        e=site.get("email"), st=site.get("status"), a="ДА" if has_audit else "нет"))
    if has_audit:
        print("извлечённый баг: {b}".format(b=(bug[:90] + "..." if bug and len(bug) > 90 else bug)))
    print("-" * 64)
    print("ТЕМА: {s}".format(s=subj))
    print("-" * 64)
    print(bod)
    print()


def main():
    ap = argparse.ArgumentParser(description="Доказательство персонализации письма (без отправки)")
    ap.add_argument("--all", action="store_true", help="все лиды с email")
    ap.add_argument("--id", type=int, default=None, help="только сайт с этим id")
    ap.add_argument("--with-bug", action="store_true", help="только с AUDIT:: багом")
    args = ap.parse_args()

    conn = _conn()
    if args.id is not None:
        rows = conn.execute("SELECT * FROM sites WHERE id=?", (args.id,)).fetchall()
    elif args.all:
        rows = conn.execute("SELECT * FROM sites WHERE email IS NOT NULL ORDER BY id").fetchall()
    elif args.with_bug:
        rows = conn.execute("SELECT * FROM sites WHERE notes LIKE '%AUDIT::%' ORDER BY id").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM sites WHERE email IS NOT NULL ORDER BY id LIMIT 10").fetchall()
    conn.close()

    if not rows:
        print("Нет лидов под фильтр.")
        return

    shown = 0
    for r in rows:
        site = dict(r)
        if args.with_bug and "AUDIT::" not in (site.get("notes") or ""):
            continue
        proof_one(site)
        shown += 1
    print("Показано писем: {n}".format(n=shown))
    print("\n[read-only] Ничего не отправлено, БД не изменена.")


if __name__ == "__main__":
    main()
