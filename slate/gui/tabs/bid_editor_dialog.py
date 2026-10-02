"""
The bid editor: a header, the line items and the totals, live.

The old "New Project Bid" dialog (also used, unchanged, for editing) had one
complexity and one day rate for the whole project, a shot count read-only from
the tracker (so a bid could not be made before turnover, and editing an
approved bid silently re-priced it to today's shot count), spin boxes for
figures that could not be edited, no cost or profit, the margin always at 20%
whatever the studio set, a $1,000,000,000 cap on the price, and validation
only after it had closed.

Now a bid is built from lines - a shot, a department's work on a group of
shots, anything with days and a rate - and every figure is computed by
core/domain/bidding.price_bid in Decimal and shown as text: artist days, cost,
margin (what it earns), price, discount, tax (GST on rupee bids) and total.
Save stays off, with the reason, until the bid makes sense, and the dialog
closes only once it is saved. A bid that was sent or decided opens read-only,
with "Create new revision".
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QSpinBox, QTableWidget,
    QVBoxLayout, QWidget,
)

from slate.core.domain import bidding as DB
from slate.core.domain import money
from slate.core.domain.dates import format_datetime
from slate.core.infra.db_results import DatabaseUnavailableError
from slate.core.infra.gate import Gate
from slate.gui.core.controls import form_layout, make_button, style_button
from slate.gui.core.table_style import style_table
from slate.gui.components.table_tools import make_item

logger = logging.getLogger(__name__)

# Columns of the lines table.
L_LABEL, L_SHOT, L_DEPT, L_COMPLEXITY, L_SHOTS, L_PER_SHOT, L_RATE, L_DAYS, L_COST = range(9)
LINE_HEADERS = ["Description", "Shot", "Department", "Complexity", "Shots", "Days / shot",
                "Day rate", "Artist days", "Cost"]

MAX_RATE = 100_000_000          # 10 crore a day: a typing slip, not a rate


def _money_spin(maximum=MAX_RATE) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0, maximum)
    spin.setDecimals(2)
    # No locale grouping in an editable box: the formatted figure is shown in
    # the totals, in the bid's own currency.
    spin.setGroupSeparatorShown(False)
    spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
    spin.setAlignment(Qt.AlignmentFlag.AlignRight)
    return spin


def _percent_spin(maximum=100.0) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0, maximum)
    spin.setDecimals(2)
    spin.setSuffix(" %")
    spin.setAlignment(Qt.AlignmentFlag.AlignRight)
    return spin


def _hint(colour=None) -> QLabel:
    label = QLabel("")
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {colour or Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")
    label.hide()
    return label


def _set(label: QLabel, text: str) -> None:
    label.setText(text)
    label.setVisible(bool(text))


def project_label(code: str, name: str) -> str:
    return f"{code} – {name}" if name and name.casefold() != code.casefold() else code


def default_rate(code: str) -> Decimal:
    """The studio's day rate in this currency (0 when it has none for it)."""
    rate = money.day_rate(code)
    if rate is None and code == "USD":
        rate = Decimal(str(DB.day_rate()))
    return DB.dec(rate)


