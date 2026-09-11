"""
ULTRA-COMPACT Database Speed Indicator
Even more minimal version - just color dot + number
"""

from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
import time
from slate.core.infra.database_manager import database_manager
import logging

logger = logging.getLogger(__name__)


class DBSpeedIndicatorCompact(QWidget):
    """
    Ultra-compact version: [●] 12ms
    Dot color indicates speed, number shows latency
    """
    
    EXCELLENT = 10
    GOOD = 50
    FAIR = 100
    SLOW = 200
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.baseline_ms = None
        self.current_ms = 0
        self.current_color = QColor("#87857F")
        
        self.setup_ui()
        self.start_monitoring()
    
    def setup_ui(self):
        """Ultra-minimal UI"""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(4)
        
        # Colored dot indicator
        self.dot_label = QLabel("●")
        self.dot_label.setStyleSheet("font-size: 14px; color: #87857F;")
        
        # Speed number
        self.speed_label = QLabel("--")
        self.speed_label.setStyleSheet("font-size: 11px; color: #B4B1AA;")
        
        layout.addWidget(self.dot_label)
        layout.addWidget(self.speed_label)
        
        self.setMaximumHeight(20)
        self.setMaximumWidth(70)
    
    def start_monitoring(self):
        """Start speed monitoring"""
        self.measure_speed()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.measure_speed)
        self.timer.start(5000)
    
    def measure_speed(self):
        """Measure DB speed"""
        try:
            db = database_manager
            start = time.perf_counter()
            db.execute_query("SELECT 1")
            end = time.perf_counter()
            
            ms = (end - start) * 1000
            self.current_ms = ms
            
            if self.baseline_ms is None:
                self.baseline_ms = ms
            
            self.update_display(ms)
            self.update_tooltip()
            
        except Exception as e:
            logger.error(f"DB check failed: {e}")
            self.show_error()
    
    def update_display(self, ms):
        """Update dot color and number"""
        # Determine color
        if ms < self.EXCELLENT:
            color = "#5FBF8F"  # Green
        elif ms < self.GOOD:
            color = "#3EA8BF"  # Cyan
        elif ms < self.FAIR:
            color = "#D9A441"  # Yellow
        elif ms < self.SLOW:
            color = "#D9A441"  # Orange
        else:
            color = "#D9635F"  # Red
        
        self.current_color = QColor(color)
        
        # Update UI
        self.dot_label.setStyleSheet(f"font-size: 14px; color: {color};")
        self.speed_label.setText(f"{ms:.0f}ms")
        self.speed_label.setStyleSheet(f"font-size: 11px; color: {color};")
    
    def show_error(self):
        """Error state"""
        self.dot_label.setStyleSheet("font-size: 14px; color: #D9635F;")
        self.speed_label.setText("ERR")
        self.speed_label.setStyleSheet("font-size: 11px; color: #D9635F;")
    
    def update_tooltip(self):
        """Tooltip with details"""
        if self.baseline_ms:
            ratio = self.current_ms / self.baseline_ms
            trend = "↑ Faster" if ratio < 0.9 else "↓ Slower" if ratio > 1.1 else "→ Stable"
            
            tooltip = (
                f"Database Speed\n"
                f"━━━━━━━━━━━━━━\n"
                f"Current: {self.current_ms:.1f}ms\n"
                f"Baseline: {self.baseline_ms:.1f}ms\n"
                f"Status: {trend} ({ratio:.1f}x)\n\n"
                f"Color Guide:\n"
                f"● Green   < 10ms  (Excellent)\n"
                f"● Cyan    < 50ms  (Good)\n"
                f"● Yellow  < 100ms (Fair)\n"
                f"● Orange  < 200ms (Slow)\n"
                f"● Red     > 200ms (Critical)"
            )
        else:
            tooltip = f"Database Speed: {self.current_ms:.1f}ms"
        
        self.setToolTip(tooltip)
