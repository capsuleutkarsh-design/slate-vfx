from PySide6.QtCore import QThread, Signal, QMutex
import logging

class PollWorker(QThread):
    """
    Background worker to poll the database for changes.
    Emits updates_available when it detects a newer 'last_updated' timestamp.
    """
    updates_available = Signal()
    
    def __init__(self, project_code, db_manager, interval=3000):
        super().__init__()
        self.project_code = project_code
        self.db_manager = db_manager
        self.interval = interval # milliseconds
        self.running = True
        self.last_known_timestamp = None
        self._mutex = QMutex()

    def _sleep_interruptibly(self, total_ms: int, step_ms: int = 100) -> bool:
        elapsed = 0
        while elapsed < total_ms:
            if not self.running or self.isInterruptionRequested():
                return False
            slice_ms = min(step_ms, total_ms - elapsed)
            self.msleep(slice_ms)
            elapsed += slice_ms
        return self.running and not self.isInterruptionRequested()
        
    def run(self):
        logging.info(f"PollWorker started for {self.project_code}")
        
        # Initial check to set baseline
        self.last_known_timestamp = self._get_max_timestamp()
        
        while self.running and not self.isInterruptionRequested():
            try:
                if not self._sleep_interruptibly(self.interval):
                    break
                    
                current_max = self._get_max_timestamp()
                
                if current_max and self.last_known_timestamp:
                    if current_max > self.last_known_timestamp:
                        logging.info(f"PollWorker: New data detected! Local={self.last_known_timestamp}, DB={current_max}")
                        self.last_known_timestamp = current_max
                        self.updates_available.emit()
                elif current_max and not self.last_known_timestamp:
                     self.last_known_timestamp = current_max
                     
            except Exception as e:
                logging.exception(f"PollWorker error: {e}")
                if not self._sleep_interruptibly(5000):  # Backoff
                    break
                
    def _get_max_timestamp(self):
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT MAX(last_updated) FROM tracking_shots WHERE project_code=%s", 
                    (self.project_code,)
                )
                result = cursor.fetchone()
                if result and result[0]:
                    return result[0]
        except Exception:
            # logging.exception(f"PollWorker query failed: {e}")
            pass
        return None

    def stop(self, timeout_ms: int = 2000):
        self.running = False
        self.requestInterruption()
        if not self.wait(timeout_ms):
            logging.warning("PollWorker did not stop in %sms for project %s", timeout_ms, self.project_code)
            return False
        return True
