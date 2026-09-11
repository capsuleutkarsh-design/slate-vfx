from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFormLayout, QFrame
from PySide6.QtCore import Qt, Signal

from ..design_system import C, T

class SettingsView(QWidget):
    """
    Settings View for Slate Central Server.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        
    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(40, 40, 40, 40)
        main_layout.setSpacing(30)
        
        # --- HEADER ---
        lbl_title = QLabel("Server Configuration")
        lbl_title.setStyleSheet(f"font-size: 28px; font-weight: {T.WEIGHT_BOLD}; color: {C.TEXT_PRIMARY};")
        main_layout.addWidget(lbl_title)
        
        # --- SETTINGS FORM ---
        form_panel = QWidget()
        form_panel.setStyleSheet(f"""
            QWidget {{
                background-color: {C.BG_SURFACE};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 12px;
            }}
            QLineEdit {{
                background-color: {C.BG_ROOT};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 8px;
                color: {C.TEXT_PRIMARY};
                font-family: {T.FAMILY};
                font-size: 14px;
            }}
            QLineEdit:focus {{
                border: 1px solid {C.ACCENT_PRIMARY};
            }}
        """)
        form_layout = QFormLayout(form_panel)
        form_layout.setContentsMargins(24, 24, 24, 24)
        form_layout.setSpacing(20)
        
        # Database Path
        self.input_db_path = QLineEdit()
        self.input_db_path.setPlaceholderText("e.g. X:\\Extra\\UT_Central\\Database")
        self.input_db_path.setText("X:\\Extra\\UT_Central\\Database")
        
        lbl_db_path = QLabel("Database Root Path:")
        lbl_db_path.setStyleSheet(f"font-size: 14px; font-weight: {T.WEIGHT_SEMI}; color: {C.TEXT_SECONDARY};")
        
        form_layout.addRow(lbl_db_path, self.input_db_path)
        
        # Port
        self.input_port = QLineEdit()
        self.input_port.setText("5440")
        self.input_port.setFixedWidth(100)
        
        lbl_port = QLabel("PostgreSQL Port:")
        lbl_port.setStyleSheet(f"font-size: 14px; font-weight: {T.WEIGHT_SEMI}; color: {C.TEXT_SECONDARY};")
        
        form_layout.addRow(lbl_port, self.input_port)
        
        main_layout.addWidget(form_panel)
        
        # --- SAVE BUTTON ---
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.btn_firewall = QPushButton("Allow Firewall")
        self.btn_firewall.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_firewall.setFixedSize(140, 40)
        self.btn_firewall.setStyleSheet(f"""
            QPushButton {{
                background-color: {C.BG_SURFACE_HOVER};
                color: {C.TEXT_PRIMARY};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 6px;
                font-size: 14px;
                font-weight: {T.WEIGHT_BOLD};
            }}
            QPushButton:hover {{
                background-color: {C.BG_SURFACE};
            }}
        """)
        
        self.btn_save = QPushButton("Save Configuration")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.setFixedSize(180, 40)
        self.btn_save.setStyleSheet(f"""
            QPushButton {{
                background-color: {C.ACCENT_PRIMARY};
                color: {C.TEXT_PRIMARY};
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: {T.WEIGHT_BOLD};
            }}
            QPushButton:hover {{
                background-color: #3EA8BF;
            }}
            QPushButton:pressed {{
                background-color: #3EA8BF;
            }}
        """)
        
        btn_layout.addWidget(self.btn_firewall)
        btn_layout.addWidget(self.btn_save)
        main_layout.addLayout(btn_layout)
        
        main_layout.addSpacing(20)
        
        # --- UPDATE SECTION ---
        update_panel = QFrame()
        update_panel.setStyleSheet(f"""
            QFrame {{
                background-color: {C.BG_SURFACE};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 12px;
            }}
        """)
        update_layout = QHBoxLayout(update_panel)
        update_layout.setContentsMargins(24, 24, 24, 24)
        
        self.lbl_update_status = QLabel("Ready to check for updates.")
        self.lbl_update_status.setStyleSheet(f"font-size: 14px; color: {C.TEXT_SECONDARY}; border: none;")
        
        self.btn_check_update = QPushButton("Check for Updates")
        self.btn_check_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_check_update.setFixedSize(160, 40)
        self.btn_check_update.setStyleSheet(f"""
            QPushButton {{
                background-color: {C.BG_SURFACE};
                color: {C.TEXT_PRIMARY};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 6px;
                font-size: 14px;
                font-weight: {T.WEIGHT_BOLD};
            }}
            QPushButton:hover {{
                background-color: {C.BG_SURFACE_HOVER};
                border: 1px solid {C.BORDER_FOCUS};
            }}
        """)
        
        update_layout.addWidget(self.lbl_update_status)
        update_layout.addStretch()
        update_layout.addWidget(self.btn_check_update)
        
        main_layout.addWidget(update_panel)
        main_layout.addStretch()
