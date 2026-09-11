"""
The IT side of support: a queue, not a list.

The difference between the two is what the rows are sorted by. A list shows you
what is newest. A queue shows you what is closest to breaking a promise - and
that is the whole job of a service desk.

What the old tab was: a table of log entries with an Add button, no owner, no
priority that meant anything, and nothing that could tell you a ticket had been
sitting untouched past its response window.
"""

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from slate.core.infra.gate import Gate
from slate.core.domain.service_desk import (
    CATEGORIES, OPEN_STATUSES, PRIORITY_LABEL, STATUSES,
    priority_rank, priority_tone, sla_state, sla_tone, status_tone,
)
from ..core.controls import make_button, page_title
from ..core.empty_state import EmptyState
from .my_tickets_view import TicketThreadDialog
from slate.gui.core.offline_notice import on_database_error

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else keeps the fallback it already had.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""


def _tone(token: str) -> str:
    return getattr(Gate, token, Gate.TEXT_DIM)


class DeskStat(QFrame):
    """One figure at the top of the queue."""

    def __init__(self, label: str, value: str, tone: str = "TEXT", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {Gate.PANEL};
                border: 1px solid {Gate.LINE};
                border-left: 2px solid {_tone(tone)};
                border-radius: {Gate.RADIUS_MD}px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(1)

        name = QLabel(label.upper())
        name.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-family: {Gate.FONT_LABEL}; font-size: 11px; "
            f"letter-spacing: 1.4px; background: transparent; border: none;")
        layout.addWidget(name)

        figure = QLabel(str(value))
        figure.setStyleSheet(
            f"color: {_tone(tone)}; font-family: {Gate.FONT_LABEL_STRONG}; font-size: 26px; "
            f"font-weight: 600; background: transparent; border: none;")
        layout.addWidget(figure)


