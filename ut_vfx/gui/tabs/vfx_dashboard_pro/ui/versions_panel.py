"""
Version history for a shot, shown inside the shot detail panel.

Answers the questions that used to need an email search: what did we send, when,
to whom, what came back, and which version are we on now.
"""

import logging
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ut_vfx.core.domain.departments import load_departments
from ut_vfx.core.domain.versions import (
    SENT_TO_CHOICES, STATUS_CHOICES, VersionStore, next_version_name,
)


class AddVersionDialog(QDialog):
    """Record a new version of a shot."""

    def __init__(self, shot_name, suggested_name, artists=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"New Version - {shot_name}")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_input = QLineEdit(suggested_name)
        form.addRow("Version:", self.name_input)

        self.dept_input = QComboBox()
        self.dept_input.addItem("", "")
        for dept in load_departments():
            self.dept_input.addItem(dept.name, dept.key)
        form.addRow("Department:", self.dept_input)

        self.artist_input = QComboBox()
        self.artist_input.setEditable(True)
        self.artist_input.addItem("")
        self.artist_input.addItems(sorted({a for a in (artists or []) if a}))
        form.addRow("Artist:", self.artist_input)

        self.status_input = QComboBox()
        self.status_input.addItems(STATUS_CHOICES)
        form.addRow("Status:", self.status_input)

        self.sent_to_input = QComboBox()
        for choice in SENT_TO_CHOICES:
            self.sent_to_input.addItem(choice or "(not sent)", choice)
        form.addRow("Sent to:", self.sent_to_input)

        self.media_input = QLineEdit()
        self.media_input.setPlaceholderText("Path to the mov or frames (optional)")
        form.addRow("Media:", self.media_input)

        self.comment_input = QPlainTextEdit()
        self.comment_input.setPlaceholderText("What changed in this version")
        self.comment_input.setMaximumHeight(80)
        form.addRow("Comment:", self.comment_input)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_values(self):
        return {
            "version_name": self.name_input.text().strip(),
            "department": self.dept_input.currentData() or "",
            "artist": self.artist_input.currentText().strip(),
            "status": self.status_input.currentText(),
            "sent_to": self.sent_to_input.currentData() or "",
            "media_path": self.media_input.text().strip(),
            "comment": self.comment_input.toPlainText().strip(),
        }


class AddNoteDialog(QDialog):
    """Attach feedback to a specific version."""

    def __init__(self, version_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Note on {version_name}")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.source_input = QComboBox()
        self.source_input.addItems(["client", "director", "internal"])
        form.addRow("From:", self.source_input)

        self.author_input = QLineEdit()
        self.author_input.setPlaceholderText("Who gave the note (optional)")
        form.addRow("Author:", self.author_input)

        self.text_input = QPlainTextEdit()
        self.text_input.setMinimumHeight(110)
        form.addRow("Note:", self.text_input)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_values(self):
        return {
            "source": self.source_input.currentText(),
            "author": self.author_input.text().strip(),
            "text": self.text_input.toPlainText().strip(),
        }


class VersionsPanel(QGroupBox):
    """Version list for one shot, with add / send / note actions."""

    changed = Signal()

    HEADERS = ["Version", "Dept", "Artist", "Status", "Sent to", "Date", "Notes"]

    def __init__(self, project_code="", shot_name="", can_edit=True,
                 artists=None, store=None, user_name="", parent=None):
        super().__init__("Versions", parent)

        self.project_code = project_code
        self.shot_name = shot_name
        self.can_edit = bool(can_edit)
        self.artists = artists or []
        self.user_name = user_name
        self.store = store or VersionStore()
        self.versions = []

        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(120)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color: #B4B1AA; font-size: 11px;")
        layout.addWidget(self.detail_label)

        buttons = QHBoxLayout()
        self.add_btn = QPushButton("New Version")
        self.add_btn.clicked.connect(self.add_version)
        self.note_btn = QPushButton("Add Note")
        self.note_btn.clicked.connect(self.add_note)
        self.status_combo = QComboBox()
        self.status_combo.addItems(STATUS_CHOICES)
        self.set_status_btn = QPushButton("Set Status")
        self.set_status_btn.clicked.connect(self.set_status)

        for widget in (self.add_btn, self.note_btn, self.status_combo,
                       self.set_status_btn):
            buttons.addWidget(widget)
        buttons.addStretch()
        layout.addLayout(buttons)

        for widget in (self.add_btn, self.note_btn, self.status_combo,
                       self.set_status_btn):
            widget.setEnabled(self.can_edit)

        # Loaded on first show rather than in the constructor, so building a
        # shot detail panel never blocks on a database round-trip.
        self._loaded = False

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self.refresh()

    # ------------------------------------------------------------------
    def set_shot(self, project_code, shot_name):
        self.project_code = project_code
        self.shot_name = shot_name
        self._loaded = True
        self.refresh()

    def refresh(self):
        self.versions = []
        if self.project_code and self.shot_name:
            try:
                self.versions = self.store.list_for_shot(self.project_code,
                                                         self.shot_name)
            except Exception as exc:
                logging.warning("Could not load versions: %s", exc)

        self.table.setRowCount(len(self.versions))
        for row, version in enumerate(self.versions):
            values = [
                version.version_name,
                version.department,
                version.artist,
                version.status,
                version.sent_to or "-",
                version.sent_date or "-",
                str(len(version.notes)) if version.notes else "",
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._selection_changed()

    def selected_version(self):
        rows = self.table.selectionModel().selectedRows() \
            if self.table.selectionModel() else []
        if not rows:
            return None
        index = rows[0].row()
        if 0 <= index < len(self.versions):
            return self.versions[index]
        return None

    # ------------------------------------------------------------------
    def _selection_changed(self):
        version = self.selected_version()
        has_selection = version is not None and self.can_edit
        self.note_btn.setEnabled(has_selection)
        self.set_status_btn.setEnabled(has_selection)
        self.status_combo.setEnabled(has_selection)

        if not version:
            self.detail_label.setText("")
            return

        parts = []
        if version.comment:
            parts.append(version.comment)
        if version.media_path:
            parts.append(version.media_path)
        for note in version.notes:
            who = note.author or note.source or "note"
            parts.append(f"[{note.note_date} {who}] {note.text}")
        self.detail_label.setText("\n".join(parts))

    def add_version(self):
        if not self.project_code or not self.shot_name:
            return

        suggested = next_version_name([v.version_name for v in self.versions])
        dialog = AddVersionDialog(self.shot_name, suggested,
                                  artists=self.artists, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.get_values()
        if not values["version_name"]:
            return

        if any(v.version_name.lower() == values["version_name"].lower()
               for v in self.versions):
            QMessageBox.warning(self, "Version exists",
                                f"{values['version_name']} already exists "
                                f"for {self.shot_name}.")
            return

        created = self.store.add_version(
            self.project_code, self.shot_name,
            created_by=self.user_name, **values
        )
        if created is None:
            QMessageBox.critical(self, "Error", "Could not record the version.")
            return

        self.refresh()
        self.changed.emit()

    def add_note(self):
        version = self.selected_version()
        if version is None:
            return

        dialog = AddNoteDialog(version.version_name, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.get_values()
        if not values["text"]:
            return

        self.store.add_note(
            version.id, values["text"], source=values["source"],
            author=values["author"] or self.user_name,
            note_date=date.today().isoformat(),
        )
        self.refresh()
        self.changed.emit()

    def set_status(self):
        version = self.selected_version()
        if version is None:
            return

        self.store.update_version(version.id,
                                  status=self.status_combo.currentText())
        self.refresh()
        self.changed.emit()
