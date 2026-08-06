#!/usr/bin/env python3
"""
agent_overpass.py — Генератор лидов через OpenStreetMap Overpass API.

Ищет бизнесы в указанном городе, находит тех у кого
есть телефон НО нет сайта/бота — лиды для QA-аутрича.

Использование:
  python agent_overpass.py --city minsk --output leads.csv
  python agent_overpass.py --city minsk,brest --json
  python agent_overpass.py --city minsk --db          # сразу в outreach.db
  python agent_overpass.py --city minsk --dry-run
"""

import argparse
import csv
import json
import os
import random
import re
import sqlite3
import subprocess
import time
from typing import Any

OVERPASS_URL = "https://z.overpass-api.de/api/interpreter"
DELAY_BETWEEN = 12  # секунд между запросами (Overpass лимитирует)

# Города Беларуси
CITIES: dict[str, tuple[float, float, float, float]] = {
    "minsk":    (53.80, 27.25, 53.98, 27.72),
    "brest":    (52.05, 23.60, 52.20, 23.80),
    "grodno":   (53.62, 23.72, 53.75, 23.95),
    "gomel":    (52.38, 30.85, 52.55, 31.15),
    "mogilev":  (53.85, 30.25, 53.95, 30.45),
    "vitebsk":  (55.14, 30.12, 55.28, 30.35),
}

# Категории для запросов (по 2-3 за запрос)
QUERY_BLOCKS: list[list[str]] = [
    ["cafe_restaurant", "shop"],
    ["beauty", "car_service", "hotel"],
    ["clothing", "other"],
]

CATEGORY_TAGS: dict[str, list[tuple[str, str]]] = {
    "cafe_restaurant": [("amenity","cafe"), ("amenity","restaurant"),
                        ("amenity","fast_food"), ("amenity","bar")],
    "shop":            [("shop","")],
    "beauty":          [("amenity","hairdresser"), ("amenity","beauty")],
    "car_service":     [("shop","car_repair"), ("shop","car_parts"),
                        ("amenity","car_wash"), ("shop","tyres")],
    "hotel":           [("amenity","hotel"), ("amenity","hostel"),
                        ("amenity","guest_house")],
    "clothing":        [("shop","clothes"), ("shop","boutique")],
    "other":           [("amenity","fitness_centre"), ("leisure","fitness_centre"),
                        ("shop","electronics"), ("shop","computer"),
                        ("shop","mobile_phone"), ("tourism","")],
}


def query_overpass(query: str, max_retries: int = 3) -> dict[str, Any]:
    """Отправляет запрос к Overpass через curl с ретраями при пустом ответе."""
    import random
    for attempt in range(max_retries):
        if attempt > 0:
            delay = DELAY_BETWEEN * (2 ** attempt) + random.uniform(2, 5)
            print(f"      ⏳ retry {attempt+1}/{max_retries} after {delay:.0f}s...")
            time.sleep(delay)

        r = subprocess.run(
            ["curl", "-s", "-X", "POST", OVERPASS_URL, "--data", query, "--max-time", "60"],
            capture_output=True, encoding="utf-8", timeout=120,
        )
        stdout = r.stdout.strip()

        if stdout and "error" in stdout.lower():
            print(f"      ⚠️ Overpass error: {stdout[:200]}")
            continue

        if not stdout:
            if attempt < max_retries - 1:
                continue
            return {}

        try:
            data = json.loads(stdout)
            elements = data.get("elements", [])
            if not elements and attempt < max_retries - 1:
                print(f"      ⚠️ пустой ответ (rate limit?), ретрай...")
                continue
            return data
        except json.JSONDecodeError:
            if attempt < max_retries - 1:
                continue
            return {}
    return {}


def classify_tags(tags: dict[str, str]) -> str:
    a = tags.get("amenity", "")
    s = tags.get("shop", "")
    t = tags.get("tourism", "")
    if a in ("cafe","restaurant","fast_food","bar","pub"): return a
    if a in ("hairdresser","beauty"): return a
    if a in ("car_wash",): return "car_service"
    if a in ("hotel","hostel","guest_house"): return "hotel"
    if s: return f"shop"
    if t: return t
    return "other"


def extract_lead(element: dict, city: str) -> dict | None:
    tags = element.get("tags", {})
    if not tags: return None

    name = (tags.get("name") or "").strip()
    if not name: return None
    phone = (tags.get("phone") or "").strip()
    website = (tags.get("website") or "").strip()
    email = (tags.get("email") or "").strip()
    tg = (tags.get("contact:telegram") or tags.get("telegram") or "").strip()
    ig = (tags.get("contact:instagram") or tags.get("instagram") or "").strip()

    if website: return None       # есть онлайн-присутствие
    if not phone and not email and not tg: return None  # нет контакта

    cat = classify_tags(tags)
    osm_id = element.get("id", 0)
    osm_type = element.get("type", "node")
    addr = ", ".join(filter(None, [
        tags.get("addr:street",""),
        tags.get("addr:housenumber",""),
    ])) or ""

    # Чистим телефон
    phone = re.sub(r"[;|/\\]", ", ", phone).strip()

    tags_str = f"{city},{cat}"
    if ig: tags_str += ",instagram"
    if tg: tags_str += ",telegram"
    if email: tags_str += ",email"

    return {
        "url": f"https://osm.org/{osm_type}/{osm_id}",
        "email": email,
        "telegram": tg,
        "status": "review",
        "tags": tags_str,
        "source": f"overpass-{city}",
        "notes": json.dumps({"name":name,"phone":phone,"address":addr,
                             "category":cat,"city":city,"instagram":ig,"osm_id":osm_id},
                            ensure_ascii=False),
        "score": 50,
    }


