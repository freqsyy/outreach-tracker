#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_scout_heatmap.py - "тепловая карта" review-лидов по возрасту (read-only).

Задача тик-26 (по спеке тик-25). Рендерит markdown-таблицу: свежие лиды сверху,
остывшие снизу, чтобы Назару было удобно аппрувить молодые сайты первыми.

DRIFT от текста задачи:
  Задача просит `SELECT domain, age_days, score, contact FROM sites`.
  Реальная схема sites = id,url,email,telegram,status,tags,source,notes,
  amount_earned,created_at,updated_at,score. Колонок domain/age_days/contact НЕТ.
  Поэтому деривируем:
    - domain    <- norm_domain(url)                      (dedup.py)
    - age_days  <- парсинг notes 'age=Nd'; fallback 'launched~YYYY-MM-DD'
                   -> дни до created_at; иначе None (НЕИЗВЕСТНО)
    - contact   <- email, отфильтрованный is_junk_email  (common_contacts.py)
    - score     <- колонка score (как есть)

Сегменты (по спеке тик-25):
    <30д     -> "свежак"    (HOT)
    30-120д  -> "тёплый"    (WARM)
    >=120д   -> "остывший"  (COLD)
    None     -> "неизвестно" (?)

Строго read-only: только SELECT + запись файла-артефакта. Статусы НЕ меняем,
лидов НЕ кладём, agent_scout.py НЕ запускаем.

Запуск:
    python agent_scout_heatmap.py [--dry-run]  (--dry-run по умолчанию ВКЛ)
