"""
The morning view: where the show is, who is loaded, what is late.

Built from the shots already on screen, so it reflects exactly what the
coordinator is looking at - including any filter they have applied.
"""

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ut_vfx.core.domain.production_summary import build_summary


def _headline(value, caption, colour="#E8E6E1"):
    """One big number with a caption under it."""
    box = QFrame()
    box.setFrameShape(QFrame.Shape.StyledPanel)
    box.setStyleSheet(
        "QFrame { background: rgba(255,255,255,0.04);"
        " border: 1px solid rgba(255,255,255,0.10); border-radius: 6px; }"
    )
    layout = QVBoxLayout(box)
    layout.setContentsMargins(14, 10, 14, 10)
    layout.setSpacing(2)

    number = QLabel(str(value))
    number.setStyleSheet(f"color: {colour}; font-size: 22px; font-weight: 600;"
                         " background: transparent; border: none;")
    label = QLabel(caption)
    label.setStyleSheet("color: #B4B1AA; font-size: 11px;"
                        " background: transparent; border: none;")

    layout.addWidget(number)
    layout.addWidget(label)
    return box


def _section(title):
    label = QLabel(title)
    label.setStyleSheet("color: #E8E6E1; font-size: 13px; font-weight: 600;"
                        " margin-top: 10px;")
    return label


def _table(headers, rows, max_visible=12):
    table = QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    table.setAlternatingRowColors(True)

    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            if c > 0:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(r, c, item)

    table.resizeColumnsToContents()
    header = table.horizontalHeader()
    header.setStretchLastSection(True)

    row_height = table.verticalHeader().defaultSectionSize()
    visible = min(max(len(rows), 1), max_visible)
    table.setMinimumHeight(row_height * visible + header.height() + 4)
    table.setMaximumHeight(row_height * visible + header.height() + 4)
    return table


class ProductionSummaryDialog(QDialog):
    """Read-only roll-up of the currently loaded shots."""

    def __init__(self, shots, project_name="", parent=None, today=None):
        super().__init__(parent)
        self.setWindowTitle("Production Summary")
        self.setMinimumSize(760, 620)

        summary = build_summary(shots, today=today or date.today())

        outer = QVBoxLayout(self)

        title = QLabel(project_name or "Production Summary")
        title.setStyleSheet("color: #E8E6E1; font-size: 16px; font-weight: 600;")
        outer.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setSpacing(6)

        # --- headline numbers ------------------------------------------
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        tiles.addWidget(_headline(summary.total_shots, "shots"))
        tiles.addWidget(_headline(f"{summary.percent_complete}%", "approved",
                                  "#3EA8BF"))
        tiles.addWidget(_headline(summary.outstanding_bid_days,
                                  "bid days remaining", "#3EA8BF"))
        tiles.addWidget(_headline(len(summary.late), "overdue",
                                  "#D9635F" if summary.late else "#B4B1AA"))
        tiles.addWidget(_headline(len(summary.unassigned), "unassigned",
                                  "#D9A441" if summary.unassigned else "#B4B1AA"))
        tiles.addStretch()
        layout.addLayout(tiles)

        # --- status breakdown ------------------------------------------
        if summary.status_counts:
            layout.addWidget(_section("Status"))
            layout.addWidget(_table(
                ["Status", "Shots"],
                [(status, count) for status, count in summary.status_counts.items()],
                max_visible=8,
            ))

        # --- department load -------------------------------------------
        if summary.departments:
            layout.addWidget(_section("Departments"))
            layout.addWidget(_table(
                ["Department", "Shots", "Bid days", "Remaining",
                 "Not started", "In progress", "Done", "% done"],
                [(d.label, d.shots, round(d.bid_days, 1), d.outstanding_days,
                  d.not_started, d.in_progress, d.done, f"{d.percent_done}%")
                 for d in summary.departments],
            ))

        # --- artist load ------------------------------------------------
        if summary.artists:
            layout.addWidget(_section("Artist load (busiest first)"))
            layout.addWidget(_table(
                ["Artist", "Shots", "Bid days", "Remaining"],
                [(a.name, a.shots, a.bid_days, a.outstanding_days)
                 for a in summary.artists],
            ))

        # --- late -------------------------------------------------------
        if summary.late:
            layout.addWidget(_section("Overdue"))
            layout.addWidget(_table(
                ["Shot", "Reel", "Target", "Days late", "Status", "Artist"],
                [(e.shot_name, e.reel, e.target.isoformat(), e.days_late,
                  e.status, e.artist) for e in summary.late],
            ))

        if summary.due_soon:
            layout.addWidget(_section("Due in the next 7 days"))
            layout.addWidget(_table(
                ["Shot", "Reel", "Target", "Status", "Artist"],
                [(e.shot_name, e.reel, e.target.isoformat(), e.status, e.artist)
                 for e in summary.due_soon],
            ))

        if summary.unassigned:
            layout.addWidget(_section("Nobody assigned"))
            names = QLabel(", ".join(summary.unassigned))
            names.setWordWrap(True)
            names.setStyleSheet("color: #D9A441;")
            layout.addWidget(names)

        layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        outer.addWidget(buttons)

        self.summary = summary
