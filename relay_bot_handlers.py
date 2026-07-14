#!/usr/bin/env python3
"""
relay_bot_handlers.py - обработчики ТГ-команд управления армией (RELAY, lane08).

ГЛАЗАМИ Назара: ВСЕ команды READ-ONLY. Никаких мутаций без явного "да" от Назара.
- /status      -> сводка: артефакты/день, done, воронка БД (SELECT only), ahead commits
- /lanes       -> какие lane активны/пусты (из dispatch/)
- /escalations -> список E1..E9 + статус (из escalations.md)
- /approve 59  -> ЗАГЛУШКА: "требуется аппрув Назара (red zone), не выполняю"

Безопасность (строго по ТЗ):
- НЕ пишет в outreach.db (только SELECT, режим mode=ro).
- НЕ пишет в git, НЕ пушит (может только ЧИТАТЬ ahead/behind через git rev-list).
- НЕ шлёт письма, НЕ трогает bridge.env/.fcc/секреты/fcc-server.
- НЕ запускает бота без ТГ-токена (интеграция с bridge = заглушка).
- /approve НЕ создаёт задачу и НЕ меняет БД - только отказ (red zone).

Запуск (только для проверки, без ТГ):
    python relay_bot_handlers.py --stdin      # читает команды из stdin, печатает ответ
    python relay_bot_handlers.py --self-test  # прогон handle() по всем командам

Реальный бот (poll bridge / send_telegram) НЕ реализован - это заглушка по ТЗ.
"""
import os
import re
import sys
import sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DISPATCH = os.path.normpath(os.path.join(HERE, "..", "dispatch"))
DB = os.path.join(HERE, "outreach.db")
ESC_FILE = os.path.join(DISPATCH, "escalations.md")
ARTIFACTS = os.path.join(DISPATCH, "artifacts")
DONE = os.path.join(DISPATCH, "done")
LANES = [f"lane{i:02d}" for i in range(1, 10)]


# ---------- read-only helpers ----------

def _db():
    """Только read-only соединение к БД (uri mode=ro). SELECT only."""
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def funnel():
    try:
        c = _db()
        rows = c.execute(
            "SELECT status, COUNT(*) FROM sites GROUP BY status"
        ).fetchall()
        c.close()
        return {r[0]: r[1] for r in rows}
    except Exception as e:
        return {"error": str(e)}


def lanes_state():
    out = {}
    for ln in LANES:
        p = os.path.join(DISPATCH, "pending", ln)
        c = os.path.join(DISPATCH, "claimed")
        d = DONE
        pend = [f for f in os.listdir(p)] if os.path.isdir(p) else []
        claim = [f for f in os.listdir(c) if f.startswith(ln)] if os.path.isdir(c) else []
        done = [f for f in os.listdir(d) if f.startswith(ln)] if os.path.isdir(d) else []
        out[ln] = (len(pend), len(claim), len(done))
    return out


def read_esc():
    if not os.path.exists(ESC_FILE):
        return "-"
    t = open(ESC_FILE, encoding="utf-8").read().strip()
    return t if t else "-"


def parse_escalations(text):
    """E1..En + status (RESOLVED если в заголовке, иначе ожидает)."""
    if text in ("-",):
        return []
    parts = re.split(r"\n(?=###\s+E\d+\.)", text)
    out = []
    for part in parts:
        m = re.match(r"###\s+(E\d+\.[^\n]*)", part)
        if not m:
            continue
        header = m.group(1).strip()
        eid = re.match(r"(E\d+)\.", header).group(1)
        resolved = bool(re.search(r"RESOLVED", header, re.IGNORECASE))
        title = re.sub(r"\s*[-—]\s*RESOLVED.*$", "", header,
                       flags=re.IGNORECASE).strip()
        out.append({"id": eid, "title": title, "status": "RESOLVED" if resolved else "ожидает"})
    return out


def git_ahead_behind():
    """Читает ahead/behind через git rev-list (ТОЛЬКО чтение, НЕ пишет)."""
    try:
        import subprocess
        def cnt(args):
            return int(subprocess.run(
                ["git"] + args, cwd=HERE, capture_output=True, text=True
            ).stdout.strip() or 0)
        ahead = cnt(["rev-list", "--count", "@{u}..HEAD"])
        behind = cnt(["rev-list", "--count", "HEAD..@{u}"])
        return ahead, behind
    except Exception:
        return 0, 0


def artifacts_today():
    """Новые артефакты за сегодня (по mtime в artifacts/ + artifacts/self/)."""
    today = datetime.now().strftime("%Y-%m-%d")
    n = 0
    for base in (ARTIFACTS, os.path.join(ARTIFACTS, "self")):
        if not os.path.isdir(base):
            continue
        for f in os.listdir(base):
            if not f.endswith(".md"):
                continue
            try:
                mt = datetime.fromtimestamp(
                    os.path.getmtime(os.path.join(base, f))
                ).strftime("%Y-%m-%d")
                if mt == today:
                    n += 1
            except Exception:
                pass
    return n


# ---------- command handlers (pure: return text) ----------

