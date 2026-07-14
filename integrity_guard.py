#!/usr/bin/env python3
"""
integrity_guard.py - страж целостности outreach.db (Гордон).
Часть защиты истинника (задача RELAY lane08, relay-2026-07-14-14).

ЧТО ДЕЛАЕТ (безопасно):
  - PRAGMA integrity_check перед любой записью в БД;
  - детект "грязной" БД от другого окна: lock/WAL-конфликт -> НЕ писать,
    залогировать, ждать (один писатель = one-writer);
  - preflight(): целостность + свободен ли файл + свежесть бэкапа;
  - guard_write(conn, fn): обёртка вокруг чужой записи (fn делает изменения),
    проверяет ДО и ПОСЛЕ, при БАД - откат транзакции + не коммитит.

ЧТО НЕ ДЕЛАЕТ (запреты ТЗ):
  - НЕ пишет бизнес-данные сам (только проверяет + оборачивает чужие записи);
  - НЕ отправляет в ТГ (токен = секрет; REALTIME_GUARD_TG=False по умолчанию,
    алерт только в лог/print). Можно включить через --tg при наличии bridge.env;
  - НЕ пушит в git, НЕ трогает токены/секреты, НЕ kill/fcc, НЕ vision.

Запуск:
  python integrity_guard.py --check            # integrity_check (exit 0/1)
  python integrity_guard.py --backup           # снять снапшот через backup_db
  python integrity_guard.py --restore          # откат на последний good-бэкап
  python integrity_guard.py --preflight        # полная проверка перед стартом агента
  python integrity_guard.py --tg               # + пинговать ТГ при аварии (если настроено)

guard_write используют ДРУГИЕ пишущие агенты (sender/recorder/track):
    from integrity_guard import guard_write
    guard_write(conn, lambda c: c.execute("UPDATE sites SET status='sent' WHERE id=?"))
"""
import os
import sys
import time
import shutil
import re
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "outreach.db")
BACKUPS = os.path.join(HERE, "backups")
N_KEEP = 10
FRESH_MAX_SEC = 24 * 3600
TAG = "guard"
MASK = re.compile(r"^outreach\.db\.guard-\d{8}-\d{6}\.bak$")
REALTIME_GUARD_TG = False      # по умолчанию ТГ выключен (токен = секрет)

LOG = os.path.join(HERE, "backups", "backup_guard.log")


def log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [integrity_guard] {msg}"
    try:
        os.makedirs(BACKUPS, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)


def _alert(text):
    """Алерт: по умолчанию только лог/print. С ТГ - только если явно --tg
    и доступен bridge.env + send_telegram (НЕ правим, только читаем)."""
    log("ALERT: " + text)
    if REALTIME_GUARD_TG:
        try:
            sys.path.insert(0, HERE)
            from send_telegram import send_telegram
            send_telegram(text)
        except Exception as e:
            log("ТГ-алерт не прошёл (не критично): " + str(e))


def integrity_ok():
    """PRAGMA integrity_check. Возвращает (ok:bool, detail:str)."""
    import sqlite3
    try:
        c = sqlite3.connect(DB)
        rows = c.execute("PRAGMA integrity_check").fetchall()
        c.close()
        bad = [r for r in rows if r[0] != "ok"]
        return (len(bad) == 0, "" if not bad else "; ".join(r[0] for r in bad))
    except Exception as e:
        return (False, "exception: " + str(e))


def is_locked():
    """Детект: БД занята другим окном (lock/WAL busy) или недоступна.
    Возвращает (locked:bool, detail:str). Пробуем открыть с busy_timeout=0,
    чтобы сразу увидеть конфликт, не блокируясь."""
    import sqlite3
    try:
        c = sqlite3.connect(DB, timeout=0)
        c.execute("PRAGMA busy_timeout=0")
        # лёгкий тест на доступность записи (не меняет данные)
        c.execute("BEGIN IMMEDIATE")
        c.execute("ROLLBACK")
        c.close()
        return (False, "")
    except sqlite3.OperationalError as e:
        return (True, "locked: " + str(e))
    except Exception as e:
        return (True, "error: " + str(e))


