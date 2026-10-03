"""
Version history for a shot, shown inside the shot detail panel.

Answers the questions that used to need an email search: what did we send, when,
to whom, what came back, and which version are we on now.

A shot is its reel and its name: SH010 in R01 and SH010 in R02 have separate
versions. A verdict (Approved / Retake) here asks first and can be undone;
every write says when it did not happen.
"""

import logging
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from slate.core.domain.dates import format_date
from slate.core.domain.departments import load_departments
from slate.core.domain.versions import (
    AWAITING_REVIEW, SENT_TO_CHOICES, STATUS_CHOICES, VersionStore,
    department_label, next_version_name, sent_to_label,
)
from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button
from slate.gui.core.empty_state import EmptyState
from slate.gui.core.table_style import style_table

NOTE_SOURCES = ["client", "director", "internal"]


def _ok_needs(buttons: QDialogButtonBox, *fields):
    """OK stays off until every one of these fields has text."""
    ok = buttons.button(QDialogButtonBox.StandardButton.Ok)

    def check(*_):
        ok.setEnabled(all((f.text() if isinstance(f, QLineEdit) else f.toPlainText()).strip()
                          for f in fields))
    for field in fields:
        field.textChanged.connect(check)
    check()


class AddVersionDialog(QDialog):
    """Record a new version of a shot."""

    def __init__(self, shot_name, suggested_name, artists=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Record a version - {shot_name}")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_input = QLineEdit(suggested_name)
        form.addRow("Version", self.name_input)

        self.dept_input = QComboBox()
        self.dept_input.addItem("", "")
        for dept in load_departments():
            self.dept_input.addItem(dept.name, dept.key)
        form.addRow("Department", self.dept_input)

        self.artist_input = QComboBox()
        self.artist_input.setEditable(True)
        self.artist_input.addItem("")
        self.artist_input.addItems(sorted({a for a in (artists or []) if a}))
        form.addRow("Artist", self.artist_input)

        # A new version waits for review; the verdict is given in the review.
        self.status_input = QComboBox()
        self.status_input.addItems([s for s in STATUS_CHOICES if s in AWAITING_REVIEW])
        form.addRow("Status", self.status_input)

        self.sent_to_input = QComboBox()
        for choice in SENT_TO_CHOICES:
            self.sent_to_input.addItem(sent_to_label(choice), choice)
        self.sent_to_input.setToolTip("Sent versions appear in the Review queue")
        form.addRow("Sent to", self.sent_to_input)

        self.media_input = QLineEdit()
        self.media_input.setPlaceholderText("Path to the mov or frames (optional)")
        form.addRow("Media", self.media_input)

        self.comment_input = QPlainTextEdit()
        self.comment_input.setPlaceholderText("What changed in this version")
        self.comment_input.setMaximumHeight(80)
        form.addRow("Comment", self.comment_input)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Record version")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        _ok_needs(buttons, self.name_input)
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

    def __init__(self, version_name, parent=None, source="client", author=""):
        super().__init__(parent)
        self.setWindowTitle(f"Note on {version_name}")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.source_input = QComboBox()
        for value in NOTE_SOURCES:
            self.source_input.addItem(value.title(), value)
        self.source_input.setCurrentIndex(max(0, self.source_input.findData(source)))
        form.addRow("From", self.source_input)

        self.author_input = QLineEdit(author)
        self.author_input.setPlaceholderText("Who gave the note")
        form.addRow("Author", self.author_input)

        self.text_input = QPlainTextEdit()
        self.text_input.setMinimumHeight(110)
        form.addRow("Note", self.text_input)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add note")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        _ok_needs(buttons, self.text_input)
        layout.addWidget(buttons)

    def get_values(self):
        return {
            "source": self.source_input.currentData(),
            "author": self.author_input.text().strip(),
            "text": self.text_input.toPlainText().strip(),
        }


class VersionsPanel(QWidget):
    """Version list for one shot, with record / note / status actions."""

    changed = Signal()
    # A version was recorded: its name, so the shot's Version field can follow.
    version_recorded = Signal(str)
    # A verdict was given here: (version, new status).
    verdict_given = Signal(object, str)

    HEADERS = ["Version", "Department", "Artist", "Status", "Sent to", "Date", "Notes"]

    def __init__(self, project_code="", shot_name="", can_edit=True,
                 artists=None, store=None, user_name="", parent=None, reel="",
                 roles=None, departments=None):
        super().__init__(parent)

        self.project_code = project_code
        self.shot_name = shot_name
        self.reel = reel
        self.can_edit = bool(can_edit)
        self.artists = artists or []
        self.user_name = user_name
        self.store = store or VersionStore(roles=roles, departments=departments)
        self.versions = []
        self.read_failed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        style_table(self.table, {"Notes": "numeric"}, multi_select=False)
        self.table.setMinimumHeight(120)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table)
        self.empty = EmptyState.over(self.table, "No versions recorded yet",
                                     "Record one with New version.", glyph="")

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        # Plain text: a note with '<' in it is not markup.
        self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_label.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: {Gate.SIZE_XS}px;")
        layout.addWidget(self.detail_label)

        buttons = QHBoxLayout()
        self.add_btn = make_button("New version", "secondary", on_click=self.add_version)
        self.note_btn = make_button("Add note", "secondary", on_click=lambda: self.add_note())
        self.status_combo = QComboBox()
        self.status_combo.addItems(STATUS_CHOICES)
        self.set_status_btn = make_button("Set status", "secondary", on_click=self.set_status)

        for widget in (self.add_btn, self.note_btn, self.status_combo, self.set_status_btn):
            buttons.addWidget(widget)
        buttons.addStretch()
        layout.addLayout(buttons)

        for widget in (self.add_btn, self.note_btn, self.status_combo, self.set_status_btn):
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
    def set_shot(self, project_code, shot_name, reel=""):
        self.project_code = project_code
        self.shot_name = shot_name
        self.reel = reel
        self._loaded = True
        self.refresh()

    def refresh(self):
        self.versions = []
        self.read_failed = False
        if self.project_code and self.shot_name:
            try:
                self.versions = self.store.list_for_shot(self.project_code, self.shot_name,
                                                         reel=self.reel)
            except Exception as exc:
                logging.warning("Could not load versions: %s", exc)
                self.read_failed = True

        self.table.setRowCount(len(self.versions))
        for row, version in enumerate(self.versions):
            values = [
                version.version_name,
                department_label(version.department) or "-",
                version.artist or "-",
                version.status,
                sent_to_label(version.sent_to),
                format_date(version.sent_date) or "-",
                str(len(version.notes)) if version.notes else "",
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()
        if self.read_failed:
            self.empty.set_message("Versions could not be read", "Try again in a moment.")
        else:
            self.empty.set_message("No versions recorded yet", "Record one with New version.")
        self.empty.refresh()
        self._selection_changed()
        self.changed.emit()

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
        self.note_btn.setEnabled(self.can_edit and bool(self.versions))
        self.set_status_btn.setEnabled(has_selection)
        self.status_combo.setEnabled(has_selection)

        if not version:
            self.detail_label.setText("")
            return
        # The combo shows the selected version's status, so Set status never
        # re-applies whatever was picked last.
        self.status_combo.setCurrentText(version.status)

        parts = []
        if version.comment:
            parts.append(version.comment)
        if version.media_path:
            parts.append(version.media_path)
        for note in version.notes:
            who = note.author or note.source.title() or "Note"
            parts.append(f"[{format_date(note.note_date) or note.note_date} {who}] {note.text}")
        self.detail_label.setText("\n".join(parts))

    def _say(self, title, text):
        QMessageBox.warning(self, title, text)

    def add_version(self):
        if not self.project_code or not self.shot_name:
            return

        suggested = next_version_name([v.version_name for v in self.versions])
        dialog = AddVersionDialog(self.shot_name, suggested,
                                  artists=self.artists, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.get_values()
        if any(v.version_name.lower() == values["version_name"].lower()
               for v in self.versions):
            self._say("Record version", f"{values['version_name']} already exists for {self.shot_name}.")
            return

        try:
            created = self.store.add_version(self.project_code, self.shot_name,
                                             created_by=self.user_name, reel=self.reel, **values)
        except PermissionError as exc:
            self._say("Record version", str(exc))
            return
        if created is None:
            self._say("Record version", "The version could not be recorded.")
            return

        self.refresh()
        self.version_recorded.emit(created.version_name)

    def add_note(self, source: str = "client"):
        """A note on the selected version, or on the newest one."""
        version = self.selected_version() or (self.versions[0] if self.versions else None)
        if version is None:
            self._say("Add note", "Record a version first: notes belong to a version.")
            return

        dialog = AddNoteDialog(version.version_name, parent=self, source=source, author=self.user_name)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.get_values()
        if not self.store.add_note(version.id, values["text"], source=values["source"],
                                   author=values["author"] or self.user_name,
                                   note_date=date.today().isoformat()):
            self._say("Add note", "The note could not be saved.")
            return
        self.refresh()

    def set_status(self):
        version = self.selected_version()
        if version is None:
            return
        status = self.status_combo.currentText()
        old = version.status
        if status == old:
            return
        answer = QMessageBox.question(
            self, "Set version status", f"Set {self.shot_name} {version.version_name} to {status}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not self._write_status(version, status):
            return
        from slate.gui.components.feedback import toast
        toast(self, f"{self.shot_name} {version.version_name} set to {status}.", "success",
              action=("Undo", lambda v=version, s=old: self._write_status(v, s)))
        if status not in AWAITING_REVIEW:
            self.verdict_given.emit(version, status)

    def _write_status(self, version, status) -> bool:
        try:
            ok = self.store.update_version(version.id, status=status)
        except PermissionError as exc:
            self._say("Set version status", str(exc))
            return False
        if not ok:
            self._say("Set version status", f"{version.version_name} could not be set to {status}.")
            return False
        version.status = status
        self.refresh()
        return True
