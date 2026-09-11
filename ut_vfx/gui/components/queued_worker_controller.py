"""
Queued worker controller for long-running QThread refresh cycles.

Behavior:
- If a refresh is requested while worker is running, queue one deferred run.
- On worker finished, run the queued refresh once.
- Provide a single shutdown path that stops worker and clears pending state.
"""

import logging

from PySide6.QtCore import QObject
from .qt_safety import safe_single_shot


class QueuedWorkerController(QObject):
    """Serializes refresh requests for a single QThread worker."""

    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self._worker = worker
        self._pending = False
        self._shutting_down = False
        self._worker.finished.connect(self._on_worker_finished)

    def request_refresh(self) -> bool:
        """
        Start worker now if idle, otherwise queue one deferred refresh.

        Returns True if worker started immediately, False if queued/skipped.
        """
        if self._shutting_down:
            return False

        if self._worker.isRunning():
            self._pending = True
            return False

        self._pending = False
        self._worker.start()
        return True

    def _on_worker_finished(self):
        if self._shutting_down:
            return

        if self._pending:
            self._pending = False
            safe_single_shot(0, self, self.request_refresh)

    def shutdown(self, timeout_ms: int = 3000):
        """Cancel queued refresh and stop worker thread gracefully."""
        self._shutting_down = True
        self._pending = False

        if self._worker.isRunning():
            self._worker.requestInterruption()
            if not self._worker.wait(timeout_ms):
                logging.warning(
                    "QueuedWorkerController: worker did not stop in %sms",
                    timeout_ms,
                )
