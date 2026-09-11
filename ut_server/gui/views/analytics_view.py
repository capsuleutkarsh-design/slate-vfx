from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                               QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QAbstractItemView, QGridLayout, QDialog, QPushButton)
from PySide6.QtCore import Qt, Signal
import psycopg2
from ..design_system import C, T

class StatCard(QFrame):
    clicked = Signal(str)
    
    def __init__(self, title, initial_value="-", icon="📊", parent=None):
        super().__init__(parent)
        self.title = title
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {C.BG_SURFACE};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 12px;
            }}
            QFrame:hover {{
                background-color: {C.BG_SURFACE_HOVER};
                border: 1px solid {C.ACCENT_PRIMARY};
            }}
        """)
        self.setFixedSize(220, 120)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)
        
        # Header
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        lbl_icon = QLabel(icon)
        lbl_icon.setStyleSheet("font-size: 18px; border: none; background: transparent;")
        
        lbl_title = QLabel(title)
        lbl_title.setStyleSheet(f"font-size: 14px; font-weight: {T.WEIGHT_SEMI}; color: {C.TEXT_SECONDARY}; border: none; background: transparent;")
        
        header_layout.addWidget(lbl_icon)
        header_layout.addWidget(lbl_title)
        header_layout.addStretch()
        
        layout.addLayout(header_layout)
        
        # Value
        self.lbl_value = QLabel(initial_value)
        self.lbl_value.setStyleSheet(f"font-size: 32px; font-weight: {T.WEIGHT_BOLD}; color: {C.TEXT_PRIMARY}; border: none; background: transparent;")
        layout.addWidget(self.lbl_value)
        layout.addStretch()

    def set_value(self, value):
        self.lbl_value.setText(str(value))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.title)
        super().mousePressEvent(event)

class DataViewerDialog(QDialog):
    def __init__(self, title, query, port, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(800, 500)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {C.BG_ROOT}; }}
            QLabel {{ color: {C.TEXT_PRIMARY}; font-family: {T.FAMILY}; font-size: 18px; font-weight: {T.WEIGHT_BOLD}; }}
            QTableWidget {{
                background-color: {C.BG_SURFACE};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 8px;
                color: {C.TEXT_PRIMARY};
                font-family: {T.FAMILY};
                font-size: 13px;
                alternate-background-color: {C.BG_ROOT};
            }}
            QHeaderView::section {{
                background-color: {C.BG_ROOT};
                color: {C.TEXT_SECONDARY};
                font-weight: {T.WEIGHT_BOLD};
                padding: 8px;
                border: none;
                border-bottom: 1px solid {C.BORDER_DEFAULT};
            }}
            QTableWidget::item {{
                padding: 8px;
                border-bottom: 1px solid {C.BORDER_DEFAULT};
            }}
            QPushButton {{
                background-color: {C.ACCENT_PRIMARY};
                color: white;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: {T.WEIGHT_BOLD};
            }}
            QPushButton:hover {{
                background-color: #3EA8BF;
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        lbl = QLabel(title)
        layout.addWidget(lbl)
        
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        
        layout.addWidget(self.table)
        
        btn_close = QPushButton("Close")
        btn_close.setFixedWidth(100)
        btn_close.clicked.connect(self.close)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)
        
        self.load_data(query, port)
        
    def load_data(self, query, port):
        try:
            from ut_server.core.db_credentials import connect_kwargs
            conn = psycopg2.connect(**connect_kwargs(port, connect_timeout=2))
            with conn.cursor() as cur:
                cur.execute(query)
                rows = cur.fetchall()
                col_names = [desc[0] for desc in cur.description]
                
                self.table.setColumnCount(len(col_names))
                self.table.setHorizontalHeaderLabels([c.replace("_", " ").title() for c in col_names])
                
                self.table.setRowCount(len(rows))
                for r_idx, row in enumerate(rows):
                    for c_idx, val in enumerate(row):
                        item = QTableWidgetItem(str(val) if val is not None else "")
                        self.table.setItem(r_idx, c_idx, item)
            conn.close()
            self.table.resizeColumnsToContents()
        except Exception as e:
            self.table.setColumnCount(1)
            self.table.setRowCount(1)
            self.table.setHorizontalHeaderLabels(["Error"])
            self.table.setItem(0, 0, QTableWidgetItem(str(e)))


class AnalyticsView(QWidget):
    """
    Analytics Dashboard to view database stats and live connections.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        
    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(40, 40, 40, 40)
        main_layout.setSpacing(30)
        
        # --- HEADER ---
        lbl_title = QLabel("Database Analytics")
        lbl_title.setStyleSheet(f"font-size: 28px; font-weight: {T.WEIGHT_BOLD}; color: {C.TEXT_PRIMARY};")
        main_layout.addWidget(lbl_title)
        
        # --- STAT CARDS GRID ---
        cards_layout = QGridLayout()
        cards_layout.setSpacing(20)
        
        self.card_connections = StatCard("Active PCs", "0", "💻")
        self.card_projects = StatCard("Total Projects", "-", "📁")
        self.card_assets = StatCard("Stock Assets", "-", "🎞️")
        self.card_load = StatCard("DB Load", "0%", "🔥")
        self.card_db_size = StatCard("Database Size", "-", "🗄️")
        self.card_cpu = StatCard("CPU Usage", "0%", "⚙️")
        self.card_ram = StatCard("RAM Usage", "0%", "🧠")
        
        cards_layout.addWidget(self.card_connections, 0, 0)
        cards_layout.addWidget(self.card_projects, 0, 1)
        cards_layout.addWidget(self.card_assets, 0, 2)
        cards_layout.addWidget(self.card_load, 0, 3)
        cards_layout.addWidget(self.card_db_size, 1, 0)
        cards_layout.addWidget(self.card_cpu, 1, 1)
        cards_layout.addWidget(self.card_ram, 1, 2)
        
        main_layout.addLayout(cards_layout)
        
        # --- TABLE HEADER ---
        lbl_table_title = QLabel("Live Connected Clients")
        lbl_table_title.setStyleSheet(f"font-size: 18px; font-weight: {T.WEIGHT_SEMI}; color: {C.TEXT_PRIMARY}; margin-top: 20px;")
        main_layout.addWidget(lbl_table_title)
        
        # --- CONNECTIONS TABLE ---
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Client IP", "Application", "State", "Query"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {C.BG_SURFACE};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 8px;
                color: {C.TEXT_PRIMARY};
                font-family: {T.FAMILY};
                font-size: 13px;
                alternate-background-color: {C.BG_ROOT};
            }}
            QHeaderView::section {{
                background-color: {C.BG_ROOT};
                color: {C.TEXT_SECONDARY};
                font-weight: {T.WEIGHT_BOLD};
                padding: 8px;
                border: none;
                border-bottom: 1px solid {C.BORDER_DEFAULT};
            }}
            QTableWidget::item {{
                padding: 8px;
                border-bottom: 1px solid {C.BORDER_DEFAULT};
            }}
        """)
        
        main_layout.addWidget(self.table)
        
    def update_table(self, connections_data):
        """Update the data grid with fresh connections"""
        self.table.setRowCount(len(connections_data))
        for row, data in enumerate(connections_data):
            # data is a tuple: (client_addr, application_name, state, query)
            for col, value in enumerate(data):
                item = QTableWidgetItem(str(value) if value else "")
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                
                # Colorize state
                if col == 2:  # State column
                    if value == 'active':
                        item.setForeground(Qt.GlobalColor.green)
                    elif value == 'idle':
                        item.setForeground(Qt.GlobalColor.gray)
                        
                self.table.setItem(row, col, item)
