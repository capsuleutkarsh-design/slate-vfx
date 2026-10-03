"""
The studio's bidding figures, edited where bids are made.

Day rate, days per shot by complexity and margin could only be changed by
hand-editing settings.json on each machine, and a complexity added there never
reached the dropdown (it was built from the defaults). They are studio
settings now (one value for every workstation): the day rate per currency and
the GST rate are the same keys the Settings tab's Studio Currency card edits,
the rest live in the 'bidding' setting. Saved together, checked first, and
applied at once.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from slate.core.domain import bidding as DB
from slate.core.domain import money
from slate.core.infra.db_results import DatabaseUnavailableError
from slate.core.infra.gate import Gate
from slate.gui.core.controls import form_layout, make_button, style_button
from slate.gui.core.table_style import style_table

logger = logging.getLogger(__name__)


class BiddingSettingsDialog(QDialog):
    def __init__(self, parent=None, *, username: str = "", store=None, can_edit_rates: bool = True):
        super().__init__(parent)
        # Day rates, GST and the studio name are studio-wide (the Settings
        # tab's Studio Currency card keeps the same keys): studio_settings
        # holders change them; an approver sees them read-only.
        self.can_edit_rates = can_edit_rates
        from slate.gui.tabs.bid_editor_dialog import _money_spin
        from slate.core.infra.studio_settings import StudioSettings
        import slate.core.infra.bid_repository  # noqa: F401 - registers the 'bidding' check
        self.store = store or StudioSettings()
        self.username = username
        self.setWindowTitle("Bidding settings")
        self.setMinimumSize(560, 640)
        layout = QVBoxLayout(self)
        layout.setSpacing(Gate.SPACE_3)
        intro = QLabel("These figures are the studio's: every workstation uses them for new bids. "
                       "Bids already made keep their own figures.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {Gate.TEXT_2};")
        layout.addWidget(intro)

        rates = self.store.get("day_rates") or {}
        figures = dict(self.store.get("bidding") or {})
        form = form_layout()
        self.rate_inputs = {}
        self.studio_input = QLineEdit(str(self.store.get("studio_name") or ""))
        self.studio_input.setMaxLength(120)
        self.studio_input.setPlaceholderText("Printed at the top of bid PDFs")
        form.addRow("Studio name", self.studio_input)
        for code, cur in money.CURRENCIES.items():
            spin = _money_spin()
            spin.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            spin.setPrefix(cur.symbol + " ")
            spin.setSpecialValueText("Not set")
            spin.setValue(float(DB.dec(rates.get(code))))
            self.rate_inputs[code] = spin
            form.addRow(f"Day rate ({code})", spin)
        self.margin_input = QDoubleSpinBox()
        self.margin_input.setRange(0, 95)
        self.margin_input.setSuffix(" %")
        self.margin_input.setValue(float(figures.get("margin_percent", DB.DEFAULT_MARGIN_PERCENT)))
        form.addRow("Default margin", self.margin_input)
        self.max_margin_input = QDoubleSpinBox()
        self.max_margin_input.setRange(1, 95)
        self.max_margin_input.setSuffix(" %")
        self.max_margin_input.setValue(float(figures.get("max_margin_percent", DB.DEFAULT_MAX_MARGIN_PERCENT)))
        self.max_margin_input.setToolTip("The highest margin a bid may have")
        form.addRow("Max margin", self.max_margin_input)
        # The default can never be above the maximum - checked as you type.
        self.margin_input.setMaximum(self.max_margin_input.value())
        self.max_margin_input.valueChanged.connect(self.margin_input.setMaximum)
        tax_row = QHBoxLayout()
        self.tax_label_input = QLineEdit(str(figures.get("tax_label") or "GST"))
        self.tax_label_input.setMaxLength(20)
        self.tax_label_input.setFixedWidth(90)
        self.gst_input = QDoubleSpinBox()
        self.gst_input.setRange(0, 100)
        self.gst_input.setSuffix(" %")
        self.gst_input.setValue(float(self.store.get("gst_rate") or 0))
        tax_row.addWidget(self.tax_label_input)
        tax_row.addWidget(self.gst_input, 1)
        form.addRow("Tax on rupee bids", tax_row)
        layout.addLayout(form)
        if not can_edit_rates:
            for w in [self.studio_input, self.gst_input, *self.rate_inputs.values()]:
                w.setReadOnly(True)
            self.gst_input.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
            note = QLabel("Day rates, the rupee tax and the studio name are changed by somebody "
                          "with Studio settings.")
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")
            layout.addWidget(note)

        layout.addWidget(QLabel("Days per shot, by complexity"))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Complexity", "Days per shot"])
        style_table(self.table, {"Complexity": "stretch", "Days per shot": ("fixed", 140)},
                    sortable=False, editable=True)
        DB_table = DB.multipliers()
        for name, days in sorted(DB_table.items(), key=lambda kv: kv[1]):
            self._add_row(name, days)
        self.table.setMinimumHeight(150)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(make_button("Add complexity", "secondary", icon="plus",
                                  on_click=lambda: self._add_row("", 1.0, edit=True)))
        row.addWidget(make_button("Remove", "ghost", icon="trash", on_click=self._remove_row))
        row.addStretch()
        layout.addLayout(row)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {Gate.BAD};")
        self.error.hide()
        layout.addWidget(self.error)

        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton("Save for the studio", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        style_button(self.save_button, "primary")
        style_button(cancel, "secondary")
        self.save_button.clicked.connect(self.save)
        cancel.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _add_row(self, name, days, edit=False):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(str(name)))
        spin = QDoubleSpinBox()
        spin.setRange(0.1, 999)
        spin.setDecimals(2)
        spin.setValue(float(days))
        self.table.setCellWidget(r, 1, spin)
        if edit:
            self.table.editItem(self.table.item(r, 0))

    def _remove_row(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def values(self) -> dict:
        multipliers = {}
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            name = " ".join((item.text() if item else "").split())
            if name:
                multipliers[name] = round(self.table.cellWidget(r, 1).value(), 2)
        figures = dict(self.store.get("bidding") or {})
        figures.update(multipliers=multipliers, multipliers_complete=True,
                       margin_percent=round(self.margin_input.value(), 2),
                       max_margin_percent=round(self.max_margin_input.value(), 2),
                       tax_label=self.tax_label_input.text().strip() or "GST")
        if not self.can_edit_rates:
            return {"bidding": figures}
        rates = {code: round(spin.value(), 2) for code, spin in self.rate_inputs.items()
                 if spin.value() > 0}
        return {"bidding": figures, "day_rates": rates, "gst_rate": round(self.gst_input.value(), 2),
                "studio_name": self.studio_input.text().strip()}

    def save(self) -> bool:
        values = self.values()
        if not values["bidding"]["multipliers"]:
            self._fail("Keep at least one complexity.")
            return False
        try:
            result = self.store.set_many(values, by=self.username)
        except DatabaseUnavailableError:
            self._fail("Can't reach the studio database, so nothing was saved.")
            return False
        if not result:
            # set_many names the setting ('bidding: The default margin ...'); the
            # person needs only the sentence.
            error = str(getattr(result, "error", "") or "The settings were not saved.")
            key, _sep, sentence = error.partition(": ")
            self._fail(sentence if key in values and sentence else error)
            return False
        DB.set_overrides(values["bidding"])
        self.accept()
        return True

    def _fail(self, text):
        self.error.setText(text)
        self.error.show()
