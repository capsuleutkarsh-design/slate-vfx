import uuid
import logging
from typing import Dict, Callable, Optional
from PySide6.QtCore import QObject, Signal

class TaskInfo:
    def __init__(self, task_id: str, name: str, description: str):
        self.task_id = task_id
        self.name = name
        self.description = description
        self.status = "Running"
        self.progress = 0
        self.cancel_hook: Optional[Callable[[], None]] = None
        self.pause_hook: Optional[Callable[[], None]] = None
        self.is_paused = False

class GlobalTaskRegistry(QObject):
    """
    Central registry for long-running QThread and QRunnable tasks.
    Allows the UI to subscribe to task statuses and issue cancel/pause signals.
    """
    task_added = Signal(TaskInfo)
    task_removed = Signal(str)
    task_updated = Signal(str)  # task_id

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(GlobalTaskRegistry, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        super().__init__()
        self._tasks: Dict[str, TaskInfo] = {}
        self._initialized = True

    def register_task(self, name: str, description: str = "") -> TaskInfo:
        task_id = str(uuid.uuid4())
        info = TaskInfo(task_id, name, description)
        self._tasks[task_id] = info
        self.task_added.emit(info)
        logging.info(f"Task Registered: {name} [{task_id}]")
        return info

    def update_progress(self, task_id: str, progress: int, status_text: str = None):
        if task_id in self._tasks:
            self._tasks[task_id].progress = progress
            if status_text:
                self._tasks[task_id].status = status_text
            self.task_updated.emit(task_id)

    def finish_task(self, task_id: str):
        if task_id in self._tasks:
            name = self._tasks[task_id].name
            del self._tasks[task_id]
            self.task_removed.emit(task_id)
            logging.info(f"Task Finished: {name} [{task_id}]")

    def cancel_task(self, task_id: str):
        if task_id in self._tasks:
            info = self._tasks[task_id]
            if info.cancel_hook:
                info.cancel_hook()
                info.status = "Cancelling..."
                self.task_updated.emit(task_id)
                logging.info(f"Task Cancel Requested: {info.name}")

    def toggle_pause_task(self, task_id: str):
        if task_id in self._tasks:
            info = self._tasks[task_id]
            if info.pause_hook:
                info.pause_hook()
                info.is_paused = not info.is_paused
                info.status = "Paused" if info.is_paused else "Running"
                self.task_updated.emit(task_id)

    def cancel_all_tasks(self):
        """Cancel all running tasks during application shutdown."""
        for task_id, info in list(self._tasks.items()):
            try:
                if info.cancel_hook:
                    info.cancel_hook()
            except Exception as e:
                logging.debug(f"Error cancelling task {task_id}: {e}")
        self._tasks.clear()

    def get_all_tasks(self) -> list:
        return list(self._tasks.values())

task_registry = GlobalTaskRegistry()
