import sqlite3, time, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_engine as ae

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "outreach.db")
LOGF = os.path.join(HERE, "audit_refresh.log")

def log(msg):
    with open(LOGF, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

def main():
    if os.path.exists(LOGF):
        os.remove(LOGF)
    if not ae.ensure_cdp():
        log("Chromium недоступен — запустите Chrome на CDP 9222.")
        return
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "SELECT id,url,email,notes FROM sites "
        "WHERE status='pending' AND email IS NOT NULL AND email!='' "
        "ORDER BY id DESC"
    ).fetchall()
    total = len(rows)
    done = 0
    for i, row in enumerate(rows, 1):
        res = ae.audit_url(row["url"], check_forms=True)
        if res.get("skipped"):
            log(f"[{i}/{total}] #{row['id']}: Chromium недоступен — пропущен.")
            continue
        note = ae.bug_to_note(res)
        base = row["notes"] or ""
        keep = "\n".join(l for l in base.splitlines() if not l.startswith("AUDIT::"))
        new = (keep + "\n" + note).strip() if note else keep.strip()
        c.execute("UPDATE sites SET notes=? WHERE id=?", (new, row["id"]))
        c.commit()
        done += 1
        if note:
            b = res["bugs"][0]
            log(f"[{i}/{total}] #{row['id']}: {b['severity']} :: {b['where']}")
        else:
            log(f"[{i}/{total}] #{row['id']}: багов не найдено (обычное письмо)")
        time.sleep(1)
    c.close()
    log(f"DONE. обработано={total} записано_с_багом={done}")

if __name__ == "__main__":
    main()
