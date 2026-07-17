#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_pitcher.py - АГЕНТ-ПИТЧЕР (v0.4 core-funnel): дожим ответивших до hired.

Берёт лиды со статусом 'replied' (ответили, но ещё не наняли) и формирует
короткое follow-up предложение. По умолчанию DRY-RUN: только печатает,
что предложит и кому. Реальная смена статуса -> 'hired' делается ТОЛЬКО
при --commit (и только для тех, кто явно подтвердил сотрудничество в ответе).

БЕЗОПАСНОСТЬ:
  --dry (по умолчанию) ИЛИ без --commit: БД НЕ меняется, письма НЕ шлются.
  --commit: только ставит status='hired' для лидов из файла подтверждений.

Режимы:
  python agent_pitcher.py            # dry-run: кого дожмём
  python agent_pitcher.py --list     # просто список replied
  python agent_pitcher.py --commit --from confirmed.txt
        # confirmed.txt = id по одному на строку тех, кто реально согласился
"""
import os
import sys
import sqlite3
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gordon_common as gc

PITCH = (
    "Привет! Рад, что откликнулись. Предлагаю так: я бесплатно прогоняю "
    "ваш сайт и присылаю короткий отчёт с 3 главными багами и как их закрыть. "
    "Если зайдёт - обсудим условия. Мой Telegram: @oojdo."
)


def _conn():
    return gc.get_conn()


def get_replied():
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM sites WHERE status='replied' ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def main():
    ap = argparse.ArgumentParser(description="Питчер: дожим replied до hired")
    ap.add_argument("--list", action="store_true", help="только список replied")
    ap.add_argument("--commit", action="store_true", help="реально ставить hired")
    ap.add_argument("--from", dest="confirmed", default=None,
                    help="файл с id подтвердивших (по строке), только с --commit")
    args = ap.parse_args()

    replied = get_replied()
    if not replied:
        print("Нет лидов со статусом 'replied'. Дожимать некого.")
        return

    print("=== REPLIED ({n}) ===".format(n=len(replied)))
    for s in replied:
        print("  #{id} {url} -> {e}".format(
            id=s.get("id"), url=s.get("url"), e=s.get("email")))

    if args.list:
        print("\n[--list] Только список. БД не изменена.")
        return

    if not args.commit:
        print("\n[DRY-RUN] Для каждого предложу pitch (не шлю, БД не трогаю):")
        for s in replied:
            print("\n--- #{id} {e} ---".format(id=s.get("id"), e=s.get("email")))
            print(PITCH)
        print("\nЧтобы зафиксировать hired: --commit --from confirmed.txt")
        return

    # --commit
    if not args.confirmed or not os.path.exists(args.confirmed):
        print("ОШИБКА: --commit требует --from <файл с id подтвердивших>.")
        return
    with open(args.confirmed, "r", encoding="utf-8") as f:
        ids = set()
        for line in f:
            line = line.strip()
            if line.isdigit():
                ids.add(int(line))
    to_close = [s for s in replied if s.get("id") in ids]
    if not to_close:
        print("В confirmed.txt нет совпадений со replied. Ничего не делаю.")
        return
    conn = _conn()
    for s in to_close:
        conn.execute("UPDATE sites SET status='hired' WHERE id=?", (s["id"],))
        gc.log("Pitcher: #{id} -> hired (podtverzhdeno)".format(id=s["id"]), "PITCHER")
    conn.commit()
    conn.close()
    print("Зафиксировано hired: {n} лидов.".format(n=len(to_close)))


if __name__ == "__main__":
    main()
