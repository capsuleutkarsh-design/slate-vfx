"""
What is waiting to be looked at.

The nearest thing to a review playlist: every version that has been submitted
and not yet approved or kicked back, oldest submission first, so nothing sits
forgotten in someone's inbox.

Giving a verdict needs the right to (dashboard_write) - here and in the
store, as in the shot panel; anybody else can look and play, not approve. An
empty queue shows its empty state, not disabled controls.
"""

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from slate.core.domain.versions import STATUS_CHOICES, VersionStore
from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button
from slate.gui.core.empty_state import EmptyState
from slate.gui.core.table_style import style_table


class ReviewQueueDialog(QDialog):
    """Versions submitted and still awaiting a verdict."""

    HEADERS = ["Shot", "Version", "Dept", "Artist", "Sent to", "Sent", "Status", "Notes"]

    def __init__(self, project_code, parent=None, store=None, roles=None):
        super().__init__(parent)
        self.setWindowTitle("Review queue")
        self.setMinimumSize(780, 480)

        self.project_code = project_code
        self.store = store or VersionStore(roles=roles)
        if roles is not None and getattr(self.store, "roles", None) is None:
            self.store.roles = roles
        self.can_verdict = getattr(self.store, "can_give_verdicts", lambda: True)()
        self.versions = []

        layout = QVBoxLayout(self)

        self.heading = QLabel("")
        self.heading.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600;")
        layout.addWidget(self.heading)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        style_table(self.table, {"Notes": "stretch"}, multi_select=False)
        self.table.cellDoubleClicked.connect(self.open_review_player)
        layout.addWidget(self.table, 1)
        self.empty = EmptyState.over(self.table, "Nothing is waiting for review",
                                     "Versions appear here when they are sent and have no verdict yet.",
                                     glyph="check-circle")

        self.actions = QWidget()
        actions = QHBoxLayout(self.actions)
        actions.setContentsMargins(0, 0, 0, 0)
        self.play_btn = make_button("Open in player", "secondary", icon="play",
                                    tooltip="Review the selected version (or double-click it)",
                                    on_click=lambda: self.open_review_player())
        actions.addWidget(self.play_btn)
        actions.addStretch(1)
        self.verdict_label = QLabel("Set the selected version to")
        actions.addWidget(self.verdict_label)
        self.status_combo = QComboBox()
        self.status_combo.addItems(STATUS_CHOICES)
        actions.addWidget(self.status_combo)
        self.apply_btn = make_button("Apply", "primary", on_click=self.apply_status)
        actions.addWidget(self.apply_btn)
        for widget in (self.verdict_label, self.status_combo, self.apply_btn):
            widget.setVisible(self.can_verdict)
        layout.addWidget(self.actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.refresh()

    def refresh(self):
        try:
            self.versions = self.store.awaiting_review(self.project_code)
        except Exception:
            self.versions = []

        count = len(self.versions)
        self.heading.setText(f"{count} version{'s' if count != 1 else ''} waiting for review"
                             if count else "Nothing is waiting for review.")

        self.table.setRowCount(count)
        for row, version in enumerate(self.versions):
            notes = self.store.notes_for_version(version.id)
            values = [version.shot_name, version.version_name, version.department or "-",
                      version.artist or "-", version.sent_to or "-", version.sent_date or "-",
                      version.status, str(len(notes)) if notes else ""]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()
        # Nothing to act on: no controls, just the empty state.
        self.actions.setVisible(bool(self.versions))
        self.empty.refresh()

    def selected_version(self):
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return None
        index = rows[0].row()
        return self.versions[index] if 0 <= index < len(self.versions) else None

    def open_review_player(self, row=None, col=None):
        if not self.versions:
            return
        if row is None or row < 0:
            version = self.selected_version()
            if version is None:
                self.heading.setText("Select a version first.")
                return
            index = self.versions.index(version)
        else:
            index = row
            version = self.versions[index] if 0 <= index < len(self.versions) else None
        if version is None:
            return
        from .review_player_dialog import ReviewPlayerDialog
        ReviewPlayerDialog(version=version, queue_versions=self.versions, current_index=index,
                           store=self.store, parent=self).exec()
        self.refresh()

    def apply_status(self):
        version = self.selected_version()
        if version is None:
            self.heading.setText("Select a version first.")
            return
        try:
            ok = self.store.update_version(version.id, status=self.status_combo.currentText())
        except PermissionError as exc:
            self.heading.setText(str(exc))
            return
        if not ok:
            self.heading.setText(f"{version.shot_name} {version.version_name} could not be updated.")
            return
        self.refresh()
