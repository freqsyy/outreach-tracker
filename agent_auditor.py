#!/usr/bin/env python3
"""
agent_auditor.py — АГЕНТ Гордона (часть 1: тёплый аутрич с приклеенным багом).

Для каждого сайта со статусом pending прогоняет audit_engine через Chromium,
находит баг и:
  1. пишет краткий баг в notes (маркер AUDIT::) — чтобы agent_sender подхватил
  2. сохраняет полный отчёт в audits/<domain>.md

Запускается отдельно от отправки (не ломает автоматику Гордона):
  python agent_auditor.py          # все pending
  python agent_auditor.py --limit 5 # только 5
  python agent_auditor.py --id 7    # конкретный сайт

ТРЕБОВАНИЕ: Chrome поднят на CDP 9222 (см. audit_engine.py).
Если порт закрыт — агент просто логирует warning и не падает.
"""

import os
import sys
import time

import gordon_common as gc
import audit_engine as ae

HERE = os.path.dirname(os.path.abspath(__file__))
TRACK = os.path.join(HERE, "track.py")
AUDITS_DIR = os.path.join(HERE, "audits")


def get_targets(limit=None, site_id=None):
    conn = gc.get_conn()
    if site_id:
        rows = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchall()
    else:
        q = "SELECT * FROM sites WHERE status='pending' AND email IS NOT NULL"
        if limit:
            q += f" ORDER BY id LIMIT {int(limit)}"
        rows = conn.execute(q).fetchall()
    conn.close()
    return rows


def write_note(site_id, note_line):
    """Дописывает строку бага в notes через track.py note (не затирает старые)."""
    if not note_line:
        return
    import subprocess
    subprocess.run(
        [sys.executable, TRACK, "note", str(site_id), note_line],
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )


def audit_one(row):
    url = row["url"]
    gc.log(f"Аудит #{row['id']} -> {url}", "AUDITOR")
    try:
        result = ae.audit_url(url)
    except Exception as e:
        gc.log(f"Ошибка аудита #{row['id']}: {e}", "AUDITOR")
        return None

    # краткий баг в notes
    if result.get("skipped"):
        # Chromium не поднялся даже после авто-попыток — НЕ пишем ничего в notes,
        # чтобы в письмо не ушёл служебный мусор. Сайт остаётся pending на след. прогон.
        gc.log(f"#{row['id']}: Chromium недоступен — сайт пропущен, notes не тронут.", "AUDITOR")
        return result
    note_line = ae.bug_to_note(result)
    write_note(row["id"], note_line)

    # полный отчёт
    if result["bugs"]:
        try:
            from audit_site import render_report, save_report
            report = render_report(result, row["email"] or "")
            path = save_report(result["domain"], report)
            gc.log(f"Отчёт: {path}", "AUDITOR")
        except Exception as e:
            gc.log(f"Не удалось сохранить отчёт #{row['id']}: {e}", "AUDITOR")

    gc.log(f"#{row['id']}: багов={len(result['bugs'])} critical={result['has_critical']} :: {result['summary'][:80]}", "AUDITOR")
    return result


def main():
    limit = None
    site_id = None
    if "--limit" in sys.argv:
        i = sys.argv.index("--limit")
        if i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
    if "--id" in sys.argv:
        i = sys.argv.index("--id")
        if i + 1 < len(sys.argv):
            site_id = int(sys.argv[i + 1])

    if not ae.cdp_alive():
        gc.log("CDP 9222 закрыт — Chrome не запущен. Аудит пропущен. Запустите Chrome.", "AUDITOR")
        gc.record_pitfall(
            "Auditor: CDP 9222 closed",
            "Chromium не запущен, аудит невозможен",
            "Chrome не поднят на --remote-debugging-port=9222",
            "запустить Chrome вручную (см. audit_engine.py), затем перезапустить agent_auditor.py"
        )
        return

    targets = get_targets(limit, site_id)
    if not targets:
        gc.log("Нет целей для аудита (pending с email).", "AUDITOR")
        return

    # гарантируем живой CDP до старта (поднимет Chromium, если порт мёртв)
    if not ae.ensure_cdp():
        gc.log("Не удалось поднять Chromium (CDP 9222). Аудит невозможен.", "AUDITOR")
        gc.record_pitfall(
            "Auditor: Chromium not startable",
            "Chromium не поднялся автоматически",
            "бинарь не найден или порт занят",
            "проверить ~/.agent-browser/browsers, запустить Chrome вручную, перезапустить agent_auditor.py"
        )
        return

    gc.log(f"Старт аудита: {len(targets)} сайт(ов)", "AUDITOR")
    for row in targets:
        audit_one(row)
        time.sleep(3)  # не долбить Chromium подряд
    gc.log("Аудит завершён.", "AUDITOR")


if __name__ == "__main__":
    main()
