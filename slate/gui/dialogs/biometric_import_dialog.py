"""
Import attendance from a biometric machine's export.

Pick the file, check that each column has been understood, match any
employee codes the machine uses that Slate does not know, and import. The
mapping is remembered in the studio's shared folder, so the next file from
the same machine needs only the first and last of those.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QHeaderView, QWidget,
)

from slate.core.domain import biometric_import as bio

logger = logging.getLogger(__name__)

PREVIEW_ROWS = 40


class BiometricImportDialog(QDialog):
    """Three steps on one screen: the file, the columns, the unknown codes."""

    def __init__(self, attendance, known_ids, parent=None):
        super().__init__(parent)
        self.attendance = attendance
        self.known_ids = sorted({str(i) for i in known_ids if str(i).strip()})
        self.header: list[str] = []
        self.rows: list[list[str]] = []
        self.mapping = bio.Mapping()
        self.days: list[bio.DayRecord] = []
        self.unknown: dict = {}
        self.code_map: dict = {}
        self.result_summary: dict | None = None
        self.file_path: Path | None = None

        self.setWindowTitle("Import attendance from the biometric machine")
        self.setMinimumSize(900, 640)
        self._build()

    # ------------------------------------------------------------------ ui
    def _build(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        intro = QLabel(
            "Copy the export from the machine (CSV or Excel) onto this PC, then pick it "
            "here. Slate works out which column is which; correct it if it guessed wrong. "
            "Punches become one line per person per day: earliest in, latest out.")
        intro.setWordWrap(True)
        root.addWidget(intro)

        # 1. File
        file_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("No file chosen")
        self.path_edit.setReadOnly(True)
        browse = QPushButton("Choose file...")
        browse.clicked.connect(self.choose_file)
        file_row.addWidget(QLabel("1. File"))
        file_row.addWidget(self.path_edit, 1)
        file_row.addWidget(browse)
        root.addLayout(file_row)

        # 2. Columns
        col_row = QHBoxLayout()
        col_row.addWidget(QLabel("2. Columns"))
        self.profile_label = QLabel("")
        self.profile_label.setStyleSheet("color: #87857F;")
        col_row.addWidget(self.profile_label, 1)
        self.dayfirst = QCheckBox("Dates are day first (31/12/2026)")
        self.dayfirst.setChecked(True)
        self.dayfirst.toggled.connect(self._on_dayfirst)
        col_row.addWidget(self.dayfirst)
        root.addLayout(col_row)

        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        root.addWidget(self.table, 2)

        self.mapping_status = QLabel("")
        self.mapping_status.setWordWrap(True)
        root.addWidget(self.mapping_status)

        # 3. Unknown codes
        unk_row = QHBoxLayout()
        unk_row.addWidget(QLabel("3. Codes Slate does not know"))
        self.unknown_hint = QLabel("Pick the person each code belongs to, or leave it to skip them.")
        self.unknown_hint.setStyleSheet("color: #87857F;")
        unk_row.addWidget(self.unknown_hint, 1)
        root.addLayout(unk_row)

        self.unknown_table = QTableWidget(0, 3)
        self.unknown_table.setHorizontalHeaderLabels(["Machine code", "Punches", "Slate Employee ID"])
        self.unknown_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.unknown_table.verticalHeader().setVisible(False)
        self.unknown_table.setMaximumHeight(160)
        root.addWidget(self.unknown_table, 1)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-weight: 600;")
        root.addWidget(self.summary)

        buttons = QDialogButtonBox()
        self.import_btn = buttons.addButton("Import", QDialogButtonBox.ButtonRole.AcceptRole)
        self.import_btn.setEnabled(False)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.run_import)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ---------------------------------------------------------------- file
    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "The machine's export", "",
            "Attendance exports (*.csv *.txt *.xlsx *.xlsm *.xls);;All files (*)")
        if path:
            self.load_file(Path(path))

    def load_file(self, path: Path):
        try:
            self.header, self.rows = bio.read_table(path)
        except Exception as exc:
            QMessageBox.warning(self, "Cannot read this file", str(exc))
            return
        self.file_path = path
        self.path_edit.setText(str(path))

        profile = bio.find_profile(self.header)
        if profile:
            self.mapping = bio.Mapping.from_dict(profile.get("mapping", {}))
            self.code_map = dict(profile.get("code_map", {}))
            self.profile_label.setText("Known file layout: %s (saved %s)"
                                       % (profile.get("label", ""), profile.get("saved_at", "")))
        else:
            self.mapping = bio.guess_mapping(self.header, self.rows, dayfirst=self.dayfirst.isChecked())
            self.code_map = {}
            self.profile_label.setText("New file layout: check the column roles below.")
        self.dayfirst.blockSignals(True)
        self.dayfirst.setChecked(self.mapping.dayfirst)
        self.dayfirst.blockSignals(False)
        self._fill_preview()
        self._recompute()

    def _on_dayfirst(self, checked: bool):
        self.mapping.dayfirst = bool(checked)
        if self.rows:
            # A different date order can change which column looks like a date.
            guessed = bio.guess_mapping(self.header, self.rows, dayfirst=bool(checked))
            if self.mapping.problems():
                self.mapping = guessed
                self._fill_preview()
            self._recompute()

    # -------------------------------------------------------------- preview
    def _fill_preview(self):
        columns = len(self.header)
        shown = self.rows[:PREVIEW_ROWS]
        self.table.clear()
        self.table.setColumnCount(columns)
        self.table.setRowCount(len(shown) + 1)
        self.table.setHorizontalHeaderLabels(self.header)

        self.role_boxes: list[QComboBox] = []
        for col in range(columns):
            box = QComboBox()
            for role in bio.ROLES:
                box.addItem(bio.ROLE_LABELS[role], role)
            box.setCurrentIndex(bio.ROLES.index(self.mapping.role_of(col)))
            box.currentIndexChanged.connect(lambda _i, c=col: self._on_role_changed(c))
            self.table.setCellWidget(0, col, box)
            self.role_boxes.append(box)

        for r, row in enumerate(shown, start=1):
            for col in range(columns):
                text = row[col] if col < len(row) else ""
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(r, col, item)
        self.table.resizeRowsToContents()

    def _on_role_changed(self, column: int):
        role = self.role_boxes[column].currentData()
        self.mapping.set_role(column, role)
        # Only one column per role: clear any other box that claimed it.
        for col, box in enumerate(self.role_boxes):
            wanted = self.mapping.role_of(col)
            if box.currentData() != wanted:
                box.blockSignals(True)
                box.setCurrentIndex(bio.ROLES.index(wanted))
                box.blockSignals(False)
        self._recompute()

    # ------------------------------------------------------------ reduce
    def _recompute(self):
        problems = self.mapping.problems()
        if problems:
            self.mapping_status.setText("Cannot import yet: " + "; ".join(problems) + ".")
            self.mapping_status.setStyleSheet("color: #D9635F;")
            self.days, self.unknown = [], {}
            self._fill_unknown()
            self.summary.setText("")
            self.import_btn.setEnabled(False)
            return

        punches, skipped = bio.extract_punches(self.header, self.rows, self.mapping)
        self.days, self.unknown = bio.reduce_to_days(punches, self.known_ids, self.code_map)
        self._fill_unknown()

        people = len({d.user_id for d in self.days})
        text = ("%d punch(es) read, %d skipped. %d day(s) for %d person/people."
                % (len(punches), len(skipped), len(self.days), people))
        if self.unknown:
            text += " %d code(s) match nobody in Slate (%d punch(es))." % (
                len(self.unknown), sum(self.unknown.values()))
        if self.days:
            first = min(d.day for d in self.days)
            last = max(d.day for d in self.days)
            text += " Dates %s to %s." % (first.isoformat(), last.isoformat())
        self.mapping_status.setText(text)
        self.mapping_status.setStyleSheet("")
        self.summary.setText("")
        self.import_btn.setEnabled(bool(self.days))

    def _fill_unknown(self):
        self.unknown_table.setRowCount(0)
        for code, count in sorted(self.unknown.items(), key=lambda kv: -kv[1]):
            r = self.unknown_table.rowCount()
            self.unknown_table.insertRow(r)
            self.unknown_table.setItem(r, 0, QTableWidgetItem(code))
            self.unknown_table.setItem(r, 1, QTableWidgetItem(str(count)))
            box = QComboBox()
            box.addItem("(skip)", "")
            for user_id in self.known_ids:
                box.addItem(user_id, user_id)
            box.currentIndexChanged.connect(lambda _i, c=code, b=box: self._assign(c, b.currentData()))
            self.unknown_table.setCellWidget(r, 2, box)

    def _assign(self, code: str, user_id: str):
        if user_id:
            self.code_map[code] = user_id
        else:
            self.code_map.pop(code, None)
        # Re-reduce with the new match; the unknown list shrinks accordingly.
        punches, _ = bio.extract_punches(self.header, self.rows, self.mapping)
        self.days, self.unknown = bio.reduce_to_days(punches, self.known_ids, self.code_map)
        self.mapping_status.setText("%d day(s) for %d person/people; %d code(s) still unmatched."
                                    % (len(self.days), len({d.user_id for d in self.days}), len(self.unknown)))
        self.import_btn.setEnabled(bool(self.days))

    # ------------------------------------------------------------ import
    def run_import(self):
        if not self.days:
            return
        people = len({d.user_id for d in self.days})
        if QMessageBox.question(
            self, "Import attendance",
            "Write %d day(s) of attendance for %d person/people?\n\nA day a workstation "
            "also recorded keeps the earlier in and the later out. Importing the same "
            "file again changes nothing." % (len(self.days), people),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return

        source = self.file_path.name if self.file_path else ""
        result = bio.apply_days(self.days, self.attendance, source=source)
        self.result_summary = result
        bio.save_profile(self.header, self.mapping, self.code_map,
                         label=source or "biometric export")

        text = "%d day(s) written, %d already correct, %d failed." % (
            result["written"], result["unchanged"], result["failed"])
        if self.unknown:
            text += " %d code(s) were skipped as unknown." % len(self.unknown)
        if result["failures"]:
            text += "\nFirst failure: %s on %s: %s" % result["failures"][0]
        self.summary.setText(text)
        if result["failed"] and not result["written"]:
            QMessageBox.warning(self, "Nothing was imported", text)
            return
        QMessageBox.information(self, "Imported", text)
        self.accept()
