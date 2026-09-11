"""
IT support, as the person who needs help sees it.

Three things, and nothing else: report a problem, see what state it is in and
who has it, and talk to whoever picked it up. Every service desk worth using
gives the person who raised a ticket that much visibility - not knowing whether
anyone has looked at it is the single most common complaint about internal IT.

Triage, assignment and the queue belong to the other side of this module.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QFrame,
)

from ut_vfx.core.infra.gate import Gate
from ut_vfx.core.domain.service_desk import (
    CATEGORIES, IMPACT, URGENCY, PRIORITY_LABEL, describe_promise,
    priority_for, priority_tone, status_tone,
)
from ..core.controls import make_button, page_title
from ..core.empty_state import EmptyState


def _tone(token: str) -> str:
    return getattr(Gate, token, Gate.TEXT_DIM)


class RaiseTicketDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Report a problem")
        self.setMinimumWidth(460)
        self.setStyleSheet(f"background-color: {Gate.GROUND}; color: {Gate.TEXT};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(Gate.SPACE_3)

        form = QFormLayout()
        form.setSpacing(Gate.SPACE_3)

        self.category = QComboBox()
        self.category.addItems(CATEGORIES)

        # Two questions an artist can answer, rather than a priority they
        # cannot. Asked directly, everybody picks Critical and the queue stops
        # meaning anything at all.
        self.impact = QComboBox()
        for value, description in IMPACT:
            self.impact.addItem("%s - %s" % (value, description), value)
        self.impact.setCurrentIndex(2)

        self.urgency = QComboBox()
        for value, description in URGENCY:
            self.urgency.addItem("%s - %s" % (value, description), value)
        self.urgency.setCurrentIndex(1)

        self.summary = QLineEdit()
        self.summary.setPlaceholderText("One line - what is wrong?")

        self.detail = QPlainTextEdit()
        self.detail.setPlaceholderText(
            "What were you doing, what happened, and what you expected instead. "
            "Shot or project name helps.")
        self.detail.setFixedHeight(110)

        form.addRow("Category", self.category)
        form.addRow("Who is affected", self.impact)
        form.addRow("Can you carry on", self.urgency)
        form.addRow("Summary", self.summary)
        form.addRow("Detail", self.detail)
        outer.addLayout(form)

        self.promise = QLabel("")
        self.promise.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 12.5px;")
        self.promise.setWordWrap(True)
        outer.addWidget(self.promise)
        self.impact.currentIndexChanged.connect(self._show_promise)
        self.urgency.currentIndexChanged.connect(self._show_promise)
        self._show_promise()

        self.note = QLabel("")
        self.note.setStyleSheet(f"color: {Gate.WARN}; font-size: 12.5px;")
        self.note.hide()
        outer.addWidget(self.note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        buttons.addWidget(make_button("Send to IT", "primary", on_click=self._submit))
        outer.addLayout(buttons)

    def current_priority(self) -> str:
        return priority_for(self.impact.currentData(), self.urgency.currentData())

    def _show_promise(self, *_):
        # Show the consequence as the answers are given, so the priority reads
        # as a result rather than a setting.
        self.promise.setText(describe_promise(self.current_priority()))

    def _submit(self):
        if not self.summary.text().strip():
            self.note.setText("Give it a one-line summary so IT can triage it.")
            self.note.show()
            return
        self.accept()

    def values(self) -> dict:
        summary = self.summary.text().strip()
        detail = self.detail.toPlainText().strip()
        return {
            "category": self.category.currentText(),
            "impact": self.impact.currentData(),
            "urgency": self.urgency.currentData(),
            "priority": self.current_priority(),
            "description": f"{summary}\n\n{detail}".strip() if detail else summary,
            "summary": summary,
        }


class TicketThreadDialog(QDialog):
    """A ticket, its state, and the conversation on it."""

    def __init__(self, ticket: dict, db, username: str, parent=None):
        super().__init__(parent)
        self.ticket = ticket
        self.db = db
        self.username = username
        self.setWindowTitle("Ticket #%s" % ticket.get("id"))
        self.setMinimumSize(560, 520)
        self.setStyleSheet(f"background-color: {Gate.GROUND}; color: {Gate.TEXT};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(Gate.SPACE_3)

        status = (ticket.get("status") or "Open").title()
        priority = (ticket.get("priority") or "P3").upper()

        heading = QLabel((ticket.get("description") or "").splitlines()[0] or "Ticket")
        heading.setWordWrap(True)
        heading.setStyleSheet(
            f"color: {Gate.TEXT}; font-family: {Gate.FONT_LABEL_STRONG}; "
            f"font-size: 19px; font-weight: 600;")
        outer.addWidget(heading)

        meta = QLabel(
            f"{ticket.get('category') or 'Other'}   ·   {PRIORITY_LABEL.get(priority, priority)}   ·   {status}")
        meta.setStyleSheet(f"color: {_tone(status_tone(status))}; font-size: 12.5px;")
        outer.addWidget(meta)

        body = QLabel("\n".join((ticket.get("description") or "").splitlines()[1:]).strip())
        body.setWordWrap(True)
        body.setStyleSheet(
            f"color: {Gate.TEXT_2}; font-size: 13px; background: {Gate.PANEL}; "
            f"border: 1px solid {Gate.LINE}; border-radius: {Gate.RADIUS_MD}px; padding: 12px;")
        if body.text():
            outer.addWidget(body)

        conversation = QLabel("CONVERSATION")
        conversation.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-family: {Gate.FONT_LABEL}; font-size: 11.5px; "
            f"letter-spacing: 1.6px;")
        outer.addWidget(conversation)

        self.thread_area = QScrollArea()
        self.thread_area.setWidgetResizable(True)
        self.thread_area.setStyleSheet(
            f"background: {Gate.PANEL}; border: 1px solid {Gate.LINE}; "
            f"border-radius: {Gate.RADIUS_MD}px;")
        self.thread_holder = QWidget()
        self.thread_layout = QVBoxLayout(self.thread_holder)
        self.thread_layout.setContentsMargins(12, 12, 12, 12)
        self.thread_layout.setSpacing(10)
        self.thread_area.setWidget(self.thread_holder)
        outer.addWidget(self.thread_area, 1)

        self.reply = QPlainTextEdit()
        self.reply.setPlaceholderText("Add something for IT - a screenshot path, a shot name, an update.")
        self.reply.setFixedHeight(64)
        outer.addWidget(self.reply)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(make_button("Close", "ghost", on_click=self.accept))
        row.addWidget(make_button("Send reply", "primary", on_click=self._send))
        outer.addLayout(row)

        self._load_thread()

    def _load_thread(self):
        while self.thread_layout.count():
            item = self.thread_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        try:
            rows = self.db.execute_query(
                "SELECT author, comment_text, timestamp FROM it_ticket_comments "
                "WHERE ticket_id = %s ORDER BY id ASC",
                (self.ticket.get("id"),), fetch="all") or []
        except Exception:
            rows = []

        if not rows:
            blank = QLabel("Nothing yet. IT will reply here.")
            blank.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 12.5px; background: transparent;")
            self.thread_layout.addWidget(blank)
        else:
            for row in rows:
                self.thread_layout.addWidget(self._bubble(dict(row)))

        self.thread_layout.addStretch(1)

    def _bubble(self, row) -> QFrame:
        mine = (row.get("author") or "").strip().lower() == self.username.strip().lower()
        frame = QFrame()
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Gate.RAISED if mine else Gate.GROUND};
                border: 1px solid {Gate.LINE};
                border-left: 2px solid {Gate.ACCENT if mine else Gate.LINE};
                border-radius: {Gate.RADIUS_MD}px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(11, 9, 11, 9)
        layout.setSpacing(3)

        who = QLabel(("You" if mine else (row.get("author") or "IT")) +
                     ("   ·   %s" % row.get("timestamp") if row.get("timestamp") else ""))
        who.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-size: 11.5px; background: transparent; border: none;")
        layout.addWidget(who)

        text = QLabel(row.get("comment_text") or "")
        text.setWordWrap(True)
        text.setStyleSheet(
            f"color: {Gate.TEXT_2}; font-size: 13px; background: transparent; border: none;")
        layout.addWidget(text)
        return frame

    def _send(self):
        message = self.reply.toPlainText().strip()
        if not message:
            return
        try:
            self.db.execute_update(
                "INSERT INTO it_ticket_comments (ticket_id, author, comment_text) "
                "VALUES (%s, %s, %s)",
                (self.ticket.get("id"), self.username, message))
        except Exception as exc:
            QMessageBox.warning(self, "Not sent", "Your reply was not saved.\n\n%s" % exc)
            return
        self.reply.clear()
        self._load_thread()


class MyTicketsView(QWidget):
    """The artist's side of Ticketing."""

    changed = Signal()

    def __init__(self, username: str, db_manager=None, parent=None):
        super().__init__(parent)
        self.username = (username or "").strip()
        self.db = db_manager
        if self.db is None:
            from ut_vfx.core.infra.database_manager import database_manager
            self.db = database_manager
        self._rows = []

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_5, Gate.SPACE_4, Gate.SPACE_5, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_4)

        header = QHBoxLayout()
        header.addWidget(page_title(
            "IT support", "Report a problem, and follow what happens to it"))
        header.addStretch(1)
        header.addWidget(make_button("Report a problem", "primary", on_click=self.raise_ticket),
                         0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["#", "Summary", "Category", "Priority", "Status", "With", "Raised"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i in (0, 2, 3, 4, 5, 6):
            head.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(self.open_selected)
        root.addWidget(self.table, 1)

        hint = QLabel("Double-click a ticket to read it and reply.")
        hint.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 12px;")
        root.addWidget(hint)

        self.empty = EmptyState(
            "No tickets raised",
            "If something is broken - a workstation, a licence, the farm - tell IT here "
            "and you can follow what happens to it.",
            primary=("Report a problem", self.raise_ticket),
            glyph="ticket",
        )
        root.addWidget(self.empty, 1)
        self.empty.attach_to(self.table)

        self.refresh()

    # ------------------------------------------------------------------ data
    def refresh(self):
        try:
            rows = self.db.execute_query(
                "SELECT id, category, description, status, priority, created_at, assigned_to "
                "FROM it_tickets WHERE LOWER(submitted_by) = LOWER(%s) "
                "ORDER BY id DESC",
                (self.username,), fetch="all") or []
            self._rows = [dict(r) for r in rows]
        except Exception:
            self._rows = []

        self.table.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            status = (row.get("status") or "Open").title()
            priority = (row.get("priority") or "P3").upper()
            summary = (row.get("description") or "").splitlines()[0] if row.get("description") else ""
            created = row.get("created_at")
            cells = [
                str(row.get("id") or ""),
                summary,
                row.get("category") or "",
                priority,
                status,
                row.get("assigned_to") or "Not yet picked up",
                str(created)[:16] if created else "",
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 3:
                    item.setForeground(QColor(_tone(priority_tone(priority))))
                if c == 4:
                    item.setForeground(QColor(_tone(status_tone(status))))
                self.table.setItem(r, c, item)

        self.empty.refresh()

    # --------------------------------------------------------------- actions
    def raise_ticket(self):
        dialog = RaiseTicketDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            ok = self.db.execute_update(
                "INSERT INTO it_tickets "
                "(submitted_by, category, description, status, priority, impact, urgency) "
                "VALUES (%s, %s, %s, 'Open', %s, %s, %s)",
                (self.username, values["category"], values["description"],
                 values["priority"], values["impact"], values["urgency"]))
        except Exception as exc:
            QMessageBox.warning(self, "Not sent", "The ticket was not saved.\n\n%s" % exc)
            return
        if not ok:
            QMessageBox.warning(self, "Not sent",
                                "The database did not accept the ticket. Nothing was saved.")
            return
        self.refresh()
        self.changed.emit()

    def open_selected(self, *_):
        rows = {i.row() for i in self.table.selectedIndexes()}
        if not rows:
            return
        row = self._rows[sorted(rows)[0]]
        TicketThreadDialog(row, self.db, self.username, self).exec()
        self.refresh()
