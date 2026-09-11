"""
What is waiting to be looked at.

The nearest thing to a review playlist: every version that has been submitted
and not yet approved or kicked back, oldest submission first, so nothing sits
forgotten in someone's inbox.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ut_vfx.core.domain.versions import STATUS_CHOICES, VersionStore


class ReviewQueueDialog(QDialog):
    """Versions submitted and still awaiting a verdict."""

    HEADERS = ["Shot", "Version", "Dept", "Artist", "Sent to", "Sent",
               "Status", "Notes"]

    def __init__(self, project_code, parent=None, store=None):
        super().__init__(parent)
        self.setWindowTitle("Review Queue")
        self.setMinimumSize(780, 480)

        self.project_code = project_code
        self.store = store or VersionStore()
        self.versions = []

        layout = QVBoxLayout(self)

        self.heading = QLabel("")
        self.heading.setStyleSheet("color: #E8E6E1; font-size: 14px;"
                                   " font-weight: 600;")
        layout.addWidget(self.heading)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self.open_review_player)
        layout.addWidget(self.table)

        actions = QHBoxLayout()
        self.play_btn = QPushButton("▶ Open in Player")
        self.play_btn.setToolTip("Double-click a version or click here to review in player")
        self.play_btn.clicked.connect(lambda: self.open_review_player())
        actions.addWidget(self.play_btn)

        actions.addWidget(QLabel("Set selected to:"))
        self.status_combo = QComboBox()
        self.status_combo.addItems(STATUS_CHOICES)
        actions.addWidget(self.status_combo)

        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self.apply_status)
        actions.addWidget(self.apply_btn)
        actions.addStretch()
        layout.addLayout(actions)

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

        self.heading.setText(
            f"{len(self.versions)} version(s) awaiting review"
            if self.versions else "Nothing is waiting for review."
        )

        self.table.setRowCount(len(self.versions))
        for row, version in enumerate(self.versions):
            notes = self.store.notes_for_version(version.id)
            values = [
                version.shot_name,
                version.version_name,
                version.department or "-",
                version.artist or "-",
                version.sent_to or "-",
                version.sent_date or "-",
                version.status,
                str(len(notes)) if notes else "",
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.apply_btn.setEnabled(bool(self.versions))
        if hasattr(self, "play_btn"):
            self.play_btn.setEnabled(bool(self.versions))

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
                QMessageBox.information(self, "Review Queue", "Select a version first.")
                return
            index = self.versions.index(version)
        else:
            index = row
            version = self.versions[index] if 0 <= index < len(self.versions) else None

        if version is None:
            return

        from .review_player_dialog import ReviewPlayerDialog
        player_dialog = ReviewPlayerDialog(
            version=version,
            queue_versions=self.versions,
            current_index=index,
            store=self.store,
            parent=self,
        )
        player_dialog.exec()
        self.refresh()

    def apply_status(self):
        version = self.selected_version()
        if version is None:
            QMessageBox.information(self, "Review Queue",
                                    "Select a version first.")
            return

        self.store.update_version(version.id,
                                  status=self.status_combo.currentText())
        self.refresh()
