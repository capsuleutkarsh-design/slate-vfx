from pathlib import Path
from PySide6.QtCore import QThread, Signal
from ut_vfx.utils.reporting import report_generator

class ReportWorker(QThread):
    finished_signal = Signal(bool, str)
    
    def __init__(self, output_path: Path, project_id: int = None):
        super().__init__()
        self.output_path = output_path
        self.project_id = project_id
        
    def run(self):
        try:
            success, msg = report_generator.generate_project_summary_report(self.output_path, self.project_id)
            self.finished_signal.emit(success, msg)
        except Exception as e:
            self.finished_signal.emit(False, str(e))
    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()