"""

import argparse
import os
import re
import sqlite3
import sys
from datetime import date, datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outreach.db")
OUT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "dispatch", "artifacts", "scout_heatmap.md",
)

# Пороги сегментов (дни)
FRESH_MAX = 30    # <30 -> свежак
WARM_MAX = 120    # 30..120 -> тёплый; >=120 -> остывший

SEG_FRESH = ("свежак", "HOT")
SEG_WARM = ("тёплый", "WARM")
SEG_COLD = ("остывший", "COLD")
SEG_UNK = ("неизвестно", "?")

# Мягкие импорты хелперов: без них скрипт всё равно работает (деградация).
try:
    from dedup import norm_domain
except Exception:  # pragma: no cover
    def norm_domain(url):
        u = (url or "").strip().lower()
        u = re.sub(r"^https?://", "", u)
        u = u.split("/")[0].split(":")[0]
        if u.startswith("www."):
            u = u[4:]
        return u

try:
    from common_contacts import is_junk_email
except Exception:  # pragma: no cover
    def is_junk_email(email):
        e = (email or "").lower()
        return (not e) or ("example" in e) or ("@" not in e)

# Суффиксы, которыми "маскируется" мусорный email (имя файла картинки/страницы).
_BAD_EMAIL_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".html", ".htm",
                     ".svg", ".webp", ".css", ".js", ".json", ".xml")


_AGE_RE = re.compile(r"age\s*=\s*(\d+)\s*d", re.IGNORECASE)
_LAUNCH_RE = re.compile(r"launched\s*~\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)


def _parse_created(created_at):
    """created_at -> date. Терпим к форматам '2026-07-11 20:09:00' и '2026-07-11'."""
    s = (created_at or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[: len(fmt) + (9 if "%H" in fmt else 0)], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def derive_age_days(notes, created_at, today):
    """Возраст лида в днях.
    1) прямой маркер scout 'age=Nd' в notes (самый точный, доменный возраст);
    2) 'launched~YYYY-MM-DD' -> дни от launched до created_at (или до today);
    3) None -> НЕИЗВЕСТНО.
    """
    n = notes or ""
    m = _AGE_RE.search(n)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    m = _LAUNCH_RE.search(n)
    if m:
        try:
            launched = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            end = _parse_created(created_at) or today
            delta = (end - launched).days
            if delta >= 0:
                return delta
        except ValueError:
            pass
    return None


def segment_for(age_days):
    """age_days -> (метка, тег)."""
    if age_days is None:
        return SEG_UNK
    if age_days < FRESH_MAX:
        return SEG_FRESH
    if age_days < WARM_MAX:
        return SEG_WARM
    return SEG_COLD


def clean_contact(email):
    """Чистый контакт или '-' если мусорный/пустой.

    Доп. страховка поверх is_junk_email: email, чей домен заканчивается на
    суффикс файла (напр. '005_22@0.75x-1-500x350.png') - это не адрес, а
    имя картинки/страницы, скаут кладёт такое при парсинге.
    """
    e = (email or "").strip()
    if not e or is_junk_email(e):
        return "-"
    local, sep, dom = e.lower().rpartition("@")
    if not sep or "." not in dom:
        return "-"
    if dom.endswith(_BAD_EMAIL_SUFFIX):
        return "-"
    return e


def load_review_rows(conn):
    """read-only: тянем review-лиды."""
    cur = conn.execute(
        "SELECT id, url, email, notes, score, created_at "
        "FROM sites WHERE status='review'"
    )
    return cur.fetchall()


def build_rows(raw_rows, today):
    out = []
    for rid, url, email, notes, score, created_at in raw_rows:
        age = derive_age_days(notes, created_at, today)
        seg_label, seg_tag = segment_for(age)
        out.append({
            "id": rid,
            "domain": norm_domain(url) or (url or "-"),
            "age_days": age,
            "seg_label": seg_label,
            "seg_tag": seg_tag,
            "score": score if score is not None else 0.0,
            "contact": clean_contact(email),
        })
    # Сортировка по age_days ASC; None (НЕИЗВЕСТНО) в конец.
    out.sort(key=lambda r: (r["age_days"] is None, r["age_days"] if r["age_days"] is not None else 0, r["id"]))
    return out


def render_md(rows, today):
    total = len(rows)
    counts = {"HOT": 0, "WARM": 0, "COLD": 0, "?": 0}
    for r in rows:
        counts[r["seg_tag"]] = counts.get(r["seg_tag"], 0) + 1

    lines = []
    lines.append("# SCOUT - тепловая карта review-лидов по возрасту")
    lines.append("")
    lines.append("Сгенерировано: agent_scout_heatmap.py (read-only, --dry-run).")
    lines.append("Дата рендера: " + today.isoformat())
    lines.append("")
    lines.append("Легенда сегментов:")
    lines.append("- свежак (HOT): возраст < {}д - аппрувить первыми".format(FRESH_MAX))
    lines.append("- тёплый (WARM): {}-{}д".format(FRESH_MAX, WARM_MAX))
    lines.append("- остывший (COLD): >= {}д - в конец очереди".format(WARM_MAX))
    lines.append("- неизвестно (?): возраст не вычислен из notes/created_at")
    lines.append("")
    lines.append("## Сводка")
    lines.append("")
    lines.append("| сегмент | кол-во |")
    lines.append("| --- | ---: |")
    lines.append("| свежак (HOT) | {} |".format(counts.get("HOT", 0)))
    lines.append("| тёплый (WARM) | {} |".format(counts.get("WARM", 0)))
    lines.append("| остывший (COLD) | {} |".format(counts.get("COLD", 0)))
    lines.append("| неизвестно (?) | {} |".format(counts.get("?", 0)))
    lines.append("| ВСЕГО | {} |".format(total))
    lines.append("")
    lines.append("## Тепловая карта (свежие сверху)")
    lines.append("")
    lines.append("| # | domain | age_days | сегмент | score | contact-чистый |")
    lines.append("| ---: | --- | ---: | --- | ---: | --- |")
    for i, r in enumerate(rows, 1):
        age = "?" if r["age_days"] is None else str(r["age_days"])
        seg = "{} ({})".format(r["seg_label"], r["seg_tag"])
        try:
            score_s = "{:.1f}".format(float(r["score"]))
        except (TypeError, ValueError):
            score_s = str(r["score"])
        lines.append("| {} | {} | {} | {} | {} | {} |".format(
            i, r["domain"], age, seg, score_s, r["contact"]))
    lines.append("")
    lines.append("---")
    lines.append("DRIFT: колонок domain/age_days/contact в БД нет - деривированы из "
                 "url (norm_domain), notes (age=Nd / launched~DATE), email (is_junk_email).")
    lines.append("Read-only: БД не менялась, лиды не отправлялись, статусы не трогались.")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Тепловая карта review-лидов по возрасту (read-only).")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                    help="Без сайд-эффектов (по умолчанию ВКЛ). Пишет только файл-артефакт.")
    ap.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                    help="Зарезервировано; поведение идентично (скрипт всегда read-only по БД).")
    ap.add_argument("--db", default=DB_PATH, help="Путь к outreach.db")
    ap.add_argument("--out", default=OUT_PATH, help="Путь к выходному .md")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print("ОШИБКА: БД не найдена: {}".format(args.db), file=sys.stderr)
        return 2

    # Строго read-only подключение (URI mode=ro).
    uri = "file:{}?mode=ro".format(args.db.replace("\\", "/"))
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        conn = sqlite3.connect(args.db)  # fallback
    try:
        raw = load_review_rows(conn)
    finally:
        conn.close()

    today = date.today()
    rows = build_rows(raw, today)
    md = render_md(rows, today)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(md)

    hot = sum(1 for r in rows if r["seg_tag"] == "HOT")
    warm = sum(1 for r in rows if r["seg_tag"] == "WARM")
    cold = sum(1 for r in rows if r["seg_tag"] == "COLD")
    unk = sum(1 for r in rows if r["seg_tag"] == "?")
    print("OK: review-лидов {} -> {}".format(len(rows), args.out))
    print("  свежак(HOT)={} тёплый(WARM)={} остывший(COLD)={} неизвестно(?)={}".format(
        hot, warm, cold, unk))
    print("  режим: read-only (--dry-run={})".format(args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
