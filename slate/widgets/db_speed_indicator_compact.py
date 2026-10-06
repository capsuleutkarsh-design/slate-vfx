"""
The database latency dot in the header: [●] 12 ms

It shows what the main window's DatabaseMonitor measures on its own thread
(set_status). It used to run its own "SELECT 1" from a timer on the UI thread
every five seconds, so a slow or unreachable server froze the whole window for
the connection timeout, every five seconds.
"""

from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout
from PySide6.QtGui import QColor
import logging
from slate.core.infra.gate import Gate

logger = logging.getLogger(__name__)


class DBSpeedIndicatorCompact(QWidget):
    """
    Ultra-compact version: [●] 12 ms
    Dot colour and a word in the tooltip say how quick the database is.
    """

    EXCELLENT = 10
    GOOD = 50
    FAIR = 100
    SLOW = 200

    def __init__(self, parent=None):
        super().__init__(parent)
        self.baseline_ms = None
        self.current_ms = 0.0
        self.connected = None
        self.current_color = QColor(Gate.TEXT_DIM)
        self.setup_ui()
        self.setToolTip("Database speed: checking…")

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(4)

        self.dot_label = QLabel("●")
        self.dot_label.setStyleSheet(f"font-size: 14px; color: {Gate.TEXT_DIM};")

        self.speed_label = QLabel("--")
        self.speed_label.setStyleSheet(f"font-size: 11px; color: {Gate.TEXT_2};")

        layout.addWidget(self.dot_label)
        layout.addWidget(self.speed_label)

        self.setMaximumHeight(20)
        self.setMaximumWidth(80)

    # ------------------------------------------------------------ levels
    @classmethod
    def level(cls, ms: float):
        """(word, colour) for a latency. Each band has its own colour."""
        if ms < cls.EXCELLENT:
            return "Excellent", Gate.OK
        if ms < cls.GOOD:
            return "Good", Gate.mix(Gate.OK, Gate.WARN, 0.35)
        if ms < cls.FAIR:
            return "Fair", Gate.WARN
        if ms < cls.SLOW:
            return "Slow", Gate.mix(Gate.WARN, Gate.BAD, 0.5)
        return "Very slow", Gate.BAD

    @staticmethod
    def format_ms(ms: float) -> str:
        return "<1 ms" if ms < 1 else f"{ms:.0f} ms"

    # ------------------------------------------------------------ input
    def set_status(self, is_connected: bool, latency_ms: float = 0.0):
        """What the DatabaseMonitor found (connected, round trip in ms)."""
        self.connected = bool(is_connected)
        if not is_connected:
            self.show_error()
            return
        ms = max(0.0, float(latency_ms or 0.0))
        self.current_ms = ms
        if self.baseline_ms is None:
            self.baseline_ms = ms
        self.update_display(ms)
        self.update_tooltip()

    def update_display(self, ms):
        _word, color = self.level(ms)
        self.current_color = QColor(color)
        self.dot_label.setStyleSheet(f"font-size: 14px; color: {color};")
        self.speed_label.setText(self.format_ms(ms))
        self.speed_label.setStyleSheet(f"font-size: 11px; color: {color};")

    def show_error(self):
        self.dot_label.setStyleSheet(f"font-size: 14px; color: {Gate.BAD};")
        self.speed_label.setText("Offline")
        self.speed_label.setStyleSheet(f"font-size: 11px; color: {Gate.BAD};")
        # The tooltip used to keep the last good reading.
        self.setToolTip("Can't reach the studio database.\n"
                        "Slate keeps trying every few seconds.")

    def update_tooltip(self):
        word, _colour = self.level(self.current_ms)
        lines = [f"Database speed: {word}",
                 f"Last check: {self.format_ms(self.current_ms)}"]
        if self.baseline_ms:
            ratio = self.current_ms / self.baseline_ms
            trend = "faster" if ratio < 0.9 else "slower" if ratio > 1.1 else "about the same"
            lines.append(f"Since start-up: {trend} (first check {self.format_ms(self.baseline_ms)})")
        lines += ["",
                  "Excellent  under 10 ms",
                  "Good       under 50 ms",
                  "Fair       under 100 ms",
                  "Slow       under 200 ms",
                  "Very slow  200 ms or more"]
        self.setToolTip("\n".join(lines))
