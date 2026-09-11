import sys
import os
import logging

RESULT_FILE = "verification_result.txt"

def log_msg(msg):
    print(msg, flush=True)
    with open(RESULT_FILE, "a") as f:
        f.write(str(msg) + "\n")

# Clear previous result
if os.path.exists(RESULT_FILE):
    os.remove(RESULT_FILE)

log_msg("Starting verification script...")

# Add project root to path
sys.path.append(os.getcwd())

try:
    log_msg("Importing DatabaseManager...")
    from ut_vfx.core.infra.database_manager import DatabaseManager
    log_msg("Import successful.")
except ImportError as e:
    log_msg(f"Import Error: {e}")
    sys.exit(1)

def check_stock_db():
    try:
        log_msg("Initializing DatabaseManager...")
        db = DatabaseManager()
        # Direct query for count
        log_msg("Executing query...")
        res = db.execute_query("SELECT COUNT(*) FROM stock_library", fetch="one")
        
        count = 0
        if res:
            # Postgres returns RealDictCursor, so access by key 'count' or index if tuple
            # SELECT COUNT(*) usually returns key 'count'
            count = res.get('count', 0)
            
        log_msg(f"STOCK_ASSET_COUNT: {count}")
        
    except Exception as e:
        log_msg(f"ERROR: {e}")

if __name__ == "__main__":
    check_stock_db()
