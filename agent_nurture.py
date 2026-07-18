#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_nurture.py - АГЕНТ 6 (Нуртюр / Follow-up Drip, state-machine).
Личность: Nurture. Держит лиды тёплыми, возвращает не-отвечающих.

СОСТОЯНИЯ ЛИДА (state-machine, поле stage в nurture_state):
  opened_no_reply  - письмо открыли, не ответили (вход +3/+7/+14)
  replied_question - ответил вопросом/возражением, не купил (ветки A/B/C/D)
  reactivate_30d   - молчал >=30 дней
  sequence_abc     - базовая 3-касание дожима (v1, day 0/3/7)
  done             - цепочка завершена

ПЛЕЙСХОЛДЕРЫ (read-only из БД): {site} {tg} {bug} {case} {answer}.
  {bug}  = реальный баг из audits/<domain>.md (либо честный fallback)
  {case} = релевантный кейс (из nurture_cases.json, если есть)
  {answer}= текст вопроса лида из notes (CLIENT::replied::), если есть
  {tg}   = хендл из sites.telegram (чистится от CSS-мусора)

БЕЗОПАСНОСТЬ (Red zone):
  --dry (по умолчанию) ИЛИ без --send: НИЧЕГО не уходит, БД НЕ мутируется.
  Отправка = ТОЛЬКО при --send И снятом STOP (NURTURE_ENABLED=true в .env).
  ensure_schema() пишет в БД ТОЛЬКО при реальной отправке (иначе read-only).

Запуск:
  python agent_nurture.py                 # DRY-RUN (превью очереди, read-only)
  python agent_nurture.py --dry           # явный dry (то же)
  python agent_nurture.py --state         # сводка состояний лидов (read-only)
  python agent_nurture.py --send          # реальная отправка (толко если STOP снят)
  python agent_nurture.py --send --force-stop-off   # ОПАСНО, игнор STOP (нужен флаг)
"""
import os
import re
import sys
import json
import smtplib
import argparse
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import sqlite3

import gordon_common as gc

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "outreach.db")


def get_ro_conn():
    """Строго read-only коннект (SQLite физически отклоняет запись)."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_cols(conn, table):
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()
TRACK = os.path.join(HERE, "track.py")
TOUCH_GLOB = {
    # seq_set -> список (touch_idx, день, угол)
    "v1": [(1, 0, "HA/FREE AUDIT"), (2, 3, "LR/LOST REVENUE"), (3, 7, "LC/LAUNCH READINESS")],
    "v2": [(1, 3, "LC/QUESTION"), (2, 7, "LR/PROOF"), (3, 14, "SOFT-FINAL/CHECKLIST")],
    "v3": [(1, 0, "TIMING/PORTFOLIO/UNDECIDED/HAS_QA")],
    "react": [(1, 0, "REACTIVATE/FRESH-LOOK"), (2, 7, "LR"), (3, 18, "BREAKUP")],
    "bounce": [(1, 0, "CHANNEL-SWITCH"), (2, 5, "VALUE"), (3, 12, "BREAKUP")],
    "audit": [(1, 0, "GOT-IT?"), (2, 5, "RETAINER"), (3, 12, "DERISK")],
    "refer": [(1, 0, "ASK"), (2, 6, "NUDGE")],
}
def _seq_files(seq):
    # v1 использует исторические nurture_touchN.txt; остальные -> nurture_<seq>N.txt
    if seq == "v1":
        return [os.path.join(HERE, f"nurture_touch{i}.txt") for i in (1, 2, 3)]
    return [os.path.join(HERE, f"nurture_{seq}{i}.txt") for i in (1, 2, 3)]


TOUCH_FILES = {seq: _seq_files(seq) for seq in TOUCH_GLOB}
CASES_PATH = os.path.join(HERE, "nurture_cases.json")
AUDITS_DIR = os.path.join(HERE, "audits")

MIN_GAP_DAYS = 3  # правило: <=1 письмо / 3 дня / лид
REACTIVATE_DAYS = 30

OPTOUT_RE = re.compile(r"(stop|unsub|отпис|unsubscribe|ne nado|ne nadо|quiet)", re.I)
TG_RE = re.compile(r"@([A-Za-z0-9_]{4,32})")
CSS_TOKENS = {"context", "media", "type", "keyframes", "graph", "latest",
              "theme", "scub", "var", "root", "body", "head", "charset"}
