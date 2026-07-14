#!/usr/bin/env python3
"""
agent_scout_scorer.py - рантайм-скорер кандидатов `review` (lane01 SCOUT).

ЧИТАЕТ веса скора из army.toml (секция [scout]) и ранжирует лиды со статусом
'review' для ручного аппрува Назаром. ТОЛЬКО ЧТЕНИЕ БД + генерация файла-артефакта.
НЕ пишет в outreach.db, НЕ меняет статусы, НЕ пушит, НЕ шлёт рассылку.

Поведение:
  1. SELECT id, url, score, email, telegram, tags, notes, created_at
     FROM sites WHERE status='review'        (read-only)
  2. composite = score*w_score + (upvotes/10)*w_up
                + (age_days<120 ? w_fresh : 0) + (stack ? w_stack : 0)
  3. Сортировка по composite DESC, топ-N (review_top_n из army.toml, default 25).
  4. Вывод: artifacts/scout_ranked_review.md (таблица id|domain|composite|разложение|контакт-чистый).
  5. --dry-run ВКЛЮЧЁН по умолчанию (никаких сайд-эффектов в любом случае).

DRIFT (зафиксировано, см. artifacts/scout_ranked_review_spec или отчёт):
  - Задача просит секцию [scout.score] в army.toml. Реально там ЕСТЬ [score]
    (веса freshness/technographic/signals/buggy/contact), а [scout] НЕТ.
    Скорер читает [scout] если есть, иначе берёт дефолты (w_score=1.0,
    w_up=0.5, w_fresh=2.0, w_stack=2.0, review_top_n=25).
  - Схема sites НЕ имеет колонок domain/age_days/upvotes/stack. Реальные
    колонки: id, url, email, telegram, status, tags, source, notes,
    amount_earned, created_at, updated_at, score.
    -> domain берём из url; age_days из notes 'launched~DATE' или created_at;
       upvotes из notes 'upvotes=N' (иначе 0); stack из маркера 'stack='
       в notes (детект стека из HTML пока не персистится, см. scout-17).
  - Контакт-чистый: через common_contacts.extract_contacts (отсекает мусор-email
    и CSS/JSON-LD TG-хэндлы вроде @font/@type/@context).

SECURITY: скрипт read-only. SSRF-гарды НЕ трогаем (это парсинг-рантайм, сети нет).
ONE-WRITER: 0 записей в БД/git.

Запуск:
  python agent_scout_scorer.py                 # dry-run, топ-N из конфига
  python agent_scout_scorer.py --top 40        # топ-40
  python agent_scout_scorer.py --no-dry-run    # флаг есть, но эффект тот же (только файл)
"""

import os
import re
import sys
import sqlite3
import argparse
from datetime import datetime, date

# --- пути: этот скрипт в outreach-tracker/ ---
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # import common_contacts рядом

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None

from common_contacts import extract_contacts, is_junk_email

# score_lead - read-only сухой скор (БЕЗ записи в БД, БЕЗ фетча => БЕЗ сети).
# Импортируем опционально: если track.py недоступен - фолбэк на хранимый score.
try:
    import track as _track
    _HAS_TRACK = True
except Exception:
    _HAS_TRACK = False

DB_PATH = os.path.join(HERE, "outreach.db")

# --- поиск army.toml (dispatch/ рядом с outreach-tracker/) ---
def _find_toml():
    if os.environ.get("ARMY_TOML"):
        return os.environ["ARMY_TOML"]
    cands = [
        os.path.join(HERE, "..", "dispatch", "army.toml"),
        os.path.join(HERE, "army.toml"),
        os.path.join(os.getcwd(), "army.toml"),
        os.path.join(HERE, "..", "army.toml"),
    ]
    for c in cands:
        if os.path.isfile(c):
            return os.path.abspath(c)
    return os.path.join(HERE, "..", "dispatch", "army.toml")


# --- дефолтные веса (если [scout] секции нет в army.toml) ---
DEFAULT_SCOUT = {
    "w_score": 1.0,
    "w_up": 0.5,
    "w_fresh": 2.0,
    "w_stack": 2.0,
    "review_top_n": 25,
}


def load_scout_cfg(toml_path):
    """Читает секцию [scout] из army.toml. Если нет - дефолты.
    Возвращает (cfg_dict, source_str)."""
    if tomllib is None:
        return dict(DEFAULT_SCOUT), "defaults (no tomllib)"
    try:
        with open(toml_path, "rb") as f:
            raw = tomllib.loads(f.read().decode("utf-8"))
    except Exception as e:
        return dict(DEFAULT_SCOUT), f"defaults (toml read error: {e})"
    scout = raw.get("scout") or {}
    if not scout:
        return dict(DEFAULT_SCOUT), "defaults ([scout] absent; army.toml has [score])"
    cfg = dict(DEFAULT_SCOUT)
    cfg.update({k: v for k, v in scout.items() if k in DEFAULT_SCOUT})
    return cfg, "army.toml [scout]"


