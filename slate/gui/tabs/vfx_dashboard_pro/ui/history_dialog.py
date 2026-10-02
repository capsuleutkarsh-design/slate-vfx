from PySide6.QtWidgets import (QDialog, QVBoxLayout, QTableWidget, QDialogButtonBox,
                               QTableWidgetItem, QHeaderView, QPushButton, QLabel)
from slate.gui.core.table_style import style_table
from slate.gui.core.empty_state import EmptyState
from ..utils.history import HistoryManager
from slate.core.infra.database_manager import database_manager
from slate.core.infra.design_tokens import ColorTokens as C, TypographyTokens as T
from slate.gui.core.data_display import datetime_item

class HistoryDialog(QDialog):
    def __init__(self, project_code, shot_name=None, parent=None, shot_id=None, reel=None):
        super().__init__(parent)
        if shot_name:
            self.setWindowTitle(f"History - {shot_name}" + (f" ({reel})" if reel else ""))
        else:
            self.setWindowTitle(f"History - {project_code}")
        self.setMinimumSize(600, 400)
        self.project_code = project_code
        self.shot_name = shot_name
        # The shot's database id: history is found by it, so SH010 in one reel
        # never shows SH010 in another, or SH010A.
        self.shot_id = shot_id if shot_id and int(shot_id) > 0 else None
        self.reel = reel
        self.history_manager = HistoryManager(database_manager=database_manager)
        
        layout = QVBoxLayout(self)
        
        self.label = QLabel("Loading history…")
        self.label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.label)
        
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Time", "User", "Field", "Old Value", "New Value"])
        style_table(self.table, {"New Value": "stretch"}, multi_select=False)
        layout.addWidget(self.table)
        self.empty = EmptyState.over(self.table, "No changes recorded yet",
                                     "Edits to this shot are listed here once they are saved.", glyph="clock")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self.close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        layout.addWidget(buttons)
        
        self.load_data()
        
    def load_data(self):
        history = []
        if hasattr(database_manager, "get_history"):
            history = database_manager.get_history(
                self.project_code, self.shot_name, shot_id=self.shot_id, reel=self.reel) or []
        if not history:
            history = self.history_manager.get_history(self.project_code, self.shot_name) or []

        count = len(history)
        self.label.setText(f"{count} change{'s' if count != 1 else ''}, newest first")
        self.table.setRowCount(len(history))
        
        for row_idx, row in enumerate(history):
            if isinstance(row, dict):
                timestamp = row.get("timestamp")
                # The database returns user_name / field_changed; the local
                # history file uses user / field. Read either.
                user = row.get("user") or row.get("user_name")
                field = row.get("field") or row.get("field_changed")
                old_val = row.get("old_value")
                new_val = row.get("new_value")
            else:
                try:
                    timestamp, user, field, old_val, new_val = row
                except Exception:
                    timestamp, user, field, old_val, new_val = ("", "", "", "", "")

            self.table.setItem(row_idx, 0, datetime_item(timestamp))
            self.table.setItem(row_idx, 1, QTableWidgetItem(str(user or "Unknown")))
            self.table.setItem(row_idx, 2, QTableWidgetItem("" if field is None else str(field)))
            self.table.setItem(row_idx, 3, QTableWidgetItem("" if old_val is None else str(old_val)))
            self.table.setItem(row_idx, 4, QTableWidgetItem("" if new_val is None else str(new_val)))
            
        self.table.resizeColumnsToContents()
        if hasattr(self, "empty"):
            self.empty.refresh()
