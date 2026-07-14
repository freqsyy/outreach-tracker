#!/usr/bin/env python3
"""
scout_dedup_bridge.py - мост скаута к dedup.py (тик-22).

ПОКАЗЫВАЕТ, как agent_scout.py будет переиспользовать dedup.py перед
кладкой лида в `review` (спека тик-21 "дубль-домен"). ЭТОТ СКРИПТ НЕ
пишет в БД и НЕ кладёт лидов - толькО read-only проверка + вывод.

Поведение:
  1. `scout_dedup_bridge.py --check <domain>` -> SELECT url FROM sites
     (read-only) + norm_domain (lower, без www, без протокола/порта/пути)
     + сверка -> печатает "DUPE" / "NEW".
  2. НЕ пишет в БД, НЕ кладёт лидов. ТолькО проверка + вывод.
  3. Функция `should_ingest(domain)` -> bool (True = можно класть, нет дубля).
  4. `--dry-run` ВКЛЮЧЁН по умолчанию (ноль side-effects, кроме печати).
  5. Док-строка ниже описывает, как agent_scout.py вызовет should_ingest
     перед INSERT (agent_scout.py НЕ правится этим скриптом).

DRIFT (зафиксировано):
  - Спека тик-21 требует блока по ЛЮБОМУ статусу в `sites`
    (включая review-vs-review). Существующий `dedup.is_already_contacted`
    ловит ТОЛЬКО closed-статусы (sent/bounced/rejected) - этого НЕдостаточно
    для дубля-домена внутри review-пула. Поэтому should_ingest читает
    ВСЕ статусы (`SELECT url,status FROM sites`), а не через is_already_contacted.
    norm_domain/apex_of переиспользуются из dedup.py как есть.
  - Счётчик `seen=N` (инкремент в notes при дубле) - НЕ реализуется здесь:
    это прод-запись в БД (yellow), делает ТОЛЬКО agent_scout в проде. Мост
    только говорит DUPE/NEW.

SECURITY: скрипт read-only. Нет сети (ssrf не нужен - только локальная БД).
ONE-WRITER: 0 записей в БД/git.
SSRF: домен проверяется ТОЛЬКО локально (нормализация строки), никаких
  сетевых вызовов.

Запуск:
  python scout_dedup_bridge.py --check frienddraft.app
  python scout_dedup_bridge.py --check https://www.Foo.com/path
  python scout_dedup_bridge.py --check foo.com --dry-run
"""

import os
import sys
import sqlite3
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from dedup import norm_domain, apex_of  # переиспользуем нормализацию

DB_PATH = os.path.join(HERE, "outreach.db")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_all_domains(conn=None):
    """READ-ONLY. Возвращает dict:
        {"domains": set(норм-доменов),
         "apex":    set(apex TLD+1),
         "by_domain": {норм_домен: (id, status)}}
    Берёт ВСЕ статусы из sites (по спеке тик-21, не только closed).
    """
    own = conn is None
    c = conn if conn else _conn()
    try:
        rows = c.execute("SELECT id, url, status FROM sites").fetchall()
    finally:
        if own:
            c.close()
    domains, apex, by_domain = set(), set(), {}
    for r in rows:
        dom = norm_domain(r["url"] or "")
        if not dom:
            continue
        domains.add(dom)
        ap = apex_of(dom)
        if ap:
            apex.add(ap)
        by_domain[dom] = (r["id"], r["status"])
    return {"domains": domains, "apex": apex, "by_domain": by_domain}


def is_duplicate_domain(url, conn=None, strict_apex=False):
    """READ-ONLY. True, если норм-домен УЖЕ в sites при ЛЮБОМ статусе.

    Возвращает (bool, reason, existing_id). Переиспользует norm_domain/apex_of
    из dedup.py. strict_apex=True -> блокирует и по apex-семье
    (app.foo.com == foo.com). По умолчанию strict_apex=False (точный домен).
    """
    dom = norm_domain(url) if url else ""
    if not dom:
        return False, "empty domain (skip, not a dupe)", None
    keys = load_all_domains(conn)
    if dom in keys["domains"]:
        rid, st = keys["by_domain"].get(dom, (None, "?"))
        return True, f"domain {dom} already in sites status={st}", rid
    if strict_apex:
        ap = apex_of(dom)
        if ap and ap in keys["apex"]:
            return True, f"apex {ap} already in sites", None
    return False, "", None


def should_ingest(domain, conn=None, strict_apex=False):
    """Главный интерфейс для agent_scout.

    True  = домена НЕТ в sites (любой статус) -> можно класть лида.
    False = дубль -> НЕ класть (agent_scout логирует [DEDUP-SKIP] и идёт дальше).

    Переиспользует dedup (norm_domain/apex_of) и читает ВСЕ статусы
    (по спеке тик-21). READ-ONLY, не меняет БД.
    """
    dup, _why, _eid = is_duplicate_domain(domain, conn, strict_apex)
    return not dup


def _check_and_print(domain, strict_apex=False):
    dup, why, eid = is_duplicate_domain(domain, strict_apex=strict_apex)
    norm = norm_domain(domain)
    if dup:
        print(f"DUPE  {norm}  ->  {why}" + (f" (id={eid})" if eid else ""))
    else:
        verdict = "NEW" if norm else "NEW? (empty domain - NOT a valid lead)"
        print(f"{verdict}  {norm}  ->  no duplicate in sites (any status)")
    return dup


def main():
    ap = argparse.ArgumentParser(
        description="Scout dedup bridge: check domain dupe against sites (read-only)."
    )
    ap.add_argument("--check", metavar="DOMAIN",
                    help="Domain or URL to check (prints DUPE / NEW).")
    ap.add_argument("--strict-apex", action="store_true",
                    help="Also block by apex family (app.foo.com == foo.com).")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="Dry-run (DEFAULT ON, no side effects ever).")
    ap.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                    help="Flag exists for symmetry; effect is identical (print only).")
    args = ap.parse_args()

    if not args.check:
        ap.print_help()
        print("\n[!] нужен --check <domain|url>")
        sys.exit(2)

    dup = _check_and_print(args.check, strict_apex=args.strict_apex)
    ingest = should_ingest(args.check, strict_apex=args.strict_apex)
    print(f"[*] should_ingest({norm_domain(args.check)!r}) = {ingest}  "
          f"(dup={dup})")
    print("[*] [DRY] БД НЕ изменена. Только чтение + вывод.")


if __name__ == "__main__":
    main()
