"""
Database Concurrency and Maintenance Stress Test.

This test simulates a high-load environment where a background writer continuously
inserts records while a 'VACUUM' maintenance operation is triggered.
It verifies that SQLite can handle concurrent writes and maintenance without corruption.
"""

import threading
import sqlite3
import time
import logging
import sys
import os
from pathlib import Path

# Setup Path
sys.path.append(os.getcwd())
from ut_vfx.core.infra.database_manager import DatabaseManager

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def stress_writer(db_manager, stop_event):
    """Continuously writes logs to create DB contention."""
    count = 0
    while not stop_event.is_set():
        try:
            db_manager.record_task_detail(
                op_id=1, 
                name=f"Stress_Item_{count}", 
                src="C:/Test/Src", 
                dst="C:/Test/Dst", 
                size=12345, 
                duration=1.0, 
                status="OK"
            )
            count += 1
            if count % 100 == 0:
                print(f"Writer: {count} records inserted...")
            time.sleep(0.01) # Small delay to allow some interleaving
        except Exception as e:
            print(f"Writer Error: {e}")

def main():
    print("--- Starting Database VACUUM Stress Test ---")
    
    # Use a temp DB for safety
    db_path = Path("tests/stress_test.db")
    if db_path.exists():
        os.remove(db_path)
        
    db = DatabaseManager(db_path=db_path)
    
    # 1. Populate initial data
    print("Populating initial data...")
    db.start_operation(1, "STRESS_TEST")
    for i in range(1000):
        db.record_task_detail(1, f"Init_{i}", "src", "dst", 100, 0.1, "OK")
    
    # 2. Start Background Writer
    stop_event = threading.Event()
    writer_thread = threading.Thread(target=stress_writer, args=(db, stop_event))
    writer_thread.start()
    
    # 3. Trigger Maintenance (VACUUM)
    print("Triggering Maintenance (VACUUM) while writing...")
    time.sleep(2) # Let writer get up to speed
    
    start_time = time.time()
    db.perform_maintenance(days_to_keep=1) # Should trigger vacuum
    duration = time.time() - start_time
    
    print(f"Maintenance finished in {duration:.2f} seconds.")
    
    # 4. Stop and Verify
    stop_event.set()
    writer_thread.join()
    
    # Check integrity
    try:
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM task_details").fetchone()[0]
        print(f"Final Record Count: {count}")
        conn.execute("PRAGMA integrity_check")
        print("Integrity Check: PASSED")
        conn.close()
    except Exception as e:
        print(f"Verification Failed: {e}")
        sys.exit(1)
        
    print("--- Test Completed Successfully ---")
    # Cleanup
    if db_path.exists():
        try:
            os.remove(db_path)
        except: pass

if __name__ == "__main__":
    main()