class ServiceDeskView(QWidget):
    """IT's queue."""

    changed = Signal()

    def __init__(self, username: str, db_manager=None, parent=None):
        super().__init__(parent)
        self.username = (username or "").strip()
        self.db = db_manager
        if self.db is None:
            from slate.core.infra.database_manager import database_manager
            self.db = database_manager
        self._rows = []

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_5, Gate.SPACE_4, Gate.SPACE_5, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        root.addWidget(page_title(
            "Service desk", "Sorted by what is closest to breaching, not by what is newest"))

        self.stats_row = QHBoxLayout()
        self.stats_row.setSpacing(Gate.SPACE_2)
        root.addLayout(self.stats_row)

        # ---------------------------------------------------------- filtering
        controls = QHBoxLayout()
        controls.setSpacing(Gate.SPACE_2)

        self.filter_status = QComboBox()
        self.filter_status.addItem("Open tickets", "open")
        self.filter_status.addItem("Everything", "all")
        for s in STATUSES:
            self.filter_status.addItem(s, s)
        self.filter_status.currentIndexChanged.connect(self.refresh)

        self.filter_mine = QComboBox()
        self.filter_mine.addItem("Anyone", "")
        self.filter_mine.addItem("Mine", self.username)
        self.filter_mine.addItem("Unassigned", "__none__")
        self.filter_mine.currentIndexChanged.connect(self.refresh)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tickets...")
        self.search.textChanged.connect(self.refresh)

        controls.addWidget(self.filter_status)
        controls.addWidget(self.filter_mine)
        controls.addWidget(self.search, 1)

        self.btn_take = make_button("Assign to me", "primary", on_click=self.take)
        self.btn_respond = make_button("Mark responded", "secondary", on_click=self.mark_responded)
        self.btn_status = make_button("Set status", "secondary", on_click=self.change_status)
        for b in (self.btn_take, self.btn_respond, self.btn_status):
            controls.addWidget(b)
        root.addLayout(controls)

        # -------------------------------------------------------------- queue
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["#", "Summary", "Raised by", "Category", "Priority", "Status", "Owner", "SLA"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i in (0, 2, 3, 4, 5, 6, 7):
            head.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(self.open_selected)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        root.addWidget(self.table, 1)

        self.empty = EmptyState(
            "Nothing in the queue",
            "Tickets raised by the studio land here, worst-first.",
            glyph="ticket")
        root.addWidget(self.empty, 1)
        self.empty.attach_to(self.table)

        self.refresh()

    # ------------------------------------------------------------------ data
    def _fetch(self) -> list:
        try:
            rows = self.db.execute_query(
                "SELECT id, submitted_by, category, description, status, priority, "
                "       created_at, assigned_to, first_response_at, impact, urgency "
                "FROM it_tickets ORDER BY id DESC", fetch="all") or []
            return [dict(r) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            return []

    @on_database_error
    def refresh(self, *_):
        rows = self._fetch()

        wanted = self.filter_status.currentData()
        if wanted == "open":
            rows = [r for r in rows if (r.get("status") or "Open").title() in OPEN_STATUSES]
        elif wanted not in ("all", None):
            rows = [r for r in rows if (r.get("status") or "").title() == wanted]

        owner = self.filter_mine.currentData()
        if owner == "__none__":
            rows = [r for r in rows if not (r.get("assigned_to") or "").strip()]
        elif owner:
            rows = [r for r in rows
                    if (r.get("assigned_to") or "").strip().lower() == owner.lower()]

        needle = self.search.text().strip().lower()
        if needle:
            rows = [r for r in rows if needle in " ".join(
                str(r.get(k) or "") for k in
                ("description", "submitted_by", "category", "assigned_to")).lower()]

        # Worst first: breached, then at risk, then by priority, then by age.
        order = {"breached": 0, "at risk": 1, "met": 2, "closed": 3}
        for r in rows:
            r["_sla"] = sla_state(r)
        rows.sort(key=lambda r: (
            order.get(r["_sla"]["state"], 4),
            priority_rank(r.get("priority") or "P3"),
            r["_sla"]["hours_left"] if r["_sla"]["hours_left"] is not None else 9e9,
        ))
        self._rows = rows

        self._paint_stats(self._fetch())
        self._paint_rows(rows)
        self.empty.refresh()
        self._sync_buttons()

    def _paint_stats(self, everything):
        while self.stats_row.count():
            item = self.stats_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        live = [r for r in everything if (r.get("status") or "Open").title() in OPEN_STATUSES]
        states = [sla_state(r) for r in live]
        breached = sum(1 for s in states if s["state"] == "breached")
        at_risk = sum(1 for s in states if s["state"] == "at risk")
        unassigned = sum(1 for r in live if not (r.get("assigned_to") or "").strip())
        mine = sum(1 for r in live
                   if (r.get("assigned_to") or "").strip().lower() == self.username.lower())

        for label, value, tone in (
            ("Open", len(live), "TEXT"),
            ("Breached", breached, "BAD" if breached else "TEXT_DIM"),
            ("At risk", at_risk, "WARN" if at_risk else "TEXT_DIM"),
            ("Unassigned", unassigned, "WARN" if unassigned else "TEXT_DIM"),
            ("Mine", mine, "ACCENT"),
        ):
            card = DeskStat(label, value, tone)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.stats_row.addWidget(card)

    def _paint_rows(self, rows):
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            status = (row.get("status") or "Open").title()
            priority = (row.get("priority") or "P3").upper()
            summary = (row.get("description") or "").splitlines()[0] if row.get("description") else ""
            state = row["_sla"]

            if state["state"] == "closed":
                sla_text = "-"
            elif state["hours_left"] is None:
                sla_text = "-"
            elif state["hours_left"] < 0:
                sla_text = "%.1fh over (%s)" % (abs(state["hours_left"]), state["against"])
            else:
                sla_text = "%.1fh left (%s)" % (state["hours_left"], state["against"])

            cells = [
                str(row.get("id") or ""),
                summary,
                row.get("submitted_by") or "",
                row.get("category") or "",
                PRIORITY_LABEL.get(priority, priority),
                status,
                row.get("assigned_to") or "-",
                sla_text,
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 4:
                    item.setForeground(QColor(_tone(priority_tone(priority))))
                elif c == 5:
                    item.setForeground(QColor(_tone(status_tone(status))))
                elif c == 6 and not (row.get("assigned_to") or "").strip():
                    item.setForeground(QColor(Gate.WARN))
                elif c == 7:
                    item.setForeground(QColor(_tone(sla_tone(state["state"]))))
                self.table.setItem(r, c, item)

    # --------------------------------------------------------------- actions
    def _selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self._rows[r] for r in rows if r < len(self._rows)]

    def _sync_buttons(self, *_):
        picked = bool(self._selected())
        for b in (self.btn_take, self.btn_respond, self.btn_status):
            b.setEnabled(picked)

    def _write(self, sql, params) -> bool:
        try:
            self.db.execute_update(sql, params)
            return True
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            QMessageBox.warning(self, "Not saved", str(exc))
            return False

    def take(self):
        for row in self._selected():
            self._write("UPDATE it_tickets SET assigned_to = %s, "
                        "status = CASE WHEN status = 'Open' THEN 'In Progress' ELSE status END "
                        "WHERE id = %s", (self.username, row.get("id")))
        self.refresh()
        self.changed.emit()

    def mark_responded(self):
        # Stops the response clock. The resolution clock keeps running - they
        # are different promises and the queue measures them separately.
        now = datetime.now()
        for row in self._selected():
            if row.get("first_response_at"):
                continue
            self._write("UPDATE it_tickets SET first_response_at = %s WHERE id = %s",
                        (now, row.get("id")))
        self.refresh()

    def change_status(self):
        picked = self._selected()
        if not picked:
            return
        from PySide6.QtWidgets import QInputDialog
        choice, ok = QInputDialog.getItem(
            self, "Set status", "Move %d ticket(s) to:" % len(picked),
            list(STATUSES), 0, False)
        if not ok or not choice:
            return
        for row in picked:
            resolved = "CURRENT_TIMESTAMP" if choice in ("Resolved", "Closed") else "resolved_at"
            self._write("UPDATE it_tickets SET status = %%s, resolved_at = %s WHERE id = %%s"
                        % resolved, (choice, row.get("id")))
        self.refresh()
        self.changed.emit()

    def open_selected(self, *_):
        picked = self._selected()
        if not picked:
            return
        TicketThreadDialog(picked[0], self.db, self.username, self).exec()
        self.refresh()
