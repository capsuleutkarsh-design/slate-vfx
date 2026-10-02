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
from slate.core.infra.gate import Gate

logger = logging.getLogger(__name__)

PREVIEW_ROWS = 40


def count(n: int, one: str, many: str = None) -> str:
    """'1 punch', '11 punches', '1 person', '3 people' - not 'punch(es)'."""
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


class ElidedPath(QLabel):
    """A path cut in the middle with '...', the whole of it in the tooltip."""

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self._full = ""
        self._placeholder = placeholder
        self.setMinimumWidth(80)
        self.setText(placeholder)

    def set_path(self, text: str):
        self._full = str(text or "")
        self.setToolTip(self._full)
        self._paint()

    def path(self) -> str:
        return self._full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._paint()

    def _paint(self):
        if not self._full:
            QLabel.setText(self, self._placeholder)
            return
        QLabel.setText(self, self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideMiddle, max(40, self.width() - 4)))


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
        self.path_edit = ElidedPath("No file chosen")
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
        self.profile_label.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        col_row.addWidget(self.profile_label, 1)
        self.dayfirst = QCheckBox("Dates are day first (31/12/2026)")
        self.dayfirst.setChecked(True)
        self.dayfirst.toggled.connect(self._on_dayfirst)
        col_row.addWidget(self.dayfirst)
        root.addLayout(col_row)

        # The role pickers sit in their own row above the preview, so they
        # stay in sight while the preview scrolls (they were cell widgets in
        # row 0 and scrolled away). Same columns, scrolled together.
        self.roles_row = QTableWidget(1, 0)
        self.roles_row.horizontalHeader().setVisible(False)
        self.roles_row.verticalHeader().setVisible(False)
        self.roles_row.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.roles_row.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.roles_row.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.roles_row.setFixedHeight(44)
        self.roles_row.setShowGrid(False)
        root.addWidget(self.roles_row)

        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalScrollBar().valueChanged.connect(
            self.roles_row.horizontalScrollBar().setValue)
        root.addWidget(self.table, 2)

        self.mapping_status = QLabel("")
        self.mapping_status.setWordWrap(True)
        root.addWidget(self.mapping_status)

        # Lines that could not be read, and why (they were counted and the
        # reasons thrown away).
        self.btn_skipped = QPushButton("Show skipped lines")
        self.btn_skipped.setCheckable(True)
        self.btn_skipped.setVisible(False)
        self.btn_skipped.toggled.connect(lambda on: self.skipped_table.setVisible(on))
        root.addWidget(self.btn_skipped, 0, Qt.AlignmentFlag.AlignLeft)
        self.skipped_table = QTableWidget(0, 3)
        self.skipped_table.setHorizontalHeaderLabels(["Line", "Text", "Why it was skipped"])
        self.skipped_table.verticalHeader().setVisible(False)
        self.skipped_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.skipped_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.skipped_table.setMaximumHeight(140)
        self.skipped_table.setVisible(False)
        root.addWidget(self.skipped_table)

        # 3. Unknown codes
        unk_row = QHBoxLayout()
        unk_row.addWidget(QLabel("3. Codes Slate does not know"))
        self.unknown_hint = QLabel("Pick the person each code belongs to, or leave it to skip them.")
        self.unknown_hint.setStyleSheet(f"color: {Gate.TEXT_DIM};")
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

        # Every day that could not be written, with the reason - not just the
        # first one.
        self.failures_table = QTableWidget(0, 3)
        self.failures_table.setHorizontalHeaderLabels(["Person", "Day", "Reason"])
        self.failures_table.verticalHeader().setVisible(False)
        self.failures_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.failures_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.failures_table.setMaximumHeight(160)
        self.failures_table.setVisible(False)
        root.addWidget(self.failures_table)

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
        self.path_edit.set_path(str(path))

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
        self.table.setRowCount(len(shown))
        self.table.setHorizontalHeaderLabels(self.header)
        self.roles_row.clear()
        self.roles_row.setColumnCount(columns)

        self.role_boxes: list[QComboBox] = []
        for col in range(columns):
            box = QComboBox()
            for role in bio.ROLES:
                box.addItem(bio.ROLE_LABELS[role], role)
            box.setCurrentIndex(bio.ROLES.index(self.mapping.role_of(col)))
            box.currentIndexChanged.connect(lambda _i, c=col: self._on_role_changed(c))
            self.roles_row.setCellWidget(0, col, box)
            self.role_boxes.append(box)
        self.roles_row.setRowHeight(0, 40)

        for r, row in enumerate(shown):
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
            self.mapping_status.setStyleSheet(f"color: {Gate.BAD};")
            self.days, self.unknown = [], {}
            self._fill_unknown()
            self.summary.setText("")
            self.import_btn.setEnabled(False)
            return

        punches, skipped = bio.extract_punches(self.header, self.rows, self.mapping)
        self.days, self.unknown = bio.reduce_to_days(punches, self.known_ids, self.code_map)
        self._fill_unknown()
        self._fill_skipped(skipped)

        people = len({d.user_id for d in self.days})
        text = ("%s read, %d skipped. %s for %s."
                % (count(len(punches), "punch", "punches"), len(skipped),
                   count(len(self.days), "day"), count(people, "person", "people")))
        if self.unknown:
            text += " %s match nobody in Slate (%s)." % (
                count(len(self.unknown), "code"),
                count(sum(self.unknown.values()), "punch", "punches"))
        if self.days:
            first = min(d.day for d in self.days)
            last = max(d.day for d in self.days)
            text += " Dates %s to %s." % (first.isoformat(), last.isoformat())
        self.mapping_status.setText(text)
        self.mapping_status.setStyleSheet("")
        self.summary.setText("")
        self.import_btn.setEnabled(bool(self.days))

    def _fill_skipped(self, skipped):
        self.skipped_table.setRowCount(len(skipped))
        offset = 2 if self.header else 1
        for r, (line, why) in enumerate(skipped):
            index = line - offset
            raw = ", ".join(self.rows[index]) if 0 <= index < len(self.rows) else ""
            for c, text in enumerate((str(line), raw, why)):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.skipped_table.setItem(r, c, item)
        self.btn_skipped.setVisible(bool(skipped))
        self.btn_skipped.setText("Show %s" % count(len(skipped), "skipped line"))
        if not skipped:
            self.btn_skipped.setChecked(False)

    def _people_snapshot(self):
        """
        Everybody the codes can be matched to, read once for all the rows:
        active people whose Employee ID Slate knows.
        """
        known = {str(i).strip().lower() for i in self.known_ids}
        try:
            from slate.core.domain.user_manager import UserManager
            records = UserManager().get_all_users() or {}
        except Exception:
            records = {}
        records = {u: r for u, r in records.items() if str(u).strip().lower() in known}
        for user_id in self.known_ids:          # an ID with no account row still counts
            records.setdefault(user_id, {"display_name": user_id})

        class _Snapshot:
            def get_all_users(self_inner):
                return records
        return _Snapshot()

    def _fill_unknown(self):
        """
        One searchable person picker per unmatched code ("Display Name
        (username)", type to filter), pre-filled from the file's Name column
        when that names exactly one person. It was a combo of every raw
        username, 150 long, with no search.
        """
        from slate.gui.components.person_picker import PersonPicker
        self.unknown_table.setRowCount(0)
        names = {}
        try:
            punches, _ = bio.extract_punches(self.header, self.rows, self.mapping)
            for punch in punches:
                if punch.name and punch.code not in names:
                    names[punch.code] = punch.name
        except Exception:
            names = {}
        people = self._people_snapshot()
        for code, count in sorted(self.unknown.items(), key=lambda kv: -kv[1]):
            r = self.unknown_table.rowCount()
            self.unknown_table.insertRow(r)
            label = code + (f"  -  {names[code]}" if names.get(code) else "")
            self.unknown_table.setItem(r, 0, QTableWidgetItem(label))
            self.unknown_table.setItem(r, 1, QTableWidgetItem(str(count)))
            picker = PersonPicker(people, placeholder="Skip, or type a name…")
            picker.setToolTip("Leave empty to skip this code")
            self.unknown_table.setCellWidget(r, 2, picker)
            self.unknown_table.setRowHeight(
                r, max(self.unknown_table.rowHeight(r), picker.sizeHint().height() + 8))
            if names.get(code):
                picker.suggest(names[code])
                if picker.username():
                    self._assign(code, picker.username())
            picker.person_changed.connect(lambda username, c=code: self._assign(c, username))

    def _assign(self, code: str, user_id: str):
        if user_id:
            self.code_map[code] = user_id
        else:
            self.code_map.pop(code, None)
        # Re-reduce with the new match; the unknown list shrinks accordingly.
        punches, _ = bio.extract_punches(self.header, self.rows, self.mapping)
        self.days, self.unknown = bio.reduce_to_days(punches, self.known_ids, self.code_map)
        self.mapping_status.setText("%s for %s; %s still unmatched." % (
            count(len(self.days), "day"), count(len({d.user_id for d in self.days}), "person", "people"),
            count(len(self.unknown), "code")))
        self.import_btn.setEnabled(bool(self.days))

    # ------------------------------------------------------------ import
    def run_import(self):
        if not self.days:
            return
        people = len({d.user_id for d in self.days})
        if QMessageBox.question(
            self, "Import attendance",
            "Write %s of attendance for %s?\n\nA day a workstation "
            "also recorded keeps the earlier in and the later out. Importing the same "
            "file again changes nothing." % (count(len(self.days), "day"),
                                              count(people, "person", "people")),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return

        source = self.file_path.name if self.file_path else ""
        result = bio.apply_days(self.days, self.attendance, source=source)
        self.result_summary = result
        bio.save_profile(self.header, self.mapping, self.code_map,
                         label=source or "biometric export")

        text = "%s written, %d already correct, %d failed." % (
            count(result["written"], "day"), result["unchanged"], result["failed"])
        if self.unknown:
            text += " %s skipped as unknown." % count(len(self.unknown), "code")
        self.summary.setText(text)
        self.show_failures(result["failures"])
        if result["failed"]:
            # Stay open with the list, so HR can see every day that needs
            # fixing. Importing again is harmless only for days that worked.
            text += (" The days below were not written; fix them (or the file) and "
                     "import again - days already written are left alone.")
            self.summary.setText(text)
            QMessageBox.warning(self, "Import attendance", text)
            return
        QMessageBox.information(self, "Import attendance", text)
        self.accept()

    def show_failures(self, failures):
        """Every day that failed: person, day, reason."""
        self.failures_table.setRowCount(len(failures))
        for r, (user_id, day, reason) in enumerate(failures):
            for c, text in enumerate((str(user_id), str(day), str(reason))):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.failures_table.setItem(r, c, item)
        self.failures_table.setVisible(bool(failures))