# --- парсинг полей из реальной схемы ---
DOMAIN_RE = re.compile(r"https?://([^/]+)/?", re.I)
LAUNCHED_RE = re.compile(r"launched~(\d{4}-\d{2}-\d{2})")
UPVOTES_RE = re.compile(r"upvotes[=:]?\s*(\d+)", re.I)
STACK_RE = re.compile(r"stack=([a-z0-9.\-]+)", re.I)


def domain_from_url(url):
    if not url:
        return ""
    m = DOMAIN_RE.match(url)
    return (m.group(1) if m else url).lower().strip()


def age_days(notes, created_at):
    """Возраст домена: из notes 'launched~DATE' (приоритет) иначе из created_at.
    НЕ ходит в сеть (RDAP) - для live-возраста нужен score_lead --fetch."""
    today = date.today()
    if notes:
        m = LAUNCHED_RE.search(notes)
        if m:
            try:
                d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                return max(0, (today - d).days)
            except Exception:
                pass
    if created_at:
        try:
            d = datetime.strptime(created_at[:10], "%Y-%m-%d").date()
            return max(0, (today - d).days)
        except Exception:
            pass
    return None


def upvotes_from(notes):
    if notes:
        m = UPVOTES_RE.search(notes)
        if m:
            return int(m.group(1))
    return 0


def stack_from(notes):
    """True если в notes есть маркер 'stack=' (результат детекта фреймворка).
    Детект стека из HTML (scout-2026-07-14-17) пока НЕ персистится -> иначе False."""
    if notes and STACK_RE.search(notes):
        return True
    return False


def contact_clean(url, email, telegram):
    """Чистый контакт через common_contacts (отсекает мусор-email и CSS TG-хэндлы).

    Расширяет common_contacts: мусор-email вроде '005_22@0.75x-1-500x350.png'
    (локальная часть = цифры/точки/тире без букв, либо домен-картинка .png)
    is_junk_email не ловит -> отсекаем вручную. Числовые TG-хэндлы
    ('@1783768792') тоже мусор -> отсекаем.
    """
    em, tg1, _ = extract_contacts(email or "")
    _, tg2, _ = extract_contacts(telegram or "")

    def _email_real(e):
        if is_junk_email(e):
            return False
        local, _, dom = e.lower().partition("@")
        # домен-картинка / без-букв в локали = мусор парсера
        if dom.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
            return False
        if not any(c.isalpha() for c in local):
            return False  # локаль только цифры/тире/точки
        return True

    def _tg_real(h):
        h = h.lstrip("@")
        if h.isdigit():
            return False  # числовой хэндл = мусор
        return True

    clean_emails = sorted({e for e in em if _email_real(e)})
    clean_tg = sorted({h for h in (set(tg1) | set(tg2)) if _tg_real(h)})
    return bool(clean_emails) or bool(clean_tg), (clean_emails[0] if clean_emails else ""), (clean_tg[0] if clean_tg else "")


def compute_composite(row, cfg):
    """row: sqlite3.Row. Возвращает (composite, breakdown_dict).

    Терм `score`: берём on-the-fly через score_lead(row) (read-only, БЕЗ
    записи в БД - как гарантирует track.py task-16), если доступен track.py.
    Иначе - хранимый row['score'] (обычно 0 для review). Это консистентно с
    `track.py list --by-score` (он тоже считает сухой score on-the-fly).
    """
    notes = row["notes"] or ""
    up = upvotes_from(notes)
    age = age_days(notes, row["created_at"])
    stk = stack_from(notes)

    if _HAS_TRACK:
        days = None
        if age is not None:
            days = age
        res = _track.score_lead(dict(row), days_since_dump=days, fetch=False)
        score = float(res["score"])
    else:
        score = float(row["score"] or 0)

    w_score = float(cfg["w_score"])
    w_up = float(cfg["w_up"])
    w_fresh = float(cfg["w_fresh"])
    w_stack = float(cfg["w_stack"])

    term_score = score * w_score
    term_up = (up / 10.0) * w_up
    term_fresh = w_fresh if (age is not None and age < 120) else 0
    term_stack = w_stack if stk else 0

    composite = term_score + term_up + term_fresh + term_stack
    return round(composite, 3), {
        "score": score, "w_score": w_score, "term_score": round(term_score, 2),
        "up": up, "w_up": w_up, "term_up": round(term_up, 2),
        "age": age, "w_fresh": w_fresh, "term_fresh": term_fresh,
        "stack": stk, "w_stack": w_stack, "term_stack": term_stack,
    }


