from PySide6.QtCore import QThread, Signal, QMutex
import logging

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else keeps the fallback it already had.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""

class PollWorker(QThread):
    """
    Background worker to poll the database for changes.
    Emits updates_available when it detects a newer 'last_updated' timestamp.
    """
    updates_available = Signal()
    
    def __init__(self, project_code, db_manager, interval=3000, skip_when=None):
        super().__init__()
        self.project_code = project_code
        self.db_manager = db_manager
        self.interval = interval # milliseconds
        self.running = True
        self.last_known_timestamp = None
        self._mutex = QMutex()
        # While this returns True (the change feed is working) the worker
        # sleeps without asking the database anything.
        self.skip_when = skip_when
        self._skipping = False

    def _should_skip(self) -> bool:
        if self.skip_when is None:
            return False
        try:
            return bool(self.skip_when())
        except Exception:
            return False

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

                if self._should_skip():
                    self._skipping = True
                    continue
                if self._skipping:
                    # The feed just stopped: whatever happened meanwhile was
                    # heard by nobody, so ask for one catch-up read.
                    self._skipping = False
                    self.last_known_timestamp = self._get_max_timestamp()
                    self.updates_available.emit()
                    continue

                current_max = self._get_max_timestamp()
                
                if current_max and self.last_known_timestamp:
                    if current_max != self.last_known_timestamp:
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
        """
        A fingerprint of the project's shots: (count, sum of versions). Every
        save bumps a version and a delete changes the count; neither depends on
        a workstation's clock (last_updated is each writer's own time, so a
        slow clock's saves went unnoticed).
        """
        try:
            row = self.db_manager.execute_query(
                "SELECT COUNT(*) AS n, COALESCE(SUM(version), 0) AS v FROM tracking_shots "
                "WHERE project_code=%s", (self.project_code,), fetch="one")
            if row:
                row = dict(row)
                return (int(row.get("n") or 0), int(row.get("v") or 0), 1)
        except DatabaseUnavailableError:
            raise
        except Exception:
            pass
        return None

    def stop(self, timeout_ms: int = 2000):
        self.running = False
        self.requestInterruption()
        if not self.wait(timeout_ms):
            logging.warning("PollWorker did not stop in %sms for project %s", timeout_ms, self.project_code)
            return False
        return True
