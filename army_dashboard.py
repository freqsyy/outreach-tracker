#!/usr/bin/env python3
"""
army_dashboard.py - "Пульт" армии Назара (локальный Flask, read-only).
Реализация спецы artifacts/relay_army_dashboard_spec.md (задача relay-2026-07-14-16).

Что делает (безопасно):
  - GET  /           -> 9-колоночный дашборд: живые ленты lane (pending/claimed/done)
                       + последние артефакты + панель эскалаций (всё read-only)
  - GET  /api/lanes  -> JSON {laneNN: {pending, claimed, done, last_artifact}}
  - GET  /lane/<n>   -> задачи лейна + ссылки на артефакты
  - GET  /escalations-> Red-zone список из escalations.md
  - GET  /review     -> review-лиды из БД (status='review')
  - POST /review/<id>/approve -> Yellow: НЕ в БД. Пишет запрос в
        dispatch/review_approve_queue.txt (Сage аппрувает через one-writer)
  - GET  /db         -> read-only сводка статусов БД

Запуск: python army_dashboard.py [--port 5050]  -> http://127.0.0.1:<port>
ЧТО НЕ ДЕЛАЕТ (запреты ТЗ):
  - НЕ пишет в outreach.db (только SELECT, mode=ro);
  - НЕ публикует (host=127.0.0.1, debug=False);
  - НЕ push в git, НЕ ТГ/соцсети, НЕ kill/fcc/vision, НЕ правит токены.
"""
import os
import re
import sys
import time
import sqlite3
from datetime import datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, jsonify

HERE = os.path.dirname(os.path.abspath(__file__))
DISPATCH = os.path.normpath(os.path.join(HERE, "..", "dispatch"))
OUTREACH = HERE
DB = os.path.join(OUTREACH, "outreach.db")
ESC_FILE = os.path.join(DISPATCH, "escalations.md")
APPROVE_QUEUE = os.path.join(DISPATCH, "review_approve_queue.txt")
ARTIFACTS = os.path.join(DISPATCH, "artifacts")
DONE = os.path.join(DISPATCH, "done")

LANES = [f"lane{i:02d}" for i in range(1, 10)]
LANE_ROLE = {
    "lane01": "SCOUT", "lane02": "GORDON", "lane03": "HERALD", "lane04": "HOOK",
    "lane05": "NURTURE", "lane06": "PITCH", "lane07": "NOVA", "lane08": "RELAY",
    "lane09": "SAGE",
}
LANE_DESC = {
    "lane01": "парсинг, лиды, фильтры -> БД как review/pending",
    "lane02": "тексты писем, ротация (НЕ отправка)",
    "lane03": "соц-посты (НЕ публикация)",
    "lane04": "хуки, офферы, A/B",
    "lane05": "дожим-цепочки",
    "lane06": "DRAFT-only закрытие",
    "lane07": "спеки ленды/бренда (НЕ деплой)",
    "lane08": "ТГ-тексты (НЕ отправка)",
    "lane09": "мета/аудит/рефактор",
}

app = Flask(__name__)


# ---------- helpers (read-only) ----------

def _db():
    """Только read-only соединение к БД (uri mode=ro). SELECT only."""
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def _title(path):
    try:
        body = open(path, encoding="utf-8").read()
    except Exception:
        return None
    # пропускаем front-matter (--- ... ---) если есть
    m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    return m.group(1).strip() if m else os.path.basename(path)


def lane_state():
    out = {}
    for ln in LANES:
        p = os.path.join(DISPATCH, "pending", ln)
        c = os.path.join(DISPATCH, "claimed")
        d = DONE
        pend = [f for f in os.listdir(p)] if os.path.isdir(p) else []
        claim = [f for f in os.listdir(c) if f.startswith(ln)] if os.path.isdir(c) else []
        done = [f for f in os.listdir(d) if f.startswith(ln)] if os.path.isdir(d) else []
        titles = []
        for f in claim:
            t = _title(os.path.join(c, f))
            if t:
                titles.append(t)
        out[ln] = {
            "role": LANE_ROLE[ln],
            "desc": LANE_DESC[ln],
            "pending": len(pend),
            "claimed": len(claim),
            "done": len(done),
            "pending_files": sorted(pend),
            "claimed_files": sorted(claim),
            "done_files": sorted(done),
            "titles": titles,
            "last_artifact": last_artifacts(ln, 1)[0] if last_artifacts(ln, 1) else None,
        }
    return out


def _list_md(folder):
    if not os.path.isdir(folder):
        return []
    return [f for f in os.listdir(folder)
            if f.lower().endswith(".md") and not f.startswith(".")]


def last_artifacts(lane, n=5):
    """Последние N артефактов из artifacts/ (и artifacts/self/), связанных
    с lane (по имени файла laneNN- или упоминанию). Сортировка по mtime."""
    found = []
    for base in (ARTIFACTS, os.path.join(ARTIFACTS, "self")):
        for f in _list_md(base):
            if f.startswith(lane):
                found.append(os.path.join(base, f))
    found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return [os.path.basename(p) for p in found[:n]]


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


