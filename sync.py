#!/usr/bin/env python3
"""
sync.py — локальный helper для синхронизации БД Гордона между ПК и GitHub.

ПРАВИЛО (см. DEPLOY_FREE.md):
  - GitHub Actions — единственный, кто гоняет Гордона (sender пишет статусы).
  - На ПК ты ТОЛЬКО редактируешь/добавляешь сайты (track.py, пульт).
  - ПЕРЕД правкой на ПК:        python sync.py pull
  - СРАЗУ ПОСЛЕ правки на ПК:    python sync.py push
  - Windows Task Scheduler GordonOutreach ДОЛЖЕН БЫТЬ ОТКЛЮЧЁН (иначе двойная
    запись одной БД с двух IP -> риск бана Gmail).

Команды:
  python sync.py pull     # забрать накопленное Actions (git pull --rebase)
  python sync.py push     # запушить правки БД/логов (git add + commit + push)

Сливает WAL в основной файл перед любой операцией, чтобы git видел все данные.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "outreach.db")


def checkpoint():
    """Слить WAL/SHM в outreach.db, чтобы git увидел все записи."""
    try:
        import sqlite3
        if os.path.exists(DB_PATH):
            c = sqlite3.connect(DB_PATH)
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.close()
    except Exception as e:
        print(f"[!] checkpoint warning: {e}")


def run(cmd):
    print(f"[>] {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if out:
        print(out)
    return r.returncode


def do_pull():
    checkpoint()
    rc = run(["git", "pull", "--rebase", "--autostash", "origin", "main"])
    if rc != 0:
        print("[!] pull не прошёл — разрули конфликт вручную.")
        sys.exit(1)
    print("[+] Подтянул свежую БД из репо.")


def do_push():
    checkpoint()
    # добавляем только те артефакты, что меняются от правки на ПК
    run(["git", "add", "outreach.db", "gordon_run.log",
         "status.json", "gordon_send_state.json"])
    # проверяем, есть ли что коммитить
    r = subprocess.run(["git", "diff", "--cached", "--quiet"],
                       cwd=HERE, capture_output=True, text=True)
    if r.returncode == 0:
        print("[-] Нет локальных изменений для пуша.")
        return
    run(["git", "commit", "-m", "local: edit sites via PC"])
    rc = run(["git", "push", "origin", "main"])
    if rc != 0:
        print("[!] push не прошёл — возможно, Actions успел записать. Сделай pull, потом push.")
        sys.exit(1)
    print("[+] Запушил правки в репо. Actions подхватит на следующем часе.")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("pull", "push"):
        print("Использование: python sync.py [pull|push]")
        sys.exit(1)
    if sys.argv[1] == "pull":
        do_pull()
    else:
        do_push()


if __name__ == "__main__":
    main()
