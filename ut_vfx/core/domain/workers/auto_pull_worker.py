"""
Auto-Pull Worker - Asynchronous Project Scanning
"""

from PySide6.QtCore import QThread, Signal
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class AutoPullWorker(QThread):
    """
    Worker thread to run AutoPullEngine scanning in background.
    """
    finished = Signal(list)  # Emits list of ReviewShot objects
    error = Signal(str)      # Emits error message

    def __init__(self, engine, project_path: Path):
        super().__init__()
        self.engine = engine
        self.project_path = project_path

    def run(self):
        try:
            logger.info(f"Starting auto-pull scan for: {self.project_path}")
            shots = self.engine.detect_shots(self.project_path)
            self.finished.emit(shots)
        except Exception as e:
            logger.error(f"Auto-pull scan failed: {e}", exc_info=True)
            self.error.emit(str(e))

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()
