from PySide6.QtWidgets import (QDialog, QVBoxLayout, QTableWidget, 
                               QTableWidgetItem, QHeaderView, QPushButton, QLabel)
from ..utils.history import HistoryManager
from ut_vfx.core.infra.database_manager import database_manager
from ut_vfx.core.infra.design_tokens import ColorTokens as C, TypographyTokens as T

class HistoryDialog(QDialog):
    def __init__(self, project_code, shot_name=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Project History")
        self.setMinimumSize(600, 400)
        self.project_code = project_code
        self.shot_name = shot_name
        self.history_manager = HistoryManager(database_manager=database_manager)
        
        layout = QVBoxLayout(self)
        
        self.label = QLabel("Loading history...")
        self.label.setStyleSheet(f"font-size: {T.SIZE_XL}px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: {C.TEXT_SECONDARY};")
        layout.addWidget(self.label)
        
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Time", "User", "Field", "Old Value", "New Value"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)
        
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        layout.addWidget(self.close_btn)
        
        self.load_data()
        
    def load_data(self):
        history = []
        if hasattr(database_manager, "get_history"):
            history = database_manager.get_history(self.project_code, self.shot_name) or []
        if not history:
            history = self.history_manager.get_history(self.project_code, self.shot_name) or []

        self.label.setText(
            f"History records: {len(history)}"
            + (f" | Shot: {self.shot_name}" if self.shot_name else "")
        )
        self.table.setRowCount(len(history))
        
        for row_idx, row in enumerate(history):
            if isinstance(row, dict):
                timestamp = row.get("timestamp")
                user = row.get("user")
                field = row.get("field")
                old_val = row.get("old_value")
                new_val = row.get("new_value")
            else:
                try:
                    timestamp, user, field, old_val, new_val = row
                except Exception:
                    timestamp, user, field, old_val, new_val = ("", "", "", "", "")

            self.table.setItem(row_idx, 0, QTableWidgetItem(str(timestamp)))
            self.table.setItem(row_idx, 1, QTableWidgetItem(str(user or "Unknown")))
            self.table.setItem(row_idx, 2, QTableWidgetItem(str(field)))
            self.table.setItem(row_idx, 3, QTableWidgetItem(str(old_val)))
            self.table.setItem(row_idx, 4, QTableWidgetItem(str(new_val)))
            
        self.table.resizeColumnsToContents()
