from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, QPushButton
from PySide6.QtCore import Qt

from ..design_system import C, T
from ..components.toggle_switch import ToggleSwitch
from ..components.status_badge import StatusBadgeWidget
from ..components.stat_card import StatCard

class DashboardView(QWidget):
    """
    Main Dashboard View for UT Central Server.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        
    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(40, 40, 40, 40)
        main_layout.setSpacing(30)
        
        # --- HEADER ---
        header_layout = QHBoxLayout()
        
        title_layout = QVBoxLayout()
        title_layout.setSpacing(4)
        lbl_title = QLabel("Slate Server")
        lbl_title.setStyleSheet(f"font-size: 28px; font-weight: {T.WEIGHT_BOLD}; color: {C.TEXT_PRIMARY};")
        lbl_subtitle = QLabel("Database engine and master node")
        lbl_subtitle.setStyleSheet(f"font-size: 14px; color: {C.TEXT_SECONDARY};")
        
        title_layout.addWidget(lbl_title)
        title_layout.addWidget(lbl_subtitle)
        
        self.status_badge = StatusBadgeWidget("Server Offline", "error")
        
        header_layout.addLayout(title_layout)
        header_layout.addStretch()
        header_layout.addWidget(self.status_badge, 0, Qt.AlignTop)
        
        main_layout.addLayout(header_layout)
        
        # --- CONTROL PANEL ---
        control_panel = QWidget()
        control_panel.setStyleSheet(f"""
            QWidget {{
                background-color: {C.BG_SURFACE};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 12px;
            }}
        """)
        cp_layout = QHBoxLayout(control_panel)
        cp_layout.setContentsMargins(24, 24, 24, 24)
        
        lbl_power = QLabel("Database Server Power")
        lbl_power.setStyleSheet(f"font-size: 16px; font-weight: {T.WEIGHT_SEMI}; border: none;")
        
        self.toggle_power = ToggleSwitch()
        
        self.btn_force_kill = QPushButton("Force Kill Database")
        self.btn_force_kill.setStyleSheet(f"""
            QPushButton {{
                background-color: {C.STATUS_ERROR};
                color: {C.TEXT_PRIMARY};
                font-weight: {T.WEIGHT_BOLD};
                padding: 6px 12px;
                border-radius: 4px;
                border: none;
            }}
            QPushButton:hover {{
                background-color: #D9635F;
            }}
        """)
        
        self.btn_api_dashboard = QPushButton("Open Web Dashboard")
        self.btn_api_dashboard.setStyleSheet(f"""
            QPushButton {{
                background-color: {C.ACCENT_PRIMARY};
                color: white;
                font-weight: {T.WEIGHT_BOLD};
                padding: 6px 16px;
                border-radius: 4px;
                border: none;
            }}
            QPushButton:hover {{
                background-color: #3EA8BF;
            }}
            QPushButton:disabled {{
                background-color: {C.BORDER_DEFAULT};
            }}
        """)
        self.btn_api_dashboard.setEnabled(False)
        
        cp_layout.addWidget(lbl_power)
        cp_layout.addStretch()
        cp_layout.addWidget(self.btn_api_dashboard)
        cp_layout.addSpacing(10)
        cp_layout.addWidget(self.btn_force_kill)
        cp_layout.addSpacing(10)
        cp_layout.addWidget(self.toggle_power)
        
        main_layout.addWidget(control_panel)
        
        # --- STATS GRID ---
        grid_layout = QGridLayout()
        grid_layout.setSpacing(20)
        
        self.card_ip = StatCard("Server IP (LAN)", "127.0.0.1")
        self.card_port = StatCard("PostgreSQL Port", "-")
        self.card_users = StatCard("Active Connections", "0")
        
        grid_layout.addWidget(self.card_ip, 0, 0)
        grid_layout.addWidget(self.card_port, 0, 1)
        grid_layout.addWidget(self.card_users, 0, 2)
        
        main_layout.addLayout(grid_layout)
        
        # --- LOG CONSOLE ---
        log_panel = QWidget()
        log_panel.setStyleSheet(f"""
            QWidget {{
                background-color: {C.BG_ROOT};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 8px;
            }}
        """)
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(16, 16, 16, 16)
        
        lbl_log_title = QLabel("SYSTEM LOGS")
        lbl_log_title.setStyleSheet(f"color: {C.TEXT_SECONDARY}; font-size: 11px; font-weight: {T.WEIGHT_BOLD}; letter-spacing: 1px; border: none;")
        
        self.lbl_logs = QLabel("Waiting for server to start...")
        self.lbl_logs.setStyleSheet(f"font-family: Consolas, monospace; font-size: 12px; color: {C.TEXT_SECONDARY}; border: none;")
        self.lbl_logs.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        
        log_layout.addWidget(lbl_log_title)
        log_layout.addWidget(self.lbl_logs)
        log_layout.addStretch()
        
        main_layout.addWidget(log_panel, 1) # Give it stretch
