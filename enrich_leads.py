#!/usr/bin/env python3
"""
enrich_leads.py — Обогащение лидов контактами.

Берёт CSV из agent_overpass.py (бизнесы без сайтов, с телефоном),
пытается найти email/соцсети через веб-поиск,
и генерирует HTML-отчёт для ручной проверки.

Использование:
  python enrich_leads.py --input overpass_leads.csv --output enriched_leads.csv
  python enrich_leads.py --input overpass_leads.csv --html report.html
  python enrich_leads.py --input overpass_leads.csv --auto  # авто-поиск
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
from datetime import datetime
from typing import Any
from urllib.parse import quote

# ---- Конфиг ----
SEARCH_DELAY = (3, 7)         # сек между поисковыми запросами
CURL_TIMEOUT = 20             # секунд на curl запрос
MAX_AUTO = 50                 # макс лидов для авто-поиска (чтобы не заблокировали)

# User-Agent ротация
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]


def load_leads(csv_path: str) -> list[dict]:
    """Загружает лиды из CSV."""
    leads = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                notes = json.loads(row.get("notes", "{}"))
            except json.JSONDecodeError:
                notes = {}
            row["_notes"] = notes
            leads.append(row)
    return leads


def search_google(query: str) -> str | None:
    """Ищет в Google и возвращает текст результатов."""
    ua = random.choice(USER_AGENTS)
    url = f"https://www.google.com/search?q={quote(query)}&hl=ru"

    r = subprocess.run(
        ["curl", "-s", "-L", url,
         "-H", f"User-Agent: {ua}",
         "-H", "Accept-Language: ru-RU,ru;q=0.9",
         "--max-time", str(CURL_TIMEOUT)],
        capture_output=True, encoding="utf-8", timeout=30,
    )

    html = r.stdout
    # Если гугл вернул капчу — html пустой или короткий
    if not html or len(html) < 500:
        return None

    # Вырезаем текст из результатов
    # Ищем блоки с результатами
    texts = re.findall(r'>([^<]{30,300})<', html)
    # Фильтруем мусор
    real = [t for t in texts if not any(x in t.lower() for x in
            ["cookie", "consent", "captcha", "google", "script"])]

    return "\n".join(real[:20])


def extract_email_from_text(text: str) -> list[str]:
    """Вытаскивает email из текста."""
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    # Фильтруем мусорные
    clean = []
    for e in emails:
        if any(dom in e.lower() for dom in [
            "example.com", "domain.com", "google.com", "gmail.com",
            "yandex.ru", "mail.ru", "yahoo.com", "outlook.com"
        ]):
            # Это может быть реальный ящик на gmail/yandex — оставляем
            if e.lower().startswith(("info", "support", "contact", "admin", "hello",
                                     "shop", "order", "booking", "help", "market",
                                     "manager", "partner", "sales", "office", "mail")):
                pass  # служебные префиксы — ок
            elif any(c.isdigit() for c in e.split("@")[0]):
                pass  # если есть цифры в логине — тоже похоже на реальный
            else:
                continue  # michael@gmail.com — скорее тестовый
        clean.append(e)
    return list(set(clean))


def extract_instagram(text: str) -> list[str]:
    """Находит Instagram ссылки."""
    return list(set(re.findall(r'instagram\.com/([a-zA-Z0-9_.]+)', text)))


def extract_telegram(text: str) -> list[str]:
    """Находит Telegram ссылки."""
    tgs = re.findall(r't\.me/([a-zA-Z0-9_]+)', text)
    tg2 = re.findall(r'telegram\.me/([a-zA-Z0-9_]+)', text)
    return list(set(tgs + tg2))


def enrich_lead(lead: dict) -> dict:
    """Пытается найти контакты для одного лида."""
    notes = lead["_notes"]
    name = notes.get("name", "")
    city = notes.get("city", "")
    address = notes.get("address", "")
    phone = notes.get("phone", "")
    category = notes.get("category", "")

    result = {
        "found_emails": [],
        "found_instagram": [],
        "found_telegram": [],
        "search_text": "",
    }

    # Строим поисковые запросы
    queries = [
        f'"{name}" {city} email контакты',
        f'"{name}" {city} Instagram',
        f'"{name}" {city} Telegram',
    ]

    if address:
        queries.insert(0, f'"{name}" {city} {address} телефон email')

    for q in queries[:2]:  # макс 2 запроса на лид
        delay = random.uniform(*SEARCH_DELAY)
        time.sleep(delay)

        text = search_google(q)
        if not text:
            continue

        result["search_text"] += text + "\n---\n"

        emails = extract_email_from_text(text)
        result["found_emails"].extend(emails)

        igs = extract_instagram(text)
        result["found_instagram"].extend(igs)

        tgs = extract_telegram(text)
        result["found_telegram"].extend(tgs)

    result["found_emails"] = list(set(result["found_emails"]))
    result["found_instagram"] = list(set(result["found_instagram"]))
    result["found_telegram"] = list(set(result["found_telegram"]))

    return result


def generate_html_report(leads: list[dict], enriched: list[dict] | None = None,
                         output_path: str = "leads_report.html"):
    """Генерирует HTML-отчёт для ручного обогащения."""
    if enriched is None:
        enriched = [{} for _ in leads]

    # Группируем по городам
    groups: dict[str, list] = {}
    for i, lead in enumerate(leads):
        notes = lead["_notes"]
        city = notes.get("city", "?") or "?"
        if city not in groups:
            groups[city] = []
        groups[city].append((i, lead))

    html_parts = []
    html_parts.append("""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Лиды для аутрича — ручное обогащение</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #0d0d1a; color: #e0e0f0; padding: 20px; }
  h1 { background: linear-gradient(135deg, #a855f7, #22d3ee);
       -webkit-background-clip: text; -webkit-text-fill-color: transparent;
       font-size: 28px; margin-bottom: 8px; }
  .subtitle { color: #8888aa; margin-bottom: 24px; font-size: 14px; }
  .city-group { margin-bottom: 32px; }
  .city-title { color: #22d3ee; font-size: 20px; margin-bottom: 12px;
                padding-bottom: 6px; border-bottom: 1px solid #22d3ee33; }
  .city-count { color: #8888aa; font-size: 13px; margin-left: 8px; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 8px; }
  th { text-align: left; padding: 8px 10px; font-size: 12px; text-transform: uppercase;
       color: #a855f7; letter-spacing: 1px; border-bottom: 1px solid #333355; }
  td { padding: 8px 10px; font-size: 13px; border-bottom: 1px solid #1a1a33;
       vertical-align: top; }
  tr:hover { background: #1a1a33; }
  .name { color: #fff; font-weight: 600; }
  .phone { color: #22d3ee; font-family: monospace; font-size: 12px; }
  .category { display: inline-block; padding: 2px 8px; border-radius: 10px;
              font-size: 11px; background: #a855f733; color: #c084fc; }
  .instagram { color: #f472b6; }
  .actions a { color: #22d3ee; text-decoration: none; margin-right: 6px;
               font-size: 12px; }
  .actions a:hover { text-decoration: underline; }
  .email-found { background: #22d3ee22; padding: 2px 6px; border-radius: 4px;
                 font-size: 11px; color: #22d3ee; }
  .empty { color: #555577; font-style: italic; font-size: 12px; }
  .manual-input { width: 100%; background: #1a1a33; border: 1px solid #333355;
                  color: #e0e0f0; padding: 4px 8px; border-radius: 4px;
                  font-size: 12px; }
  .manual-input:focus { border-color: #a855f7; outline: none; }
  .stats { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
  .stat-card { background: #1a1a33; border: 1px solid #333355; border-radius: 8px;
               padding: 12px 20px; flex: 1; min-width: 120px; }
  .stat-value { font-size: 24px; font-weight: 700; color: #fff; }
  .stat-label { font-size: 11px; color: #8888aa; text-transform: uppercase; }
  .tg { color: #22d3ee; }
</style>
</head>
<body>
<h1>📋 Лиды для аутрича — ручное обогащение</h1>
""")

    total = len(leads)
    with_phone = sum(1 for l in leads if l.get("_notes", {}).get("phone"))
    with_email = sum(1 for l in leads if l.get("email"))
    with_ig = sum(1 for l in leads if "instagram" in l.get("tags", ""))

    html_parts.append(f"""
<div class="stats">
  <div class="stat-card"><div class="stat-value">{total}</div><div class="stat-label">Всего лидов</div></div>
  <div class="stat-card"><div class="stat-value">{with_phone}</div><div class="stat-label">С телефоном</div></div>
  <div class="stat-card"><div class="stat-value">{with_email}</div><div class="stat-label">С email</div></div>
  <div class="stat-card"><div class="stat-value">{with_ig}</div><div class="stat-label">С Instagram</div></div>
</div>
<div class="subtitle">📌 Сгенерировано {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
""")

    for city in sorted(groups.keys()):
        items = groups[city]
        html_parts.append(f'<div class="city-group">')
        html_parts.append(f'<div class="city-title">📍 {city.capitalize()} <span class="city-count">{len(items)} лидов</span></div>')
        html_parts.append("""
<table>
<thead><tr>
  <th style="width:22%">Название</th>
  <th style="width:15%">Телефон</th>
  <th style="width:10%">Категория</th>
  <th style="width:10%">Соцсети</th>
  <th style="width:18%">Поиск / Действия</th>
  <th style="width:25%">Email (найден / впиши)</th>
</tr></thead>
<tbody>
""")

        for idx, lead in items:
            n = lead["_notes"]
            name = n.get("name", "?")
            phone = n.get("phone", "")
            cat = n.get("category", "?")
            ig = n.get("instagram", "")
            addr = n.get("address", "")
            city_name = n.get("city", "")

            tg_links = ""
            if lead.get("telegram"):
                tg_links = f'📱 {lead["telegram"]}'

            instagram_link = ""
            if ig:
                instagram_link = f'📷 <a class="instagram" href="https://instagram.com/{ig}" target="_blank">{ig}</a>'

            # Поисковые ссылки
            search_url = f"https://www.google.com/search?q={quote(name + ' ' + city_name + ' контакты')}"
            insta_search = f"https://www.instagram.com/{name.replace(' ', '')}" if name else ""

            actions = f'<div class="actions">'
            actions += f'<a href="{search_url}" target="_blank">🔍 Google</a>'
            if addr:
                addr_q = quote(addr + " " + city_name)
                actions += f'<a href="https://yandex.by/maps/?text={addr_q}" target="_blank">🗺️ Яндекс</a>'
            actions += '</div>'

            # Email — из CSV или пустое поле для ввода
            existing_email = lead.get("email", "").strip()
            email_cell = ""
            if existing_email:
                email_cell = f'<span class="email-found">{existing_email}</span>'
            else:
                email_cell = f'<span class="empty">—</span>'

            html_parts.append(f"<tr>")
            html_parts.append(f'<td><span class="name">{name}</span></td>')
            html_parts.append(f'<td><span class="phone">{phone}</span></td>')
            html_parts.append(f'<td><span class="category">{cat}</span></td>')
            html_parts.append(f'<td>{instagram_link} {tg_links}</td>')
            html_parts.append(f'<td>{actions}</td>')
            html_parts.append(f'<td>{email_cell}</td>')
            html_parts.append("</tr>")

        html_parts.append("</tbody></table></div>")

    html_parts.append("""
<div style="margin-top: 40px; padding: 20px; background: #1a1a33; border-radius: 8px;">
<h3 style="color: #a855f7; margin-bottom: 12px;">📝 Инструкция по обогащению</h3>
<ol style="color: #cccce0; font-size: 13px; line-height: 1.8; padding-left: 20px;">
  <li>Открой каждый лид через 🔍 Google — ищи по названию + город</li>
  <li>Зайди на Instagram бизнеса (если есть ссылка) — часто там указан email в bio</li>
  <li>Проверь Яндекс.Карты — там есть контакты</li>
  <li>Если нашёл email — добавь в БД через <code>track.py note &lt;id&gt; "email=..."</code></li>
  <li>Цель: найти email для 20-30 лидов за сегодня</li>
  <li>Приоритет: кафе/рестораны → магазины → красота</li>
</ol>
</div>
</body>
</html>""")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html_parts))

    print(f"  HTML: {output_path}")
    return output_path


def auto_enrich(leads: list[dict], limit: int = MAX_AUTO) -> list[dict]:
    """Автоматическое обогащение через поиск."""
    enriched = []
    to_process = [l for l in leads if not l.get("email")]
    to_process = to_process[:limit]

    print(f"  Авто-поиск для {len(to_process)} лидов...")

    for i, lead in enumerate(to_process):
        name = lead["_notes"].get("name", "?")
        city = lead["_notes"].get("city", "?")

        print(f"  [{i+1}/{len(to_process)}] {name} ({city})...", end=" ")

        result = enrich_lead(lead)

        if result["found_emails"]:
            lead["email"] = ";".join(result["found_emails"])
            print(f"✅ email: {result['found_emails'][0]}")
        elif result["found_instagram"]:
            print(f"📷 instagram: {result['found_instagram'][0]}")
        else:
            print("❌ ничего")

        # Добавляем найденные соцсети в notes
        if result["found_instagram"]:
            notes = lead["_notes"]
            existing = notes.get("instagram", "")
            new_igs = [ig for ig in result["found_instagram"] if ig != existing]
            if new_igs:
                notes["instagram"] = (existing + "," if existing else "") + new_igs[0]
                lead["notes"] = json.dumps(notes, ensure_ascii=False)

        # Обогащаем tags
        if result["found_instagram"] and "instagram" not in lead.get("tags", ""):
            lead["tags"] = (lead.get("tags", "") + ",instagram").strip(",")

        enriched.append(result)

    return enriched


def export_csv(leads: list[dict], path: str):
    """Сохраняет обогащённый CSV."""
    if not leads:
        return

    # Обновляем notes из _notes
    for lead in leads:
        if "_notes" in lead:
            lead["notes"] = json.dumps(lead["_notes"], ensure_ascii=False)

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "url", "email", "telegram", "status", "tags", "source", "notes", "score",
        ], extrasaction="ignore")
        w.writeheader()
        w.writerows(leads)

    print(f"  CSV: {path} ({len(leads)} лидов)")


def print_stats(leads: list[dict]):
    """Статистика по обогащению."""
    total = len(leads)
    with_email = sum(1 for l in leads if l.get("email"))
    with_telegram = sum(1 for l in leads if l.get("telegram"))
    with_ig = sum(1 for l in leads if "instagram" in l.get("tags", ""))

    print(f"\n📊 Статистика обогащения:")
    print(f"  Всего лидов:    {total}")
    print(f"  С email:        {with_email} ({with_email*100//total if total else 0}%)")
    print(f"  С Telegram:     {with_telegram}")
    print(f"  С Instagram:    {with_ig}")

    # По городам
    cities: dict[str, dict] = {}
    for l in leads:
        city = l["_notes"].get("city", "?")
        if city not in cities:
            cities[city] = {"total": 0, "email": 0, "tg": 0}
        cities[city]["total"] += 1
        if l.get("email"):
            cities[city]["email"] += 1
        if l.get("telegram"):
            cities[city]["tg"] += 1

    print(f"\nПо городам:")
    for city, stats in sorted(cities.items()):
        print(f"  {city:12s} {stats['total']:4d} лидов, email: {stats['email']:2d}, tg: {stats['tg']:2d}")


def main():
    parser = argparse.ArgumentParser(description="Обогащение лидов контактами")
    parser.add_argument("--input", "-i", default="overpass_leads.csv",
                        help="Входной CSV от agent_overpass.py")
    parser.add_argument("--output", "-o", default="enriched_leads.csv",
                        help="Выходной обогащённый CSV")
    parser.add_argument("--html", default="leads_report.html",
                        help="HTML-отчёт для ручного обогащения")
    parser.add_argument("--auto", action="store_true",
                        help="Авто-поиск email через Google")
    parser.add_argument("--limit", type=int, default=MAX_AUTO,
                        help=f"Макс лидов для авто-поиска (default: {MAX_AUTO})")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Файл не найден: {args.input}")
        print("Сначала запусти: python agent_overpass.py --city minsk,brest,grodno")
        return

    print(f"📥 Загружаю лиды из {args.input}...")
    leads = load_leads(args.input)
    print(f"  Загружено: {len(leads)} лидов")

    if args.auto:
        enriched = auto_enrich(leads, limit=args.limit)
        export_csv(leads, args.output)

    # Всегда генерируем HTML-отчёт
    print(f"\n📄 Генерирую HTML-отчёт...")
    generate_html_report(leads, output_path=args.html)

    print_stats(leads)

    print(f"\n👉 Открой {args.html} в браузере и вручную добудь email для топ-20 лидов.")


if __name__ == "__main__":
    main()