def main():
    ap = argparse.ArgumentParser(description="Rank review-leads for manual approval (read-only).")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="Dry-run (DEFAULT ON, no side effects ever).")
    ap.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                    help="Flag exists for symmetry; effect is identical (file only).")
    ap.add_argument("--top", type=int, default=None,
                    help="Override review_top_n from config.")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "dispatch", "artifacts", "scout_ranked_review.md"),
                    help="Output markdown path.")
    args = ap.parse_args()

    toml_path = _find_toml()
    cfg, cfg_src = load_scout_cfg(toml_path)
    top_n = args.top if args.top else int(cfg.get("review_top_n", 25))

    # 1. read-only SELECT review
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, url, score, email, telegram, tags, notes, created_at "
            "FROM sites WHERE status='review'"
        ).fetchall()
    finally:
        conn.close()

    # 2-3. composite + sort DESC
    ranked = []
    for r in rows:
        comp, br = compute_composite(r, cfg)
        cc, cc_email, cc_tg = contact_clean(r["url"], r["email"], r["telegram"])
        ranked.append({
            "id": r["id"],
            "domain": domain_from_url(r["url"]),
            "composite": comp,
            "breakdown": br,
            "contact_clean": cc,
            "cc_email": cc_email,
            "cc_tg": cc_tg,
        })
    ranked.sort(key=lambda x: (-x["composite"], x["id"]))
    top = ranked[:top_n]

    # 4. output markdown
    lines = []
    lines.append("# SCOUT ranked review-leads (ручной аппрув)")
    lines.append("")
    lines.append(f"- Дата прогона: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- Источник весов: {cfg_src}")
    lines.append(f"- army.toml: {toml_path}")
    lines.append(f"- Всего review-лидов: {len(ranked)} | топ-N: {top_n}")
    lines.append("")
    lines.append("## Веса (composite = score*w_score + (upvotes/10)*w_up + (age<120 ? w_fresh : 0) + (stack ? w_stack : 0))")
    lines.append("")
    lines.append(f"- w_score={cfg['w_score']}  w_up={cfg['w_up']}  w_fresh={cfg['w_fresh']}  w_stack={cfg['w_stack']}")
    lines.append("")
    lines.append("> DRIFT: army.toml имеет [score], а НЕ [scout.score] (как в тексте задачи).")
    lines.append("> Скорер читает [scout] при наличии, иначе дефолты. Чтобы задать веса -")
    lines.append("> добавьте секцию [scout] в army.toml (w_score/w_up/w_fresh/w_stack/review_top_n).")
    lines.append("> Поля domain/age_days/upvotes/stack извлекаются из url/notes/created_at")
    lines.append("> (схема sites не имеет этих колонок).")
    lines.append("> Терм `score` берётся on-the-fly через score_lead(row) (read-only, БЕЗ записи в БД,")
    lines.append("> как гарантирует track.py task-16). Консистентно с `track.py list --by-score`.")
    lines.append("")
    lines.append(f"{'#':<4} {'DOMAIN':<34} {'COMP':<7} {'SCORE':<6} {'UP':<4} {'AGE':<5} {'STK':<4} {'CONTACT':<8}")
    lines.append("-" * 86)
    for it in top:
        br = it["breakdown"]
        age = br["age"] if br["age"] is not None else "-"
        stk = "Y" if br["stack"] else "-"
        cc = "Y" if it["contact_clean"] else "N"
        lines.append(
            f"{it['id']:<4} {it['domain']:<34} {it['composite']:<7} "
            f"{br['score']:<6.0f} {br['up']:<4} {age:<5} {stk:<4} {cc:<8}"
        )
    lines.append("")
    lines.append("### Разложение топ-10")
    lines.append("")
    for it in top[:10]:
        br = it["breakdown"]
        age = br["age"] if br["age"] is not None else "?"
        contact = it["cc_email"] or it["cc_tg"] or "-"
        lines.append(
            f"- **{it['domain']}** (id={it['id']}) comp={it['composite']} | "
            f"score*ws={br['term_score']} + up/10*wu={br['term_up']} "
            f"+ age<120?w_fresh={br['term_fresh']} + stack?w_stack={br['term_stack']} | "
            f"age={age}d stack={br['stack']} contact={contact}"
        )
    lines.append("")
    lines.append("> [DRY] БД НЕ изменена. Только чтение + этот файл. Для аппрува лидов - "
                 "вручную через track.py edit, либо будущий batch-аппрув.")

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"[*] review-лидов: {len(ranked)} | топ-N: {top_n}")
    print(f"[*] веса из: {cfg_src}")
    print(f"[*] топ-5:")
    for it in top[:5]:
        print(f"    {it['id']:<4} {it['domain']:<34} comp={it['composite']}")
    print(f"[*] артефакт: {out_path}")
    print("[*] [DRY] БД НЕ изменена.")


if __name__ == "__main__":
    main()