def build_query(bbox: tuple, tags_list: list[tuple[str,str]], limit: int = 500) -> str:
    """Строит Overpass-запрос для набора тегов."""
    min_lat, min_lon, max_lat, max_lon = bbox
    blocks = []
    for k, v in tags_list:
        if v:
            blocks.append(f'  node["{k}"="{v}"]({min_lat},{min_lon},{max_lat},{max_lon});')
            blocks.append(f'  way["{k}"="{v}"]({min_lat},{min_lon},{max_lat},{max_lon});')
        else:
            blocks.append(f'  node["{k}"]({min_lat},{min_lon},{max_lat},{max_lon});')
            blocks.append(f'  way["{k}"]({min_lat},{min_lon},{max_lat},{max_lon});')

    blocks_str = "\n".join(blocks)
    q = f"[out:json][timeout:45];(\n{blocks_str}\n);out center {limit};"
    return re.sub(r"\n\s+", "\n", q.strip())


def dedupe(leads: list[dict]) -> list[dict]:
    seen: set[str] = set()
    deduped = []
    for lead in leads:
        n = json.loads(lead["notes"])
        key = n.get("phone", "") or lead["url"]
        if key not in seen:
            seen.add(key)
            deduped.append(lead)
    return deduped


def search_city(city: str, dry_run: bool = False) -> list[dict]:
    bbox = CITIES.get(city.lower())
    if not bbox:
        print(f"  ❌ Город не найден")
        return []

    all_leads = []

    for block_name in ["cafe_restaurant", "shop", "beauty", "car_service", "hotel", "clothing"]:
        tags = CATEGORY_TAGS.get(block_name, [])
        if not tags:
            continue

        query = build_query(bbox, tags)
        data = query_overpass(query)
        elements = data.get("elements", [])

        count_before = len(all_leads)
        for e in elements:
            lead = extract_lead(e, city)
            if lead:
                all_leads.append(lead)

        gained = len(all_leads) - count_before
        print(f"    {block_name:20s} → {len(elements):5d} объектов, {gained:3d} лидов")
        delay = DELAY_BETWEEN + random.uniform(1, 6)
        print(f"      ⏳ ждём {delay:.0f}s...")
        time.sleep(delay)

    # Дедуплицируем
    unique = dedupe(all_leads)
    print(f"  Итого: {len(all_leads)} сырых → {len(unique)} уникальных")
    return unique


def export_csv(leads: list[dict], path: str):
    if not leads: return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "url","email","telegram","status","tags","source","notes","score",
        ], extrasaction="ignore")
        w.writeheader()
        w.writerows(leads)
    print(f"💾 CSV: {path} ({len(leads)} лидов)")


def export_json(leads: list[dict], path: str):
    if not leads: return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(leads, f, ensure_ascii=False, indent=2)
    print(f"JSON: {path} ({len(leads)} leads)")


def import_to_db(leads: list[dict], db_path: str = "outreach.db"):
    """Import leads into Gordon's database."""
    if not leads: return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_full = os.path.join(script_dir, db_path)

    if not os.path.exists(db_full):
        print(f"  DB not found: {db_full}")
        return

    conn = sqlite3.connect(db_full)
    c = conn.cursor()

    imported = 0
    skipped = 0
    for lead in leads:
        try:
            c.execute(
                """INSERT OR IGNORE INTO sites
                   (url, email, telegram, status, tags, source, notes, score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (lead["url"], lead["email"], lead["telegram"],
                 lead["status"], lead["tags"], lead["source"],
                 lead["notes"], lead["score"]),
            )
            if c.rowcount:
                imported += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  DB error: {e}")
            skipped += 1

    conn.commit()
    conn.close()
    print(f"DB: {imported} imported, {skipped} skipped → {db_full}")


def print_summary(leads: list[dict]):
    if not leads: return
    by_cat: dict[str, int] = {}
    for lead in leads:
        n = json.loads(lead["notes"])
        c = n.get("category", "?")
        by_cat[c] = by_cat.get(c, 0) + 1
    total = len(leads)
    print(f"\n{'='*50}")
    print(f"ИТОГО: {total} лидов")
    print(f"{'='*50}")
    for c, cnt in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"  {c:20s} {cnt:4d} ({cnt*100//total:2d}%)")
    print(f"\nПримеры:")
    for lead in leads[:5]:
        n = json.loads(lead["notes"])
        print(f"  {n.get('name','?'):30s} ☎ {n.get('phone','?'):20s}")


def main():
    parser = argparse.ArgumentParser(description="Генератор лидов через OpenStreetMap")
    parser.add_argument("--city", default="minsk",
                        help="Город(а): " + ", ".join(CITIES))
    parser.add_argument("--output", "-o", default="overpass_leads.csv")
    parser.add_argument("--db", action="store_true",
                        help="Импортировать в outreach.db")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cities = [c.strip() for c in args.city.split(",")]
    all_leads = []

    for ci, city in enumerate(cities):
        print(f"\n🔍 {city.capitalize()}:")
        leads = search_city(city, dry_run=args.dry_run)
        all_leads.extend(leads)

    if not all_leads:
        print("\nNo leads found")
        return

    if args.dry_run:
        print_summary(all_leads)
        return

    if args.json:
        export_json(all_leads, args.output)
    else:
        export_csv(all_leads, args.output)

    if args.db and not args.dry_run:
        import_to_db(all_leads)

    print_summary(all_leads)


if __name__ == "__main__":
    main()
