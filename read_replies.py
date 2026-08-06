# -*- coding: utf-8 -*-
import sqlite3
import sys
import io

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

conn = sqlite3.connect('outreach.db')
conn.text_factory = lambda x: x.decode('utf-8', errors='replace')
c = conn.cursor()
c.execute("SELECT id, url, notes FROM sites WHERE status='replied'")
rows = c.fetchall()
conn.close()

for r in rows:
    id, url, notes = r
    print(f'=== ID {id}: {url} ===')
    if 'REPLY::' in notes:
        parts = notes.split('REPLY::')
        for i, p in enumerate(parts):
            if p.strip():
                lines = p.strip().split('\n')
                print(f'  [{i}]: {lines[0][:400]}')
    print()
