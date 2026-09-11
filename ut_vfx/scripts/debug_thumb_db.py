import logging
import sqlite3
import os
import sys
from pathlib import Path

# Important: make sure we can import GlobalConfig from the parent directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
try:
    from ut_vfx.core.infra.global_config import GlobalConfig
except ImportError:
    pass # Will fallback gracefully below

# Try to find the DB based on config or local app data
DB_PATHS = [
    str(GlobalConfig.server_root() / "Database" / "ut_vfx.db") if 'GlobalConfig' in locals() else "",
    os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local")), r"UTVFX\Database\ut_vfx.db")
]

target_db = None
for p in DB_PATHS:
    if os.path.exists(p):
        target_db = p
        break

logging.info(f"Target DB: {target_db}")

if not target_db:
    logging.info("No database found!")
    sys.exit(1)

try:
    conn = sqlite3.connect(target_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Count
    cursor.execute("SELECT count(*) FROM stock_library")
    count = cursor.fetchone()[0]
    logging.info(f"Total Assets: {count}")

    # Fetch rows with weird thumbs
    logging.info("\n--- CHECKING FOR 'None' STRINGS ---")
    cursor.execute("SELECT id, file_path, thumb_path FROM stock_library WHERE thumb_path = 'None'")
    bad_rows = cursor.fetchall()
    logging.info(f"Count of 'None' string paths: {len(bad_rows)}")
    if bad_rows:
        logging.info("Example Bad Row:", dict(bad_rows[0]))

    logging.info("\n--- FIRST 5 ASSETS ---")
    cursor.execute("SELECT id, file_path, thumb_path FROM stock_library LIMIT 5")
    rows = cursor.fetchall()
    for row in rows:
        t = row['thumb_path']
        logging.info(f"ID: {row['id']}")
        logging.info(f"File: {row['file_path']}")
        logging.info(f"Thumb: '{t}' (Type: {type(t)})")
        logging.info("-" * 20)
        
    conn.close()

except Exception as e:
    logging.exception(f"FAIL: {e}")
