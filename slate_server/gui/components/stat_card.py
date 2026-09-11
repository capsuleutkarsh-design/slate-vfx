from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt

from ..design_system import C, T

class StatCard(QWidget):
    """
    Glassmorphism statistics card for the dashboard.
    """
    def __init__(self, title, value, parent=None):
        super().__init__(parent)
        
        self.setStyleSheet(f"""
            StatCard {{
                background-color: {C.BG_SURFACE};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 12px;
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)
        
        lbl_title = QLabel(title.upper())
        lbl_title.setStyleSheet(f"color: {C.TEXT_SECONDARY}; font-size: 11px; font-weight: {T.WEIGHT_BOLD}; letter-spacing: 1px;")
        
        self.lbl_value = QLabel(str(value))
        self.lbl_value.setStyleSheet(f"color: {C.TEXT_PRIMARY}; font-size: 28px; font-weight: {T.WEIGHT_BOLD};")
        
        layout.addWidget(lbl_title)
        layout.addWidget(self.lbl_value)
        layout.addStretch()

    def set_value(self, value):
        self.lbl_value.setText(str(value))
