#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline_health.py - READ-ONLY аналитика пайплайна Гордона/Nurture.

Печатает воронку и конверсии по outreach.db:
  - Воронка: sent -> replied -> hired (+ контекст pending/rejected/bounced).
  - Конверсия по углам (HA/LR/CE/LC) и по источникам (auto-scout/fresh/parser).
  - Топ-10 лидов по score (фолбэк по давности).
  - Markdown-таблица + короткий вердикт ("где течёт").

ЖЁСТКО READ-ONLY: БД открывается в режиме mode=ro (SQLite физически не даст
писать). Никаких INSERT/UPDATE/DELETE, никакого аппрува, никакого изменения
статусов. Безопасно запускать сколько угодно раз.

Режимы:
  python pipeline_health.py                 # stdout + пишет reports/pipeline_health_<date>.md
  python pipeline_health.py --no-file       # только stdout
  python pipeline_health.py --out path.md   # свой путь выхода

НЕ пушит в git, НЕ трогает fcc/.fcc/.env, НЕ убивает процессы.
"""
import os
import re
import sys
import sqlite3
from datetime import datetime

# Windows-консоль по умолчанию cp1251 — Unicode (стрелки и пр.) падает при print.
# Переключаем stdout/stderr на utf-8. По железному правилу Назара тире только "-",
# поэтому стрелки -> заменяем на "-" и в консоль, и в файл.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "outreach.db")
DISPATCH = os.path.join(os.path.dirname(HERE), "dispatch")
REPORTS_DIR = os.path.join(DISPATCH, "reports")

HOOK_RE = re.compile(r"HOOK::\s*(HA|LR|CE|LC)-\d+", re.I)
ANGLES = ["HA", "LR", "CE", "LC"]


def get_conn():
    # mode=ro -> SQLite откажет в любой записи (физический read-only)
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def cols_of(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def hook_angle_of(notes):
    if not notes:
        return "untracked"
    m = HOOK_RE.search(notes)
    return m.group(1).upper() if m else "untracked"


def source_bucket(row):
    """Группируем по источнику: auto-scout/fresh идут по тегам."""
    tags = (row.get("tags") or "").lower()
    src = (row.get("source") or "").lower()
    if "auto-scout" in tags or "auto_scout" in tags:
        return "auto-scout"
    if "fresh" in tags:
        return "fresh"
    if src in ("manual-search", "parser", "manual_search"):
        return "parser"
    if src == "scout":
        return "scout"
    if src == "manual":
        return "manual"
    return src or "manual"


# ---------- воронка ----------
def funnel(rows):
    sent = [r for r in rows if r["status"] in ("sent", "replied", "hired")]
    replied = [r for r in rows if r["status"] == "replied"]
    hired = [r for r in rows if r["status"] == "hired"]
    n_opened = sum(1 for r in sent if r.get("opened", 0) == 1)
    pending = [r for r in rows if r["status"] == "pending"]
    rejected = [r for r in rows if r["status"] == "rejected"]
    bounced = [r for r in rows if r["status"] == "bounced"]
    review = [r for r in rows if r["status"] == "review"]

    def pct(a, b):
        return (100.0 * a / b) if b else 0.0

    return {
        "total": len(rows),
        "sent": len(sent),
        "opened": n_opened,
        "replied": len(replied),
        "hired": len(hired),
        "pending": len(pending),
        "rejected": len(rejected),
        "bounced": len(bounced),
        "review": len(review),
        "conv_sent_reply": pct(len(replied), len(sent)),
        "conv_reply_hired": pct(len(hired), len(replied) or 1),
        "conv_sent_hired": pct(len(hired), len(sent)),
        "earned": sum((r.get("amount_earned") or 0) for r in hired),
        "bounce_rate": pct(len(bounced), len(sent)),
    }


def by_hook(rows):
    out = {a: [] for a in ANGLES + ["untracked"]}
    for r in rows:
        out[hook_angle_of(r["notes"])].append(r)
    return {a: funnel(v) for a, v in out.items()}


def by_source(rows):
    groups = {}
    for r in rows:
        groups.setdefault(source_bucket(r), []).append(r)
    return {s: funnel(v) for s, v in groups.items()}


def top_leads(conn, limit=10):
    cols = cols_of(conn, "sites")
    if "score" in cols:
        order = "ORDER BY COALESCE(score,0) DESC, created_at DESC"
    else:
        order = "ORDER BY created_at DESC"
    rows = conn.execute(
        f"SELECT id, url, status, source, COALESCE(score,0) AS score, "
        f"created_at, notes FROM sites {order} LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def verdict(f, by_hook_res, by_src_res):
    lines = []
    if f["sent"] == 0:
        lines.append("- Прогонов пока нет (sent=0) - нечего дожимать.")
        return lines
    if f["reply_rate_pending"] if "reply_rate_pending" in f else False:
        pass
    if f["conv_sent_reply"] < 5:
        lines.append(
            f"- ТЕЧЁТ на sent->replied: только {f['conv_sent_reply']:.1f}% "
            f"({f['replied']}/{f['sent']}) отвечают. Холодная рассылка почти не конвертит в диалог."
        )
    else:
        lines.append(f"- sent->replied ок: {f['conv_sent_reply']:.1f}%.")
    if f["hired"] == 0:
        lines.append(
            "- ТЕЧЁТ на replied->hired: hired=0. Даже из 2 ответов никто не закрыт - "
            "Питч не дожимает до сделки (или ответы не аппрувнуты)."
        )
    else:
        lines.append(f"- replied->hired: {f['conv_reply_hired']:.1f}%.")
    if f["bounce_rate"] >= 10:
        lines.append(
            f"- ВЫСОКИЙ bounce: {f['bounce_rate']:.1f}% ({f['bounced']}/{f['sent']}). "
            "Проверь валидацию email при парсинге - мёртвые адреса жгут репутацию."
        )
    # углы
    tracked = sum(by_hook_res[a]["sent"] for a in ANGLES)
    if tracked == 0:
        lines.append(
            "- Углы хука НЕ проставлены (0 маркеров HOOK::). Разбивка по углам "
            "слепая - добавь HOOK::<ID> в sender при отправке."
        )
    else:
        dead = [a for a in ANGLES if by_hook_res[a]["sent"] > 0 and by_hook_res[a]["replied"] == 0]
        if dead:
            lines.append(f"- Мёртвый угол(ы): {', '.join(dead)} (0 reply при >0 sent).")
    # источники
    empty = [s for s, fr in by_src_res.items() if fr["sent"] > 0 and fr["hired"] == 0]
    if empty:
        lines.append(f"- Источники без найма: {', '.join(empty)} (генерят, но не закрывают).")
    return lines


def render(f, by_hook_res, by_src_res, top, verdict_lines):
    L = []
    L.append("=== PIPELINE HEALTH (read-only) ===\n")
    L.append(f"**Всего сайтов:** {f['total']}  |  **Заработано:** BYN {f['earned']:.0f}\n")
    L.append("**Воронка:**")
    L.append("| Этап | Кол-во | Конверсия |")
    L.append("|---|---|---|")
    L.append(f"| sent (прогон) | {f['sent']} | - |")
    if f["opened"]:
        L.append(f"| opened (открытие) | {f['opened']} | {f['conv_sent_opened']:.1f}% от sent |")
    L.append(f"| replied (ответ) | {f['replied']} | {f['conv_sent_reply']:.1f}% от sent |")
    L.append(f"| hired (наём) | {f['hired']} | {f['conv_sent_hired']:.1f}% от sent |")
    L.append("")
    L.append(f"- sent-replied: **{f['conv_sent_reply']:.1f}%**")
    L.append(f"- replied-hired (dozhim): **{f['conv_reply_hired']:.1f}%**")
    L.append(f"- bounce rate: **{f['bounce_rate']:.1f}%** ({f['bounced']} из {f['sent']})")
    L.append(f"- контекст: pending={f['pending']}, rejected={f['rejected']}, review={f['review']}\n")

    L.append("**По углам хука (HA/LR/CE/LC):**")
    L.append("| Угол | sent | replied | hired | sent-reply |")
    L.append("|---|---|---|---|---|")
    for a in ANGLES + ["untracked"]:
        fr = by_hook_res[a]
        if fr["sent"] == 0 and a != "untracked":
            continue
        L.append(f"| {a} | {fr['sent']} | {fr['replied']} | {fr['hired']} | {fr['conv_sent_reply']:.1f}% |")
    L.append("")

    L.append("**По источнику:**")
    L.append("| Источник | total | sent | replied | hired | bounce |")
    L.append("|---|---|---|---|---|---|")
    for s in sorted(by_src_res.keys()):
        fr = by_src_res[s]
        L.append(f"| {s} | {fr['total']} | {fr['sent']} | {fr['replied']} | {fr['hired']} | {fr['bounced']} |")
    L.append("")

    L.append("**Топ-10 лидов (по score, фолбэк по давности):**")
    L.append("| # | ID | URL | Status | Score |")
    L.append("|---|---|---|---|---|")
    for r in top:
        url = (r.get("url") or "")[:42]
        L.append(f"| {r['id']} | {r['id']} | {url} | {r['status']} | {r['score']:.0f} |")
    L.append("")

    L.append("**Вердикт (где течёт):**")
    if not verdict_lines:
        L.append("- Пайплайн в норме (по базовым метрикам).")
    for v in verdict_lines:
        L.append(v)
    L.append("")
    L.append(f"_Сгенерировано: {datetime.now().strftime('%Y-%m-%d %H:%M')} · read-only_")
    return "\n".join(L)


def main():
    no_file = "--no-file" in sys.argv
    out_arg = None
    for i, a in enumerate(sys.argv):
        if a == "--out" and i + 1 < len(sys.argv):
            out_arg = sys.argv[i + 1]

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cols = cols_of(conn, "sites")
    has_notes = "notes" in cols
    # подтягиваем opened из nurture_state, если колонка есть
    ncols = cols_of(conn, "nurture_state") if "nurture_state" in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    } else set()

    base = "SELECT id, url, status, source, tags, COALESCE(score,0) AS score, created_at"
    base += ", notes" if has_notes else ", '' AS notes"
    base += " FROM sites"
    rows = [dict(r) for r in conn.execute(base).fetchall()]

    # opened из nurture_state (если есть колонка opened)
    if "opened" in ncols:
        ns = {r["site_id"]: r["opened"] for r in conn.execute("SELECT site_id, opened FROM nurture_state").fetchall()}
        for r in rows:
            r["opened"] = ns.get(r["id"], 0)
    else:
        for r in rows:
            r["opened"] = 0

    f = funnel(rows)
    # добавляем conv_sent_opened для вывода, если opened>0
    f["conv_sent_opened"] = (100.0 * f["opened"] / f["sent"]) if f["sent"] else 0.0
    by_hook_res = by_hook(rows)
    by_src_res = by_source(rows)
    top = top_leads(conn, 10)
    conn.close()

    verdict_lines = verdict(f, by_hook_res, by_src_res)
    md = render(f, by_hook_res, by_src_res, top, verdict_lines)

    print(md)

    if not no_file:
        try:
            os.makedirs(REPORTS_DIR, exist_ok=True)
        except Exception:
            pass
        out_path = out_arg or os.path.join(
            REPORTS_DIR, f"pipeline_health_{datetime.now().strftime('%Y-%m-%d')}.md"
        )
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(md + "\n")
            print(f"\n[written] {out_path}", file=sys.stderr)
        except Exception as e:
            print(f"[!] не смог записать отчёт: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
