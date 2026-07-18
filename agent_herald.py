#!/usr/bin/env python3
"""
agent_herald.py - АГЕНТ 3 (Herald / Соц-амбассадор, faceless).
Личность: Herald. Анонимный "QA worker" в неоне — греет аудиторию в Reddit/X/
инди-форумах, ловит тёплые лиды, роутит их (никогда не закрывает).

ВАЖНО (безопасность ночного конверта): Herald НЕ публикует сам. Он ГЕНЕРИТ
ЧЕРНОВИКИ постов (drafts) из углов Hook + бартер-текстов, кладёт их в папку
herald_drafts/ и останавливается. Реальная публикация — красная зона (нужен
Назар / ручной аппрув). Это закрывает дыру из agent-audit: "Herald нет
рантайма, Sage дёрнет призрака".

Источники копирайта:
- Проекты/Гордон/hooks.md (20 A/B хуков, углы HA/LR/CE/LC) — читает и
  адаптирует под платформу, НЕ копирует тупо.
- barter_posts.md (18 готовых текстов бартера) — опц. сырьё для Reddit-постов.
- agent_hook.md (runtime-источник истины Hook) — если есть, берём оттуда.

Правила (из 03-herald.md): faceless, 80/20 (Reddit 90/10), value-first,
answer-first plug-second, техточность, НЕТ астротурфинга, НЕТ дубликата копий
по сабам. Herald только ЧИТАЕТ outreach.db (ни одной записи лидов — те
принадлежат Gordon/Nurture/Pitch).

Запуск:
  python agent_herald.py                  # сгенерить черновики в herald_drafts/
  python agent_herald.py --channel x      # только X-треды
  python agent_herald.py --channel reddit  # только Reddit-посты/комменты
  python agent_herald.py --dry-run        # то же, но НЕ пишет файлы (печать)
"""

import os
import re
import sys
import json
import argparse
from datetime import datetime

import gordon_common as gc

HERE = os.path.dirname(os.path.abspath(__file__))
DRAFTS_DIR = os.path.join(HERE, "herald_drafts")
HOOKS_PATH = os.path.join(HERE, "hooks.md")
AGENT_HOOK_PATH = os.path.join(HERE, "agent_hook.md")
BARTER_PATH = os.path.join(HERE, "barter_posts.md")

# Хук-углы, закреплённые за Herald (из hooks.md handoff)
HERALD_HOOK_IDS = ["CE-01", "CE-03", "LC-01", "LC-02"]

# платформы
CHANNELS = ["reddit", "x", "forum"]


# ----------------------------------------------------------------------------
# Чтение сырья
# ----------------------------------------------------------------------------
def read_text(path):
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        gc.log(f"Herald ne prochital {os.path.basename(path)}: {e}", "HERALD")
        return ""


def parse_hook_blocks(md):
    """Извлечь {ID: {headline, support, cta}} из markdown хуков.
    Формат: **HA-01** "..."
             - Support: ...
             - CTA: ..."""
    blocks = {}
    # ищем строки **ID** "..."
    for m in re.finditer(r"\*\*(HA|LR|CE|LC)-(\d{2})\*\*\s*(.+)", md):
        hid = f"{m.group(1)}-{m.group(2)}"
        headline = m.group(3).strip().strip('"').strip()
        blocks[hid] = {"headline": headline, "support": "", "cta": ""}
    # support/cta под каждым блоком
    lines = md.splitlines()
    cur = None
    for ln in lines:
        hm = re.match(r"\*\*(HA|LR|CE|LC)-(\d{2})\*\*", ln)
        if hm:
            cur = f"{hm.group(1)}-{hm.group(2)}"
            continue
        if cur:
            s = ln.strip()
            if s.lower().startswith("- support:"):
                blocks[cur]["support"] = s.split(":", 1)[1].strip()
            elif s.lower().startswith("- cta:"):
                blocks[cur]["cta"] = s.split(":", 1)[1].strip()
    return blocks


def load_hooks():
    """Источник истины — agent_hook.md, fallback hooks.md."""
    md = read_text(AGENT_HOOK_PATH) or read_text(HOOKS_PATH)
    if not md:
        return {}
    return parse_hook_blocks(md)


def load_barter():
    txt = read_text(BARTER_PATH)
    if not txt:
        return []
    # бартер-посты разбиты на блоки (пустые строки / заголовки)
    chunks = re.split(r"\n\s*\n", txt)
    return [c.strip() for c in chunks if c.strip() and len(c.strip()) > 30]


# ----------------------------------------------------------------------------
# Рендер черновиков под платформу
# ----------------------------------------------------------------------------
def reddit_value_post(hook):
    """Template B (value post) на базе хука-угла."""
    head = hook.get("headline", "")
    sup = hook.get("support", "")
    cta = hook.get("cta", "happy to click yours free if you drop the link")
    body = (
        "1. [Bug 1] — what it is, why it happens, 2-line fix.\n"
        "2. [Bug 2] — same pattern, fresh site.\n"
        "3. [Bug 3] — the silent one that kills conversions.\n\n"
        f"{sup}\n\n"
        "Happy to click through yours free if you drop the link — I'll send "
        "back the 3 worst issues, no strings. (Anonymous, just a QA worker.)"
    )
    return {
        "kind": "reddit_value_post",
        "title_seed": head,
        "body": body,
        "plug": cta,
    }


