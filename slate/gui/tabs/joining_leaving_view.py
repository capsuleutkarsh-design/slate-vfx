"""
Joining and leaving.

One screen, two audiences. HR start somebody and own the paperwork half; IT own
the provisioning half and are the only ones who issue a machine. Neither is
shown the other's work, because a checklist you cannot action is noise.

The point of putting both directions on one screen is that they are the same
list. Somebody leaves and the machine they were given on day one is already
named on the row - nobody has to remember it.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QSizePolicy, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from slate.core.infra.gate import Gate
from slate.core.domain.onboarding_service import (
    OnboardingService, JOINING, LEAVING, HR, IT,
)
from ..core.controls import make_button, page_title
from ..core.offline_notice import on_database_error
from ..core.empty_state import EmptyState
from .my_leave_view import Figure


class StartPersonDialog(QDialog):
    """Put somebody on the list, in one direction or the other."""

    def __init__(self, service: OnboardingService, direction: str, parent=None):
        super().__init__(parent)
        self.service = service
        self.direction = direction
        joining = direction == JOINING
        self.setWindowTitle("Start joining" if joining else "Start leaving")
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        form = QFormLayout()
        form.setSpacing(Gate.SPACE_2)

        self.person = QComboBox()
        self.person.setEditable(True)
        for row in service.people():
            name = row.get("display_name") or row.get("username") or ""
            self.person.addItem(
                "%s (%s)" % (name, row.get("username")) if name else str(row.get("username")),
                row.get("username"))
        form.addRow("Person", self.person)

        self.employment = QComboBox()
        self.employment.addItem("Staff", "staff")
        self.employment.addItem("Freelance (per project)", "freelance")
        form.addRow("Employment", self.employment)

        self.department = QLineEdit()
        self.department.setPlaceholderText("Comp, Roto, Pipeline...")
        form.addRow("Department", self.department)
        root.addLayout(form)

        note = QLabel(
            "This lays down the checklist. HR see the paperwork lines, IT see the "
            "provisioning lines, and each team ticks its own."
            if joining else
            "This lays down the leaving checklist - the reverse of joining. Anything "
            "still issued to this person will show up on it.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 12px;")
        root.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        buttons.addWidget(make_button(
            "Start joining" if joining else "Start leaving", "primary", on_click=self.accept))
        root.addLayout(buttons)

    def payload(self):
        username = self.person.currentData() or self.person.currentText().strip()
        return (str(username), self.employment.currentData(),
                self.department.text().strip())


class JoiningLeavingView(QWidget):
    """The spine - people on their way in and on their way out."""

    changed = Signal()

    def __init__(self, username: str, team: str = HR, db_manager=None, parent=None):
        """team is the half of the list this person owns - "HR" or "IT"."""
        super().__init__(parent)
        self.username = (username or "").strip()
        self.team = IT if str(team).upper() == IT else HR
        self.service = OnboardingService(db_manager)
        self._people = []
        self._tasks = []
        self._selected_person = None

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_5, Gate.SPACE_4, Gate.SPACE_5, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        root.addWidget(page_title(
            "Joining and leaving",
            "Your paperwork, and who is still waiting on it" if self.team == HR
            else "Accounts, access and machines - what is still outstanding"))

        self.figures = QHBoxLayout()
        self.figures.setSpacing(Gate.SPACE_2)
        root.addLayout(self.figures)

        controls = QHBoxLayout()
        controls.setSpacing(Gate.SPACE_2)

        self.filter_direction = QComboBox()
        self.filter_direction.addItem("Everyone outstanding", "open")
        self.filter_direction.addItem("Joining", JOINING)
        self.filter_direction.addItem("Leaving", LEAVING)
        self.filter_direction.addItem("Everyone", "all")
        self.filter_direction.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self.filter_direction)
        controls.addStretch(1)

        # Only HR put people on the list. IT work the list HR started, which is
        # the actual division of labour - IT provisioning somebody nobody hired
        # is how ghost accounts happen.
        if self.team == HR:
            controls.addWidget(make_button(
                "Start joining", "primary", on_click=lambda: self._start(JOINING)))
            controls.addWidget(make_button(
                "Start leaving", "secondary", on_click=lambda: self._start(LEAVING)))
        root.addLayout(controls)

        split = QSplitter(Qt.Orientation.Horizontal)

        # --- left: who
        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.setSpacing(Gate.SPACE_2)

        self.people_table = QTableWidget(0, 4)
        self.people_table.setHorizontalHeaderLabels(["Person", "Which way", "Yours", "All"])
        self.people_table.verticalHeader().setVisible(False)
        self.people_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.people_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.people_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.people_table.setAlternatingRowColors(True)
        head = self.people_table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in (1, 2, 3):
            head.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.people_table.itemSelectionChanged.connect(self._person_picked)
        left_box.addWidget(self.people_table, 1)

        self.people_empty = EmptyState(
            "Nobody on the move",
            "When HR start somebody joining or leaving, they appear here.",
            glyph="users")
        left_box.addWidget(self.people_empty, 1)
        self.people_empty.attach_to(self.people_table)
        split.addWidget(left)

        # --- right: what
        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.setSpacing(Gate.SPACE_2)

        self.heading = QLabel("Pick somebody")
        self.heading.setStyleSheet(
            f"color: {Gate.TEXT}; font-family: {Gate.FONT_LABEL_STRONG}; font-size: 15px;")
        right_box.addWidget(self.heading)

        self.holding = QLabel("")
        self.holding.setWordWrap(True)
        self.holding.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 12px;")
        right_box.addWidget(self.holding)

        self.task_table = QTableWidget(0, 4)
        self.task_table.setHorizontalHeaderLabels(["State", "Task", "Owner", "Machine"])
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.task_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.task_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.task_table.setAlternatingRowColors(True)
        thead = self.task_table.horizontalHeader()
        thead.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        thead.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        thead.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        thead.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.task_table.itemSelectionChanged.connect(self._sync_buttons)
        right_box.addWidget(self.task_table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(Gate.SPACE_2)
        self.btn_done = make_button("Mark done", "primary", on_click=lambda: self._tick(True))
        self.btn_undo = make_button("Reopen", "ghost", on_click=lambda: self._tick(False))
        actions.addWidget(self.btn_done)
        actions.addWidget(self.btn_undo)
        actions.addStretch(1)

        # Issuing kit is IT's job and nobody else's.
        if self.team == IT:
            self.btn_issue = make_button("Issue machine", "secondary", on_click=self.issue_machine)
            self.btn_collect = make_button("Collect machine", "secondary", on_click=self.collect_machine)
            actions.addWidget(self.btn_issue)
            actions.addWidget(self.btn_collect)
        right_box.addLayout(actions)
        split.addWidget(right)

        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        root.addWidget(split, 1)

        self.refresh()

    # ------------------------------------------------------------------ data
    @on_database_error
    def refresh(self, *_):
        keep = self._selected_person
        outstanding = self.service.open_tasks()

        wanted = self.filter_direction.currentData()
        by_person = {}
        for task in outstanding:
            direction = task.get("direction") or JOINING
            if wanted in (JOINING, LEAVING) and direction != wanted:
                continue
            user = str(task.get("user_id") or "")
            entry = by_person.setdefault(user, {"direction": direction, "mine": 0, "all": 0})
            entry["all"] += 1
            if (task.get("owner_team") or "").upper() == self.team:
                entry["mine"] += 1

        rows = [dict(user_id=u, **v) for u, v in by_person.items()]

        if wanted == "all":
            # Include people whose list is already finished, so somebody can go
            # back and look at what was done.
            seen = {r["user_id"].lower() for r in rows}
            for person in self.service.people():
                name = str(person.get("username") or "")
                if name.lower() in seen or not name:
                    continue
                if self.service.tasks_for(name):
                    rows.append({"user_id": name, "direction": JOINING, "mine": 0, "all": 0})
                    seen.add(name.lower())

        # Yours first, then the biggest pile - the person who needs the most
        # work is the one to start on.
        rows.sort(key=lambda r: (-r["mine"], -r["all"], r["user_id"].lower()))
        self._people = rows

        self._paint_figures(outstanding)
        self._paint_people(rows)

        if keep:
            for r, row in enumerate(rows):
                if row["user_id"].lower() == keep.lower():
                    self.people_table.selectRow(r)
                    break
            else:
                self._selected_person = None
        self._paint_tasks()
        self.people_empty.refresh()
        self._sync_buttons()

    def _paint_figures(self, outstanding):
        while self.figures.count():
            item = self.figures.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        mine = [t for t in outstanding if (t.get("owner_team") or "").upper() == self.team]
        joining = {str(t.get("user_id")).lower() for t in outstanding
                   if (t.get("direction") or JOINING) == JOINING}
        leaving = {str(t.get("user_id")).lower() for t in outstanding
                   if (t.get("direction") or JOINING) == LEAVING}
        stranded = self.service.unreturned()

        cards = [
            ("On your list", len(mine), "tasks for %s" % self.team,
             "WARN" if mine else "OK"),
            ("Joining", len(joining), "people still being set up", "ACCENT"),
            ("Leaving", len(leaving), "people still being wound down", "TEXT_2"),
        ]
        # A machine still out with somebody who has left is money on somebody
        # else's desk. It only earns a card when it is actually happening.
        if stranded:
            cards.append(("Kit not returned", len(stranded),
                          "issued to people who are leaving", "BAD"))

        for label, value, caption, tone in cards:
            card = Figure(label, value, caption, tone)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.figures.addWidget(card)

    def _paint_people(self, rows):
        self.people_table.blockSignals(True)
        self.people_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            leaving = row["direction"] == LEAVING
            cells = [row["user_id"], "Leaving" if leaving else "Joining",
                     str(row["mine"]), str(row["all"])]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 1:
                    item.setForeground(QColor(Gate.TEXT_2 if leaving else Gate.ACCENT))
                if c in (2, 3):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c == 2 and row["mine"]:
                    item.setForeground(QColor(Gate.WARN))
                self.people_table.setItem(r, c, item)
        self.people_table.blockSignals(False)

    def _person_picked(self):
        rows = {i.row() for i in self.people_table.selectedIndexes()}
        if not rows:
            self._selected_person = None
        else:
            r = min(rows)
            self._selected_person = self._people[r]["user_id"] if r < len(self._people) else None
        self._paint_tasks()
        self._sync_buttons()

    def _paint_tasks(self):
        person = self._selected_person
        if not person:
            self.heading.setText("Pick somebody")
            self.holding.setText("")
            self.task_table.setRowCount(0)
            self._tasks = []
            return

        tasks = self.service.tasks_for(person)
        # Only this team's half. The other team's lines are not hidden work -
        # they are somebody else's work.
        tasks = [t for t in tasks if (t.get("owner_team") or "").upper() == self.team]
        # Outstanding first; a finished line is a record, not a job.
        tasks.sort(key=lambda t: (bool(t.get("is_completed")), t.get("id") or 0))
        self._tasks = tasks

        done = sum(1 for t in tasks if t.get("is_completed"))
        self.heading.setText("%s  -  %d of %d done" % (person, done, len(tasks)))

        held = self.service.held_by(person)
        if held:
            self.holding.setText("Currently holding: " + ", ".join(
                "%s (since %s)" % (h["machine_name"], str(h.get("issued_on") or "")[:10])
                for h in held))
        else:
            self.holding.setText("Nothing issued to this person.")

        self.task_table.setRowCount(len(tasks))
        for r, task in enumerate(tasks):
            complete = bool(task.get("is_completed"))
            cells = ["Done" if complete else "To do",
                     task.get("task_name") or "",
                     task.get("owner_team") or "",
                     task.get("asset_name") or ""]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setForeground(QColor(Gate.OK if complete else Gate.WARN))
                if c == 1 and complete:
                    item.setForeground(QColor(Gate.TEXT_DIM))
                self.task_table.setItem(r, c, item)

    # --------------------------------------------------------------- actions
    def _selected_tasks(self):
        rows = sorted({i.row() for i in self.task_table.selectedIndexes()})
        return [self._tasks[r] for r in rows if r < len(self._tasks)]

    def _sync_buttons(self, *_):
        picked = self._selected_tasks()
        self.btn_done.setEnabled(any(not t.get("is_completed") for t in picked))
        self.btn_undo.setEnabled(any(t.get("is_completed") for t in picked))
        if self.team == IT:
            person = self._selected_person
            self.btn_issue.setEnabled(bool(person))
            self.btn_collect.setEnabled(bool(person) and bool(self.service.held_by(person)))

    def _start(self, direction):
        dialog = StartPersonDialog(self.service, direction, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        username, employment, department = dialog.payload()
        if not username:
            return
        made = self.service.start(username, direction, employment, department)
        if not made:
            QMessageBox.information(
                self, "Already on the list",
                "%s already has a %s checklist - nothing was added."
                % (username, "joining" if direction == JOINING else "leaving"))
        self._selected_person = username
        self.refresh()
        self.changed.emit()

    def _tick(self, done: bool):
        picked = [t for t in self._selected_tasks()
                  if bool(t.get("is_completed")) != done]
        if not picked:
            return
        for task in picked:
            self.service.complete(task.get("id"), done)
        self.refresh()
        self.changed.emit()

    def issue_machine(self):
        person = self._selected_person
        if not person:
            return
        free = self.service.available_machines()
        if not free:
            QMessageBox.information(
                self, "No free machines",
                "Every machine in the inventory is already assigned. Free one up "
                "in Hardware, or add the new one first.")
            return
        machine, ok = QInputDialog.getItem(
            self, "Issue a machine", "Which machine goes to %s?" % person, free, 0, False)
        if not ok or not machine:
            return
        if self.service.issue_machine(machine, person, self.username):
            QMessageBox.information(
                self, "Issued",
                "%s is now recorded against %s. When they leave, the leaving list "
                "will ask for it back." % (machine, person))
        else:
            QMessageBox.warning(self, "Not saved", "The assignment could not be recorded.")
        self.refresh()
        self.changed.emit()

    def collect_machine(self):
        person = self._selected_person
        if not person:
            return
        held = [h["machine_name"] for h in self.service.held_by(person)]
        if not held:
            return
        machine, ok = QInputDialog.getItem(
            self, "Collect a machine", "Which machine has come back?", held, 0, False)
        if not ok or not machine:
            return
        if self.service.return_machine(machine, person):
            QMessageBox.information(
                self, "Back in stock",
                "%s is free again and can be issued to somebody else." % machine)
        self.refresh()
        self.changed.emit()
