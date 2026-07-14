import sqlite3
c = sqlite3.connect("outreach.db")
c.row_factory = sqlite3.Row
n1 = c.execute("SELECT COUNT(*) FROM sites WHERE status='pending' AND email IS NOT NULL").fetchone()[0]
print("pending+email:", n1)
rows = c.execute(
    "SELECT * FROM sites WHERE status='pending' AND email IS NOT NULL "
    "AND id IN ("
    "  SELECT MIN(id) FROM sites "
    "  WHERE status='pending' AND email IS NOT NULL "
    "  GROUP BY LOWER(email)"
    ") ORDER BY id DESC LIMIT 200"
).fetchall()
print("send_now query rows:", len(rows))
for r in rows[:5]:
    print("  #%s %s -> %s" % (r["id"], r["url"], r["email"]))
c.close()
