import logging
import threading
from PySide6.QtCore import QObject, Signal
from ut_vfx.core.infra.sync_manager import SyncManager
from ut_vfx.core.infra.task_registry import task_registry

logger = logging.getLogger(__name__)

class SyncWorker(QObject):
    """
    Background worker to execute SyncManager logic and update GlobalTaskRegistry.
    """
    finished = Signal(bool)

    def __init__(self, db_manager):
        super().__init__()
        self.sync_manager = SyncManager(db_manager)
        self.task_info = None

    def start_sync(self):
        """Starts the sync process in a new thread."""
        self.task_info = task_registry.register_task(
            name="Database Sync",
            description="Synchronizing Offline Local Database with Server"
        )

        thread = threading.Thread(target=self._run_sync, daemon=True)
        thread.start()

    def _run_sync(self):
        try:
            success = self.sync_manager.trigger_sync(self.task_info)
            if success:
                task_registry.update_progress(self.task_info.task_id, 100, "Sync Complete")
            else:
                task_registry.update_progress(self.task_info.task_id, 100, "Sync Failed")
            self.finished.emit(success)
        except Exception as e:
            logger.exception(f"SyncWorker encountered error: {e}")
            if self.task_info:
                task_registry.update_progress(self.task_info.task_id, 100, f"Error: {e}")
            self.finished.emit(False)
        finally:
            if self.task_info:
                task_registry.finish_task(self.task_info.task_id)
