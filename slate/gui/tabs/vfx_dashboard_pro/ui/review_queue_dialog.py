"""
What is waiting to be looked at.

The nearest thing to a review playlist: every version that has been sent and
not yet approved or kicked back, oldest submission first, so nothing sits
forgotten in someone's inbox. Versions nobody sent, and versions of omitted
shots, are not in it; an artist sees only their own shots.

Giving a verdict needs the right to (dashboard_write; a lead only for their
own department) - here and in the store, as in the shot panel; anybody else
can look and play, not approve. A verdict also moves the shot's status, as a
pending edit (the dashboard's on_verdict). An empty queue shows its empty
state, not disabled controls; a queue that could not be read says so.
"""

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from slate.core.domain.dates import format_date
from slate.core.domain.versions import (
    STATUS_APPROVED, STATUS_RETAKE, VersionStore, department_label, sent_to_label,
)
from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button
from slate.gui.core.empty_state import EmptyState
from slate.gui.core.table_style import style_table


class ReviewQueueDialog(QDialog):
    """Versions sent and still awaiting a verdict."""

    HEADERS = ["Reel", "Shot", "Version", "Department", "Artist", "Sent to", "Sent", "Status", "Notes"]

    def __init__(self, project_code, parent=None, store=None, roles=None, current_user="",
                 visible=None, departments=None, on_verdict=None):
        super().__init__(parent)
        self.setWindowTitle("Review queue")
        self.setMinimumSize(780, 480)

        self.project_code = project_code
        self.current_user = str(current_user or "").strip()
        self.visible = visible
        self.on_verdict = on_verdict
        self.store = store or VersionStore(roles=roles, departments=departments)
        if roles is not None and getattr(self.store, "roles", None) is None:
            self.store.roles = roles
        self.can_verdict = getattr(self.store, "can_give_verdicts", lambda *a: True)()
        self.versions = []

        layout = QVBoxLayout(self)

        self.heading = QLabel("")
        self.heading.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600;")
        layout.addWidget(self.heading)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        style_table(self.table, {"Notes": "numeric"}, multi_select=False)
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
        self.verdict_label = QLabel("Verdict for the selected version")
        actions.addWidget(self.verdict_label)
        self.status_combo = QComboBox()
        self.status_combo.addItems([STATUS_APPROVED, STATUS_RETAKE])
        actions.addWidget(self.status_combo)
        self.apply_btn = make_button("Give verdict", "secondary", on_click=self.apply_status)
        # Enter on a selected row must not hand out whatever the combo shows.
        self.apply_btn.setAutoDefault(False)
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
        failed = False
        try:
            self.versions = self.store.awaiting_review(self.project_code, visible=self.visible)
            self.store.attach_notes(self.versions)
        except Exception:
            self.versions = []
            failed = True

        count = len(self.versions)
        # The empty state says it when there is nothing; the caption only counts.
        self.heading.setText(f"{count} version{'s' if count != 1 else ''} waiting for review"
                             if count else "")
        self.heading.setVisible(bool(count))

        self.table.setRowCount(count)
        for row, version in enumerate(self.versions):
            notes = version.notes
            values = [version.reel or "-", version.shot_name, version.version_name,
                      department_label(version.department) or "-", version.artist or "-",
                      sent_to_label(version.sent_to), format_date(version.sent_date) or "-",
                      version.status, str(len(notes)) if notes else ""]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()
        # Nothing to act on: no controls, just the empty state.
        self.actions.setVisible(bool(self.versions))
        if failed:
            self.empty.set_message("The review queue could not be read",
                                   "The database did not answer. Close this and try again in a moment.")
        else:
            self.empty.set_message("Nothing is waiting for review",
                                   "Versions appear here when they are sent and have no verdict yet.")
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
                QMessageBox.information(self, "Review queue", "Select a version first.")
                return
            index = self.versions.index(version)
        else:
            index = row
            version = self.versions[index] if 0 <= index < len(self.versions) else None
        if version is None:
            return
        from .review_player_dialog import ReviewPlayerDialog
        player = ReviewPlayerDialog(version=version, queue_versions=self.versions, current_index=index,
                                    store=self.store, current_user=self.current_user, parent=self)
        if self.on_verdict is not None:
            player.verdict_submitted.connect(lambda v, status, _note: self.on_verdict(v, status))
        player.exec()
        self.refresh()

    def apply_status(self):
        version = self.selected_version()
        if version is None:
            QMessageBox.information(self, "Review queue", "Select a version first.")
            return
        status = self.status_combo.currentText()
        name = f"{version.shot_name} {version.version_name}"
        if QMessageBox.question(self, "Give a verdict", f"Set {name} to {status}?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            ok = self.store.update_version(version.id, status=status)
        except PermissionError as exc:
            QMessageBox.warning(self, "Give a verdict", str(exc))
            return
        if not ok:
            QMessageBox.warning(self, "Give a verdict", f"{name} could not be set to {status}.")
            return
        old = version.status
        version.status = status
        from slate.gui.components.feedback import toast
        toast(self, f"{name}: {status}.", "success",
              action=("Undo", lambda v=version, s=old: self._undo(v, s)))
        if self.on_verdict is not None:
            self.on_verdict(version, status)
        self.refresh()

    def _undo(self, version, status):
        if self.store.update_version(version.id, status=status):
            version.status = status
            self.refresh()
