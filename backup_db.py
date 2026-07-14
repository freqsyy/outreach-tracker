#!/usr/bin/env python3
"""
backup_db.py - авто-бэкап истинника outreach.db (Гордон).
Часть защиты истинника (задача RELAY lane08, relay-2026-07-14-14).
ТОЛЬКО локальные снапшоты в backups/ + ротация. НЕ пишет бизнес-данные,
НЕ пушит, НЕ трогает токены/ТГ (токен = секрет; алерт только в лог/print).

Запуск:
  python backup_db.py --backup           # один прогон: снапшот + ротация
  python backup_db.py --check            # показать текущий размер/свежесть БД
  python backup_db.py --interval 3600    # демон: бэкап раз в час (Ctrl+C = стоп)
  python backup_db.py --keep 10          # сколько свежих бэкапов хранить
  python backup_db.py --backup --keep 5  # разовый снапшот, оставить 5 шт.

Формат бэкапа: backups/outreach.db.guard-YYYYMMDD-HHMMSS.bak
(тег guard отличает от nurture-бэкапов; ротация трогает только свой формат).
"""
import os
import sys
import time
import shutil
import glob
import re
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "outreach.db")
BACKUPS = os.path.join(HERE, "backups")
N_KEEP = 10                       # сколько свежих СВОИХ бэкапов хранить
FRESH_MAX_SEC = 24 * 3600         # бэкап старше суток = несвежий (для preflight)
TAG = "guard"                     # свой тег в имени (отличаем от nurture-)
MASK = re.compile(r"^outreach\.db\.guard-\d{8}-\d{6}\.bak$")  # свой формат

LOG = os.path.join(HERE, "backups", "backup_guard.log")


def log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [backup_db] {msg}"
    try:
        os.makedirs(BACKUPS, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)


def ts_now():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_now():
    """Снять снапшот БД в backups/ (атомарно через copy2). Возвращает путь/None."""
    if not os.path.exists(DB):
        log("БД не найдена, бэкап НЕ сделан: " + DB)
        return None
    os.makedirs(BACKUPS, exist_ok=True)
    dst = os.path.join(BACKUPS, f"outreach.db.guard-{ts_now()}.bak")
    shutil.copy2(DB, dst)         # copy2 = атомарная копия файла целиком
    log("снапшот: " + os.path.basename(dst))
    return dst


def list_own_backups():
    if not os.path.isdir(BACKUPS):
        return []
    return sorted([f for f in os.listdir(BACKUPS) if MASK.match(f)])


def latest_backup_path():
    files = list_own_backups()
    return os.path.join(BACKUPS, files[-1]) if files else None


def rotate(keep=N_KEEP):
    """Оставить `keep` свежих СВОИХ бэкапов, стереть остальные.
    Чужие форматы (nurture-, outreach_YYYYMMDD_HHMMSS.db) НЕ трогаем."""
    if not os.path.isdir(BACKUPS):
        return
    files = list_own_backups()
    for old in files[:-keep]:
        try:
            os.remove(os.path.join(BACKUPS, old))
            log("ротация: удалён старый " + old)
        except Exception as e:
            log("ротация: не удалось удалить " + old + " (" + str(e) + ")")


def freshness():
    """Возраст (сек) последнего СВОЕГО бэкапа, или None если нет."""
    p = latest_backup_path()
    if not p:
        return None
    return time.time() - os.path.getmtime(p)


def main():
    do_backup = "--backup" in sys.argv or "--once" in sys.argv
    do_check = "--check" in sys.argv
    interval = 3600
    keep = N_KEEP
    if "--interval" in sys.argv:
        i = sys.argv.index("--interval") + 1
        interval = int(sys.argv[i]) if i < len(sys.argv) else 3600
    if "--keep" in sys.argv:
        k = sys.argv.index("--keep") + 1
        keep = int(sys.argv[k]) if k < len(sys.argv) else N_KEEP

    if do_check:
        if not os.path.exists(DB):
            print("БД НЕ найдена: " + DB); sys.exit(1)
        sz = os.path.getsize(DB)
        age = freshness()
        print(f"БД: {DB} ({sz} байт)")
        if age is None:
            print("свой бэкап: ещё нет")
        else:
            fresh = "свежий" if age <= FRESH_MAX_SEC else "СТАРЫЙ"
            print(f"последний бэкап: {os.path.basename(latest_backup_path())} "
                  f"({int(age//3600)}ч назад, {fresh})")
        print(f"своих бэкапов: {len(list_own_backups())} (храним {keep})")
        sys.exit(0)

    if not do_backup:
        # без флагов - разовый бэкап (безопасное дефолтное поведение)
        do_backup = True

    print(f"[backup_db] старт. interval={interval}с, keep={keep}")
    while True:
        dst = backup_now()
        rotate(keep)
        if "--interval" not in sys.argv:
            break
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            log("остановлен (Ctrl+C)")
            break


if __name__ == "__main__":
    main()
