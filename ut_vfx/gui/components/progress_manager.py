"""
Progress Manager Component (Improvement #8)

Manages progress indicators for long-running operations.
Provides context manager interface for easy integration.
"""

from contextlib import contextmanager
from PySide6.QtCore import QObject, Signal


class ProgressManager(QObject):
    """Manages progress indicators for long-running operations"""
    
    progress_started = Signal(str, int)  # title, total
    progress_updated = Signal(int)  # current
    progress_finished = Signal()
    
    def __init__(self, parent_window):
        super().__init__()
        self.parent = parent_window
        self.overlay = None
        self.canceled = False
    
    @contextmanager
    def show_progress(self, title, total_steps, cancelable=True):
        """
        Context manager for showing progress during operations.
        
        Usage:
            with progress_mgr.show_progress("Scanning Files", 1000) as progress:
                for i, file in enumerate(files):
                    if progress.was_canceled():
                        break
                    process_file(file)
                    progress.update(i + 1)
        """
        from .progress_overlay import ProgressOverlay
        
        self.canceled = False
        self.overlay = ProgressOverlay(self.parent, title, total_steps, cancelable)
        self.overlay.canceled.connect(self._on_cancel)
        self.overlay.show()
        
        try:
            yield self.overlay
        finally:
            if self.overlay:
                self.overlay.hide()
                self.overlay.deleteLater()
                self.overlay = None
    
    def _on_cancel(self):
        """Handle cancel button click"""
        self.canceled = True
    
    def was_canceled(self):
        """Check if operation was canceled"""
        return self.canceled
