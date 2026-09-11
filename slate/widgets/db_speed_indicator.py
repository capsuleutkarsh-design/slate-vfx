"""
Minimal Database Speed Indicator Widget
Shows database connection speed in header
"""

from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout
from PySide6.QtCore import QTimer
import time
from slate.core.infra.database_manager import database_manager
import logging

logger = logging.getLogger(__name__)


class DBSpeedIndicator(QWidget):
    """
    Minimal database speed indicator for header
    Shows: DB: 12ms (Good) with color coding
    """
    
    # Speed thresholds (milliseconds)
    EXCELLENT = 10   # < 10ms = Excellent (green)
    GOOD = 50        # < 50ms = Good (cyan)
    FAIR = 100       # < 100ms = Fair (yellow)
    SLOW = 200       # < 200ms = Slow (orange)
    # > 200ms = Critical (red)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.baseline_ms = None  # Will be set on first measurement
        self.current_ms = 0
        
        self.setup_ui()
        self.start_monitoring()
    
    def setup_ui(self):
        """Create minimal UI"""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(4)
        
        # DB icon/label
        self.db_label = QLabel("DB:")
        self.db_label.setStyleSheet("color: #87857F; font-size: 11px;")
        
        # Speed label
        self.speed_label = QLabel("--ms")
        self.speed_label.setStyleSheet("font-weight: bold; font-size: 11px;")
        
        # Status label (Excellent/Good/Fair/Slow/Critical)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-size: 10px; font-style: italic;")
        
        layout.addWidget(self.db_label)
        layout.addWidget(self.speed_label)
        layout.addWidget(self.status_label)
        
        # Compact size
        self.setMaximumHeight(24)
        self.setMaximumWidth(150)
    
    def start_monitoring(self):
        """Start periodic speed checks"""
        # Initial check
        self.measure_speed()
        
        # Check every 5 seconds
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.measure_speed)
        self.timer.start(5000)  # 5 seconds
    
    def measure_speed(self):
        """Measure database query speed"""
        try:
            db = database_manager
            
            # Simple lightweight query
            start = time.perf_counter()
            db.execute_query("SELECT 1")
            end = time.perf_counter()
            
            # Calculate milliseconds
            ms = (end - start) * 1000
            self.current_ms = ms
            
            # Set baseline on first measurement
            if self.baseline_ms is None:
                self.baseline_ms = ms
            
            # Update UI
            self.update_display(ms)
            
        except Exception as e:
            logger.error(f"DB speed check failed: {e}")
            self.show_error()
    
    def update_display(self, ms):
        """Update display with speed and color"""
        # Format speed
        self.speed_label.setText(f"{ms:.0f}ms")
        
        # Determine status and color
        if ms < self.EXCELLENT:
            status = "⚡"  # Lightning bolt
            color = "#5FBF8F"  # Bright green
        elif ms < self.GOOD:
            status = "✓"
            color = "#3EA8BF"  # Cyan
        elif ms < self.FAIR:
            status = "○"
            color = "#D9A441"  # Yellow
        elif ms < self.SLOW:
            status = "⚠"
            color = "#D9A441"  # Orange
        else:
            status = "✗"
            color = "#D9635F"  # Red
        
        # Show comparison to baseline if available
        if self.baseline_ms and self.baseline_ms > 0:
            ratio = ms / self.baseline_ms
            if ratio > 1.5:
                status += " ↓"  # Slower than baseline
            elif ratio < 0.75:
                status += " ↑"  # Faster than baseline
        
        self.status_label.setText(status)
        self.speed_label.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 11px;")
    
    def show_error(self):
        """Show error state"""
        self.speed_label.setText("ERR")
        self.speed_label.setStyleSheet("color: #D9635F; font-weight: bold; font-size: 11px;")
        self.status_label.setText("✗")
    
    def get_tooltip_text(self):
        """Generate detailed tooltip"""
        if self.baseline_ms:
            return (
                f"Database Response Time\n"
                f"Current: {self.current_ms:.1f}ms\n"
                f"Baseline: {self.baseline_ms:.1f}ms\n"
                f"Ratio: {self.current_ms/self.baseline_ms:.2f}x"
            )
        else:
            return f"Database Response Time: {self.current_ms:.1f}ms"
    
    def update_tooltip(self):
        """Update tooltip with current stats"""
        self.setToolTip(self.get_tooltip_text())