def latest_good_backup():
    """Свежайший СВОЙ бэкап (по имени таймстампа), который сам цел."""
    import sqlite3
    if not os.path.isdir(BACKUPS):
        return None
    files = [f for f in os.listdir(BACKUPS) if MASK.match(f)]
    files.sort()
    for f in reversed(files):
        path = os.path.join(BACKUPS, f)
        try:
            c = sqlite3.connect(path)
            ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            c.close()
            if ok:
                return path
        except Exception:
            continue
    return None


def restore_latest():
    """Откат БД на последний good-бэкап (только при аварии целостности)."""
    src = latest_good_backup()
    if not src:
        _alert("good-бэкап не найден, откат НЕВОЗМОЖЕН!")
        return False
    shutil.copy2(src, DB)
    _alert("целостность БД упала. Откат на " + os.path.basename(src))
    return True


def preflight():
    """Проверка перед записью: целостность + свободен ли файл + свежесть бэкапа.
    Возвращает (ok:bool, msg:str). При БАД целостности - блок + алерт + откат."""
    ok, detail = integrity_ok()
    if not ok:
        _alert("integrity_check БД УПАЛ: " + detail)
        restore_latest()
        return (False, "integrity failed, restored")
    locked, ldetail = is_locked()
    if locked:
        _alert("БД занята другим окном (" + ldetail + "). Запись ЗАБЛОКИРОВАНА, ждём.")
        return (False, "locked by another window: " + ldetail)
    files = sorted([f for f in os.listdir(BACKUPS) if MASK.match(f)]) if os.path.isdir(BACKUPS) else []
    if not files:
        return (False, "no backup yet (сделай --backup)")
    latest = os.path.join(BACKUPS, files[-1])
    age = time.time() - os.path.getmtime(latest)
    if age > FRESH_MAX_SEC:
        _alert(f"последний бэкап старше {FRESH_MAX_SEC//3600}ч "
               f"({os.path.basename(latest)}). Сделай свежий --backup перед записью.")
        # по умолчанию НЕ блокируем (только предупреждаем)
    return (True, "preflight ok")


def guard_write(conn, fn):
    """ОБЁРТКА вокруг чужой записи. fn(conn) делает изменения + commit.
    Проверяет целостность и lock ДО, при БАД - НЕ пишем, откат транзакции.
    Проверяет целостность ПОСЛЕ, при падении - rollback + откат файла."""
    ok, _ = integrity_ok()
    if not ok:
        _alert("запись заблокирована - БД нецелостна.")
        return False
    locked, _ = is_locked()
    if locked:
        _alert("запись заблокирована - БД занята другим окном.")
        return False
    try:
        fn(conn)                  # чужая логика записи (track/sender/...)
        conn.commit()
    except Exception as e:
        conn.rollback()
        _alert("ошибка записи: " + str(e) + ". Откат транзакции.")
        return False
    ok2, detail = integrity_ok()
    if not ok2:
        conn.rollback()
        _alert("целостность упала ПОСЛЕ записи: " + detail + ". Откат.")
        restore_latest()
        return False
    return True


def main():
    do_check = "--check" in sys.argv
    do_backup = "--backup" in sys.argv
    do_restore = "--restore" in sys.argv
    do_preflight = "--preflight" in sys.argv
    if "--tg" in sys.argv:
        global REALTIME_GUARD_TG
        REALTIME_GUARD_TG = True

    if do_restore:
        restore_latest(); return
    if do_backup:
        sys.path.insert(0, HERE)
        try:
            from backup_db import backup_now, rotate
            dst = backup_now()
            rotate(N_KEEP)
            print("бэкап готов: " + (os.path.basename(dst) if dst else "NONE"))
        except Exception as e:
            print("бэкап не удался: " + str(e))
        return
    if do_check:
        ok, d = integrity_ok()
        print("integrity_check: " + ("OK" if ok else "BAD: " + d))
        sys.exit(0 if ok else 1)
    if do_preflight:
        ok, msg = preflight()
        print("preflight: " + ("OK - можно писать" if ok else "БЛОК: " + msg))
        sys.exit(0 if ok else 1)
    # без флагов - preflight
    ok, msg = preflight()
    print("preflight: " + ("OK" if ok else "БЛОК: " + msg))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
