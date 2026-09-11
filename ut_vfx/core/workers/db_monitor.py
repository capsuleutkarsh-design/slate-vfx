from PySide6.QtCore import QThread, Signal
import time
import logging
from ut_vfx.core.infra.database_manager import database_manager

logger = logging.getLogger(__name__)

class DatabaseMonitor(QThread):
    """
    Background thread to monitor database connection status.
    Emits connection_status(is_connected, latency_ms).
    """
    connection_status = Signal(bool, float)
    
    def __init__(self, interval_sec=5):
        super().__init__()
        self.interval = interval_sec
        self.running = True
        self.db_manager = database_manager
        
    def run(self):
        logger.info(f"DatabaseMonitor started (Interval: {self.interval}s)")
        while self.running:
            try:
                start_time = time.time()
                # Simple ping: execute SELECT 1
                # Simple ping: execute SELECT 1
                result = self.db_manager.execute_query("SELECT 1 as status", fetch="all")
                
                latency = (time.time() - start_time) * 1000.0 # ms
                
                # Check for list of dicts (RealDictCursor) or standard list/tuple
                if result and len(result) > 0:
                    self.connection_status.emit(True, latency)
                else:
                    self.connection_status.emit(False, 0.0)
                    
            except Exception as e:
                logger.warning("DB Monitor Ping Failed: %s [%s]", e, self.db_manager.runtime_context_summary())
                self.connection_status.emit(False, 0.0)
                
            # Sleep in short bursts to allow quick stop
            for _ in range(self.interval):
                if not self.running: break
                time.sleep(1)
                
    def stop(self, timeout_ms=3000):
        self.running = False
        if not self.wait(timeout_ms):
            logger.warning("DatabaseMonitor did not stop in time; continuing shutdown without force terminate.")
