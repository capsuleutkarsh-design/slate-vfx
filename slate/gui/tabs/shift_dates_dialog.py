"""
Shift a milestone, and see what moves with it before anything is written.

The old dialog was a bare number box labelled "Shift by (days)" with a yellow
"Shift Downstream" button. It moved the milestone and everything after it by
calendar days, completed work included, without showing what would move or
what it would break, then said "Shifted milestone 87 and all its dependencies
by 1 days." - even when nothing had moved.

This one shows the plan (core/domain/scheduling.plan_shift) live as the
numbers change: every milestone that moves with its old and new dates, the
ones kept where they are and why, and any dependency the move would break.
Breaking one needs an explicit tick.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QSpinBox, QTableWidget, QVBoxLayout,
)

from slate.core.domain import scheduling as DS
from slate.core.domain.dates import format_date, format_range
from slate.core.infra.gate import Gate
from slate.gui.core.controls import form_layout, style_button
from slate.gui.core.table_style import set_cell_status, style_table
from slate.gui.components.table_tools import make_item


class ShiftDatesDialog(QDialog):
    def __init__(self, parent=None, *, milestones: Sequence[DS.Milestone] = (), root_id: int = None,
                 calendar: DS.WorkCalendar = None, days: int = 1,
                 root_to: Optional[Tuple] = None, unit: str = DS.WORKING_DAYS):
        super().__init__(parent)
        self.milestones = list(milestones)
        self.by_id = DS.index(self.milestones)
        self.root = self.by_id.get(root_id)
        self.calendar = calendar or DS.WorkCalendar()
        # A bar dragged on the timeline: exact new dates for the milestone.
        self.root_to = root_to
        self.plan: Optional[DS.ShiftPlan] = None

        name = self.root.name if self.root else "milestone"
        self.setWindowTitle("Shift dates")
        self.setMinimumSize(640, 420)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        outer.setSpacing(Gate.SPACE_3)

        intro = QLabel(f"Move <b>{_html(name)}</b> and everything that depends on it.")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = form_layout()
        self.days_spin = QSpinBox()
        self.days_spin.setRange(-365, 365)
        self.days_spin.setValue(int(days))
        self.days_spin.setToolTip("Negative moves it earlier.")
        self.unit_cb = QComboBox()
        self.unit_cb.addItem("Working days (skip weekly offs and holidays)", DS.WORKING_DAYS)
        self.unit_cb.addItem("Calendar days", DS.CALENDAR_DAYS)
        self.unit_cb.setCurrentIndex(max(self.unit_cb.findData(unit), 0))
        row = QHBoxLayout()
        row.addWidget(self.days_spin)
        row.addWidget(self.unit_cb, 1)
        if root_to is None:
            form.addRow("Shift by", row)
        else:
            self.days_spin.hide()
            self.unit_cb.hide()
            form.addRow("New dates", QLabel(format_range(*root_to)))

        self.mode_cb = QComboBox()
        self.mode_cb.addItem("Push what follows only as far as it has to", DS.PUSH)
        self.mode_cb.addItem("Move everything after it by the same amount", DS.SAME)
        if root_to is not None:
            self.mode_cb.setEnabled(False)
        form.addRow("Dependents", self.mode_cb)
        self.completed_box = QCheckBox("Also move completed and cancelled milestones")
        form.addRow("", self.completed_box)
        outer.addLayout(form)

        self.preview = QTableWidget(0, 4)
        self.preview.setHorizontalHeaderLabels(["Milestone", "Now", "After", "Note"])
        style_table(self.preview, {"Milestone": ("interactive", 180), "Now": "contents",
                                   "After": "contents", "Note": "stretch"},
                    sortable=False)
        self.preview.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        # The Note says what would break; it wraps rather than being cut.
        self.preview.setWordWrap(True)
        outer.addWidget(self.preview, 1)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)
        self.anyway_box = QCheckBox("Apply anyway - I will sort out the clashes")
        self.anyway_box.hide()
        outer.addWidget(self.anyway_box)

        self.buttons = QDialogButtonBox()
        self.apply_button = self.buttons.addButton("Shift dates", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        style_button(self.apply_button, "primary")
        style_button(cancel, "secondary")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)

        for signal in (self.days_spin.valueChanged, self.unit_cb.currentIndexChanged,
                       self.mode_cb.currentIndexChanged, self.completed_box.toggled,
                       self.anyway_box.toggled):
            signal.connect(self.recompute)
        self.recompute()
        self.days_spin.setFocus()
        self.days_spin.selectAll()

    def recompute(self, *_):
        if self.root is None:
            self.apply_button.setEnabled(False)
            self.summary.setText("That milestone no longer exists.")
            return
        delta = self.days_spin.value()
        if self.root_to is not None and self.root.end:
            delta = (self.root_to[1] - self.root.end).days
        self.plan = DS.plan_shift(
            self.milestones, self.root.id, delta,
            unit=self.unit_cb.currentData(), mode=self.mode_cb.currentData(),
            include_completed=self.completed_box.isChecked(), calendar=self.calendar,
            root_to=self.root_to)
        plan = self.plan
        conflicts = {cid: text for cid, _n, text in plan.conflicts}
        rows = []
        for m in plan.moved:
            note = conflicts.pop(m.id, "")
            rows.append((m.name, format_range(m.old_start, m.old_end),
                         format_range(m.new_start, m.new_end), note, "bad" if note else ""))
        for mid, name, why in plan.skipped:
            m = self.by_id.get(mid)
            now = format_range(m.start, m.end) if m and m.has_dates else ""
            rows.append((name, now, "stays", why, "idle"))
        for mid, text in conflicts.items():
            m = self.by_id.get(mid)
            rows.append((m.name if m else f"#{mid}", format_range(m.start, m.end) if m else "",
                         "stays", text, "bad"))
        self.preview.setRowCount(len(rows))
        for r, (name, before, after, note, tone) in enumerate(rows):
            self.preview.setItem(r, 0, make_item(name, tooltip=name))
            self.preview.setItem(r, 1, make_item(before))
            self.preview.setItem(r, 2, make_item(after))
            note_item = make_item(note, tooltip=note)
            if tone:
                set_cell_status(note_item, tone, background=tone == "bad")
            self.preview.setItem(r, 3, note_item)
        self.preview.resizeRowsToContents()

        has_conflicts = bool(plan.conflicts)
        self.anyway_box.setVisible(has_conflicts)
        text = plan.message(self.root.name, preview=True)
        if has_conflicts:
            one = len(plan.conflicts) == 1
            text += (f" {DS.plural(len(plan.conflicts), 'milestone')} would start before "
                     f"what {'it waits' if one else 'they wait'} on has ended.")
        self.summary.setText(text)
        self.summary.setStyleSheet(f"color: {Gate.BAD if has_conflicts else Gate.TEXT_2};")
        self.apply_button.setEnabled(bool(plan.moved) and (not has_conflicts
                                                           or self.anyway_box.isChecked()))


def _html(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
