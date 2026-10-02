
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QSlider, QLabel, QLineEdit
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator
from slate.core.infra.gate import Gate

class NukeSlider(QWidget):
    """
    Nuke-style timeline slider with frame range labels and direct input.
    
    Layout: [Start] <-------|Handle|-------> [End] [Current]
    """
    
    valueChanged = Signal(int)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.min_frame = 1
        self.max_frame = 100
        self.current_frame = 1
        
        self.setup_ui()
        
    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        
        # Start Frame Label
        self.lbl_start = QLabel("1")
        self.lbl_start.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-weight: bold;")
        layout.addWidget(self.lbl_start)
        
        # The Slider
        self.slider = QSlider(Qt.Orientation.Horizontal)
        
        # CRITICAL: Set single step to 1 for frame-by-frame precision
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.setTickInterval(1)
        
        self.slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                border: 1px solid {Gate.RAISED_HI};
                height: 6px;
                background: {Gate.RAISED_HI};
                margin: 2px 0;
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: {Gate.TEXT_2};
                border: 1px solid {Gate.TEXT_DIM};
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }}
            QSlider::handle:horizontal:hover {{
                background: {Gate.TEXT};
                border: 1px solid {Gate.TEXT_2};
            }}
            QSlider::sub-page:horizontal {{
                background: {Gate.BAD}; /* UT Red/Orange accent */
                border-radius: 3px;
            }}
        """)
        self.slider.valueChanged.connect(self.on_slider_changed)

        layout.addWidget(self.slider)
        
        # End Frame Label
        self.lbl_end = QLabel("100")
        self.lbl_end.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-weight: bold;")
        layout.addWidget(self.lbl_end)
        
        # Current Frame Input
        self.input_current = QLineEdit("1")
        self.input_current.setFixedWidth(50)
        self.input_current.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.input_current.setStyleSheet(f"""
            QLineEdit {{
                background-color: {Gate.RAISED};
                color: {Gate.TEXT};
                border: 1px solid {Gate.LINE};
                border-radius: 3px;
                font-weight: bold;
            }}
            QLineEdit:focus {{
                border: 1px solid {Gate.BAD};
            }}
        """)
        self.input_current.setValidator(QIntValidator())
        self.input_current.returnPressed.connect(self.on_input_changed)
        
        # Connect slider pressed signal after input exists
        self.slider.sliderPressed.connect(self.input_current.clearFocus)
        layout.addWidget(self.input_current)
        
    def setRange(self, start, end):
        self.min_frame = start
        self.max_frame = end
        
        self.slider.setMinimum(start)
        self.slider.setMaximum(end)
        
        self.lbl_start.setText(str(start))
        self.lbl_end.setText(str(end))
        
    def setValue(self, value):
        # Update without triggering feedback loop
        value = max(self.min_frame, min(value, self.max_frame))
        self.current_frame = value
        
        block = self.slider.signalsBlocked()
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(block)
        
        self.input_current.setText(str(value))
        
    def value(self):
        return self.slider.value()
        
    def on_slider_changed(self, value):
        self.current_frame = value
        self.input_current.setText(str(value))
        self.valueChanged.emit(value)
        
    def on_input_changed(self):
        try:
            text = self.input_current.text()
            if not text:
                return
            val = int(text)
            val = max(self.min_frame, min(val, self.max_frame))
            
            if val != self.current_frame:
                self.setValue(val)
                self.valueChanged.emit(val)
            else:
                # If value is same but text was different (e.g. out of bounds), reset text
                self.input_current.setText(str(val))
                
        except ValueError:
            self.input_current.setText(str(self.current_frame))
            
    def setEnabled(self, enabled):
        super().setEnabled(enabled)
        self.slider.setEnabled(enabled)
        self.input_current.setEnabled(enabled)
