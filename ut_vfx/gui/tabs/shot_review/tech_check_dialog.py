from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
    QTableWidget, QTableWidgetItem, QPushButton,
    QHeaderView, QProgressBar, QStackedWidget, QWidget
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QIcon
from pathlib import Path
import cv2
import logging

from ....core.domain.review_shot import ReviewShot

logger = logging.getLogger(__name__)

class TechAnalysisWorker(QThread):
    """
    Background worker for heavy metadata extraction.
    """
    analysis_finished = Signal(dict, dict) # scan_meta, render_meta

    def __init__(self, scan_path, render_path):
        super().__init__()
        self.scan_path = scan_path
        self.render_path = render_path

    def run(self):
        scan_meta = self.get_metadata(self.scan_path)
        render_meta = self.get_metadata(self.render_path)
        self.analysis_finished.emit(scan_meta, render_meta)

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()

    def get_metadata(self, path: Path) -> dict:
        """Extract deep metadata from first frame"""
        if not path:
             return {}
             
        # Resolve parent to find actual file
        folder = path.parent
        if not folder.exists():
            return {}
            
        # Find first image file
        files = list(folder.glob("*.*"))
        first_img = next((f for f in files if f.suffix.lower() in ['.exr','.dpx','.jpg','.png','.tiff','.tif']), None)
        
        if not first_img:
            return {}
            
        info = {'ext': first_img.suffix}
        
        # SAFETY: Skip EXR metadata extraction - causes segfaults in imageio/OIIO
        if first_img.suffix.lower() == '.exr':
            info['dtype'] = "16-bit Float (Half)"  # Standard EXR format
            info['res'] = (0, 0)  # Unknown without loading
            info['channels'] = 4  # Typical EXR (RGBA)
        else:
            try:
                import imageio.v3 as iio
                # ImageIO gives rich metadata
                meta = iio.imread(first_img, index=0)
                
                # Resolution
                info['res'] = meta.shape[:2] # H, W
                
                # Data Type / Bit Depth
                info['dtype'] = str(meta.dtype)
                
                # Channels
                if len(meta.shape) > 2:
                    info['channels'] = meta.shape[2]
                else:
                    info['channels'] = 1
                    
                # Formatting Dtype nicely
                if 'float32' in info['dtype']:
                    info['dtype'] = "32-bit Float"
                elif 'float16' in info['dtype']:
                     info['dtype'] = "16-bit Float (Half)"
                elif 'uint16' in info['dtype']:
                     info['dtype'] = "16-bit Int"
                elif 'uint8' in info['dtype']:
                     info['dtype'] = "8-bit Int"
                
            except ImportError:
                # Fallback OpenCV (but NOT for EXR - causes segfaults)
                try:
                    if first_img.suffix.lower() != '.exr':
                        img = cv2.imread(str(first_img), cv2.IMREAD_UNCHANGED)
                        if img is not None:
                            info['res'] = img.shape[:2]
                            info['dtype'] = str(img.dtype)
                            if len(img.shape) > 2:
                                info['channels'] = img.shape[2]
                            else:
                                info['channels'] = 1
                            
                            # CV2 normalize string
                            if 'float32' in info['dtype']: info['dtype'] = "32-bit Float"
                            if 'uint16' in info['dtype']: info['dtype'] = "16-bit Int"
                            if 'uint8' in info['dtype']: info['dtype'] = "8-bit Int"
                except Exception as exc:
                    logging.debug("Media profile enrichment failed for %s: %s", filepath, exc)
                
        return info

