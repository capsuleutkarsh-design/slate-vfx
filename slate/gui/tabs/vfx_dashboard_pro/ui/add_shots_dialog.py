"""
Add shots to a project by hand.

Most shots arrive through Build & Ingest, which creates their records from the
client drive. This covers the rest: a late addition, a shot split in editorial,
or a show being set up before any plates have landed.

Shot names are entered one per line so a coordinator can paste a list straight
out of an email or an edit sheet. Every line is checked against the folder
naming rules before anything is created ('bad/name', '..', spaces and
120-character names used to be accepted), a reel is required, and a name is a
duplicate only within its reel - SH010 can be added to a new reel.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QPlainTextEdit,
    QSpinBox, QVBoxLayout,
)

from slate.core.domain import shot_status
from slate.core.infra.gate import Gate
from slate.gui.core.controls import style_button

from slate.core.domain.naming import SHOT_NAME_MAX as MAX_NAME, shot_name_problem

# Kept for importers: the workflow, in order.
STATUS_CHOICES = list(shot_status.WORKFLOW)


def name_problem(name: str, what: str = "Shot name") -> str:
    """
    Why this cannot be a shot (or reel) folder name, or '' when it can.

    The rule itself lives in naming.shot_name_problem, shared with Build &
    Ingest and shot_registry, so a name refused here cannot arrive another way.
    """
    return shot_name_problem(name, what) or ""


class AddShotsDialog(QDialog):
    """Collects a reel and a list of shot names."""

    def __init__(self, parent=None, existing_reels=None, existing_shots=None):
        super().__init__(parent)
        self.setWindowTitle("Add shots")
        self.setMinimumWidth(480)

        # (reel, name) pairs; plain names are taken as "any reel" for older callers.
        self._existing = set()
        for entry in existing_shots or []:
            if isinstance(entry, (tuple, list)):
                self._existing.add((str(entry[0] or "").strip().lower(), str(entry[1] or "").strip().lower()))
            else:
                self._existing.add(("*", str(entry).strip().lower()))

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.reel_input = QComboBox()
        self.reel_input.setEditable(True)
        self.reel_input.addItems(sorted({r for r in (existing_reels or []) if r}))
        self.reel_input.setCurrentText("")
        self.reel_input.lineEdit().setPlaceholderText("e.g. R01")
        self.reel_input.currentTextChanged.connect(self._update_preview)
        form.addRow("Reel / episode", self.reel_input)

        self.shots_input = QPlainTextEdit()
        self.shots_input.setPlaceholderText("SH010\nSH020\nSH030")
        self.shots_input.setMinimumHeight(140)
        self.shots_input.textChanged.connect(self._update_preview)
        form.addRow("Shot names", self.shots_input)

        self.status_input = QComboBox()
        for value in shot_status.WORKFLOW:
            self.status_input.addItem(value, value)
        self.status_input.setCurrentText(shot_status.YTS)
        form.addRow("Status", self.status_input)

        self.priority_input = QComboBox()
        for value, label in shot_status.priorities():
            self.priority_input.addItem(label, value)
        self.priority_input.setCurrentIndex(max(0, self.priority_input.findData(2)))
        form.addRow("Priority", self.priority_input)
        layout.addLayout(form)

        hint = QLabel("One shot name per line: letters, digits, _ - and . only. "
                      "Names already in this reel are skipped.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {Gate.TEXT_2};")
        layout.addWidget(hint)

        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Add shots")
        style_button(self.ok_button, "primary")
        style_button(buttons.button(QDialogButtonBox.StandardButton.Cancel), "secondary")
        self.ok_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_preview()

    # ------------------------------------------------------------------
    def _reel(self) -> str:
        return self.reel_input.currentText().strip()

    def _exists(self, name) -> bool:
        key = name.lower()
        return (self._reel().lower(), key) in self._existing or ("*", key) in self._existing

    def _lines(self):
        return [raw.strip() for raw in self.shots_input.toPlainText().splitlines() if raw.strip()]

    def problems(self):
        out = []
        reel_problem = name_problem(self._reel(), "Reel") if self._reel() else "Choose or type a reel."
        if reel_problem:
            out.append(reel_problem)
        for line in self._lines():
            problem = name_problem(line)
            if problem:
                out.append(problem)
        return out

    def shot_names(self):
        """Valid new names, de-duplicated, in the order entered."""
        names, seen = [], set()
        for line in self._lines():
            name = line.strip()
            if name_problem(name) or name.lower() in seen or self._exists(name):
                continue
            seen.add(name.lower())
            names.append(name)
        return names

    def skipped_names(self):
        return [line.strip() for line in self._lines()
                if not name_problem(line.strip()) and self._exists(line.strip())]

    def get_values(self):
        return {
            "reel": self._reel(),
            "shots": self.shot_names(),
            "status": self.status_input.currentData(),
            "priority": self.priority_input.currentData(),
        }

    def _update_preview(self, *_):
        problems = self.problems()
        new_names = self.shot_names()
        skipped = self.skipped_names()
        parts = []
        if problems:
            parts.extend(problems[:4])
            if len(problems) > 4:
                parts.append(f"…and {len(problems) - 4} more to fix.")
        else:
            if new_names:
                parts.append(f"{len(new_names)} shot{'s' if len(new_names) != 1 else ''} will be added to {self._reel()}.")
            if skipped:
                parts.append(f"{len(skipped)} already in {self._reel()}: {', '.join(skipped[:5])}"
                             + ("…" if len(skipped) > 5 else ""))
        self.preview_label.setText("\n".join(parts))
        self.preview_label.setStyleSheet(
            f"color: {Gate.BAD};" if problems else (f"color: {Gate.ACCENT};" if new_names else f"color: {Gate.TEXT_2};"))
        self.ok_button.setEnabled(bool(new_names) and not problems)
