import logging
"""Diagnostic check of stock_library database."""
logging.info("Script starting...", flush=True)

import psycopg2
import keyring

pw = keyring.get_password("UTVFX", "db_password")

conn = psycopg2.connect(dbname="ut_vfx", user="postgres", password=pw,
                        host="172.16.1.45", port=5432, connect_timeout=5)
cur = conn.cursor()

# 1. Total count
cur.execute("SELECT COUNT(*) FROM stock_library")
logging.info(f"Total assets: {cur.fetchone()[0]}", flush=True)

# 2. Check table columns
cur.execute("""SELECT column_name, data_type 
               FROM information_schema.columns 
               WHERE table_name='stock_library' 
               ORDER BY ordinal_position""")
logging.info("\nTable columns:", flush=True)
for r in cur.fetchall():
    logging.info(f"  {r[0]}: {r[1]}", flush=True)

# 3. Check for NULL ingest_date
cur.execute("SELECT COUNT(*) FROM stock_library WHERE ingest_date IS NULL")
logging.info(f"\nAssets with NULL ingest_date: {cur.fetchone()[0]}", flush=True)

# 4. Check for NULL thumb_path
cur.execute("SELECT COUNT(*) FROM stock_library WHERE thumb_path IS NULL OR thumb_path = ''")
logging.info(f"Assets with NULL/empty thumb_path: {cur.fetchone()[0]}", flush=True)

# 5. Check for NULL file_name
cur.execute("SELECT COUNT(*) FROM stock_library WHERE file_name IS NULL OR file_name = ''")
logging.info(f"Assets with NULL/empty file_name: {cur.fetchone()[0]}", flush=True)

# 6. Check file_type distribution
cur.execute("SELECT file_type, COUNT(*) FROM stock_library GROUP BY file_type ORDER BY COUNT(*) DESC")
logging.info("\nBy file_type:", flush=True)
for r in cur.fetchall():
    logging.info(f"  {r[0] or 'NULL'}: {r[1]}", flush=True)

# 7. Check a sample of 5 rows
cur.execute("SELECT id, file_name, file_type, thumb_path IS NOT NULL as has_thumb, proxy_path IS NOT NULL as has_proxy, ingest_date FROM stock_library ORDER BY id DESC LIMIT 5")
logging.info("\nSample (5 newest):", flush=True)
for r in cur.fetchall():
    logging.info(f"  id={r[0]} name={r[1]} type={r[2]} thumb={r[3]} proxy={r[4]} date={r[5]}", flush=True)

# 8. Verify pagination works - simulate what the app does
cur.execute("SELECT COUNT(*) FROM stock_library ORDER BY ingest_date DESC LIMIT 50 OFFSET 0")
# Actually run the paginated query
cur.execute("SELECT id FROM stock_library ORDER BY ingest_date DESC LIMIT 50 OFFSET 0")
page1 = cur.fetchall()
logging.info(f"\nPage 1 (offset 0): {len(page1)} assets", flush=True)

cur.execute("SELECT id FROM stock_library ORDER BY ingest_date DESC LIMIT 50 OFFSET 50")
page2 = cur.fetchall()
logging.info(f"Page 2 (offset 50): {len(page2)} assets", flush=True)

cur.execute("SELECT id FROM stock_library ORDER BY ingest_date DESC LIMIT 50 OFFSET 11250")
page_last = cur.fetchall()
logging.info(f"Last page (offset 11250): {len(page_last)} assets", flush=True)

# 9. Check for duplicate file_paths
cur.execute("SELECT file_path, COUNT(*) FROM stock_library GROUP BY file_path HAVING COUNT(*) > 1 LIMIT 5")
dupes = cur.fetchall()
logging.info(f"\nDuplicate file_paths: {len(dupes)}", flush=True)
for d in dupes[:5]:
    logging.info(f"  {d[0]}: {d[1]} occurrences", flush=True)

conn.close()
logging.info("\nDone!", flush=True)
