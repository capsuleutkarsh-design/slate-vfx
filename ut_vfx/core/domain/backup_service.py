import time
import subprocess
import logging
import sys
from pathlib import Path
from PySide6.QtCore import QThread

class AutoBackupThread(QThread):
    """
    Background service that automatically runs the database backup script.
    """
    def __init__(self, interval_hours=12, parent=None):
        super().__init__(parent)
        self.interval_seconds = interval_hours * 3600
        self.running = True
        
        # Determine paths
        current_file = Path(__file__).resolve()
        # core/domain/backup_service.py -> core/domain -> core -> ut_vfx
        ut_vfx_root = current_file.parent.parent.parent
        self.script_path = ut_vfx_root / "scripts" / "backup_db.py"

    def run(self):
        logging.info(f"AutoBackupThread started. Interval: {self.interval_seconds / 3600} hours.")
        
        # Initial wait so it doesn't slow down startup (wait 5 minutes)
        for _ in range(300):
            if not self.running: return
            time.sleep(1)
            
        while self.running:
            self._run_backup()
            
            # Sleep for the interval
            for _ in range(int(self.interval_seconds)):
                if not self.running: return
                time.sleep(1)

    def _run_backup(self):
        if not self.script_path.exists():
            logging.error(f"Backup script not found at {self.script_path}")
            return
            
        logging.info("AutoBackupThread: Launching background backup script...")
        try:
            # We use subprocess to run it isolated from the main process memory/DB locks
            # Use sys.executable to use the same python interpreter
            subprocess.run(
                [sys.executable, str(self.script_path)], 
                capture_output=True, 
                check=True
            )
            logging.info("AutoBackupThread: Backup completed successfully.")
        except subprocess.CalledProcessError as e:
            logging.error(f"AutoBackupThread: Backup script failed: {e.stderr.decode('utf-8', errors='ignore')}")
        except Exception as e:
            logging.error(f"AutoBackupThread: Failed to launch backup script: {e}")

    def stop(self):
        self.running = False
        self.wait()
