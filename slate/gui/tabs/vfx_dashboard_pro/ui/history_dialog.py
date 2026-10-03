"""
A shot's change history, newest first, from the database only.

Fields are named as the grid names them ('Artist', 'Comp status', 'Roto bid'),
blank values say so, and older changes load page by page with "Load older".
A history that could not be read says that - it is never shown as "no changes"
(and there is no fall-back to a local file matched by shot name).
"""

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from slate.core.infra.database_manager import database_manager
from slate.gui.core.controls import make_button
from slate.gui.core.data_display import datetime_item
from slate.gui.core.empty_state import EmptyState
from slate.gui.core.table_style import style_table

PAGE = 200


def field_name(key) -> str:
    """'Comp status' for 'comp_status', 'Artist' for 'assigned_artist'."""
    from slate.core.domain.departments import load_departments
    from .shot_table_model import field_label
    key = str(key or "")
    for dept in load_departments():
        prefix = f"{dept.key}_"
        if key.startswith(prefix):
            label = field_label(f"departments.{dept.key}.{key[len(prefix):]}")
            return label[:1].upper() + label[1:]
    label = {"shot": "Shot", "project": "Project"}.get(key) or field_label(key)
    return label[:1].upper() + label[1:]


def value_text(key, value) -> str:
    """A stored value as the grid would show it; '-' for nothing."""
    from .shot_table_model import display_value
    text = "" if value is None else str(value)
    if not text.strip():
        return "No status" if str(key or "").endswith("status") else "-"
    path = "target" if str(key).endswith("target") else str(key)
    shown = display_value(path, text)
    return "-" if shown == "(empty)" else shown


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
        self.rows = []

        layout = QVBoxLayout(self)

        self.label = QLabel("")
        self.label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.label)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Time", "User", "Field", "Old value", "New value"])
        style_table(self.table, {"New value": "stretch"}, multi_select=False)
        layout.addWidget(self.table)
        self.empty = EmptyState.over(self.table, "No changes recorded yet",
                                     "Edits to this shot are listed here once they are saved.", glyph="clock")

        bottom = QHBoxLayout()
        self.more_btn = make_button("Load older", "secondary", on_click=self.load_more)
        bottom.addWidget(self.more_btn)
        bottom.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self.close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)

        self.load_data()

    def _read(self, offset):
        """One page (plus one row, to know whether there is more), or None when it could not be read."""
        from slate.core.infra.change_history import read_history
        try:
            page = read_history(database_manager, self.project_code, self.shot_name, PAGE + 1,
                                shot_id=self.shot_id, reel=self.reel, offset=offset)
            if page:
                return page
            # read_history answers a refused read with [] too: ask whether the table answers.
            probe = database_manager.execute_query("SELECT 1 AS ok FROM change_history WHERE 1=0",
                                                   fetch="all")
            return None if probe is None else []
        except Exception:
            return None

    def load_data(self):
        self.rows = []
        self.load_more()

    def load_more(self):
        page = self._read(len(self.rows))
        if page is None:
            self.empty.set_message("The history could not be read", "Try again in a moment.")
            self.label.setText("")
            self.more_btn.hide()
            self._show()
            return
        has_more = len(page) > PAGE
        self.rows += page[:PAGE]
        self.more_btn.setVisible(has_more)
        count = len(self.rows)
        if not count:
            self.label.setText("")
        elif has_more:
            self.label.setText(f"The latest {count} changes, newest first - Load older shows more")
        else:
            self.label.setText(f"{count} change{'s' if count != 1 else ''}, newest first")
        self.label.setVisible(bool(count))
        self._show()

    def _show(self):
        self.table.setRowCount(len(self.rows))
        for row_idx, row in enumerate(self.rows):
            key = row.get("field") or row.get("field_changed")
            self.table.setItem(row_idx, 0, datetime_item(row.get("timestamp")))
            self.table.setItem(row_idx, 1, QTableWidgetItem(str(row.get("user") or row.get("user_name")
                                                                or "Unknown")))
            self.table.setItem(row_idx, 2, QTableWidgetItem(field_name(key)))
            self.table.setItem(row_idx, 3, QTableWidgetItem(value_text(key, row.get("old_value"))))
            self.table.setItem(row_idx, 4, QTableWidgetItem(value_text(key, row.get("new_value"))))
        self.table.resizeColumnsToContents()
        self.empty.refresh()
