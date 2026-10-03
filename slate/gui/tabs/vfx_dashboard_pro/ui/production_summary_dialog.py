"""
The morning view: where the show is, who is loaded, what is late.

It used to be built from whatever was on screen with nothing saying so, so a
search or a scope produced a partial "summary" that looked like the whole show.
It now says which it shows - "Filtered: 33 of 262 shots" - with a switch to the
whole project, and the date it was worked out. Tables grow with their content
inside the one scroll area (no small scrolling boxes inside a scrolling
dialog), and numbers read as numbers ("3,589", not "3589.0").
"""

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from slate.core.domain.dates import format_date
from slate.core.domain.production_summary import build_summary
from slate.core.infra.gate import Gate
from slate.gui.core.table_style import style_table


def number_text(value) -> str:
    """'3,589' for 3589.0, '2.5' for 2.5."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.1f}"


def _headline(value, caption, colour=None):
    box = QFrame()
    box.setObjectName("summaryTile")
    box.setStyleSheet(f"QFrame#summaryTile {{ background: {Gate.PANEL}; border: 1px solid {Gate.LINE};"
                      f" border-radius: {Gate.RADIUS_LG}px; }}")
    layout = QVBoxLayout(box)
    layout.setContentsMargins(14, 10, 14, 10)
    layout.setSpacing(2)
    number = QLabel(str(value))
    number.setStyleSheet(f"color: {colour or Gate.TEXT}; font-size: 22px; font-weight: 600;"
                         " background: transparent; border: none;")
    label = QLabel(caption)
    label.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: {Gate.SIZE_SM}px; background: transparent; border: none;")
    layout.addWidget(number)
    layout.addWidget(label)
    return box


def _section(title):
    label = QLabel(title)
    label.setStyleSheet(f"color: {Gate.TEXT}; font-size: {Gate.SIZE_MD}px; font-weight: 600; margin-top: 10px;")
    return label


def _table(headers, rows, stretch=None):
    table = QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            text = number_text(value) if isinstance(value, (int, float)) else str(value)
            item = QTableWidgetItem(text)
            if c > 0 and (isinstance(value, (int, float)) or text.endswith("%")):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(r, c, item)
    style_table(table, {stretch: "stretch"} if stretch else None, multi_select=False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    table.resizeColumnsToContents()
    # As tall as its rows: the dialog scrolls, the table does not.
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    height = table.horizontalHeader().sizeHint().height() + 4
    for r in range(table.rowCount()):
        height += table.rowHeight(r)
    table.setFixedHeight(height)
    return table


class ProductionSummaryDialog(QDialog):
    """Read-only roll-up of the shots on screen, or of the whole project."""

    def __init__(self, shots, project_name="", parent=None, today=None, all_shots=None,
                 whole_label="whole project"):
        super().__init__(parent)
        self.setWindowTitle("Production summary")
        self.setMinimumSize(760, 620)
        self.today = today or date.today()
        self.shown_shots = list(shots or [])
        self.all_shots = list(all_shots) if all_shots is not None else list(self.shown_shots)
        self.filtered = len(self.shown_shots) != len(self.all_shots)
        # What "all of it" is for this person: an artist only has their own shots.
        self.whole_label = whole_label

        outer = QVBoxLayout(self)
        title = QLabel(project_name or "Production summary")
        title.setStyleSheet(f"color: {Gate.TEXT}; font-size: {Gate.SIZE_LG}px; font-weight: 600;")
        outer.addWidget(title)

        context = QHBoxLayout()
        self.caption = QLabel("")
        self.caption.setStyleSheet(f"color: {Gate.TEXT_2};")
        context.addWidget(self.caption, 1)
        self.whole_project = QCheckBox("Whole project")
        self.whole_project.setToolTip("Count every shot of the project, not only the ones on screen")
        self.whole_project.setVisible(self.filtered)
        self.whole_project.toggled.connect(self._rebuild)
        context.addWidget(self.whole_project)
        outer.addLayout(context)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self.scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        outer.addWidget(buttons)
        self._rebuild()

    def _rebuild(self, *_):
        whole = self.whole_project.isChecked() or not self.filtered
        shots = self.all_shots if whole else self.shown_shots
        summary = build_summary(shots, today=self.today)
        self.summary = summary
        as_of = f"As of {format_date(self.today)}"
        if whole:
            self.caption.setText(f"{as_of} · {self.whole_label}, {len(self.all_shots)} shots")
        else:
            self.caption.setText(f"{as_of} · Filtered: {len(self.shown_shots)} of {len(self.all_shots)} shots")

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setSpacing(6)
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        omitted = f" ({summary.omitted} omitted, not counted)" if summary.omitted else ""
        tiles.addWidget(_headline(number_text(summary.total_shots), "Shots" + omitted))
        # Approved and finished (Done, Delivered) shots, as the group headings count.
        tiles.addWidget(_headline(f"{number_text(summary.percent_complete)}%", "Done or approved",
                                  Gate.ACCENT))
        tiles.addWidget(_headline(number_text(summary.outstanding_bid_days), "Bid days left", Gate.ACCENT))
        tiles.addWidget(_headline(len(summary.late), "Overdue", Gate.BAD if summary.late else Gate.TEXT_2))
        # Not the board's "Unassigned" (no shot artist): nobody on any department either.
        tiles.addWidget(_headline(len(summary.unassigned), "No artist on any department",
                                  Gate.WARN if summary.unassigned else Gate.TEXT_2))
        tiles.addStretch()
        layout.addLayout(tiles)

        if summary.status_counts:
            layout.addWidget(_section("By status"))
            status_table = _table(["Status", "Shots"], list(summary.status_counts.items()))
            status_table.setMaximumWidth(360)
            layout.addWidget(status_table)
        if summary.departments:
            layout.addWidget(_section("Departments"))
            layout.addWidget(_table(
                ["Department", "Shots", "Bid days", "Left", "Not started", "In progress", "Done", "% done"],
                [(d.label, d.shots, d.bid_days, d.outstanding_days, d.not_started, d.in_progress,
                  d.done, f"{number_text(d.percent_done)}%") for d in summary.departments]))
        if summary.artists:
            layout.addWidget(_section("Artist load, busiest first"))
            layout.addWidget(_table(["Artist", "Shots", "Bid days", "Left"],
                                    [(a.name, a.shots, a.bid_days, a.outstanding_days)
                                     for a in summary.artists], stretch="Artist"))
        if summary.late:
            layout.addWidget(_section("Overdue"))
            layout.addWidget(_table(["Shot", "Reel", "Target", "Days late", "Status", "Artist"],
                                    [(e.shot_name, e.reel, format_date(e.target), e.days_late, e.status, e.artist)
                                     for e in summary.late], stretch="Artist"))
        if summary.due_soon:
            layout.addWidget(_section("Due in the next 7 days"))
            layout.addWidget(_table(["Shot", "Reel", "Target", "Status", "Artist"],
                                    [(e.shot_name, e.reel, format_date(e.target), e.status, e.artist)
                                     for e in summary.due_soon], stretch="Artist"))
        if summary.unassigned:
            layout.addWidget(_section("Open shots with no artist on any department"))
            names = QLabel(", ".join(summary.unassigned))
            names.setWordWrap(True)
            names.setStyleSheet(f"color: {Gate.WARN};")
            layout.addWidget(names)
        layout.addStretch()
        self.scroll.setWidget(body)
