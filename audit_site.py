#!/usr/bin/env python3
"""
audit_site.py — УСЛУГА «ИИ-аудит сайта» (часть 5 склейки Гордон + глаза).

Standalone-инструмент: прогоняет сайт через audit_engine и генерит
готовый отчёт по report_template.md. Это то, что Назар продаёт
(Кwork / прямой аутрич / свой продукт).

Запуск:
  python audit_site.py https://example.com
  python audit_site.py https://example.com --contact support@example.com

Результат:
  - сохраняет отчёт в audits/<domain>.md
  - печатает краткий баг + готовый текст для вставки в письмо/продажу
"""

import os
import sys
import time
import sqlite3
from datetime import datetime

import audit_engine as ae

HERE = os.path.dirname(os.path.abspath(__file__))
AUDITS_DIR = os.path.join(HERE, "audits")
TEMPLATE_PATH = os.path.join(HERE, "report_template.md")
DB_PATH = os.path.join(HERE, "outreach.db")


def _default_url_from_db():
    """Первый pending-сайт из БД для дефолтного прогона (кнопка дашборда)."""
    if not os.path.exists(DB_PATH):
        return None
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT url FROM sites WHERE status='pending' AND url IS NOT NULL "
            "ORDER BY id LIMIT 1"
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def load_template():
    try:
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return (
            "# Отчёт по тестированию сайта {SITE}\n\n"
            "**Дата:** {DATE}\n**Проверял:** Назар\n\n"
            "## Найденные баги\n{BUGS_TABLE}\n"
        )


def render_report(result, contact_email=""):
    import re
    tpl = load_template()
    date = datetime.now().strftime("%d.%m.%Y")

    if result["bugs"]:
        rows = []
        for i, b in enumerate(result["bugs"], 1):
            rows.append(
                f"| {i} | {b['where']} | {b['desc']} | — | {b['severity'].upper()} |"
            )
        bugs_table = "\n".join(rows)
        verdict = f"Найдено {len(result['bugs'])} багов. Топ-приоритет: " + "; ".join(
            f"{b['where']}" for b in result["bugs"][:3]
        )
    else:
        bugs_table = "| — | — | Критических и функциональных багов не найдено. Сайт работает корректно. | — | — |"
        verdict = "Сайт чистый, критических багов не найдено."

    rep = tpl
    rep = rep.replace("{SITE}", result["domain"])
    rep = rep.replace("{DATE}", date)
    rep = rep.replace("{EMAIL}", contact_email or "—")
    # заменяем первую (шаблонную) строку таблицы багов на реальные строки
    rep = re.sub(
        r"\| 1 \|[^\n]*\n",
        bugs_table + "\n",
        rep, count=1,
    )
    # заполняем плейсхолдер итога (п.4)
    rep = rep.replace(
        "{Кратко: сайт чистый / найдено N багов / топ-3 приоритетных доработок}",
        verdict,
    )
    # авто-блок от Гордона — дописываем в конец (не дублируем, если уже есть)
    if "Авто-аудит (Гордон" not in rep:
        rep += f"\n\n## 5. Авто-аудит (Гордон + agent-browser)\n\n{verdict}\n"
        rep += f"\n- Всего багов: {len(result['bugs'])}\n"
        for i, b in enumerate(result["bugs"], 1):
            rep += f"- {i}. **[{b['severity'].upper()}]** {b['where']}: {b['desc']}\n"
    return rep


def save_report(domain, report):
    os.makedirs(AUDITS_DIR, exist_ok=True)
    path = os.path.join(AUDITS_DIR, f"{domain}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    return path


def letter_pitch(result):
    """Готовый текст для вставки в письмо/продажу (часть 1 + 5 вместе)."""
    if not result["bugs"]:
        return ("Проверил ваш сайт — критических багов не нашёл. "
                "Готов сделать глубокий аудит и отчёт.")
    top = result["bugs"][0]
    return (f"Пока изучал сайт {result['domain']}, нашёл баг: {top['desc']} "
            f"Готов подготовить полный отчёт по формам, адаптиву и консоли.")


def main():
    if len(sys.argv) < 2:
        # дефолт: первый pending-сайт из БД (чтобы кнопка в дашборде работала
        # без аргументов). Без БД/сайтов — usage.
        url = _default_url_from_db()
        if not url:
            print("Usage: python audit_site.py https://example.com [--contact email]")
            sys.exit(1)
        print(f"[*] URL не задан — берём первый pending из БД: {url}")
    else:
        url = sys.argv[1]
    contact = ""
    if "--contact" in sys.argv:
        idx = sys.argv.index("--contact")
        if idx + 1 < len(sys.argv):
            contact = sys.argv[idx + 1]

    print(f"[*] Аудит {url} ...")
    t0 = time.time()
    result = ae.audit_url(url)
    dt = time.time() - t0

    report = render_report(result, contact)
    path = save_report(result["domain"], report)

    print(f"\n=== РЕЗУЛЬТАТ: {result['domain']} ===")
    print(f"Багов: {len(result['bugs'])} | критичных: {result['has_critical']} | за {dt:.0f}s")
    print(f"Отчёт: {path}\n")
    for i, b in enumerate(result["bugs"], 1):
        print(f"  {i}. [{b['severity'].upper()}] {b['where']}")
        print(f"     {b['desc'][:160]}")
    print("\n--- Текст для письма/продажи ---")
    print(letter_pitch(result))


if __name__ == "__main__":
    main()
