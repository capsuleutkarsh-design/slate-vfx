"""
Unread notifications, in the tab people actually work in.

Notifications were only rendered in the Shot Review tab, so a new-scan alert on
an artist's own shot landed somewhere they might not open all week. This puts
the same messages in front of whoever is looking at the dashboard.
"""

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)


def unread_for(notifier, user_ids):
    """
    Every unread notification for this person, newest first.

    A person can be addressed by more than one identifier - a username, a
    display name, a numeric id - so all of them are collected and merged.
    """
    if not notifier:
        return []

    merged = {}
    for user_id in user_ids or []:
        if not user_id:
            continue
        try:
            for note in notifier.get_unread(user_id) or []:
                key = note.get("id")
                if key is not None:
                    merged[key] = note
        except Exception as exc:
            logging.debug("Could not read notifications for %s: %s", user_id, exc)

    return sorted(merged.values(),
                  key=lambda n: str(n.get("timestamp") or ""), reverse=True)


class NotificationsDialog(QDialog):
    """Unread messages, with the option to clear them."""

    shot_requested = Signal(str)

    HEADERS = ["When", "Type", "Message"]

    def __init__(self, notes, notifier=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Notifications")
        self.setMinimumSize(620, 400)

        self.notes = list(notes or [])
        self.notifier = notifier

        layout = QVBoxLayout(self)

        heading = QLabel(
            f"{len(self.notes)} unread" if self.notes else "Nothing unread."
        )
        heading.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(heading)

        self.table = QTableWidget(len(self.notes), len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)

        for row, note in enumerate(self.notes):
            values = [
                str(note.get("timestamp") or ""),
                str(note.get("type") or ""),
                str(note.get("message") or ""),
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self.table)

        self.mark_read_btn = QPushButton("Mark all as read")
        self.mark_read_btn.setEnabled(bool(self.notes))
        self.mark_read_btn.clicked.connect(self.mark_all_read)
        layout.addWidget(self.mark_read_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def mark_all_read(self):
        if not self.notifier or not self.notes:
            return
        ids = [n.get("id") for n in self.notes if n.get("id") is not None]
        try:
            self.notifier.mark_read(ids)
        except Exception as exc:
            logging.warning("Could not mark notifications read: %s", exc)
            return
        self.notes = []
        self.table.setRowCount(0)
        self.mark_read_btn.setEnabled(False)
        self.accept()
