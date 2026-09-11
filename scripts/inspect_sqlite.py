import sqlite3
import os

DB_PATH = r".\DB\ut_vfx.db"

if not os.path.exists(DB_PATH):
    print(f"DB not found at {DB_PATH}")
    exit()

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()

print("Tables in SQLite:")
for t in tables:
    print(f"- {t[0]}")
    # Also print row count
    c = cursor.execute(f"SELECT COUNT(*) FROM {t[0]}")
    print(f"  Rows: {c.fetchone()[0]}")

conn.close()