class ImportShotsDialog(QDialog):
    """Which department and complexity the imported shots are bid at."""

    def __init__(self, parent, count: int, skipped: int, departments, complexities):
        super().__init__(parent)
        self.setWindowTitle("Add shots from the tracker")
        layout = QVBoxLayout(self)
        note = f"{DB.WORDS['shots']}: {count} on the dashboard"
        if skipped:
            note += f" ({skipped} omitted or cancelled left out)"
        layout.addWidget(QLabel(note + ". One line per shot:"))
        form = form_layout()
        self.dept = QComboBox()
        for key, name in departments:
            self.dept.addItem(name, key)
        index = self.dept.findData("comp")
        self.dept.setCurrentIndex(max(index, 0))
        self.complexity = QComboBox()
        self.complexity.addItems(complexities)
        self.complexity.setCurrentText("Medium" if "Medium" in complexities else complexities[0])
        form.addRow("Department", self.dept)
        form.addRow("Complexity", self.complexity)
        layout.addLayout(form)
        buttons = QDialogButtonBox()
        ok = buttons.addButton(f"Add {count} shots", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        style_button(ok, "primary")
        style_button(cancel, "secondary")
        ok.setEnabled(count > 0)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class BidEditorDialog(QDialog):
    """
    repo      a BidRepository (saves; reads tracker shots)
    bid       the bid to edit or look at, or None for a new one
    lines     its lines (repo.lines(bid.id)), or None
    projects  [(code, name)] a new bid may be for
    """

    def __init__(self, parent=None, *, repo=None, bid=None, lines: Sequence[DB.BidLine] = None,
                 projects: Sequence[Tuple[str, str]] = (), username: str = "",
                 default_project: str = ""):
        super().__init__(parent)
        self.repo = repo
        self.bid = bid
        self.username = username
        self.read_only = bid is not None and not bid.editable
        self.saved_id: Optional[int] = None
        self.revision_id: Optional[int] = None
        self.lines: List[DB.BidLine] = [DB.BidLine(**{k: getattr(l, k) for k in (
            "label", "department", "complexity", "shot_count", "days_per_shot", "day_rate",
            "shot_name", "reel", "notes")}) for l in (lines or [])]
        self._rate_touched = bid is not None
        self._tax_touched = bid is not None
        self._filling = False
        self._departments = self._load_departments()

        if bid is None:
            self.setWindowTitle("New bid")
        elif self.read_only:
            self.setWindowTitle(f"Bid {bid.title} – {DB.status_label(bid.status)}")
        else:
            self.setWindowTitle(f"Edit bid – {bid.title}")
        self.setMinimumSize(980, 600)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        outer.setSpacing(Gate.SPACE_3)

        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(
            f"background: {Gate.ACCENT_SURFACE}; color: {Gate.TEXT}; border-radius: "
            f"{Gate.RADIUS_MD}px; padding: 8px 10px;")
        self.banner.hide()
        outer.addWidget(self.banner)

        # ---- header: who and what (left), the money rules (right)
        header = QHBoxLayout()
        header.setSpacing(Gate.SPACE_5)
        left = form_layout()
        if bid is None:
            self.project_cb = QComboBox()
            for code, name in projects:
                self.project_cb.addItem(project_label(code, name), code)
            index = self.project_cb.findData(default_project) if default_project else 0
            self.project_cb.setCurrentIndex(max(index, 0) if self.project_cb.count() else -1)
            self.project_cb.view().setMinimumWidth(320)
            left.addRow(DB.WORDS["project"], self.project_cb)
        else:
            # A bid stays with its project; another project gets its own draft
            # (Duplicate to project), so a shot count can never cross over.
            self.project_cb = None
            locked = QLabel(project_label(bid.project_code, bid.project_name))
            locked.setToolTip("A bid stays with its project. Use Duplicate to project to bid "
                              "the same work for another one.")
            left.addRow(DB.WORDS["project"], locked)
        self.client_input = QLineEdit(bid.client_name if bid else "")
        self.client_input.setPlaceholderText("Who the bid is for")
        self.client_input.setMaxLength(200)
        left.addRow(DB.WORDS["client"], self.client_input)
        self.notes_input = QLineEdit(bid.notes if bid else "")
        self.notes_input.setPlaceholderText("Assumptions, exclusions…")
        left.addRow("Notes", self.notes_input)
        header.addLayout(left, 3)

        right = form_layout()
        self.currency_cb = QComboBox()
        for code, cur in money.CURRENCIES.items():
            self.currency_cb.addItem(f"{cur.symbol}  {code} – {cur.name}", code)
        start_code = bid.currency if bid else money.studio_currency()
        self.currency_cb.setCurrentIndex(max(self.currency_cb.findData(start_code), 0))
        right.addRow("Currency", self.currency_cb)
        self.rate_input = _money_spin()
        self.rate_input.setToolTip("The rate new lines start with. Lines that follow it change with it.")
        right.addRow(DB.WORDS["day_rate"], self.rate_input)
        self.rate_hint = _hint(Gate.WARN)
        right.addRow("", self.rate_hint)
        self.margin_input = _percent_spin(DB.max_margin_percent())
        self.margin_input.setToolTip(f"The share of the price the studio keeps. "
                                     f"Up to {DB.fmt_percent(DB.max_margin_percent())}.")
        right.addRow(DB.WORDS["margin"], self.margin_input)
        self.margin_hint = _hint(Gate.WARN)
        right.addRow("", self.margin_hint)
        self.discount_input = _percent_spin()
        right.addRow(DB.WORDS["discount"], self.discount_input)
        tax_row = QHBoxLayout()
        self.tax_label_input = QLineEdit()
        self.tax_label_input.setMaxLength(20)
        self.tax_label_input.setFixedWidth(90)
        self.tax_input = _percent_spin()
        tax_row.addWidget(self.tax_label_input)
        tax_row.addWidget(self.tax_input, 1)
        right.addRow(DB.WORDS["tax"], tax_row)
        header.addLayout(right, 2)
        outer.addLayout(header)

        # ---- lines
        tools = QHBoxLayout()
        tools.setSpacing(Gate.SPACE_2)
        self.add_line_button = make_button("Add line", "secondary", icon="plus", on_click=self.add_line)
        self.import_button = make_button("Add shots from tracker…", "secondary", on_click=self.import_shots,
                                         tooltip="One line per shot on the dashboard (omitted shots left out)")
        self.duplicate_button = make_button("Duplicate", "ghost", icon="copy", on_click=self.duplicate_line)
        self.remove_button = make_button("Remove", "ghost", icon="trash", on_click=self.remove_line)
        self.up_button = make_button("", "ghost", icon="chevron-up", tooltip="Move up",
                                     on_click=lambda: self.move_line(-1))
        self.down_button = make_button("", "ghost", icon="chevron-down", tooltip="Move down",
                                       on_click=lambda: self.move_line(1))
        for w in (self.add_line_button, self.import_button, self.duplicate_button, self.remove_button,
                  self.up_button, self.down_button):
            tools.addWidget(w)
        tools.addStretch()
        self.import_note = QLabel("")
        self.import_note.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")
        tools.addWidget(self.import_note)
        outer.addLayout(tools)

        self.table = QTableWidget(0, len(LINE_HEADERS))
        self.table.setHorizontalHeaderLabels(LINE_HEADERS)
        style_table(self.table, {"Description": "stretch", "Shot": ("interactive", 110),
                                 "Department": ("interactive", 160), "Complexity": ("interactive", 130),
                                 "Shots": ("fixed", 70), "Days / shot": ("fixed", 90),
                                 "Day rate": ("fixed", 120), "Artist days": "numeric", "Cost": "numeric"},
                    sortable=False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setDefaultSectionSize(Gate.CONTROL_HEIGHT + 4)
        self.table.itemSelectionChanged.connect(self._sync_line_buttons)
        outer.addWidget(self.table, 1)

        # ---- totals and problems
        bottom = QHBoxLayout()
        left_bottom = QVBoxLayout()
        self.problems = _hint(Gate.BAD)
        left_bottom.addWidget(self.problems)
        self.footer = _hint()
        left_bottom.addWidget(self.footer)
        left_bottom.addStretch()
        bottom.addLayout(left_bottom, 3)
        totals = QGridLayout()
        totals.setHorizontalSpacing(Gate.SPACE_4)
        totals.setVerticalSpacing(2)
        self.total_labels = {}
        for row, (key, text) in enumerate((("days", DB.WORDS["days"]), ("cost", DB.WORDS["cost"]),
                                           ("margin", DB.WORDS["margin"]), ("price", DB.WORDS["price"]),
                                           ("discount", DB.WORDS["discount"]), ("taxable", "Subtotal"),
                                           ("tax", DB.WORDS["tax"]), ("total", DB.WORDS["total"]))):
            name = QLabel(text)
            name.setStyleSheet(f"color: {Gate.TEXT_2};")
            value = QLabel("—")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if key == "total":
                name.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600;")
                value.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600; font-size: {Gate.SIZE_LG}px;")
            totals.addWidget(name, row, 0)
            totals.addWidget(value, row, 1)
            self.total_labels[key] = (name, value)
        bottom.addLayout(totals, 2)
        outer.addLayout(bottom)

        # ---- buttons
        button_row = QHBoxLayout()
        self.pdf_button = make_button("Export PDF…", "ghost", icon="download", on_click=self.export_pdf,
                                      tooltip="The bid as the client sees it (no cost or margin)")
        button_row.addWidget(self.pdf_button)
        button_row.addStretch()
        self.buttons = QDialogButtonBox()
        if self.read_only:
            self.revise_button = self.buttons.addButton("Create new revision",
                                                        QDialogButtonBox.ButtonRole.AcceptRole)
            close = self.buttons.addButton(QDialogButtonBox.StandardButton.Close)
            style_button(self.revise_button, "primary")
            style_button(close, "secondary")
            refusal = repo.decided_refusal(bid, username) if repo is not None and hasattr(
                repo, "decided_refusal") else ""
            self.revise_button.setEnabled(bid.status != DB.SUPERSEDED and not bid.archived and not refusal)
            self.revise_button.setToolTip(refusal)
            self.revise_button.clicked.connect(self._revise)
            close.clicked.connect(self.reject)
            self.save_button = None
        else:
            self.save_button = self.buttons.addButton("Save changes" if bid else "Create bid",
                                                      QDialogButtonBox.ButtonRole.AcceptRole)
            cancel = self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
            style_button(self.save_button, "primary")
            style_button(cancel, "secondary")
            self.save_button.clicked.connect(self._save)
            cancel.clicked.connect(self.reject)
            self.revise_button = None
        button_row.addWidget(self.buttons)
        outer.addLayout(button_row)

        self._fill_header()
        self._last_rate = DB.money(Decimal(str(self.rate_input.value())))
        if not self.lines and bid is None:
            self.add_line(refresh=False)
        self._rebuild_table()
        self._wire()
        self._apply_read_only()
        self._recompute()

    # ------------------------------------------------------------ setup
    @staticmethod
    def _load_departments() -> List[Tuple[str, str]]:
        try:
            from slate.core.domain.departments import load_departments
            return [(d.key, d.name or d.label or d.key) for d in load_departments()]
        except Exception as exc:
            logger.warning("Departments could not be read: %s", exc)
            return [("comp", "Comp"), ("roto", "Roto"), ("prep", "Prep / Paint")]

    def _fill_header(self):
        self._filling = True
        bid = self.bid
        code = self.currency()
        self._apply_symbol(code)
        if bid is None:
            self.rate_input.setValue(float(default_rate(code)))
            self.margin_input.setValue(DB.margin_percent())
            self.tax_input.setValue(float(money.default_tax_rate(code)))
            self.tax_label_input.setText(DB.tax_label(code))
        else:
            rate = bid.day_rate
            if rate <= 0 and self.lines:
                rate = self.lines[0].day_rate
            self.rate_input.setValue(float(rate))
            self.margin_input.setMaximum(max(DB.max_margin_percent(), float(bid.margin)))
            self.margin_input.setValue(float(bid.margin))
            self.discount_input.setValue(float(bid.discount))
            self.tax_input.setValue(float(bid.tax))
            self.tax_label_input.setText(bid.tax_label or DB.tax_label(code))
            bits = []
            if bid.created_by or bid.created_at:
                bits.append("Created" + (f" by {self._name(bid.created_by)}" if bid.created_by else "")
                            + (f" on {format_datetime(bid.created_at)}" if bid.created_at else ""))
            if bid.sent_at:
                bits.append(f"sent {format_datetime(bid.sent_at)}")
            if bid.decided_by:
                bits.append(f"{DB.status_label(bid.status).lower()} by {self._name(bid.decided_by)}"
                            + (f" on {format_datetime(bid.decided_at)}" if bid.decided_at else ""))
            _set(self.footer, "; ".join(bits))
            if self.read_only:
                why = {DB.SENT: "was sent to the client", DB.WON: "was won", DB.LOST: "was lost",
                       DB.SUPERSEDED: "has a newer revision"}.get(bid.status, "is archived")
                if bid.archived:
                    why = "is archived"
                _set(self.banner, f"This bid {why}, so it is shown read-only. "
                     + ("Create a new revision to change it - this one is kept as it is."
                        if bid.status != DB.SUPERSEDED and not bid.archived else ""))
            if not bid.has_lines and self.lines:
                _set(self.import_note, "From before line items: shown as one line, saved as one "
                                       "when you save.")
        self._filling = False

    @staticmethod
    def _name(username):
        try:
            from slate.core.domain.people import display_name
            return display_name(username) or username
        except Exception:
            return username

    def _wire(self):
        self.currency_cb.currentIndexChanged.connect(self._currency_changed)
        self.rate_input.valueChanged.connect(self._rate_changed)
        self.rate_input.editingFinished.connect(lambda: setattr(self, "_rate_touched", True))
        self.tax_input.editingFinished.connect(lambda: setattr(self, "_tax_touched", True))
        for spin in (self.margin_input, self.discount_input, self.tax_input):
            spin.valueChanged.connect(self._recompute)
        if self.project_cb is not None:
            self.project_cb.currentIndexChanged.connect(self._recompute)

    def _apply_read_only(self):
        if not self.read_only:
            return
        for w in (self.client_input, self.notes_input, self.currency_cb, self.rate_input,
                  self.margin_input, self.discount_input, self.tax_input, self.tax_label_input,
                  self.add_line_button, self.import_button, self.duplicate_button,
                  self.remove_button, self.up_button, self.down_button):
            w.setEnabled(False)
        self.table.setEnabled(True)
        for r in range(self.table.rowCount()):
            for c in range(self.table.columnCount()):
                widget = self.table.cellWidget(r, c)
                if widget is not None:
                    widget.setEnabled(False)

    # ------------------------------------------------------------ header reactions
    def currency(self) -> str:
        return str(self.currency_cb.currentData() or money.studio_currency())

    def project_code(self) -> str:
        if self.bid is not None:
            return self.bid.project_code
        return str(self.project_cb.currentData() or "") if self.project_cb is not None else ""

    def _apply_symbol(self, code):
        symbol = money.currency(code).symbol + " "
        self.rate_input.setPrefix(symbol)
        for r in range(self.table.rowCount()):
            spin = self.table.cellWidget(r, L_RATE)
            if spin is not None:
                spin.setPrefix(symbol)

    def _currency_changed(self, *_):
        code = self.currency()
        self._apply_symbol(code)
        if not self._rate_touched:
            rate = default_rate(code)
            self.rate_input.setValue(float(rate))
        if not self._tax_touched:
            # FIX_PLAN: rupee bids start with GST, foreign clients with none.
            self.tax_input.setValue(float(money.default_tax_rate(code)))
            self.tax_label_input.setText(DB.tax_label(code))
        self._recompute()

    def _rate_changed(self, value):
        """Lines that were at the old default rate follow the new one."""
        if self._filling:
            return
        old = getattr(self, "_last_rate", None)
        new = DB.money(Decimal(str(value)))
        for r, line in enumerate(self.lines):
            if old is not None and DB.money(line.day_rate) == old:
                line.day_rate = new
                spin = self.table.cellWidget(r, L_RATE)
                if spin is not None:
                    spin.blockSignals(True)
                    spin.setValue(float(new))
                    spin.blockSignals(False)
        self._last_rate = new
        self._recompute()

    # ------------------------------------------------------------ lines
    def _complexity_choices(self, current: str) -> List[Tuple[str, str]]:
        names = DB.complexities()
        out = [(n, n) for n in names]
        if current and current not in names and current != "Mixed":
            # A complexity the studio has since removed: shown, and its days
            # kept, rather than quietly turned into Medium and re-priced.
            out.append((f"{current} (not in studio settings)", current))
        return out

    def add_line(self, *_, refresh=True, line: DB.BidLine = None):
        rate = DB.money(Decimal(str(self.rate_input.value())))
        if line is None:
            complexity = "Medium" if "Medium" in DB.complexities() else DB.complexities()[0]
            line = DB.BidLine(label="", department="comp", complexity=complexity, shot_count=1,
                              days_per_shot=Decimal(str(DB.days_per_shot(complexity))), day_rate=rate)
        self.lines.append(line)
        if refresh:
            self._rebuild_table()
            self.table.selectRow(len(self.lines) - 1)
            editor = self.table.cellWidget(len(self.lines) - 1, L_LABEL)
            if editor is not None:
                editor.setFocus()
            self._recompute()

    def duplicate_line(self):
        r = self._current_row()
        if r is None:
            return
        src = self.lines[r]
        copy = DB.BidLine(label=src.label, department=src.department, complexity=src.complexity,
                          shot_count=src.shot_count, days_per_shot=src.days_per_shot,
                          day_rate=src.day_rate, shot_name="", reel=src.reel, notes=src.notes)
        self.lines.insert(r + 1, copy)
        self._rebuild_table()
        self.table.selectRow(r + 1)
        self._recompute()

    def remove_line(self):
        r = self._current_row()
        if r is None:
            return
        del self.lines[r]
        self._rebuild_table()
        if self.lines:
            self.table.selectRow(min(r, len(self.lines) - 1))
        self._recompute()

    def move_line(self, step: int):
        r = self._current_row()
        if r is None or not 0 <= r + step < len(self.lines):
            return
        self.lines[r], self.lines[r + step] = self.lines[r + step], self.lines[r]
        self._rebuild_table()
        self.table.selectRow(r + step)
        self._recompute()

    def _current_row(self) -> Optional[int]:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if not rows:
            focus = self.focusWidget()
            for r in range(self.table.rowCount()):
                for c in range(self.table.columnCount()):
                    if self.table.cellWidget(r, c) is focus:
                        return r
            return None
        return min(rows)

    def _sync_line_buttons(self):
        if self.read_only:
            return
        r = self._current_row()
        for button in (self.duplicate_button, self.remove_button):
            button.setEnabled(r is not None)
        self.up_button.setEnabled(r is not None and r > 0)
        self.down_button.setEnabled(r is not None and r < len(self.lines) - 1)

    def _rebuild_table(self):
        symbol = money.currency(self.currency()).symbol + " "
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.lines))
        for r, line in enumerate(self.lines):
            label = QLineEdit(line.label)
            label.setPlaceholderText("e.g. SH010 comp, Roto – wide shots")
            label.textChanged.connect(lambda text, l=line: self._set_line(l, "label", text))
            self.table.setCellWidget(r, L_LABEL, label)

            shot = QLineEdit(line.shot_name)
            shot.setPlaceholderText("optional")
            shot.setToolTip("Name a shot to have it created on the dashboard when the bid is won.")
            shot.textChanged.connect(lambda text, l=line: self._set_line(l, "shot_name", text.strip()))
            self.table.setCellWidget(r, L_SHOT, shot)

            dept = QComboBox()
            dept.addItem("—", "")
            for key, name in self._departments:
                dept.addItem(name, key)
            index = dept.findData(line.department)
            if index < 0 and line.department:
                dept.addItem(line.department, line.department)
                index = dept.count() - 1
            dept.setCurrentIndex(max(index, 0))
            dept.currentIndexChanged.connect(
                lambda _i, l=line, w=dept: self._set_line(l, "department", w.currentData() or ""))
            self.table.setCellWidget(r, L_DEPT, dept)

            complexity = QComboBox()
            for text, value in self._complexity_choices(line.complexity):
                complexity.addItem(text, value)
            index = complexity.findData(line.complexity)
            if index < 0:
                complexity.insertItem(0, "—", "")
                index = 0
            complexity.setCurrentIndex(index)
            complexity.currentIndexChanged.connect(
                lambda _i, l=line, w=complexity, row=r: self._complexity_changed(l, w, row))
            self.table.setCellWidget(r, L_COMPLEXITY, complexity)

            shots = QSpinBox()
            shots.setRange(0, 100000)
            shots.setValue(int(line.shot_count or 0))
            shots.setAlignment(Qt.AlignmentFlag.AlignRight)
            shots.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            shots.valueChanged.connect(lambda v, l=line: self._set_line(l, "shot_count", int(v)))
            self.table.setCellWidget(r, L_SHOTS, shots)

            per_shot = QDoubleSpinBox()
            per_shot.setRange(0, 9999)
            per_shot.setDecimals(2)
            per_shot.setAlignment(Qt.AlignmentFlag.AlignRight)
            per_shot.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
            per_shot.setValue(float(line.days_per_shot))
            per_shot.valueChanged.connect(
                lambda v, l=line: self._set_line(l, "days_per_shot", Decimal(str(round(v, 2)))))
            self.table.setCellWidget(r, L_PER_SHOT, per_shot)

            rate = _money_spin()
            rate.setPrefix(symbol)
            rate.setValue(float(line.day_rate))
            rate.valueChanged.connect(
                lambda v, l=line: self._set_line(l, "day_rate", DB.money(Decimal(str(v)))))
            self.table.setCellWidget(r, L_RATE, rate)

            self.table.setItem(r, L_DAYS, make_item("", align=Qt.AlignmentFlag.AlignRight
                                                    | Qt.AlignmentFlag.AlignVCenter))
            self.table.setItem(r, L_COST, make_item("", align=Qt.AlignmentFlag.AlignRight
                                                    | Qt.AlignmentFlag.AlignVCenter))
        self._sync_line_buttons()
        if self.read_only:
            self._apply_read_only()

    def _complexity_changed(self, line: DB.BidLine, combo: QComboBox, row: int):
        value = combo.currentData() or ""
        line.complexity = value
        if value in DB.multipliers():
            # The studio's days per shot for it - still editable on the line.
            line.days_per_shot = Decimal(str(DB.days_per_shot(value)))
            spin = self.table.cellWidget(row, L_PER_SHOT)
            if spin is not None:
                spin.blockSignals(True)
                spin.setValue(float(line.days_per_shot))
                spin.blockSignals(False)
        self._recompute()

    def _set_line(self, line: DB.BidLine, attr: str, value):
        setattr(line, attr, value)
        self._recompute()

    # ------------------------------------------------------------ figures
    def header(self):
        """The bid as the header fields have it (a Bid for the repository)."""
        from slate.core.infra.bid_repository import Bid
        base = self.bid
        return Bid(
            id=base.id if base else None,
            project_code=self.project_code(),
            project_name=base.project_name if base else "",
            client_name=self.client_input.text().strip(),
            currency=self.currency(),
            day_rate=DB.money(Decimal(str(self.rate_input.value()))),
            margin=Decimal(str(round(self.margin_input.value(), 2))),
            discount=Decimal(str(round(self.discount_input.value(), 2))),
            tax=Decimal(str(round(self.tax_input.value(), 2))),
            tax_label=self.tax_label_input.text().strip() or DB.tax_label(self.currency()),
            notes=self.notes_input.text().strip(),
            status=base.status if base else DB.DRAFT,
            bid_group=base.bid_group if base else None,
            revision=base.revision if base else 1,
            created_by=base.created_by if base else "",
        )

    def totals(self) -> Optional[DB.BidTotals]:
        h = self.header()
        try:
            return DB.price_bid(self.lines, h.margin, h.discount, h.tax)
        except DB.BidError:
            return None

    def _recompute(self, *_):
        if self._filling or not hasattr(self, "total_labels"):
            return
        code = self.currency()
        for r, line in enumerate(self.lines):
            days_item, cost_item = self.table.item(r, L_DAYS), self.table.item(r, L_COST)
            if days_item is not None:
                days_item.setText(DB.fmt_days(line.days))
            if cost_item is not None:
                cost_item.setText(money.format_money(line.cost, code))
        h = self.header()
        totals = self.totals()
        values = {}
        if totals is not None:
            values = {
                "days": DB.fmt_days(totals.days),
                "cost": money.format_money(totals.cost, code),
                "margin": f"{money.format_money(totals.margin_amount, code)}  "
                          f"({DB.fmt_percent(totals.margin_percent)})",
                "price": money.format_money(totals.price, code),
                "discount": ("−" + money.format_money(totals.discount_amount, code)
                             + f"  ({DB.fmt_percent(totals.discount_percent)})"),
                "taxable": money.format_money(totals.taxable, code),
                "tax": money.format_money(totals.tax_amount, code)
                       + f"  ({DB.fmt_percent(totals.tax_percent)})",
                "total": money.format_money(totals.total, code),
            }
        for key, (name, value) in self.total_labels.items():
            value.setText(values.get(key, "—"))
            hide = key in ("discount", "taxable") and (totals is None or not totals.discount_amount)
            name.setVisible(not hide)
            value.setVisible(not hide)
        self.total_labels["tax"][0].setText(h.tax_label or DB.WORDS["tax"])
        self.total_labels["total"][1].setToolTip(f"{h.currency}: what the client pays")

        margin = self.margin_input.value()
        _set(self.margin_hint, "Above 50% - check this is the margin you mean."
             if margin > DB.MARGIN_WARNING_PERCENT else "")
        studio_rate = default_rate(code)
        rate = Decimal(str(self.rate_input.value()))
        _set(self.rate_hint, "More than ten times the studio's day rate - a typing slip?"
             if studio_rate > 0 and rate > studio_rate * 10 else
             ("The studio has no day rate in this currency - type one." if rate <= 0 else ""))

        if self.read_only:
            return
        problems = DB.check_bid(h.project_code, self.lines, h.margin, h.discount, h.tax)
        _set(self.problems, problems[0] if problems else "")
        self.save_button.setEnabled(not problems)
        self.save_button.setToolTip(problems[0] if problems else "")

    # ------------------------------------------------------------ tracker import
    def import_shots(self):
        code = self.project_code()
        if not code or self.repo is None:
            return
        try:
            shots, skipped = self.repo.importable_shots(code)
        except DatabaseUnavailableError:
            _set(self.problems, "Can't reach the studio database, so the shots could not be read.")
            return
        except Exception as exc:
            logger.exception("Tracker shots not read")
            _set(self.problems, f"Could not read the shots of {code}: {exc}")
            return
        have = {(l.reel.casefold(), l.shot_name.casefold()) for l in self.lines if l.shot_name}
        fresh = [(reel, shot) for reel, shot in shots if (reel.casefold(), shot.casefold()) not in have]
        dialog = ImportShotsDialog(self, len(fresh), skipped, self._departments, DB.complexities())
        if dialog.exec() != QDialog.DialogCode.Accepted or not fresh:
            if not fresh:
                _set(self.import_note, f"Every shot of {code} on the dashboard is already a line."
                     if shots else f"{code} has no shots on the dashboard yet.")
            return
        dept = dialog.dept.currentData() or "comp"
        complexity = dialog.complexity.currentText()
        rate = DB.money(Decimal(str(self.rate_input.value())))
        # An empty first line (a new bid's starting line) is replaced.
        if len(self.lines) == 1 and not self.lines[0].label and not self.lines[0].shot_name:
            self.lines.clear()
        for reel, shot in fresh:
            self.lines.append(DB.BidLine(label=f"{shot} {dept}", shot_name=shot, reel=reel,
                                         department=dept, complexity=complexity, shot_count=1,
                                         days_per_shot=Decimal(str(DB.days_per_shot(complexity))),
                                         day_rate=rate))
        self._rebuild_table()
        self._recompute()
        _set(self.import_note, f"Imported {len(fresh)} shots"
             + (f" ({skipped} omitted skipped)" if skipped else "") + ".")

    # ------------------------------------------------------------ saving
    def _save(self):
        h = self.header()
        if self.repo is None:
            self.accept()
            return
        try:
            if self.bid is None:
                self.saved_id = self.repo.create(h, self.lines, by=self.username)
            else:
                self.repo.update(h, self.lines, by=self.username)
                self.saved_id = h.id
        except DatabaseUnavailableError:
            _set(self.problems, "Can't reach the studio database, so nothing was saved. "
                                "Try again when it is back.")
            return
        except (DB.BidError, PermissionError) as exc:
            _set(self.problems, str(exc))
            return
        except Exception as exc:
            logger.exception("Bid not saved")
            _set(self.problems, f"The bid was not saved: {exc}")
            return
        self.accept()

    def _revise(self):
        if self.repo is None or self.bid is None:
            return
        try:
            self.revision_id = self.repo.revise(self.bid.id, by=self.username)
        except DatabaseUnavailableError:
            _set(self.banner, "Can't reach the studio database - no revision was made.")
            return
        except (DB.BidError, PermissionError) as exc:
            _set(self.banner, str(exc))
            return
        self.accept()

    def export_pdf(self):
        from slate.gui.tabs.prod_bidding_tab import export_bid_pdf
        totals = self.totals()
        if totals is None:
            return
        export_bid_pdf(self, self.header(), self.lines, totals)
