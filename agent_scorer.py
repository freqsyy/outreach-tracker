#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_scorer.py - АГЕНТ скоринга лидов Гордона (Блок 1, спек от 2026-07-12).

Сортирует очередь рассылки по «горячести» лида: сначала самым перспективным
сайтам, а не в порядке добавления. Балл живёт в колонке `score` БД, sender
читает его при выборе очереди (ORDER BY score DESC).

Офлайн - только данные, уже лежащие в `sites`, без сетевых проверок
(без риска бана/замедления).

БЕЗОПАСНОСТЬ (как у agent_nurture):
  --dry (по умолчанию) ИЛИ без --write: НИЧЕГО не пишется в БД,
  только печатает топ-лидов и распределение баллов.
  Запись в БД = ТОЛЬКО при --write.

Запуск:
  python agent_scorer.py            # dry-run: печатает топ, БД НЕ трогает
  python agent_scorer.py --write    # пересчитывает и пишет score в БД
  python agent_scorer.py --write --limit 20   # только первые 20 по id

Спек: docs/superpowers/specs/2026-07-12-gordon-lead-scorer-design.md
"""

import os
import sys
import sqlite3
import argparse
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "outreach.db")

# FIXME: RDAP-возраст домена лежит в notes как "age_days=N" (если скаут дописал).
# Если нет - считаем, что свежесть не доказана (0 очков по этому фактору).
FRESH_AGE_DAYS = 90
FREE_MAIL = ("gmail.com", "yandex.ru", "yandex.by", "mail.ru", "outlook.com",
             "hotmail.com", "icloud.com", "proton.me", "protonmail.com", "zoho.com")


def _domain_of(url):
    """Вытащить домен из url (без www/sub)."""
    if not url:
        return ""
    u = url.strip().lower()
    u = u.split("://", 1)[-1]
    u = u.split("/", 1)[0]
    u = u.split("?", 1)[0]
    if u.startswith("www."):
        u = u[4:]
    return u


def _domain_of_email(email):
    if not email or "@" not in email:
        return ""
    return email.strip().lower().rsplit("@", 1)[-1]


def _notes_age_days(notes):
    """Ищем 'age_days=N' в notes (скаут пишет возраст домена из RDAP)."""
    if not notes:
        return None
    import re
    m = re.search(r"age_days\s*=\s*(\d+)", notes)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def score_site(row):
    """
    Чистая функция: row (sqlite3.Row или dict с ключами
    url/email/telegram/source/tags/notes) -> float балл (сумма, ~0..100).
    """
    score = 0.0
    if not isinstance(row, dict):
        try:
            row = dict(row)
        except Exception:
            row = {k: getattr(row, k, "") for k in
                   ("url", "email", "telegram", "source", "tags", "notes")}

    url = (row.get("url") or "")
    email = (row.get("email") or "")
    telegram = (row.get("telegram") or "")
    source = (row.get("source") or "")
    tags = (row.get("tags") or "")
    notes = (row.get("notes") or "")

    # 1. Найден баг: маркер AUDIT:: в notes
    if "AUDIT::" in notes:
        score += 40

    # 2. Свежий домен: тег fresh ИЛИ возраст < FRESH_AGE_DAYS из RDAP
    fresh_by_tag = "fresh" in tags.lower()
    age = _notes_age_days(notes)
    fresh_by_age = (age is not None and age < FRESH_AGE_DAYS)
    if fresh_by_tag or fresh_by_age:
        score += 25

    # 3. Есть Telegram
    if telegram.strip():
        score += 15

    # 4. Источник scout
    if source.strip().lower() == "scout":
        score += 10

    # 5. Email на домене сайта (не free-mail)
    dom = _domain_of(url)
    mail_dom = _domain_of_email(email)
    if dom and mail_dom and mail_dom == dom and mail_dom not in FREE_MAIL:
        score += 10

    # 6. Бонус за длину email-домена (>10 символов)
    if mail_dom and len(mail_dom) > 10:
        score += 5

    return score


def _connect():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def run(write=False, limit=None):
    c = _connect()
    q = "SELECT id,url,email,telegram,source,tags,notes,status FROM sites"
    if limit:
        q += " ORDER BY id ASC LIMIT %d" % int(limit)
    else:
        q += " ORDER BY id ASC"
    rows = c.execute(q).fetchall()

    results = []
    for r in rows:
        s = score_site(r)
        results.append((s, r))

    results.sort(key=lambda x: x[0], reverse=True)

    print("=== ТОП лидов по score (спек agent_scorer) ===")
    print("%-5s %-7s %-45s %s" % ("id", "score", "url", "status"))
    for s, r in results[:25]:
        print("%-5d %-7.1f %-45s %s" % (r["id"], s, (r["url"] or "")[:45], r["status"]))

    # Распределение
    buckets = {"0-20": 0, "21-50": 0, "51-80": 0, "81-100": 0}
    for s, _ in results:
        if s <= 20:
            buckets["0-20"] += 1
        elif s <= 50:
            buckets["21-50"] += 1
        elif s <= 80:
            buckets["51-80"] += 1
        else:
            buckets["81-100"] += 1
    print("\n=== Распределение (n=%d) ===" % len(results))
    for k, v in buckets.items():
        print("  %-7s: %d" % (k, v))

    if not write:
        print("\n[dry-run] БД НЕ изменена. Для записи score: --write")
        c.close()
        return

    # Запись (только колонка score, ничего больше)
    c.execute("BEGIN")
    for s, r in results:
        c.execute("UPDATE sites SET score=? WHERE id=?", (s, r["id"]))
    c.execute("UPDATE sites SET updated_at=? WHERE id IN (SELECT id FROM sites)",
              (datetime.now().isoformat(sep=" "),))
    c.commit()
    print("\n[write] Записано score для %d сайтов." % len(results))
    c.close()


def main():
    ap = argparse.ArgumentParser(description="Гордон: скоринг лидов")
    ap.add_argument("--write", action="store_true",
                    help="записать score в БД (по умолчанию dry-run)")
    ap.add_argument("--limit", type=int, default=None,
                    help="обработать только первые N сайтов по id")
    args = ap.parse_args()
    run(write=args.write, limit=args.limit)


if __name__ == "__main__":
    main()
