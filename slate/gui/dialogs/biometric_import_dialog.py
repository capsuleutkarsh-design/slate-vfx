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
    QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QHeaderView, QWidget,
)

from slate.core.domain import biometric_import as bio
from slate.core.domain.dates import format_date
from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button

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
        self.outside: list = []
        self._read = (0, 0)

        self.setWindowTitle("Import attendance from the biometric machine")
        self.setMinimumSize(900, 640)
        # Room for the preview, the skipped lines and the unknown codes at once
        # (at 900x640 the preview shrank to one row and the code row was cut).
        try:
            from slate.gui.components.screen_fit import fit_to_screen
            fit_to_screen(self, 1040, 820)
        except Exception:
            self.resize(1040, 820)
        self._build()

    # ------------------------------------------------------------------ ui
    def _build(self):
        outer = QVBoxLayout(self)
        # Everything but the buttons scrolls, so with the skipped lines open
        # the unknown codes are not cut to one row and a sliver.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 0, 0)
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
        browse = make_button("Choose file\u2026", "secondary")
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
        self.table.setMinimumHeight(180)
        root.addWidget(self.table, 2)

        self.mapping_status = QLabel("")
        self.mapping_status.setWordWrap(True)
        root.addWidget(self.mapping_status)

        # Lines that could not be read, and why (they were counted and the
        # reasons thrown away).
        self.btn_skipped = QPushButton("Show skipped lines")
        self.btn_skipped.setCheckable(True)
        self.btn_skipped.setVisible(False)
        self.btn_skipped.toggled.connect(self._toggle_skipped)
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
        self.unknown_table.setHorizontalHeaderLabels(["Machine code", "Punches", "Person"])
        self.unknown_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.unknown_table.verticalHeader().setVisible(False)
        self.unknown_table.setMaximumHeight(160)
        self.unknown_table.setMinimumHeight(90)
        root.addWidget(self.unknown_table, 1)

        # Days before somebody joined or after their last day: listed, and
        # left out unless HR say otherwise.
        self.outside_label = QLabel("")
        self.outside_label.setWordWrap(True)
        self.outside_label.setStyleSheet(f"color: {Gate.WARN};")
        self.outside_label.setVisible(False)
        root.addWidget(self.outside_label)
        self.import_outside = QCheckBox("Import those days anyway")
        self.import_outside.setVisible(False)
        root.addWidget(self.import_outside)

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

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        self.import_btn = make_button("Import", "primary", on_click=self.run_import)
        self.import_btn.setEnabled(False)
        buttons.addWidget(self.import_btn)
        outer.addLayout(buttons)

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
        self._read = (len(punches), len(skipped))
        self._fill_unknown()
        self._fill_skipped(skipped)
        self._show_status()
        self.summary.setText("")

    def _show_status(self):
        """The status line, the same after matching a code as after reading the file."""
        punches, skipped = self._read
        people = len({d.user_id for d in self.days})
        text = ("%s read, %d skipped. %s for %s."
                % (count(punches, "punch", "punches"), skipped,
                   count(len(self.days), "day"), count(people, "person", "people")))
        if self.unknown:
            text += " %s %s nobody in Slate (%s)." % (
                count(len(self.unknown), "code"),
                "matches" if len(self.unknown) == 1 else "match",
                count(sum(self.unknown.values()), "punch", "punches"))
        if self.days:
            first = min(d.day for d in self.days)
            last = max(d.day for d in self.days)
            text += " Dates %s to %s." % (format_date(first), format_date(last))
        self.mapping_status.setText(text)
        self.mapping_status.setStyleSheet("")
        self.outside = bio.outside_employment(self.days, self._records())
        if self.outside:
            shown = ", ".join("%s %s" % (d.user_id, format_date(d.day)) for d in self.outside[:6])
            more = len(self.outside) - 6
            self.outside_label.setText(
                "%s fall before the person joined or after their last day (%s%s). "
                "They are left out unless you tick below." % (
                    count(len(self.outside), "day"), shown,
                    ", and %d more" % more if more > 0 else ""))
        self.outside_label.setVisible(bool(self.outside))
        self.import_outside.setVisible(bool(self.outside))
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
        self._skipped_count = len(skipped)
        self._toggle_skipped(self.btn_skipped.isChecked())
        if not skipped:
            self.btn_skipped.setChecked(False)

    def _toggle_skipped(self, shown):
        self.skipped_table.setVisible(bool(shown))
        n = getattr(self, "_skipped_count", 0)
        self.btn_skipped.setText(("Hide " if shown else "Show ") + count(n, "skipped line"))

    def _records(self) -> dict:
        """Everybody's account record, read once per dialog."""
        if getattr(self, "_all_records", None) is None:
            try:
                from slate.core.domain.user_manager import UserManager
                self._all_records = UserManager().get_all_users() or {}
            except Exception:
                self._all_records = {}
        return self._all_records

    def _people_snapshot(self):
        """
        Everybody the codes can be matched to, read once for all the rows:
        active people whose Employee ID Slate knows.
        """
        known = {str(i).strip().lower() for i in self.known_ids}
        records = {u: r for u, r in self._records().items() if str(u).strip().lower() in known}
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
            label = code + (f" \u2013 {names[code]}" if names.get(code) else "")
            self.unknown_table.setItem(r, 0, QTableWidgetItem(label))
            self.unknown_table.setItem(r, 1, QTableWidgetItem(str(count)))
            picker = PersonPicker(people, placeholder="Skip, or type a name…")
            picker.setToolTip("Leave empty to skip this code")
            self.unknown_table.setCellWidget(r, 2, picker)
            self.unknown_table.setRowHeight(
                r, max(self.unknown_table.rowHeight(r), picker.sizeHint().height() + 8))
            if names.get(code):
                picker.suggest(names[code])
                # Show the start of the name, not its end ('i Gupta (vihaan...').
                if picker.lineEdit() is not None:
                    picker.lineEdit().setCursorPosition(0)
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
        self._show_status()

    # ------------------------------------------------------------ import
    def run_import(self):
        days = self.days
        if self.outside and not self.import_outside.isChecked():
            left_out = {(d.user_id, d.day) for d in self.outside}
            days = [d for d in self.days if (d.user_id, d.day) not in left_out]
        if not days:
            return
        people = len({d.user_id for d in days})
        if QMessageBox.question(
            self, "Import attendance",
            "Write %s of attendance for %s?\n\nA day a workstation "
            "also recorded keeps the earlier in and the later out. Importing the same "
            "file again changes nothing." % (count(len(days), "day"),
                                              count(people, "person", "people")),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return

        source = self.file_path.name if self.file_path else ""
        result = bio.apply_days(days, self.attendance, source=source)
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
        # Said once, by the Attendance page after this closes.
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