def reddit_comment(hook):
    """Template A (helpful comment + soft plug)."""
    head = hook.get("headline", "")
    cta = hook.get("cta", "I do free 3-bug audits for small sites — happy to click yours.")
    return {
        "kind": "reddit_comment",
        "answer_seed": f"[{head} — give a specific 2-4 sentence fix, no fluff.]",
        "plug": cta,
    }


def x_thread(hook):
    """Template C (8-tweet thread)."""
    head = hook.get("headline", "")
    sup = hook.get("support", "")
    cta = hook.get("cta", "Link in bio / DM 'AUDIT'. Faceless QA worker, here to help.")
    tweets = [
        f"1/ {head}",
        "2/ Bug pattern 1 + micro-explanation",
        "3/ Bug pattern 2 + micro-explanation",
        "4/ Bug pattern 3 + micro-explanation",
        "5/ Why founders miss these (ship-fast blind spot)",
        "6/ A real example (anonymized)",
        "7/ How a free audit works (transparent, 3 bugs, 24h)",
        f"8/ {cta}",
    ]
    return {"kind": "x_thread", "hook": head, "tweets": tweets}


def forum_comment(hook):
    """Template D (HN/forum, value only, no CTA)."""
    head = hook.get("headline", "")
    return {
        "kind": "forum_comment",
        "point": f"[{head} — one specific, true technical point that adds to the thread. No CTA.]",
    }


# ----------------------------------------------------------------------------
# Запись черновиков
# ----------------------------------------------------------------------------
def write_draft(fname, content, dry_run):
    os.makedirs(DRAFTS_DIR, exist_ok=True)
    path = os.path.join(DRAFTS_DIR, fname)
    if dry_run:
        print(f"\n=== DRAFT (dry) {fname} ===\n{content}\n")
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    gc.log(f"Herald draft -> {fname}", "HERALD")


def draft_to_text(d):
    lines = [f"KIND: {d['kind']}", ""]
    for k, v in d.items():
        if k == "kind":
            continue
        if isinstance(v, list):
            lines.append(f"{k.upper()}:")
            for i, item in enumerate(v, 1):
                lines.append(f"  {i}. {item}")
        else:
            lines.append(f"{k.upper()}: {v}")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------
# Главный прогон (DRAFT-ONLY)
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Herald social DRAFT generator (no publish)")
    ap.add_argument("--channel", choices=CHANNELS + ["all"], default="all",
                    help="только этот канал (default all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="НЕ писать файлы, только печать")
    args = ap.parse_args()

    # cp1251-консоль ломает эмодзи в print -> reconfigure на utf-8.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    hooks = load_hooks()
    barter = load_barter()
    if not hooks:
        gc.log("Herald: net khukov (agent_hook.md/hooks.md). "
                "Flag Sage: Hook idle.", "HERALD")
    herald_hooks = {hid: hooks[hid] for hid in HERALD_HOOK_IDS if hid in hooks}
    if not herald_hooks:
        herald_hooks = dict(list(hooks.items())[:4])  # fallback: первые 4

    gc.log(f"Herald gen: hooks={len(hooks)} herald_angles={len(herald_hooks)} "
           f"barter={len(barter)} channel={args.channel} dry={args.dry_run}", "HERALD")

    count = 0
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    for hid, hook in herald_hooks.items():
        if args.channel in ("all", "reddit"):
            d = reddit_value_post(hook)
            fname = f"{ts}_{hid}_reddit_post.md"
            write_draft(fname, draft_to_text(d), args.dry_run)
            count += 1
            d = reddit_comment(hook)
            fname = f"{ts}_{hid}_reddit_comment.md"
            write_draft(fname, draft_to_text(d), args.dry_run)
            count += 1
        if args.channel in ("all", "x"):
            d = x_thread(hook)
            fname = f"{ts}_{hid}_x_thread.md"
            write_draft(fname, draft_to_text(d), args.dry_run)
            count += 1
        if args.channel in ("all", "forum"):
            d = forum_comment(hook)
            fname = f"{ts}_{hid}_forum_comment.md"
            write_draft(fname, draft_to_text(d), args.dry_run)
            count += 1

    # бартер -> Reddit value-post сырьё (если есть)
    if barter and args.channel in ("all", "reddit"):
        for i, chunk in enumerate(barter[:4], 1):
            d = {
                "kind": "reddit_barter_seed",
                "source": f"barter_posts.md #{i}",
                "raw": chunk[:600],
            }
            fname = f"{ts}_barter_{i:02d}.md"
            write_draft(fname, draft_to_text(d), args.dry_run)
            count += 1

    gc.log(f"Herald draft-gen done: {count} drafts -> herald_drafts/ "
            f"(publish BLOCKED, needs Nazar approval).", "HERALD")
    print(f"\n[Herald] сгенерировано черновиков: {count}. "
          f"Публикация ЗАБЛОКИРОВАНА (красная зона) — ждёт аппрува Назара.")


if __name__ == "__main__":
    main()
