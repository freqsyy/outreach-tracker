#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
funnel_analytics.py - АГЕНТ АНАЛИТИКИ ВОРОНКИ (v0.4 core-funnel).

Считает конверсию по стадиям аутрича и разбивает по источникам/тегам.
ПОЛНОСТЬЮ read-only — не пишет в БД, не меняет статусы.

Режимы:
  python funnel_analytics.py            # сводка по воронке
  python funnel_analytics.py --source  # разбивка по источнику (parser/scout/...)
  python funnel_analytics.py --by-tag  # разбивка по тегам
  python funnel_analytics.py --daily   # динамика по дням (по updated_at)

Источник правды — outreach.db (таблица sites, статусы:
sent/pending/rejected/bounced/replied/hired/review).
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


def overall():
    conn = _conn()
    total = conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
    by_status = {}
    for st, n in conn.execute("SELECT status, COUNT(*) FROM sites GROUP BY status"):
        by_status[st] = n
    conn.close()

    sent = by_status.get("sent", 0)
    pending = by_status.get("pending", 0)
    bounced = by_status.get("bounced", 0)
    replied = by_status.get("replied", 0)
    rejected = by_status.get("rejected", 0)
    hired = by_status.get("hired", 0)
    review = by_status.get("review", 0)

    print("=== ВОРОНКА ГОРДОНА ===")
    print("Всего сайтов:        {t}".format(t=total))
    print("  review (не аппрув): {n}".format(n=review))
    print("  sent (отправлено):  {n}".format(n=sent))
    print("  pending (в очереди):{n}".format(n=pending))
    print("  bounced (отказ дошло):{n}".format(n=bounced))
    print("  replied (ответили): {n}".format(n=replied))
    print("  rejected (отказ):   {n}".format(n=rejected))
    print("  hired (наняли):     {n}".format(n=hired))

    delivered = sent - bounced
    print("\n=== КОНВЕРСИЯ ===")
    if sent:
        print("Доставлено (sent-bounce): {d}".format(d=delivered))
        print("reply/sent:  {p:.1f}%  ({r}/{s})".format(
            p=100.0 * replied / sent, r=replied, s=sent))
    if replied:
        print("hire/reply:  {p:.1f}%  ({h}/{r})".format(
            p=100.0 * hired / replied, h=hired, r=replied))
    if delivered:
        print("hire/deliver:{p:.1f}%  ({h}/{d})".format(
            p=100.0 * hired / delivered, h=hired, d=delivered))


def by_source():
    conn = _conn()
    rows = conn.execute(
        "SELECT COALESCE(source,'') AS src, status, COUNT(*) FROM sites "
        "GROUP BY src, status ORDER BY src, status").fetchall()
    conn.close()
    print("=== ПО ИСТОЧНИКУ ===")
    cur = None
    for src, st, n in rows:
        if src != cur:
            cur = src
            print("\n[{s}]".format(s=src or "(пусто)"))
        print("  {st:9s}: {n}".format(st=st, n=n))


def by_tag():
    conn = _conn()
    rows = conn.execute(
        "SELECT tags, status, COUNT(*) FROM sites WHERE tags IS NOT NULL "
        "GROUP BY tags, status ORDER BY tags").fetchall()
    conn.close()
    print("=== ПО ТЕГАМ ===")
    cur = None
    for tag, st, n in rows:
        if tag != cur:
            cur = tag
            print("\n[{t}]".format(t=tag))
        print("  {st:9s}: {n}".format(st=st, n=n))


def daily():
    conn = _conn()
    rows = conn.execute(
        "SELECT substr(updated_at,1,10) AS day, status, COUNT(*) FROM sites "
        "WHERE updated_at IS NOT NULL GROUP BY day, status ORDER BY day").fetchall()
    conn.close()
    print("=== ПО ДНЯМ (updated_at) ===")
    cur = None
    for day, st, n in rows:
        if day != cur:
            cur = day
            print("\n[{d}]".format(d=day))
        print("  {st:9s}: {n}".format(st=st, n=n))


def main():
    ap = argparse.ArgumentParser(description="Аналитика воронки Гордона (read-only)")
    ap.add_argument("--source", action="store_true", help="разбивка по источнику")
    ap.add_argument("--by-tag", action="store_true", help="разбивка по тегам")
    ap.add_argument("--daily", action="store_true", help="динамика по дням")
    args = ap.parse_args()

    if args.source:
        by_source()
    elif args.by_tag:
        by_tag()
    elif args.daily:
        daily()
    else:
        overall()
    print("\n[read-only] БД не изменена.")


if __name__ == "__main__":
    main()
