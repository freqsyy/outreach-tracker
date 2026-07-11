#!/usr/bin/env python3
"""
barter_gen.py - АВТОМАТИЧЕСКИЙ генератор бартер-предложений из аудитов Гордона.

Берёт сайты из БД (где агент-аудитор нашёл баг - маркер AUDIT:: в notes),
и собирает ГОТОВЫЕ тексты для чатов владельцев:
  "нашёл у вас баг (детали) -> сделаю бесплатный полный аудит вашего сайта
   ВЗАМЕН на пост-отзыв у вас в канале / на стене".

ПОСТИНГ В ЧАТЫ НЕ ДЕЛАЕТ (нет токенов/риска бана) - только генерит тексты.
Один запуск -> готовый пакет, без ручного копипаста.

Запуск:
  python barter_gen.py            # пакет всех pending-сайтов с багом -> barter_posts.md
  python barter_gen.py --id N     # один сайт в консоль
  python barter_gen.py --all      # ВКЛЮЧАЯ уже отправленные (sent) - полный охват
"""

import os
import sys
import sqlite3

import gordon_common as gc

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "outreach.db")
OUT_PATH = os.path.join(HERE, "barter_posts.md")

SEV_RU = {"critical": "критический", "high": "серьёзный", "medium": "средний", "low": "низкий"}


def parse_audit(notes):
    """Достаёт из notes первый маркер AUDIT::<sev>::<where>::<desc>::<total>.
    Возвращает dict или None."""
    if not notes:
        return None
    for line in notes.splitlines():
        if line.strip().startswith("AUDIT::"):
            parts = line.split("::", 4)
            if len(parts) < 4:
                continue
            sev = parts[1]
            where = parts[2]
            desc = parts[3]
            total = 0
            if len(parts) >= 5:
                try:
                    total = int(parts[4])
                except ValueError:
                    total = 0
            # страховка: не показываем служебный мусор
            if where == "инфраструктура" or "CDP 9222" in desc or "Chromium не запущен" in desc:
                continue
            return {"sev": sev, "where": where, "desc": desc, "total": total}
    return None


def get_targets(include_sent=False, site_id=None):
    conn = gc.get_conn()
    if site_id:
        rows = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchall()
    else:
        q = ("SELECT * FROM sites WHERE notes LIKE 'AUDIT::%' "
             "AND status IN ('pending','sent')" if include_sent
             else "SELECT * FROM sites WHERE notes LIKE 'AUDIT::%' AND status='pending'")
        rows = conn.execute(q).fetchall()
    conn.close()
    return rows


def build_post(row):
    """Собирает готовый бартер-текст для одного сайта."""
    a = parse_audit(row["notes"])
    if not a:
        return None
    url = row["url"]
    domain = (url.split("//", 1)[1] if "//" in url else url).rstrip("/")
    sev_ru = SEV_RU.get(a["sev"], a["sev"])
    total = a["total"]

    # доказательство: 1 конкретный баг
    proof = (
        f"При проверке вашего сайта {domain} нашёл {sev_ru} баг: {a['desc']}"
    )
    # счётчик остальных (как в письме)
    extra = total - 1
    if extra >= 1:
        if extra == 1:
            proof += " Заметил ещё 1 проблему."
        elif 2 <= extra <= 4:
            proof += f" Заметил ещё {extra} проблемы."
        else:
            proof += f" Заметил ещё {extra} проблем."

    text = (
        f"👋 Привет! Я тестировщик сайтов (меня зовут Назар, 15 лет).\n\n"
        f"{proof}\n\n"
        f"Предлагаю бартер: сделаю БЕСПЛАТНЫЙ полный аудит вашего сайта "
        f"(формы, адаптив, консоль, логические баги) с подробным отчётом и скриншотами — "
        f"ВЗАМЕН на небольшой пост-отзыв обо мне у вас в канале или на стене. "
        f"Честно, без воды: если багов нет — так и напишу.\n\n"
        f"Если интересно — напишите в ответ, и я пришлю полный отчёт. "
        f"Мой Telegram: @oojdo"
    )
    return {"domain": domain, "url": url, "email": row["email"], "text": text}


def main():
    site_id = None
    include_sent = False
    if "--id" in sys.argv:
        i = sys.argv.index("--id")
        if i + 1 < len(sys.argv):
            site_id = int(sys.argv[i + 1])
    if "--all" in sys.argv:
        include_sent = True

    targets = get_targets(include_sent, site_id)
    if not targets:
        gc.log("Нет сайтов с AUDIT:: для бартера.", "BARTER")
        print("Нет сайтов с найденными багами.")
        return

    posts = []
    for row in targets:
        p = build_post(row)
        if p:
            posts.append(p)

    if site_id:
        # один в консоль
        if posts:
            print(posts[0]["text"])
        return

    # пакет в файл
    out = ["# Бартер-предложения (сгенерировано автоматически)\n"]
    out.append(f"_Всего готовых текстов: {len(posts)}_\n")
    for i, p in enumerate(posts, 1):
        out.append(f"\n---\n\n### {i}. {p['domain']}")
        out.append(f"- **Сайт:** {p['url']}")
        out.append(f"- **Контакт:** {p['email'] or '—'}")
        out.append(f"\n```\n{p['text']}\n```\n")
    content = "\n".join(out)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    gc.log(f"Бартер-пакет: {len(posts)} текстов -> {OUT_PATH}", "BARTER")
    print(f"Готово: {len(posts)} бартер-текстов -> {OUT_PATH}")


if __name__ == "__main__":
    main()
