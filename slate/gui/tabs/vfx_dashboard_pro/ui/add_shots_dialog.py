"""
Add shots to a project by hand.

Most shots arrive through Build & Ingest, which creates their records from the
client drive. This covers the rest: a late addition, a shot split in editorial,
or a show being set up before any plates have landed.

Shot names are entered one per line so a coordinator can paste a list straight
out of an email or an edit sheet.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QSpinBox, QVBoxLayout,
)


STATUS_CHOICES = ["YTS", "WIP", "SENT FOR REVIEW", "RETAKE", "APPROVED"]


class AddShotsDialog(QDialog):
    """Collects a reel and a list of shot names."""

    def __init__(self, parent=None, existing_reels=None, existing_shots=None):
        super().__init__(parent)
        self.setWindowTitle("Add Shots")
        self.setMinimumWidth(460)

        self._existing_shots = {
            str(name).strip().lower() for name in (existing_shots or [])
        }

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)

        self.reel_input = QComboBox()
        self.reel_input.setEditable(True)
        self.reel_input.addItems(sorted({r for r in (existing_reels or []) if r}))
        self.reel_input.setCurrentText("")
        self.reel_input.lineEdit().setPlaceholderText("e.g. ReelA")
        form.addRow("Reel / Episode:", self.reel_input)

        self.shots_input = QPlainTextEdit()
        self.shots_input.setPlaceholderText("SH010\nSH020\nSH030")
        self.shots_input.setMinimumHeight(140)
        self.shots_input.textChanged.connect(self._update_preview)
        form.addRow("Shot names:", self.shots_input)

        self.status_input = QComboBox()
        self.status_input.addItems(STATUS_CHOICES)
        form.addRow("Status:", self.status_input)

        self.priority_input = QSpinBox()
        self.priority_input.setRange(0, 5)
        self.priority_input.setValue(3)
        form.addRow("Priority:", self.priority_input)

        layout.addLayout(form)

        hint = QLabel("One shot name per line. Names already in the project "
                      "are ignored.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #B4B1AA; font-style: italic;")
        layout.addWidget(hint)

        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Add Shots")
        self.ok_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_preview()

    # ------------------------------------------------------------------
    def shot_names(self):
        """Shot names to create: de-duplicated, in the order entered."""
        names = []
        seen = set()
        for raw in self.shots_input.toPlainText().splitlines():
            name = raw.strip()
            if not name:
                continue
            key = name.lower()
            if key in seen or key in self._existing_shots:
                continue
            seen.add(key)
            names.append(name)
        return names

    def skipped_names(self):
        """Entered names that already exist in the project."""
        skipped = []
        for raw in self.shots_input.toPlainText().splitlines():
            name = raw.strip()
            if name and name.lower() in self._existing_shots:
                skipped.append(name)
        return skipped

    def get_values(self):
        return {
            "reel": self.reel_input.currentText().strip(),
            "shots": self.shot_names(),
            "status": self.status_input.currentText(),
            "priority": self.priority_input.value(),
        }

    # ------------------------------------------------------------------
    def _update_preview(self):
        new_names = self.shot_names()
        skipped = self.skipped_names()

        parts = []
        if new_names:
            parts.append(f"{len(new_names)} shot(s) will be added.")
        if skipped:
            parts.append(f"{len(skipped)} already in the project: "
                         f"{', '.join(skipped[:5])}"
                         + (" ..." if len(skipped) > 5 else ""))

        self.preview_label.setText("  ".join(parts))
        self.preview_label.setStyleSheet(
            "color: #3EA8BF;" if new_names else "color: #B4B1AA;"
        )
        self.ok_button.setEnabled(bool(new_names))