def read_esc():
    if not os.path.exists(ESC_FILE):
        return "-"
    t = open(ESC_FILE, encoding="utf-8").read().strip()
    return t if t else "-"


def parse_esc_sections(text):
    """Разбить escalations.md на секции ### E1. ... для компактного показа."""
    if text in ("-",):
        return []
    parts = re.split(r"\n(?=###\s+E\d+\.)", text)
    secs = []
    for part in parts:
        m = re.match(r"###\s+(E\d+\.[^\n]*)", part)
        if m:
            secs.append({"title": m.group(1).strip(), "body": part.strip()})
    return secs


def parse_escalations(text):
    """Парсер escalations.md: список E1..En с id/title/text/status.
    status = 'RESOLVED' если в заголовке есть RESOLVED, иначе 'ожидает'.
    ТОЛЬКО чтение. НИКАКИХ записей в файл/БД."""
    if not text or text.strip() in ("-",):
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
        status = "RESOLVED" if resolved else "ожидает"
        # убираем суффикс "- RESOLVED (...)" из заголовка -> чистый title
        title = re.sub(r"\s*[-—]\s*RESOLVED.*$", "", header,
                       flags=re.IGNORECASE).strip()
        out.append({"id": eid, "title": title, "text": title, "status": status})
    return out


# ---------- digest helpers (read-only: dispatch/ + track.py + git) ----------

def _git_ahead():
    """Только ЧТЕНИЕ ahead через git rev-list. НЕ пишет."""
    try:
        import subprocess
        out = subprocess.run(
            ["git", "rev-list", "--count", "@{u}..HEAD"],
            cwd=HERE, capture_output=True, text=True
        ).stdout.strip()
        return int(out or 0)
    except Exception:
        return 0


def _week_bounds():
    """Понедельник..воскресенье текущей недели (ДД.ММ - ДД.ММ)."""
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return (monday.strftime("%d.%m"), sunday.strftime("%d.%m"))


def _count_since(folder, days, name_filter=None):
    """Счётчик .md в папке (и subdir artifacts/self) с mtime <= days назад.
    ТОЛЬКО чтение mtime, НЕ трогает файлы."""
    if not os.path.isdir(folder):
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    for base in (folder, os.path.join(folder, "self")):
        if not os.path.isdir(base):
            continue
        for f in os.listdir(base):
            if not f.endswith(".md"):
                continue
            if name_filter and not name_filter(f):
                continue
            try:
                if os.path.getmtime(os.path.join(base, f)) >= cutoff:
                    n += 1
            except Exception:
                pass
    return n


def _active_empty_lanes():
    """Возвращает (активные, пустые) списки laneNN по dispatch/."""
    active, empty = [], []
    for ln in LANES:
        p = os.path.join(DISPATCH, "pending", ln)
        c = os.path.join(DISPATCH, "claimed")
        pend = [f for f in os.listdir(p)] if os.path.isdir(p) else []
        claim = [f for f in os.listdir(c) if f.startswith(ln)] if os.path.isdir(c) else []
        if pend or claim:
            active.append(ln)
        else:
            empty.append(ln)
    return active, empty


def _esc_open_ids():
    """Открытые (не RESOLVED) эскалации E1..En из escalations.md."""
    out = []
    for e in parse_escalations(read_esc()):
        if e["status"] != "RESOLVED":
            out.append(e["id"])
    return out


def build_daily_digest():
    """Дневной SAGE-дайджест (relay-17) с подставленными плейсхолдерами.
    Все данные read-only из dispatch/ + track.py (mode=ro) + git."""
    f = funnel()
    active, empty = _active_empty_lanes()
    arts = _count_since(ARTIFACTS, 1)
    ahead = _git_ahead()
    esc = _esc_open_ids()
    data = datetime.now().strftime("%d.%m.%Y")
    return (
        f"\U0001F916 SAGE-дайджест {data}\n"
        f"Лейны: 01..09 (активные: {', '.join(active) or '-'} / "
        f"пустые: {', '.join(empty) or '-'})\n"
        f"Сделано за день: {arts} артефактов\n"
        f"Метрика: sent {f.get('sent',0)} / replied {f.get('replied',0)} / "
        f"hired {f.get('hired',0)}\n"
        f"Эскалации: {', '.join(esc) if esc else '-'}\n"
        f"Действие от Назара: {'да/пуш' if ahead else 'пусто'}"
    )


