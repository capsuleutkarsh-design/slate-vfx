from PySide6.QtCore import QThread, Signal
import shutil
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class FileOperationWorker(QThread):
    """
    Generic worker for file operations to prevent blocking the Main Thread.
    """
    started_op = Signal(str)      # description
    finished_op = Signal(bool, str) # success, message/result_path
    
    def __init__(self, mode: str, src: str, dst: str):
        super().__init__()
        self.mode = mode # 'copy', 'move'
        self.src = src
        self.dst = dst
        
    def run(self):
        try:
            op_desc = f"{self.mode.title()}: {Path(self.src).name} -> {Path(self.dst).name}"
            self.started_op.emit(op_desc)
            
            src_path = Path(self.src)
            dst_path = Path(self.dst)
            
            if not src_path.exists():
                raise FileNotFoundError(f"Source file not found: {self.src}")
                
            if dst_path.exists():
                raise FileExistsError(f"Destination file already exists: {self.dst}")
                
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            
            if self.mode == 'copy':
                shutil.copy2(self.src, self.dst)
            elif self.mode == 'move':
                shutil.move(self.src, self.dst)
            else:
                raise ValueError(f"Unknown mode: {self.mode}")
                
            self.finished_op.emit(True, str(self.dst))
            
        except Exception as e:
            logger.error(f"FileOp Failed: {e}")
            self.finished_op.emit(False, str(e))

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()