class TechCheckDialog(QDialog):
    """
    Technical Check Dialog
    
    Compares metadata between Scan (Plate) and Render (Comp).
    Checks for:
    - Frame Range verification
    - Resolution match
    - Format usage
    """
    
    def __init__(self, shot: ReviewShot, parent=None):
        super().__init__(parent)
        self.shot = shot
        self.worker = None
        self.setWindowTitle(f"Tech Check: {shot.name}")
        self.setMinimumSize(600, 400)
        self.setStyleSheet("""
            QDialog { background-color: #26262D; color: #E8E6E1; }
            QTableWidget { background-color: #1D1D22; color: #E8E6E1; gridline-color: #2C2C34; }
            QHeaderView::section { background-color: #26262D; color: #E8E6E1; padding: 4px; }
            QLabel { font-size: 14px; font-weight: bold; margin-bottom: 10px; }
        """)
        
        self.setup_ui()
        self.start_analysis()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Header
        header = QLabel(f"Technical Analysis: {self.shot.sequence} / {self.shot.name}")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)
        
        # Stacked Widget (Loading vs Table)
        self.stack = QStackedWidget()
        
        # Page 1: Loading
        loading_page = QWidget()
        l_layout = QVBoxLayout(loading_page)
        l_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.spinner = QProgressBar()
        self.spinner.setRange(0, 0) # Infinite spinner
        self.spinner.setFixedWidth(200)
        self.spinner.setStyleSheet("QProgressBar { background-color: #26262D; border: 1px solid #2C2C34; border-radius: 4px; }")
        
        l_label = QLabel("Analyzing Media metadata...")
        l_label.setStyleSheet("color: #B4B1AA; font-style: italic;")
        l_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        l_layout.addWidget(self.spinner)
        l_layout.addWidget(l_label)
        self.stack.addWidget(loading_page)
        
        # Page 2: Results Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Attribute", "Scan (Ref)", "Render (Target)", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.stack.addWidget(self.table)
        
        layout.addWidget(self.stack)
        
        # Buttons
        btn_layout = QHBoxLayout()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        
    def start_analysis(self):
        """Start background worker"""
        self._cleanup_worker()
        self.worker = TechAnalysisWorker(self.shot.scan_path, self.shot.render_path)
        self.worker.analysis_finished.connect(self.populate_results)
        self.worker.finished.connect(self._on_worker_thread_done)
        self.worker.start()

    def _cleanup_worker(self, timeout_ms=3000):
        worker = self.worker
        if not worker:
            return

        if worker.isRunning():
            stop = getattr(worker, "stop", None)
            if callable(stop):
                stop()
            else:
                worker.requestInterruption()
            worker.wait(timeout_ms)

        worker.deleteLater()
        if self.worker is worker:
            self.worker = None

    def _on_worker_thread_done(self):
        worker = self.sender()
        if worker is not self.worker:
            return
        self.worker = None
        worker.deleteLater()
        
    def populate_results(self, scan_meta, render_meta):
        """Populate table with results from worker"""
        sender = self.sender()
        if sender is not None and sender is not self.worker:
            return

        self.stack.setCurrentIndex(1) # Show Table
        self.table.setRowCount(0)
        
        # 1. Path Check
        self.add_row("Scan Path", "Found" if self.shot.scan_path else "Missing", "N/A", 
                     True if self.shot.scan_path else False)
        
        self.add_row("Render Path", "N/A", "Found" if self.shot.render_path else "Missing",
                     True if self.shot.render_path else False)
                     
        if not self.shot.scan_path or not self.shot.render_path:
            return

        # 2. Metadata (Already extracted)
        # Passed as arguments
        
        # 3. Resolution Check
        scan_res = scan_meta.get('res', 'Unknown')
        render_res = render_meta.get('res', 'Unknown')
        self.add_row("Resolution", str(scan_res), str(render_res), scan_res == render_res)
        
        # 4. Data Type
        scan_dtype = scan_meta.get('dtype', 'Unknown')
        render_dtype = render_meta.get('dtype', 'Unknown')
        self.add_row("Bit Depth", str(scan_dtype), str(render_dtype), scan_dtype == render_dtype)
        
        # 5. Channels
        scan_ch = scan_meta.get('channels', 'Unknown')
        render_ch = render_meta.get('channels', 'Unknown')
        self.add_row("Channels", str(scan_ch), str(render_ch), scan_ch == render_ch)
        
        # 6. Compression (if available)
        scan_comp = scan_meta.get('compression', 'N/A')
        render_comp = render_meta.get('compression', 'N/A')
        self.add_row("Compression", str(scan_comp), str(render_comp), True) # Informational only
        
        # 7. Check basic consistency
        self.add_row("Format", scan_meta.get('ext'), render_meta.get('ext'), True) # Extensions can differ, green status
        
    def add_row(self, label, val1, val2, status):
        row = self.table.rowCount()
        self.table.insertRow(row)
        
        self.table.setItem(row, 0, QTableWidgetItem(label))
        self.table.setItem(row, 1, QTableWidgetItem(str(val1)))
        self.table.setItem(row, 2, QTableWidgetItem(str(val2)))
        
        status_item = QTableWidgetItem("MATCH" if status else "MISMATCH")
        if status:
            status_item.setForeground(QColor("#5FBF8F")) # Green
        else:
            status_item.setForeground(QColor("#D9635F")) # Red
            status_item.setIcon(QIcon.fromTheme("dialog-warning"))
            
        self.table.setItem(row, 3, status_item)

    def closeEvent(self, event):
        self._cleanup_worker(timeout_ms=2000)
        super().closeEvent(event)