def cmd_status():
    f = funnel()
    ls = lanes_state()
    active_lanes = [ln for ln, v in ls.items() if v[0] or v[1] or v[2]]
    empty_lanes = [ln for ln, v in ls.items() if not (v[0] or v[1] or v[2])]
    ahead, behind = git_ahead_behind()
    art = artifacts_today()
    done_n = len([x for x in os.listdir(DONE) if x.endswith(".md")]) if os.path.isdir(DONE) else 0
    if "error" in f:
        db_line = f"БД: ошибка чтения ({f['error']})"
    else:
        db_line = (f"БД: sent {f.get('sent',0)} · replied {f.get('replied',0)} · "
                   f"hired {f.get('hired',0)}\n"
                   f"  pending {f.get('pending',0)} · review {f.get('review',0)} · "
                   f"bounced {f.get('bounced',0)} · rejected {f.get('rejected',0)}")
    secs = parse_escalations(read_esc())
    open_esc = [s["id"] for s in secs if s["status"] != "RESOLVED"]
    esc_line = (f"Эскалации: {', '.join(open_esc) if open_esc else '-'}"
                if open_esc else "Эскалации: нет (все RESOLVED)")
    return (f"📊 СТАТУС АРМИИ | {datetime.now().strftime('%Y-%m-%d')}\n\n"
            f"{db_line}\n\n"
            f"Артефакты сегодня: {art} · done: {done_n}\n"
            f"Лейны активны: {len(active_lanes)} "
            f"({', '.join(active_lanes) or '-'})\n"
            f"  пустые: {', '.join(empty_lanes) or '-'}\n"
            f"  claimed: {sum(v[1] for v in ls.values())} окон в работе\n"
            f"  pending: {sum(v[0] for v in ls.values())} задач в очереди\n"
            f"  done: {sum(v[2] for v in ls.values())} закрыто\n"
            f"Git: ahead {ahead} · behind {behind}\n"
            f"{esc_line}\n"
            f"Детали эскалаций: /escalations")


def cmd_lanes():
    ls = lanes_state()
    lines = [f"🗺 ЛЕЙНЫ | {datetime.now().strftime('%Y-%m-%d')}", ""]
    lines.append("lane     pending claimed done")
    for ln in LANES:
        pend, claim, done = ls[ln]
        lines.append(f"{ln:<8} {pend:<7} {claim:<7} {done}")
    active = [ln for ln, v in ls.items() if v[0] or v[1] or v[2]]
    empty = [ln for ln, v in ls.items() if not (v[0] or v[1] or v[2])]
    lines.append("")
    lines.append(f"Активные: {', '.join(active) or '-'}")
    lines.append(f"Пустые:   {', '.join(empty) or '-'}")
    return "\n".join(lines)


def cmd_escalations():
    secs = parse_escalations(read_esc())
    if not secs:
        return "⚠️ ЭСКАЛАЦИИ: нет"
    lines = ["⚠️ ЭСКАЛАЦИИ", ""]
    for s in secs:
        mark = "✅" if s["status"] == "RESOLVED" else "🔴"
        lines.append(f"{mark} {s['id']} [{s['status']}]: {s['title']}")
    return "\n".join(lines)


def cmd_approve(site_id):
    """ЗАГЛУШКА. НЕ исполняет аппрув (red zone), НЕ пишет в БД/задачи."""
    return (f"⛔ /approve {site_id}: требуется аппрув Назара (red zone), "
            f"не выполняю. Это действие меняет БД - только Назар даёт слово.")


def cmd_help():
    return ("🤖 КОМАНДЫ (read-only):\n"
            "/status - сводка БД + лейны + git\n"
            "/lanes - активные/пустые lane\n"
            "/escalations - красные зоны E1..E9\n"
            "/approve <id> - ЗАГЛУШКА (red zone, не выполняю)\n"
            "/help - это меню")


def handle(cmd_line):
    """Маршрутизация команды -> текст ответа. Чистая функция, без побочек."""
    parts = cmd_line.strip().split()
    if not parts:
        return cmd_help()
    head = parts[0].lower()
    if head in ("/status", "status"):
        return cmd_status()
    if head in ("/lanes", "lanes"):
        return cmd_lanes()
    if head in ("/escalations", "escalations", "/esc", "esc"):
        return cmd_escalations()
    if head in ("/approve", "approve"):
        if len(parts) < 2:
            return "⛔ /approve <id> - нужен номер лида."
        return cmd_approve(parts[1])
    if head in ("/help", "help", "/start", "start"):
        return cmd_help()
    return "❓ Неизвестная команда. /help"


# ---------- точка входа (НЕ бот, только проверка) ----------

def _self_test():
    for cmd in ("/status", "/lanes", "/escalations", "/approve 59",
                "/approve", "/help", "блабла"):
        print(f"--- {cmd} ---")
        print(handle(cmd))
        print()


def main():
    if "--self-test" in sys.argv:
        _self_test()
        return
    if "--stdin" in sys.argv:
        print("[relay_bot_handlers] stdin-режим (без ТГ). 'exit' для выхода.")
        while True:
            try:
                line = input("cmd> ")
            except (EOFError, KeyboardInterrupt):
                break
            if line.strip().lower() in ("exit", "quit"):
                break
            print(handle(line))
        return
    # Реальный бот (poll bridge + send_telegram) НЕ реализован - заглушка по ТЗ.
    print("[relay_bot_handlers] бот не запускается без ТГ-токена (заглушка).")
    print("Используй: --self-test | --stdin")


if __name__ == "__main__":
    main()
