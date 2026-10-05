"""
A won bid against what the dashboard holds - the cost tracking the sidebar
always promised ("Project Bidding & Cost Tracking") and the tab never had.

For the selected won bid, per department: the days that were bid, the days
planned on the dashboard (bid days on its tasks), the days delivered (tasks in
a done status), the actual days where somebody recorded them, how much of the
bid is burned, what remains, and the variance - in days and in money at the
bid's own rate, in the bid's currency. A department planned or spent above its
bid is red. Below: shots bid but not on the dashboard, and dashboard shots the
bid never priced. The computation is core/domain/bidding.track(); this is the
picture.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTableWidget, QVBoxLayout, QWidget

from slate.core.domain import bidding as DB
from slate.core.domain.money import format_money
from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button
from slate.gui.core.table_style import dim_cell, set_cell_status, style_table
from slate.gui.components.table_tools import make_item

logger = logging.getLogger(__name__)

# 'Delivered %': delivered days as a share of bid days. It was 'Burned',
# which producers read as money or time spent.
HEADERS = ["Department", "Bid days", "Planned days", "Delivered days", "Actual days", "Delivered %",
           "Remaining", "Plan vs bid", "Bid cost", "Planned cost"]


class BidTrackingView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bid = None
        self.tracking: Optional[DB.Tracking] = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, Gate.SPACE_2, 0, 0)
        layout.setSpacing(Gate.SPACE_2)

        top = QHBoxLayout()
        self.title = QLabel("Tracking")
        self.title.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600; font-size: {Gate.SIZE_LG}px;")
        top.addWidget(self.title)
        self.summary = QLabel("")
        self.summary.setStyleSheet(f"color: {Gate.TEXT_2};")
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(self.summary, 1)
        self.export_button = make_button("Export…", "ghost", icon="download",
                                         tooltip="Save this tracking table as CSV or Excel",
                                         on_click=lambda: self.export())
        top.addWidget(self.export_button)
        layout.addLayout(top)

        self.hint = QLabel("Select a won bid to see how it is tracking against the dashboard.")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.hint)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        style_table(self.table, {"Department": "stretch", **{h: "numeric" for h in HEADERS[1:]}},
                    sortable=False)
        layout.addWidget(self.table, 1)
        self.coverage = QLabel("")
        self.coverage.setWordWrap(True)
        self.coverage.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: {Gate.SIZE_SM}px;")
        layout.addWidget(self.coverage)
        # With the table hidden the hint stays under the title, not half way down.
        layout.addStretch()
        self.show_nothing()

    def show_nothing(self, message: str = ""):
        self.bid = None
        self.tracking = None
        self.title.setText("Tracking")
        self.summary.setText("")
        self.hint.setText(message or "Select a won bid to see how it is tracking against the dashboard.")
        self.hint.show()
        self.table.hide()
        self.coverage.hide()
        self.export_button.setEnabled(False)

    def show_tracking(self, bid, tracking: DB.Tracking):
        self.bid, self.tracking = bid, tracking
        code = bid.currency
        self.title.setText(f"Tracking – {bid.title}")
        parts = [f"Bid {format_money(tracking.bid_cost, code)}",
                 f"Planned {format_money(tracking.planned_cost, code)}",
                 f"Delivered {format_money(tracking.done_cost, code)}"]
        if tracking.actual_recorded:
            parts.append(f"Actual {format_money(tracking.actual_cost, code)}")
        self.summary.setText("  ·  ".join(parts) + "   (at the bid's day rates, cost before margin)")
        self.hint.setVisible(not tracking.departments)
        if not tracking.departments:
            self.hint.setText("Nothing to compare yet: the bid has no lines with days and the "
                              "dashboard has no tasks for this project.")
        self.table.setVisible(bool(tracking.departments))
        self.export_button.setEnabled(bool(tracking.departments))

        def days(value):
            return make_item(DB.fmt_days(value), sort_value=value)

        from slate.core.domain.bid_export import department_name
        self.table.setRowCount(len(tracking.departments) + 1)
        for r, d in enumerate(tracking.departments):
            self.table.setItem(r, 0, make_item(department_name(d.department)))
            self.table.setItem(r, 1, days(d.bid_days))
            self.table.setItem(r, 2, days(d.planned_days))
            self.table.setItem(r, 3, days(d.done_days))
            actual = days(d.actual_days) if tracking.actual_recorded else make_item("—")
            if not tracking.actual_recorded:
                dim_cell(actual)
                actual.setToolTip("Nobody has recorded actual days on these tasks yet "
                                  "(VFX Dashboard, a shot's department).")
            elif d.actual_days > d.bid_days:
                set_cell_status(actual, "bad", background=False)
            self.table.setItem(r, 4, actual)
            burn = d.burn_percent
            self.table.setItem(r, 5, make_item("—" if burn is None else DB.fmt_percent(burn),
                                               sort_value=burn))
            self.table.setItem(r, 6, days(d.remaining_days))
            if d.planned_days <= 0 < d.bid_days:
                # Nothing on the dashboard yet is not a saving.
                variance = make_item("not planned", sort_value=d.variance_days)
                dim_cell(variance)
                variance.setToolTip("No days are planned on the dashboard for this department yet.")
            else:
                variance = make_item(("+" if d.variance_days > 0 else "") + DB.fmt_days(d.variance_days),
                                     sort_value=d.variance_days)
            if d.variance_days > 0:
                set_cell_status(variance, "bad")
                variance.setToolTip("More days are planned on the dashboard than were bid.")
            elif d.variance_days < 0 and d.planned_days > 0:
                set_cell_status(variance, "ok", background=False)
            self.table.setItem(r, 7, variance)
            self.table.setItem(r, 8, make_item(format_money(d.bid_cost, code), sort_value=d.bid_cost))
            planned = make_item(format_money(d.planned_cost, code), sort_value=d.planned_cost)
            if d.over:
                set_cell_status(planned, "bad", background=False)
            self.table.setItem(r, 9, planned)

        # The bid's totals, in days and money.
        total = len(tracking.departments)
        cells = [make_item("Total"), days(tracking.bid_days), days(tracking.planned_days),
                 days(tracking.done_days),
                 days(tracking.actual_days) if tracking.actual_recorded else make_item("—"),
                 make_item(DB.fmt_percent(tracking.done_days * 100 / tracking.bid_days)
                           if tracking.bid_days else "—"),
                 days(tracking.bid_days - tracking.done_days),
                 make_item(("+" if tracking.planned_days > tracking.bid_days else "")
                           + DB.fmt_days(tracking.planned_days - tracking.bid_days)),
                 make_item(format_money(tracking.bid_cost, code)),
                 make_item(format_money(tracking.planned_cost, code))]
        for c, item in enumerate(cells):
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            if c:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(total, c, item)

        notes = []
        if tracking.bid_not_on_tracker:
            notes.append(f"Bid but not on the dashboard ({len(tracking.bid_not_on_tracker)}): "
                         + ", ".join(tracking.bid_not_on_tracker[:12])
                         + (" …" if len(tracking.bid_not_on_tracker) > 12 else ""))
        if tracking.tracker_not_in_bid:
            notes.append(f"On the dashboard but not in the bid ({len(tracking.tracker_not_in_bid)}): "
                         + ", ".join(tracking.tracker_not_in_bid[:12])
                         + (" …" if len(tracking.tracker_not_in_bid) > 12 else ""))
        self.coverage.setText("\n".join(notes))
        self.coverage.setVisible(bool(notes))

    def export(self, path: str = None):
        """The tracking as plain numbers with a currency column, like Export list."""
        if self.tracking is None:
            return None
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        from slate.core.domain.bid_export import tracking_rows
        from slate.core.domain.table_export import default_filename, export_rows
        from slate.gui.components.feedback import toast
        if path is None:
            default = str(Path.home() / "Documents" / default_filename(
                f"tracking_{self.bid.project_code}_v{self.bid.revision}"))
            path, _ = QFileDialog.getSaveFileName(self, "Export tracking", default,
                                                  "CSV (*.csv);;Excel workbook (*.xlsx)")
            if not path:
                return None
        headers, rows = tracking_rows(self.bid, self.tracking)
        try:
            export_rows(path, headers, rows)
        except OSError as exc:
            toast(self, f"Could not write {path}: {exc}", "error")
            return None
        toast(self, f"Exported the tracking to {Path(path).name}.", "success")
        return path
