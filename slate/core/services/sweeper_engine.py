import logging

from abc import ABC, abstractmethod
from typing import List, Dict, Any
from PySide6.QtCore import QObject, Signal, QThread

logger = logging.getLogger(__name__)

class BaseSweeper(ABC):
    """
    Abstract Base Class for all Sweepers.
    Each sweeper handles a specific domain (Temp, Proxy, Logs).
    """
    def __init__(self, name: str, enabled: bool = True):
        self.name = name
        self.enabled = enabled
        self.last_run = 0.0

    @abstractmethod
    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Execute the sweep.
        Returns a stats dict: {'freed_bytes': int, 'files_deleted': int, 'errors': []}
        """
        pass

class SweeperEngine(QObject):
    """
    Sweeper Engine: Orchestra for system cleanup.
    Runs on a low-priority background thread.
    """
    sweep_started = Signal(str) # name
    sweep_finished = Signal(str, dict) # name, stats
    all_sweeps_complete = Signal(dict) # total_stats

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sweepers: List[BaseSweeper] = []
        self._thread = None
        self._is_running = False
        
        # Integration with Main Thread
        # We can trigger scans manually or per schedule
        
    def register_sweeper(self, sweeper: BaseSweeper):
        self.sweepers.append(sweeper)
        logger.info(f"Registered Sweeper: {sweeper.name}")

    def start_sweep(self, dry_run: bool = False):
        """Start the sweep process in background."""
        if self._is_running:
            logger.warning("Sweeper Engine already running.")
            return

        self._is_running = True
        
        self._thread = QThread()
        self._thread.run = lambda: self._run_process(dry_run)
        self._thread.finished.connect(lambda: setattr(self, '_is_running', False))
        self._thread.start()

    def stop(self):
        """Stop the sweeper thread gracefully."""
        self._is_running = False
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)

    def _run_process(self, dry_run):
        logger.info(f"Sweeper Engine Started (Dry Run: {dry_run})")
        
        total_freed = 0
        total_files = 0
        
        for sweeper in self.sweepers:
            if not sweeper.enabled:
                continue
                
            self.sweep_started.emit(sweeper.name)
            try:
                stats = sweeper.run(dry_run=dry_run)
                
                freed = stats.get('freed_bytes', 0)
                files = stats.get('files_deleted', 0)
                total_freed += freed
                total_files += files
                
                logger.info(f"   - {sweeper.name}: Freed {freed/1024/1024:.2f} MB ({files} files)")
                self.sweep_finished.emit(sweeper.name, stats)
                
            except Exception as e:
                logger.error(f"   - {sweeper.name} FAILED: {e}")
                
        self._is_running = False
        
        summary = {
            'total_freed_bytes': total_freed,
            'total_files_deleted': total_files,
            'dry_run': dry_run
        }
        self.all_sweeps_complete.emit(summary)
        logger.info(f"Sweeper Engine Complete. Freed {total_freed/1024/1024:.2f} MB")