DEFAULT_TG = "@oojdo"
REPLY_RE = re.compile(r"CLIENT::replied::\s*[\d\-: ]+\s*::\s*(.*)", re.S)


def gv(row, key, default=None):
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def clean_tg(raw):
    if not raw:
        return DEFAULT_TG
    handles = [m.group(1) for m in TG_RE.finditer(raw)
               if m.group(1).lower() not in CSS_TOKENS]
    return "@" + handles[0] if len(handles) == 1 else DEFAULT_TG


def domain_of(url):
    try:
        d = urllib.parse.urlparse(url or "").netloc or (url or "")
        return d.lower().replace("www.", "")
    except Exception:
        return (url or "").lower()


# ----------------------------------------------------------------------------
# Схема nurture_state (idempotent). Пишем в БД ТОЛЬКО при реальной отправке.
# ----------------------------------------------------------------------------
def ensure_schema(allow_write=False):
    # Таблица создаётся idempotent-но ВСЕГДА (пустая таблица = read-only,
    # данные лидов не мутируются). Реальная запись строк идёт только под
    # allow_write в persist_state. Без этого --dry падает на load_state_rows.
    conn = gc.get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nurture_state (
                site_id      INTEGER PRIMARY KEY,
                touch_count  INTEGER DEFAULT 0,
                last_touch_at TEXT,
                next_touch_at TEXT,
                stage         TEXT DEFAULT 't1',
                opened        INTEGER DEFAULT 0,
                seq_set       TEXT DEFAULT 'v1',
                optout        INTEGER DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now')),
                updated_at    TEXT DEFAULT (datetime('now'))
            )
            """
        )
        # миграция: старая таблица могла быть создана без seq_set (иначе
        # persist_state падает на INSERT ... seq_set). Добавляем idempotent-но.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nurture_state)").fetchall()}
        if "seq_set" not in cols:
            conn.execute("ALTER TABLE nurture_state ADD COLUMN seq_set TEXT DEFAULT 'v1'")
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# Загрузка копирайта (письма из nurture_<seq>N.txt)
# ----------------------------------------------------------------------------
def _parse_blocks(path):
    """Читает файл и возвращает список (subject, body) по каждому 'Subject:'."""
    out = []
    subject = None
    body_lines = None
    try:
        for ln in open(path, "r", encoding="utf-8").read().splitlines():
            if ln.lower().lstrip().startswith("subject:"):
                if subject is not None:
                    out.append((subject, "\n".join(body_lines).strip()))
                subject = ln.split(":", 1)[1].strip()
                body_lines = []
            elif subject is not None:
                body_lines.append(ln)
        if subject is not None:
            out.append((subject, "\n".join(body_lines).strip()))
    except Exception as e:
        gc.log(f"Не удалось прочитать {os.path.basename(path)}: {e}", "NURTURE")
    return out


def load_touch(seq, idx):
    """idx 1..3 -> (subject, body). Для v3 (много веток) вернёт все блоки, выбор в render_v3."""
    paths = TOUCH_FILES.get(seq, TOUCH_FILES["v1"])
    path = paths[idx - 1] if idx - 1 < len(paths) else paths[-1]
    blocks = _parse_blocks(path)
    if not blocks:
        return f"re: {seq} follow-up", ""
    return blocks[0] if len(blocks) == 1 else (blocks[idx - 1] if idx - 1 < len(blocks) else blocks[-1])


def load_v3_branch(objection):
    """Возвращает (subject, body) одной ветки возражения из nurture_v31.txt."""
    branch_idx = {"timing": 0, "portfolio": 1, "undecided": 2, "has_qa": 3}.get(objection, 2)
    blocks = _parse_blocks(TOUCH_FILES["v3"][0])
    if not blocks:
        return f"re: reply {objection}", ""
    return blocks[branch_idx] if branch_idx < len(blocks) else blocks[-1]


def load_cases():
    if not os.path.exists(CASES_PATH):
        return {}
    try:
        return json.load(open(CASES_PATH, "r", encoding="utf-8"))
    except Exception:
        return {}


# ----------------------------------------------------------------------------
# Реальный баг / кейс / ответ лида (read-only из audits/ и notes)
# ----------------------------------------------------------------------------
def _is_meta_bug(text):
    """Технические заметки аудитора (не реальный баг сайта) - не годятся для письма."""
    t = (text or "").lower()
    markers = ("cdp", "chromium", "9222", "инфраструктур", "agent-browser",
               "agent_auditor", "не поднят", "не запущен")
    return any(mk in t for mk in markers)


def real_bug_for(site_row):
    dom = domain_of(gv(site_row, "url"))
    if dom:
        cand = os.path.join(AUDITS_DIR, f"{dom}.md")
        if os.path.exists(cand):
            try:
                txt = open(cand, "r", encoding="utf-8").read()
                # формат 1: "### #1 [CRITICAL] Заголовок бага"
                for m in re.finditer(r"^###\s*#\d+\s*\[[^\]]+\]\s*(.+)$", txt, re.M):
                    head = m.group(1).strip().rstrip(".")
                    if head and not _is_meta_bug(head):
                        return head, f"audit:{dom}"
                # формат 2: таблица "| N | where | desc |"
                for m in re.finditer(r"^\|\s*\d+\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", txt, re.M):
                    where, desc = m.group(1).strip(), m.group(2).strip()
                    if not _is_meta_bug(where) and not _is_meta_bug(desc):
                        return f"{where}: {desc}".strip(". "), f"audit:{dom}"
            except Exception:
                pass
    return ("пара моментов по вёрстке и логике форм, которые обычно "
            "тихо отпугивают первых посетителей"), "fallback:honest"


def extract_reply(notes):
    if not notes:
        return ""
    m = REPLY_RE.search(notes)
    return m.group(1).strip() if m else ""


def classify_objection(text):
    """Грубая классификация возражения для выбора ветки v3 (см. agent_pitcher.OBJECTIONS)."""
    t = (text or "").lower()
    if any(w in t for w in ["позже", "потом", "не сейчас", "подожд", "later", "not now"]):
        return "timing"
    if any(w in t for w in ["пример", "портфолио", "кейс", "покаж", "prove", "portfolio", "samples"]):
        return "portfolio"
    if any(w in t for w in ["подума", "рассмотр", "обсуд", "внутри", "соглас", "think", "consider"]):
        return "undecided"
    if any(w in t for w in ["уже есть qa", "есть тестировщ", "сам тестиру", "in-house", "we have qa"]):
        return "has_qa"
    return "undecided"


# ----------------------------------------------------------------------------
# Отбор лидов (read-only SELECT)
# ----------------------------------------------------------------------------
def candidate_leads(stop_active, include_pending):
    conn = get_ro_conn()
    rows = conn.execute(
        # replied ИСКЛЮЧЁН: отправили -> ответили -> стоп дозасылке (ТЗ 2026-07-18).
        # Не ответили (sent/bounced) -> цепочка follow-up идёт до конца.
        "SELECT * FROM sites WHERE status IN ('sent','bounced','review','pending')"
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        st = r["status"]
        # sent/bounced -> follow-up цепочки (Назар разрешил follow-up и на
        # 162 холодных sent — аппрув 2026-07-15). replied ИСКЛЮЧЁН: ответил ->
        # стоп дозасылке (ТЗ 2026-07-18). review НЕ трогаем (не аппрувнуты
        # к рассылке по CLAUDE.md), pending -> только если явно include_pending.
        if st in ("sent", "bounced"):
            if (r["email"] or "").strip():
                out.append(r)
        elif st == "pending" and include_pending and not stop_active:
            if (r["email"] or "").strip():
                out.append(r)
    return out


def load_state_rows(site_ids):
    if not site_ids:
        return {}
    conn = get_ro_conn()
    ph = ",".join("?" * len(site_ids))
    rows = conn.execute(
        f"SELECT * FROM nurture_state WHERE site_id IN ({ph})", site_ids
    ).fetchall()
    conn.close()
    return {r["site_id"]: r for r in rows}


def is_optout(site_row, state_row):
    if state_row and gv(state_row, "optout"):
        return True
    return bool(OPTOUT_RE.search(gv(site_row, "notes") or ""))


# ----------------------------------------------------------------------------
# Решение состояния и касания
# ----------------------------------------------------------------------------
def pick_seq_set(site_row, state_row, now):
    st = gv(site_row, "status")
    if st == "replied":
        return "v3"  # ответил, не купил -> ветки возражений
    if st == "bounced":
        return "bounce"
    if state_row and gv(state_row, "opened"):
        return "v2"  # открыл, не ответил
    # reactivate если последний touch был >30 дней назад
    if state_row:
        lt = gv(state_row, "last_touch_at")
        if lt:
            try:
                d = (now - datetime.strptime(lt, "%Y-%m-%d %H:%M:%S")).days
                if d >= REACTIVATE_DAYS:
                    return "react"
            except Exception:
                pass
    return "v1"  # базовый дожим


def decide_touch(site_row, state_row, now):
    if state_row is None:
        return 1, "fresh -> touch 1"
    if gv(state_row, "optout"):
        return None, "optout/STOP"
    tc = gv(state_row, "touch_count") or 0
    if gv(state_row, "stage") == "done" or tc >= 3:
        return None, "sequence complete"
    nt = gv(state_row, "next_touch_at")
    if nt:
        try:
            if datetime.strptime(nt, "%Y-%m-%d %H:%M:%S") > now:
                return None, f"too early ({nt})"
        except Exception:
            pass
    return tc + 1, f"touch {tc + 1} due"


def next_touch_at_for(seq, touch_idx, now):
    sched = TOUCH_GLOB.get(seq, TOUCH_GLOB["v1"])
    cur_day = sched[touch_idx - 1][1] if 1 <= touch_idx <= len(sched) else 0
    nxt = None
    for _, d, _ in sched:
        if d > cur_day:
            nxt = d
            break
    if nxt is None:
        return None
    return (now + timedelta(days=nxt - cur_day)).strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------------
# Рендер
# ----------------------------------------------------------------------------
def render(site_row, seq, idx, cases):
    dom = domain_of(gv(site_row, "url"))
    # v3 (replied_question): выбираем ветку возражения по тексту ответа лида
    if seq == "v3":
        answer = extract_reply(gv(site_row, "notes"))
        obj = classify_objection(answer)
        subj_tpl, body_tpl = load_v3_branch(obj)
        bug, bug_src = real_bug_for(site_row)
        case = cases.get(dom) or (next(iter(cases.values()), "") if cases else "")
        repl = {
            "{site}": dom,
            "{tg}": clean_tg(gv(site_row, "telegram")),
            "{bug}": bug,
            "{bug1}": bug,
            "{case}": case,
            "{answer}": answer,
            "{hook}": "Коротко про то, что нашёл на сайте, пока смотрел:",
            "{checklist_link}": "https://gist.github.com/nazar/qa-checklist",
        }
        subject = subj_tpl
        for k, v in sorted(repl.items(), key=lambda kv: -len(kv[0])):
            subject = subject.replace(k, v)
            body_tpl = body_tpl.replace(k, v)
        return subject.strip(), body_tpl.strip(), bug_src
    subj_tpl, body_tpl = load_touch(seq, idx)
    bug, bug_src = real_bug_for(site_row)
    answer = extract_reply(gv(site_row, "notes"))
    # кейс: по домену или первый из списка
    case = cases.get(dom) or (next(iter(cases.values()), "") if cases else "")
    repl = {
        "{site}": dom,
        "{tg}": clean_tg(gv(site_row, "telegram")),
        "{bug}": bug,
        "{bug1}": bug,
        "{case}": case,
        "{answer}": answer,
        "{hook}": "Коротко про то, что нашёл на сайте, пока смотрел:",
        "{checklist_link}": "https://gist.github.com/nazar/qa-checklist",
    }
    subject, body = subj_tpl, body_tpl
    # заменяем по убыванию длины ключа, иначе '{bug}' ломает '{bug1}'
    for k, v in sorted(repl.items(), key=lambda kv: -len(kv[0])):
        subject = subject.replace(k, v)
        body = body.replace(k, v)
    return subject.strip(), body.strip(), bug_src


# ----------------------------------------------------------------------------
# Отправка (только при --send и снятом STOP)
# ----------------------------------------------------------------------------
def send_one(account, to_email, subject, body, site_url=""):
    settings = load_settings()
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = f"{settings['FROM_NAME']} <{account[0]}>"
    msg["To"] = to_email
    msg["List-Unsubscribe"] = f"<mailto:{account[0]}?subject=unsubscribe%20{domain_of(site_url)}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    server = smtplib.SMTP(settings["SMTP_HOST"], int(settings["SMTP_PORT"]), timeout=30)
    server.starttls()
    server.login(account[0], account[1])
    server.sendmail(account[0], [to_email], msg.as_string())
    server.quit()


def load_settings():
    env = gc.load_env()
    return {
        "SMTP_HOST": env.get("SMTP_HOST", "smtp.gmail.com"),
        "SMTP_PORT": env.get("SMTP_PORT", "587"),
        "FROM_NAME": env.get("FROM_NAME", "Nazar"),
    }


def get_accounts(env):
    accs, i = [], 1
    while True:
        e, p = env.get(f"ACCOUNT_{i}_EMAIL"), env.get(f"ACCOUNT_{i}_PASS")
        if not e or not p:
            break
        accs.append((e, p))
        i += 1
    return accs


def persist_state(site_id, seq, touch_idx, now, conn):
    nxt = next_touch_at_for(seq, touch_idx, now)
    stage = "done" if touch_idx >= len(TOUCH_GLOB.get(seq, TOUCH_GLOB["v1"])) else f"t{touch_idx + 1}"
    conn.execute(
        """INSERT INTO nurture_state (site_id, touch_count, last_touch_at, next_touch_at, stage, seq_set, updated_at)
           VALUES (?, ?, datetime('now'), ?, ?, ?, datetime('now'))
           ON CONFLICT(site_id) DO UPDATE SET
             touch_count=?, last_touch_at=datetime('now'), next_touch_at=?, stage=?, seq_set=?, updated_at=datetime('now')""",
        (site_id, touch_idx, nxt, stage, seq, touch_idx, nxt, stage, seq),
    )
    conn.commit()


def alert_sage(msg):
    gc.log(f"[SAGE] {msg}", "NURTURE")


# ----------------------------------------------------------------------------
# Сводка состояний (read-only)
# ----------------------------------------------------------------------------
def print_state():
    conn = get_ro_conn()
    cols = _table_cols(conn, "nurture_state")
    sel = ["s.id", "s.url", "s.status"]
    if "stage" in cols:
        sel.append("COALESCE(n.stage,'none') stage")
    else:
        sel.append("'none' stage")
    if "seq_set" in cols:
        sel.append("COALESCE(n.seq_set,'none') seq")
    else:
        sel.append("'none' seq")
    if "touch_count" in cols:
        sel.append("COALESCE(n.touch_count,0) tc")
    else:
        sel.append("0 tc")
    if "opened" in cols:
        sel.append("COALESCE(n.opened,0) opened")
    else:
        sel.append("0 opened")
    if "next_touch_at" in cols:
        sel.append("COALESCE(n.next_touch_at,'') nxt")
    else:
        sel.append("'' nxt")
    join = "LEFT JOIN nurture_state n ON n.site_id=s.id" if cols else ""
    rows = conn.execute(f"SELECT {', '.join(sel)} FROM sites s {join}").fetchall()
    conn.close()
    print("\n=== NURTURE STATE ===")
    print(f"{'ID':<5} {'status':<9} {'stage':<16} {'seq':<6} {'tc':<3} {'op':<3} next_touch_at")
    print("-" * 70)
    for r in rows:
        print(f"{r['id']:<5} {r['status']:<9} {r['stage']:<16} {r['seq']:<6} "
              f"{r['tc']:<3} {r['opened']:<3} {r['nxt']}")
    print()


# ----------------------------------------------------------------------------
# Главный прогон
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Nurture follow-up drip agent (state-machine, dry-run)")
    ap.add_argument("--send", action="store_true", help="реально отправить (толко если STOP снят)")
    ap.add_argument("--dry", action="store_true", help="явный dry-run (по умолчанию)")
    ap.add_argument("--force-stop-off", action="store_true", help="ОПАСНО: игнор STOP-флага")
    ap.add_argument("--state", action="store_true", help="сводка состояний лидов (read-only)")
    args = ap.parse_args()

    if args.state:
        print_state()
        return

    env = gc.load_env()
    cases = load_cases()

    nurture_enabled = env.get("NURTURE_ENABLED", "false").lower() == "true"
    dry_run = not args.send
    stop_active = (not nurture_enabled) and (not args.force_stop_off)
    if stop_active:
        gc.log("STOP aktiv (net NURTURE_ENABLED=true). Otpravka zablokirovana. Tolko dry/preview.", "NURTURE")
    if dry_run:
        gc.log("DRY-RUN: pisma NE otpravlyayutsya, BD NE menyaetsya.", "NURTURE")

    # схема пишется ТОЛЬКО при реальной отправке (иначе read-only)
    ensure_schema(allow_write=(not dry_run and not stop_active))

    include_pending = (not stop_active) and env.get("NURTURE_INCLUDE_PENDING", "false").lower() == "true"
    leads = candidate_leads(stop_active, include_pending)
    state_map = load_state_rows([r["id"] for r in leads])
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    queue, waiting = [], []
    for r in leads:
        sid = gv(r, "id")
        st_row = state_map.get(sid)
        if is_optout(r, st_row):
            waiting.append((r, "optout/STOP"))
            continue
        seq = pick_seq_set(r, st_row, now)
        tidx, reason = decide_touch(r, st_row, now)
        if tidx is None:
            waiting.append((r, reason))
            continue
        subject, body, bug_src = render(r, seq, tidx, cases)
        queue.append({
            "id": sid, "url": gv(r, "url"), "email": gv(r, "email"),
            "seq": seq, "touch": tidx, "subject": subject, "body": body,
            "bug_src": bug_src,
        })

    gc.log(f"Nurture scan: leads={len(leads)} ready={len(queue)} waiting={len(waiting)} "
           f"(stop={stop_active} dry={dry_run})", "NURTURE")
    print("\n=== NURTURE PREVIEW ===")
    print(f"Leads scanned : {len(leads)}")
    print(f"Ready to send : {len(queue)}   (STOP={stop_active}, DRY_RUN={dry_run})")
    print(f"Waiting       : {len(waiting)}")
    print("\n--- READY QUEUE ---")
    for q in queue:
        print(f"\n[#{q['id']}] {q['url']} -> {q['email']}")
        print(f"  seq={q['seq']} touch {q['touch']}  value={q['bug_src']}")
        print(f"  SUBJ: {q['subject']}")
        print("  ----")
        print(q["body"])
        print("  ----")

    if queue and not dry_run and not stop_active:
        accounts = get_accounts(env)
        if not accounts:
            gc.log("Net akkauntov v .env. Ostanovleno.", "NURTURE")
            return
        max_run = int(env.get("NURTURE_MAX_PER_RUN", "10"))
        capped = queue[:max_run]
        gc.log(f"Otpravka {len(capped)}/{len(queue)} (NURTURE_MAX_PER_RUN={max_run})", "NURTURE")
        sent = 0
        conn = gc.get_conn()
        for q in capped:
            acc = accounts[sent % len(accounts)]
            try:
                send_one(acc, q["email"], q["subject"], q["body"], q["url"])
                persist_state(q["id"], q["seq"], q["touch"], now, conn)
                gc.log(f"NURTURE {q['seq']} touch{q['touch']} -> #{q['id']}", "NURTURE")
                sent += 1
            except Exception as e:
                gc.log(f"NURTURE OSIBKA #{q['id']}: {e}", "NURTURE")
                alert_sage(f"send fail #{q['id']}: {e}")
        conn.close()
        gc.log(f"Nurture zavershen. Otpravleno: {sent}", "NURTURE")
    elif queue:
        gc.log(f"Nichego ne otpravleno (dry={dry_run} stop={stop_active}). Gotovo: {len(queue)}.", "NURTURE")

    if waiting:
        from collections import Counter
        reasons = Counter(r for _, r in waiting)
        print("\n--- WAITING ---")
        for rs, n in reasons.most_common():
            print(f"  {n:3}  {rs}")


if __name__ == "__main__":
    main()
