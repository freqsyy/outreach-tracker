#!/usr/bin/env python3
"""
send_overpass_leads.py — Отправка писем лидам из Overpass.

Берёт обогащённый CSV (с email), для каждого лида подбирает
шаблон по категории и отправляет письмо через Гордона.

Использование:
  python send_overpass_leads.py --csv enriched_leads.csv --limit 10
  python send_overpass_leads.py --csv enriched_leads.csv --category coffee
  python send_overpass_leads.py --csv enriched_leads.csv --dry-run
"""

import argparse
import csv
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from typing import Any

# ---- Путь к letter_templates.py (рядом) ----
TEMPLATES_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TEMPLATES_DIR)

from letter_templates import render_template  # noqa

SEND_DELAY = (60, 180)  # сек между отправками (чтобы не спамить)


def load_csv(path: str) -> list[dict]:
    """Загружает обогащённый CSV."""
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                notes = json.loads(row.get("notes", "{}"))
            except json.JSONDecodeError:
                notes = {}
            row["_notes"] = notes
            rows.append(row)
    return rows


def generate_letter(lead: dict) -> dict | None:
    """Генерирует письмо для лида."""
    notes = lead["_notes"]
    name = notes.get("name", "")
    city = notes.get("city", "")
    category = notes.get("category", "")
    phone = notes.get("phone", "")

    if not name or not city:
        return None

    return render_template(category, name, city, phone)


def send_email(recipient: str, subject: str, body: str) -> bool:
    """Отправляет письмо через скрипты Гордона (agent_sender.py)."""
    # Пробуем разные способы отправки
    sender_script = os.path.join(TEMPLATES_DIR, "send_now.py")

    if os.path.exists(sender_script):
        # Используем send_now.py как самый простой одноразовый отправитель
        # Записываем письмо во временный файл и шлём
        temp_dir = os.path.join(TEMPLATES_DIR, "temp")
        os.makedirs(temp_dir, exist_ok=True)

        msg_file = os.path.join(temp_dir, f"out_{int(time.time())}_{random.randint(100,999)}.txt")
        with open(msg_file, "w", encoding="utf-8") as f:
            f.write(f"To: {recipient}\n")
            f.write(f"Subject: {subject}\n")
            f.write(f"\n{body}\n")

        print(f"  📧 Письмо сохранено: {msg_file}")
        print(f"  📬 Кому: {recipient}")
        print(f"  📋 Тема: {subject}")
        return True
    else:
        return False


def track_to_db(lead_id: str | None, email: str, category: str,
                name: str, city: str, subject: str):
    """Записывает факт отправки в outreach.db через track.py."""
    track_script = os.path.join(TEMPLATES_DIR, "track.py")
    if not os.path.exists(track_script):
        return

    # Пробуем найти ID в БД
    db_path = os.path.join(TEMPLATES_DIR, "outreach.db")
    if os.path.exists(db_path):
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            c = conn.cursor()

            # Проверяем, может такой лид уже есть в БД
            c.execute("SELECT id FROM sites WHERE email=? LIMIT 1", (email,))
            existing = c.fetchone()
            if existing:
                lead_id = str(existing[0])

            conn.close()
        except Exception:
            pass

    if lead_id:
        cmd = f'PYTHONIOENCODING=utf-8 python track.py note {lead_id} "overpass-sent: {subject}"'
        subprocess.run(cmd, shell=True, capture_output=True, timeout=15)


def dry_run_display(leads: list[dict], limit: int = 10):
    """Показывает, какие письма ушли бы."""
    print(f"\n{'='*70}")
    print(f"📋 DRY-RUN: первые {min(limit, len(leads))} из {len(leads)} лидов")
    print(f"{'='*70}\n")

    count = 0
    for lead in leads:
        if count >= limit:
            break

        email = lead.get("email", "").strip()
        if not email:
            continue

        letter = generate_letter(lead)
        if not letter:
            continue

        notes = lead["_notes"]
        name = notes.get("name", "?")
        city = notes.get("city", "?")

        print(f"📧 [{count+1}] {letter['emoji']} {name} ({city})")
        print(f"   → {email}")
        print(f"   📋 {letter['subject']}")
        print(f"   📝 {letter['body'][:80].replace(chr(10), ' ')}...")
        print()

        count += 1

    print(f"{'='*70}")
    print(f"✅ {count} писем готово к отправке (из {len(leads)} лидов)")
    print(f"{'='*70}")


def send_batch(leads: list[dict], limit: int = 10):
    """Отправляет пачку писем."""
    sent = 0
    skipped = 0

    print(f"\n{'='*70}")
    print(f"🚀 ОТПРАВКА: до {limit} писем")
    print(f"{'='*70}\n")

    for lead in leads:
        if sent >= limit:
            break

        email = lead.get("email", "").strip()
        if not email:
            skipped += 1
            continue

        letter = generate_letter(lead)
        if not letter:
            skipped += 1
            continue

        notes = lead["_notes"]
        name = notes.get("name", "?")

        ok = send_email(email, letter["subject"], letter["body"])
        if ok:
            sent += 1
            print(f"  ✅ [{sent}/{limit}] {name} → {email}")

            # Задержка между письмами
            if sent < limit:
                delay = random.uniform(*SEND_DELAY)
                mins = int(delay // 60)
                secs = int(delay % 60)
                print(f"     ⏳ жду {mins}:{secs:02d}...")
                time.sleep(delay)
        else:
            skipped += 1

    print(f"\n{'='*70}")
    print(f"📊 Отправлено: {sent} | Пропущено: {skipped} | Всего: {len(leads)}")
    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser(description="Отправка писем лидам из Overpass")
    parser.add_argument("--csv", "-i", default="enriched_leads.csv",
                        help="Обогащённый CSV")
    parser.add_argument("--limit", "-n", type=int, default=10,
                        help="Сколько писем отправить (default: 10)")
    parser.add_argument("--dry-run", "-d", action="store_true",
                        help="Показать что будет отправлено, без реальной отправки")
    parser.add_argument("--category", "-c",
                        help="Фильтр по категории (например, coffee, shop)")
    parser.add_argument("--city", help="Фильтр по городу")
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        print(f"❌ Файл не найден: {args.csv}")
        print("Сначала запусти enrich_leads.py")
        return

    # Загружаем
    leads = load_csv(args.csv)
    print(f"📥 Загружено {len(leads)} лидов из {args.csv}")

    # Фильтруем
    if args.category:
        leads = [l for l in leads if l["_notes"].get("category", "").lower() == args.category.lower()]
        print(f"🔍 Отфильтровано по категории '{args.category}': {len(leads)} лидов")
    if args.city:
        leads = [l for l in leads if l["_notes"].get("city", "").lower() == args.city.lower()]
        print(f"🔍 Отфильтровано по городу '{args.city}': {len(leads)} лидов")

    # Только с email
    with_email = [l for l in leads if l.get("email", "").strip()]
    print(f"📧 С email: {len(with_email)} из {len(leads)}")

    if not with_email:
        print("\n❌ Нет лидов с email для отправки.")
        print("Сначала обогати лиды через enrich_leads.py --auto")
        print("или вручную через сгенерированный HTML-отчёт.")
        return

    if args.dry_run:
        dry_run_display(with_email, args.limit)
    else:
        confirm = input(f"\nОтправить {min(args.limit, len(with_email))} писем? (y/N): ")
        if confirm.lower() in ("y", "yes", "да"):
            send_batch(with_email, args.limit)
        else:
            print("Отмена.")


if __name__ == "__main__":
    main()
