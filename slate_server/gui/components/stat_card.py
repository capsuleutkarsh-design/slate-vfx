from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy
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
        # The stylesheet asks for 28px and Qt then reserved exactly 28 pixels
        # for it, which is the height of the letters and not of the line - so
        # anything with a descender, or any machine with a slightly larger
        # system font, lost the bottom of the text. "49.8 MB" and "stopped"
        # were both cut in half on a studio screen.
        value_font = QFont(self.lbl_value.font())
        value_font.setPixelSize(28)
        value_font.setBold(True)
        self.lbl_value.setFont(value_font)
        self.lbl_value.setMinimumHeight(QFontMetrics(value_font).height() + 4)
        # The figure must not set the card's minimum width. A 28px IP address
        # asked for 250 pixels, four of them asked for a thousand, and the
        # dashboard then refused to fit any window narrower than that. The
        # grid decides how many columns fit; the card takes what it is given.
        self.lbl_value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        
        layout.addWidget(lbl_title)
        layout.addWidget(self.lbl_value)
        layout.addStretch()

    def set_value(self, value):
        self.lbl_value.setText(str(value))
