"""
Two revisions of a bid side by side: which lines were added, removed or
changed (and what about them), and how the figures moved. Before revisions
existed an edit overwrote the bid in place, so "what did we change since we
sent v2?" had no answer.
"""

from __future__ import annotations

from typing import Callable, List, Sequence

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QTableWidget, QVBoxLayout,
)

from slate.core.domain import bidding as DB
from slate.core.domain.money import format_money
from slate.core.infra.gate import Gate
from slate.gui.core.controls import style_button
from slate.gui.core.table_style import set_cell_status, style_table
from slate.gui.components.table_tools import make_item


class CompareDialog(QDialog):
    """
    revisions    the bid's revisions (Bid records), oldest first
    read_lines   bid id -> its lines
    """

    def __init__(self, parent, revisions: Sequence, read_lines: Callable[[int], List[DB.BidLine]]):
        super().__init__(parent)
        self.revisions = list(revisions)
        self.read_lines = read_lines
        first = self.revisions[0] if self.revisions else None
        self.setWindowTitle(f"Compare revisions – {first.project_code}" if first else "Compare revisions")
        self.setMinimumSize(820, 480)
        layout = QVBoxLayout(self)
        layout.setSpacing(Gate.SPACE_3)

        row = QHBoxLayout()
        self.before = QComboBox()
        self.after = QComboBox()
        for bid in self.revisions:
            text = f"v{bid.revision} – {DB.status_label(bid.status)}"
            self.before.addItem(text, bid.id)
            self.after.addItem(text, bid.id)
        self.before.setCurrentIndex(max(len(self.revisions) - 2, 0))
        self.after.setCurrentIndex(max(len(self.revisions) - 1, 0))
        row.addWidget(QLabel("From"))
        row.addWidget(self.before)
        row.addWidget(QLabel("to"))
        row.addWidget(self.after)
        row.addStretch()
        layout.addLayout(row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Change", "Line", "Before", "After", "Cost change"])
        style_table(self.table, {"Change": "contents", "Line": "stretch", "Before": ("interactive", 200),
                                 "After": ("interactive", 200), "Cost change": "numeric"}, sortable=False)
        # The line's cost is at the end of each description: wrap, never cut it.
        self.table.setWordWrap(True)
        layout.addWidget(self.table, 1)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        buttons = QDialogButtonBox()
        close = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        style_button(close, "secondary")
        buttons.rejected.connect(self.reject)
        close.clicked.connect(self.reject)
        layout.addWidget(buttons)

        self.before.currentIndexChanged.connect(self.refresh)
        self.after.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def _bid(self, bid_id):
        return next((b for b in self.revisions if b.id == bid_id), None)

    @staticmethod
    def _describe(line: DB.BidLine, code: str) -> str:
        if line is None:
            return ""
        return (f"{line.shot_count} × {DB.fmt_days(line.days_per_shot)} at "
                f"{format_money(line.day_rate, code)} = {format_money(line.cost, code)}")

    def refresh(self, *_):
        a, b = self._bid(self.before.currentData()), self._bid(self.after.currentData())
        if a is None or b is None:
            return
        code = b.currency
        self.changes = DB.compare(self.read_lines(a.id), self.read_lines(b.id))
        self.table.setRowCount(len(self.changes))
        words = {"added": "Added", "removed": "Removed", "changed": "Changed"}
        tones = {"added": "ok", "removed": "bad", "changed": "warn"}
        for r, c in enumerate(self.changes):
            kind = make_item(words[c.kind])
            set_cell_status(kind, tones[c.kind], background=False)
            self.table.setItem(r, 0, kind)
            label = c.label + (f"  ({', '.join(c.fields)})" if c.fields else "")
            self.table.setItem(r, 1, make_item(label, tooltip=label))
            self.table.setItem(r, 2, make_item(self._describe(c.before, code)))
            self.table.setItem(r, 3, make_item(self._describe(c.after, code)))
            delta = c.cost_delta
            self.table.setItem(r, 4, make_item(DB.signed_money(delta, code), sort_value=delta))
        self.table.resizeRowsToContents()
        price_delta = b.estimated_budget - a.estimated_budget
        same_currency = a.currency == b.currency
        word = DB.WORDS["taxable"].lower()
        text = (f"v{a.revision} {format_money(a.estimated_budget, a.currency)} → "
                f"v{b.revision} {format_money(b.estimated_budget, b.currency)} {word}")
        if same_currency:
            text += f"  ({DB.signed_money(price_delta, code)})"
        if not self.changes:
            text += ". The lines are the same."
        self.summary.setText(text)
