from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QFrame, QSizePolicy
from PySide6.QtCore import Qt
from typing import List, Dict
import logging
from ..models.shot_model import Shot

class StatsWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.stat_containers: Dict[str, QFrame] = {}
        
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(8)
        
        # Sleek neon-style colors based on the reference
        self.status_colors = {
            'APPROVED': '#5FBF8F', # Neon Green
            'DONE': '#5FBF8F',
            'WIP': '#D9A441',      # Neon Yellow
            'YTS': '#3EA8BF',      # Light Blue
            'REVIEW': '#3EA8BF',   # Cyan
            'SENT FOR REVIEW': '#3EA8BF',
            'RETAKE': '#D9635F',   # Neon Red
            'SI': '#D9635F',
            'OMIT': '#B4B1AA',     # Gray
            'OMITTED': '#B4B1AA',
            'DEFAULT': '#5FC6DA'
        }
        
    def create_pill(self, label_text, count, color_hex):
        container = QFrame()
        # Pill styling: transparent background, subtle border, fully rounded
        container.setStyleSheet(f"""
            QFrame {{
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 12px;
                padding-left: 4px;
                padding-right: 4px;
            }}
        """)
        container.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Fixed)
        container.setFixedHeight(24)
        
        layout = QHBoxLayout(container)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(4)
        
        # A small colored dot to act as the icon
        dot = QLabel("•")
        dot.setStyleSheet(f"color: {color_hex}; font-size: 16px; font-weight: bold; background: transparent; border: none;")
        dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # The text combining count and label (e.g. "18 APPR")
        text_label = QLabel(f"{count} {label_text}")
        text_label.setStyleSheet(f"color: {color_hex}; font-size: 10px; font-weight: 700; background: transparent; border: none;")
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # If it's TOTAL, we don't need the dot, just grey text
        if label_text == "SHOTS":
            dot.hide()
            text_label.setStyleSheet("color: #B4B1AA; font-size: 10px; font-weight: 700; background: transparent; border: none;")
        
        layout.addWidget(dot)
        layout.addWidget(text_label)
        
        return container
        
    def update_stats(self, shots: List[Shot]):
        logging.debug(f"StatsWidget.update_stats called with {len(shots)} shots")
        status_counts = {}
        for s in shots:
            status = s.status.upper() if s.status else "UNKNOWN"
            if status == "SENT FOR REVIEW": status = "REVIEW"
            if status == "OMITTED": status = "OMIT"
            # Keep APPROVED as APPROVED instead of mapping to DONE
            status_counts[status] = status_counts.get(status, 0) + 1
        
        # Clear existing widgets.
        #
        # deleteLater() only schedules the deletion for the next trip round the
        # event loop. Taking the item out of the layout does not take the widget
        # off the screen, so the previous set of pills carried on painting at
        # their old positions while the new set was laid out over the top -
        # which is why the row showed two generations of counts on top of each
        # other. Unparenting removes them from the display immediately.
        while self.main_layout.count():
            item = self.main_layout.takeAt(0)
            old = item.widget()
            if old is not None:
                old.setParent(None)
                old.deleteLater()
        self.stat_containers.clear()
        
        # 1. Add Total Shots Pill
        total_count = len(shots)
        total_pill = self.create_pill("SHOTS", total_count, "#B4B1AA")
        self.main_layout.addWidget(total_pill)
        self.stat_containers["TOTAL"] = total_pill

        # Priority order for standard VFX workflow
        display_order = ['APPROVED', 'WIP', 'REVIEW', 'YTS', 'RETAKE', 'OMIT']
        
        # 2. Add Fixed Statuses
        for status in display_order:
            count = status_counts.get(status, 0)
            # Always display standard statuses even if 0, so users can see the metrics
            color = self.status_colors.get(status, self.status_colors['DEFAULT'])
            pill = self.create_pill(status, count, color)
            self.main_layout.addWidget(pill)
            self.stat_containers[status] = pill
                
        # 3. Add any custom statuses that weren't in the standard order
        for status, count in status_counts.items():
            if status not in display_order and count > 0:
                color = self.status_colors.get(status, self.status_colors['DEFAULT'])
                pill = self.create_pill(status, count, color)
                self.main_layout.addWidget(pill)
                self.stat_containers[status] = pill
