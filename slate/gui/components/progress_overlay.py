"""
Progress Overlay Widget (Improvement #8)

Semi-transparent overlay with progress bar and cancel button.
Displayed during long-running operations.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QProgressBar, QPushButton, QLabel
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QPalette, QColor


class ProgressOverlay(QWidget):
    """Semi-transparent progress overlay widget"""
    
    canceled = Signal()
    
    def __init__(self, parent, title, total, cancelable=True):
        super().__init__(parent)
        
        # Setup overlay styling
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Semi-transparent dark background
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 180))
        self.setAutoFillBackground(True)
        self.setPalette(palette)
        
        # Center on parent
        if parent:
            self.setGeometry(parent.rect())
        
        # Create UI
        self._canceled = False
        self._setup_ui(title, total, cancelable)
    
    def _setup_ui(self, title, total, cancelable):
        """Setup the progress UI"""
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Container widget with solid background
        container = QWidget()
        container.setStyleSheet("""
            QWidget {
                background-color: #26262D;
                border-radius: 10px;
                padding: 30px;
            }
        """)
        
        container_layout = QVBoxLayout(container)
        container_layout.setSpacing(15)
        
        # Title label
        title_label = QLabel(title)
        title_label.setStyleSheet("""
            font-size: 16pt;
            font-weight: bold;
            color: #3EA8BF;
        """)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(title_label)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(total)
        self.progress_bar.setMinimum(0)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p% (%v/%m)")
        self.progress_bar.setMinimumWidth(400)
        self.progress_bar.setMinimumHeight(30)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 2px solid #2C2C34;
                border-radius: 5px;
                text-align: center;
                font-size: 12pt;
                background-color: #16161A;
            }
            QProgressBar::chunk {
                background-color: #3EA8BF;
                border-radius: 3px;
            }
        """)
        container_layout.addWidget(self.progress_bar)
        
        # Status label
        self.status_label = QLabel("Processing...")
        self.status_label.setStyleSheet("font-size: 11pt; color: #87857F;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(self.status_label)
        
        # Cancel button (if cancelable)
        if cancelable:
            cancel_btn = QPushButton("Cancel")
            cancel_btn.setMinimumHeight(35)
            cancel_btn.setStyleSheet("""
                QPushButton {
                    background-color: #D9635F;
                    color: white;
                    font-size: 12pt;
                    font-weight: bold;
                    border-radius: 5px;
                    padding: 8px 20px;
                }
                QPushButton:hover {
                    background-color: #D9635F;
                }
            """)
            cancel_btn.clicked.connect(self._on_cancel)
            container_layout.addWidget(cancel_btn)
        
        container.setFixedWidth(500)
        layout.addWidget(container)
    
    def update(self, current):
        """Update progress bar value"""
        self.progress_bar.setValue(current)
        
        # Update percentage text
        total = self.progress_bar.maximum()
        if total > 0:
            percent = (current / total) * 100
            self.status_label.setText(f"Processing... {current} of {total} ({percent:.1f}%)")
    
    def set_status(self, text):
        """Update status label text"""
        self.status_label.setText(text)
    
    def _on_cancel(self):
        """Handle cancel button click"""
        self._canceled = True
        self.status_label.setText("Canceling...")
        self.canceled.emit()
    
    def was_canceled(self):
        """Check if operation was canceled"""
        return self._canceled
    
    def showEvent(self, event):
        """Override show to ensure proper positioning"""
        super().showEvent(event)
        if self.parent():
            self.setGeometry(self.parent().rect())
    
    def resizeEvent(self, event):
        """Keep overlay sized to parent"""
        super().resizeEvent(event)
        if self.parent():
            self.setGeometry(self.parent().rect())
