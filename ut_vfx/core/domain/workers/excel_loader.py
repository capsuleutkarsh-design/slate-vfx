import pandas as pd
from PySide6.QtCore import QThread, Signal
import logging

class ExcelLoadWorker(QThread):
    """
    Worker thread to load Excel files without blocking the UI.
    """
    # Signal emits (DataFrame, error_message)
    # If successful: (df, None)
    # If failed: (None, error_message)
    finished_signal = Signal(object, str)

    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path

    def run(self):
        try:
            df = pd.read_excel(self.file_path)
            self.finished_signal.emit(df, None)
        except Exception as e:
            logging.exception(f"Failed to load Excel file: {e}")
            self.finished_signal.emit(None, str(e))

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()
