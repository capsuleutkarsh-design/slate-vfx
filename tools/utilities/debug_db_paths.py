import sqlite3
import os

db_path = r"X:\Extra\UT_Central\Database\ut_vfx.db"

if not os.path.exists(db_path):
    print(f"DB not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("SELECT id, file_path, thumb_path FROM stock_library LIMIT 5")
rows = cursor.fetchall()
for row in rows:
    print(f"ID: {row[0]}")
    print(f"  File:  {row[1]}")
    print(f"  Thumb: {row[2]}")
    print("-" * 20)
conn.close()