def build_weekly_digest():
    """Еженедельный SAGE-дайджест (relay-23) с подставленными плейсхолдерами.
    Горизонт 7 дней. Динамика: count по updated_at >= 7 дней назад (без
    исторического снапшота - честно помечаем как сделано за неделю)."""
    dfrom, dto = _week_bounds()
    try:
        c = _db()
        # абсолютные счётчики статусов (на сейчас)
        now = {r[0]: r[1] for r in c.execute(
            "SELECT status, COUNT(*) FROM sites GROUP BY status").fetchall()}
        # сделано за 7 дней (по updated_at)
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        made = {st: c.execute(
            f"SELECT COUNT(*) FROM sites WHERE status=? AND updated_at>=?",
            (st, cutoff)).fetchone()[0]
            for st in ("sent", "replied", "hired")}
        c.close()
    except Exception:
        now = {"sent": 0, "replied": 0, "hired": 0}
        made = {"sent": 0, "replied": 0, "hired": 0}
    arts_w = _count_since(ARTIFACTS, 7)
    done_w = _count_since(DONE, 7)
    _, _ = _active_empty_lanes()
    active, _ = _active_empty_lanes()
    ahead = _git_ahead()
    esc = _esc_open_ids()
    return (
        f"\U0001F916 SAGE-неделя {dfrom}-{dto}\n"
        f"Метрика 7 дней: sent {now.get('sent',0)} (+{made['sent']}) / "
        f"replied {now.get('replied',0)} (+{made['replied']}) / "
        f"hired {now.get('hired',0)} (+{made['hired']})\n"
        f"Армия: {arts_w} артефактов за неделю, {done_w} done, "
        f"{len(active)} активных lane\n"
        f"Топ-движ: см. done/ за неделю\n"
        f"Эскалации: {', '.join(esc) if esc else '-'}\n"
        f"Решения от Назара за неделю: {'да/пуш' if ahead else 'нет'}\n"
        f"Следующая неделя: см. BACKLOG.md"
    )


def digests():
    """Словарь обоих дайджестов для JSON/рендера."""
    return {"daily": build_daily_digest(), "weekly": build_weekly_digest()}


def review_leads(limit=50):
    try:
        c = _db()
        rows = c.execute(
            "SELECT id, url, email, telegram, score, source, notes FROM sites "
            "WHERE status='review' ORDER BY score DESC LIMIT ?", (limit,)
        ).fetchall()
        c.close()
        cols = ["id", "url", "email", "telegram", "score", "source", "notes"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        return [{"error": str(e)}]


# ---------- routes ----------

@app.route("/")
def index():
    text = read_esc()
    dg = digests()
    return render_template("index.html",
                           lanes=lane_state(), funnel=funnel(),
                           esc_text=text, esc_secs=parse_esc_sections(text),
                           esc_list=parse_escalations(text),
                           daily_digest=dg["daily"], weekly_digest=dg["weekly"],
                           now=datetime.now())


@app.route("/api/escalations")
def api_escalations():
    """Красные зоны E1..En из escalations.md -> JSON [{id,text,status}].
    Только чтение файла. НИКАКИХ записей."""
    text = read_esc()
    return jsonify(parse_escalations(text))


@app.route("/api/digests")
def api_digests():
    """Дневной + недельный дайджесты с подставленными плейсхолдерами.
    Только чтение dispatch/ + track.py (mode=ro) + git rev-list. НИКАКИХ записей."""
    return jsonify(digests())


@app.route("/lane/<n>")
def lane(n):
    if not re.fullmatch(r"lane\d{2}", n) or n not in LANE_ROLE:
        return "bad lane", 404
    st = lane_state()[n]
    return render_template("lane.html", lane=n, state=st, now=datetime.now())


@app.route("/escalations")
def escalations():
    text = read_esc()
    secs = parse_esc_sections(text)
    return render_template("escalations.html",
                           text=text, secs=secs, now=datetime.now())


@app.route("/review")
def review():
    return render_template("review.html",
                           leads=review_leads(), now=datetime.now())


@app.route("/review/<int:lid>/approve", methods=["POST"])
def review_approve(lid):
    # Yellow: НЕ пишем в БД. Только очередь запросов Сage (one-writer).
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} | REQUEST_APPROVE | id={lid} | by=dashboard\n"
    try:
        os.makedirs(os.path.dirname(APPROVE_QUEUE), exist_ok=True)
        with open(APPROVE_QUEUE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        return jsonify({"ok": False, "log": f"не удалось записать очередь: {e}"})
    return jsonify({"ok": True, "log": f"запрос аппрува #{lid} -> очередь (Сage исполнит)"})


@app.route("/db")
def db_view():
    return render_template("db.html", funnel=funnel(), now=datetime.now())


@app.route("/api/lanes")
def api_lanes():
    """Живые ленты 9 lane: pending/claimed/done + последний артефакт.
    Только чтение dispatch/. НИКАКИХ записей."""
    out = {}
    for ln in LANES:
        st = lane_state()[ln]
        out[ln] = {
            "role": st["role"],
            "pending": st["pending"],
            "claimed": st["claimed"],
            "done": st["done"],
            "pending_files": st["pending_files"],
            "claimed_files": st["claimed_files"],
            "done_files": st["done_files"],
            "last_artifact": st["last_artifact"],
        }
    return jsonify(out)


if __name__ == "__main__":
    port = 5001
    if "--port" in sys.argv:
        i = sys.argv.index("--port") + 1
        if i < len(sys.argv) and sys.argv[i].isdigit():
            port = int(sys.argv[i])
    print(f"Пульт армии -> http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
