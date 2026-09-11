import sys
import os
import time
import logging
from PySide6.QtCore import QCoreApplication

# Add project root to sys.path
sys.path.append(os.getcwd())

from ut_vfx.core.infra.database_manager import DatabaseManager
# DISABLED: Legacy vfx_dashboard archived 2026-01-16
# from ut_vfx.vfx_dashboard.core.poll_worker import PollWorker
# Manual test needs updating for new architecture

# Flag to signal success
success = False

def run_test():
    global success
    app = QCoreApplication(sys.argv)
    
    print("--- 1. Setup ---")
    db_mgr = DatabaseManager()
    project_code = "TEST_POLL"
    
    # Initialize DB
    with db_mgr.get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO tracking_projects (code, name) VALUES (?, ?)", (project_code, "Poll Test"))
        conn.execute("DELETE FROM tracking_shots WHERE project_code=?", (project_code,))
        conn.execute("INSERT INTO tracking_shots (project_code, shot_name, status, last_updated) VALUES (?, ?, ?, datetime('now', '-1 hour'))", 
                     (project_code, "shot_01", "WIP"))
        conn.commit()

    print("--- 2. Start Worker ---")
    # Interval 1s for speed
    worker = PollWorker(project_code, db_mgr, interval=1000)
    
    def on_update():
        global success
        print("SUCCESS: Signal Received!")
        success = True
        worker.stop()
        app.quit()

    worker.updates_available.connect(on_update)
    worker.start()
    
    # Process events to let thread start
    app.processEvents()
    time.sleep(2) 
    
    print("--- 3. Trigger External Update ---")
    # Update DB timestamp
    with db_mgr.get_connection() as conn:
        conn.execute("UPDATE tracking_shots SET last_updated=datetime('now') WHERE project_code=?", (project_code,))
        conn.commit()
    print("DB Updated. Waiting for worker...")

    # Wait loop
    start = time.time()
    while time.time() - start < 5:
        app.processEvents()
        if success:
            break
        time.sleep(0.1)
        
    worker.stop()
    if success:
        print("Test Passed")
        sys.exit(0)
    else:
        print("Test Failed: Timeout waiting for signal")
        sys.exit(1)

if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    run_test()
