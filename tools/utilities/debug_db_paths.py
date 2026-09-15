import sqlite3
import os

# Was one studio's drive letter. Given as an argument, or taken from where
# this machine's settings say the studio folder is.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if len(sys.argv) > 1:
    db_path = sys.argv[1]
else:
    from slate.core.infra.global_config import GlobalConfig
    db_path = str(Path(GlobalConfig.server_root()) / "Database" / "slate.db")

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
