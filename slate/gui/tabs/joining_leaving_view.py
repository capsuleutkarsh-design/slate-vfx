"""
Joining and leaving.

One screen, two audiences. HR start somebody and own the paperwork half; IT own
the provisioning half and are the only ones who issue a machine. Neither is
shown the other's work, because a checklist you cannot action is noise.

The point of putting both directions on one screen is that they are the same
list. Somebody leaves and the machine they were given on day one is already
named on the row - nobody has to remember it.
"""

import html
from datetime import date

from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QSizePolicy, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from slate.core.infra.gate import Gate
from slate.core.domain.onboarding_service import (
    OnboardingService, JOINING, LEAVING, HR, IT, EMPLOYMENT_TYPES,
)
from slate.core.domain import people
from slate.core.domain.dates import format_date
from ..core.controls import make_button, page_title
from ..core.offline_notice import on_database_error
from ..core.empty_state import EmptyState
from ..core.stat_card import StatStrip
from ..core.table_style import style_table
from ..core.data_display import setup_date_edit
from slate.gui.components.table_tools import KeepSelection, make_item, selected_keys


def _as_date(value):
    if not value:
        return None
    if hasattr(value, "year") and not hasattr(value, "hour"):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class MachinePickerDialog(QDialog):
    """
    Choose a machine by what it is, not by a bare name: type, GPU, CPU, RAM,
    location, status, with a search box.
    """

    COLUMNS = ["Machine", "Type", "GPU", "CPU", "RAM", "Location", "Status"]
    KEYS = ("machine_name", "type", "gpu", "cpu", "ram", "location", "status")

    def __init__(self, machines, person_label, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Issue a machine")
        self.resize(760, 440)
        self.machines = list(machines)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("Which machine goes to %s?" % person_label))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search machines...")
        self.search.textChanged.connect(self._filter)
        root.addWidget(self.search)
        self.table = QTableWidget(len(self.machines), len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        style_table(self.table, {"Machine": "contents", "Location": "stretch"}, multi_select=False)
        for r, machine in enumerate(self.machines):
            for c, key in enumerate(self.KEYS):
                item = make_item(machine.get(key) or "", key=machine["machine_name"] if c == 0 else None)
                self.table.setItem(r, c, item)
        self.table.cellDoubleClicked.connect(lambda *_: self.accept())
        if self.machines:
            self.table.selectRow(0)
        root.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        buttons.addWidget(make_button("Issue", "primary", on_click=self.accept))
        root.addLayout(buttons)

    def _filter(self, text):
        needle = text.strip().lower()
        for r in range(self.table.rowCount()):
            row_text = " ".join(self.table.item(r, c).text() for c in range(len(self.KEYS))).lower()
            self.table.setRowHidden(r, bool(needle) and needle not in row_text)

    def machine(self) -> str:
        keys = selected_keys(self.table)
        return keys[0] if keys else ""


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

        # A searchable picker that only ever yields a real username. The
        # editable combo it replaces took the still-selected item whatever was
        # typed, so a typo started a checklist for somebody else.
        from slate.gui.components.person_picker import PersonPicker
        self.person = PersonPicker(
            placeholder="Type a name…", allow_empty=False,
            order=self._joining_order(service) if joining else None)
        self.person.person_changed.connect(self._sync_start)
        form.addRow("Person", self.person)
        self.person_hint = QLabel("")
        self.person_hint.setWordWrap(True)
        self.person_hint.setStyleSheet(f"color: {Gate.WARN}; font-size: 12px;")
        # "Users & Roles" in the hint is a link that opens that screen.
        self.person_hint.setTextFormat(Qt.TextFormat.RichText)
        self.person_hint.linkActivated.connect(self._open_users)
        form.addRow("", self.person_hint)

        # The same values Users & Roles stores ('Staff', not 'staff').
        self.employment = QComboBox()
        for value in EMPLOYMENT_TYPES:
            self.employment.addItem("Freelance (per project)" if value == "Freelance" else value,
                                    value)
        form.addRow("Employment", self.employment)

        # The same department list as Users & Roles; typing a new one is still
        # possible, but a typo no longer quietly makes a new department.
        self.department = QComboBox()
        self.department.setEditable(True)
        try:
            from slate.core.domain.departments import staff_department_names
            self.department.addItems(staff_department_names())
        except Exception:
            pass
        self.department.setCurrentIndex(-1)
        self.department.lineEdit().setPlaceholderText("Choose a department")
        form.addRow("Department", self.department)

        # Joining needs a start date, because leave accrues from it. Leaving
        # needs a last day, because "kit not returned" is measured against it -
        # without one the alert fired the moment notice was given.
        self.effective = setup_date_edit(QDateEdit(), weekday=True)
        self.effective.setDate(QDate.currentDate())
        form.addRow("Joining on" if joining else "Last working day", self.effective)
        # The joining date already on record, said before it is changed.
        self.existing = QLabel("")
        self.existing.setWordWrap(True)
        self.existing.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 12px;")
        self.existing.hide()
        form.addRow("", self.existing)
        root.addLayout(form)

        note = QLabel(
            "This lays down the checklist and records the joining date, which is "
            "what leave accrues from. HR see the paperwork lines, IT see the "
            "provisioning lines, and each team ticks its own."
            if joining else
            "This lays down the leaving checklist - the reverse of joining. "
            "Anything still issued to this person shows up on it, and is chased "
            "from the last working day onwards rather than from today.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 12px;")
        root.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        self.start_button = make_button(
            "Start joining" if joining else "Start leaving", "primary", on_click=self.accept)
        buttons.addWidget(self.start_button)
        root.addLayout(buttons)
        self._sync_start(self.person.username())

    @staticmethod
    def _joining_order(service):
        """
        For joining: people without a finished joining list first, newest
        first among them (no joining date yet counts as newest), then those
        whose joining list is finished, alphabetical. It was every account by
        joining date, blanks first - and a new hire whose joining date was
        already set dropped to the bottom among long-standing staff.
        """
        try:
            done = service.joining_finished()
        except Exception:
            done = set()           # only the order suffers
        # The picker's records come from the people directory, which does not
        # carry joining dates; the onboarding service does.
        try:
            joined_on = {str(p.get("username") or "").lower(): p.get("joined_on")
                         for p in service.people()}
        except Exception:
            joined_on = {}

        def key(entry):
            username, display, record = entry
            if username.lower() in done:
                return (1, 0, display.casefold())
            joined = str(record.get("joined_on") or joined_on.get(username.lower()) or "")[:10]
            try:
                age = -date.fromisoformat(joined).toordinal()
            except ValueError:
                age = -date.max.toordinal()
            return (0, age, display.casefold())
        return key

    def _sync_start(self, username):
        text = self.person.currentText().strip()
        if username and self.direction == JOINING:
            joined = None
            try:
                joined = self.service.joined_on(username)
            except Exception:
                joined = None
            self._existing_joined = joined
            self.existing.setVisible(bool(joined))
            if joined:
                self.existing.setText("Joining date on record: %s." % format_date(joined))
        if username:
            self.person_hint.setText("")
        elif text and self._inactive_named(text):
            self.person_hint.setText(
                "%s is deactivated or has left - reactivate the account on "
                "%s first." % (html.escape(self._inactive_named(text)), self._users_link()))
        elif text:
            self.person_hint.setText(
                "No such person - create the account on %s first." % self._users_link())
        else:
            self.person_hint.setText("")
        self.start_button.setEnabled(bool(username))

    @staticmethod
    def _users_link() -> str:
        # Read when shown, so the colour follows the theme in force.
        return '<a href="users" style="color: %s;">Users &amp; Roles</a>' % Gate.ACCENT

    def _open_users(self, _link=None):
        """Close this dialog and open Users & Roles, where accounts are made."""
        window = self.parent().window() if self.parent() is not None else None
        switch = getattr(window, "_switch_to_tab_label", None)
        if callable(switch) and switch("Users & Roles"):
            self.reject()

    def _inactive_named(self, text: str) -> str:
        """The username of a switched-off account this text names, if any."""
        if not hasattr(self, "_inactive"):
            from slate.gui.components.person_picker import label_for, people
            self._inactive = {}
            for username, display, record in people(include_inactive=True):
                if record.get("active", True):
                    continue
                for name in (username, display, label_for(username, display)):
                    self._inactive[str(name).casefold()] = username
        return self._inactive.get(text.strip().casefold(), "")

    def accept(self):
        """Ask before changing a joining date that is already recorded."""
        self.overwrite_joined = False
        existing = getattr(self, "_existing_joined", None)
        chosen = self.effective.date().toPython()
        if self.direction == JOINING and existing and existing != chosen:
            answer = QMessageBox.question(
                self, "Change joining date",
                "Change %s's joining date from %s to %s? Leave accrual is counted from it.\n\n"
                "No keeps %s." % (self.person.currentText(), format_date(existing),
                                  format_date(chosen), format_date(existing)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel:
                return
            self.overwrite_joined = answer == QMessageBox.StandardButton.Yes
        super().accept()

    def payload(self):
        # Only a real username; never the text as typed.
        username = self.person.username()
        day = self.effective.date()
        return (str(username), self.employment.currentData(),
                self.department.currentText().strip(),
                date(day.year(), day.month(), day.day()))


class JoiningLeavingView(QWidget):
    """The spine - people on their way in and on their way out."""

    changed = Signal()

    PEOPLE_COLUMNS = ["Person", "Joining / leaving", "Date", "Your tasks", "Total open"]

    def __init__(self, username: str, team: str = HR, db_manager=None, parent=None):
        """team is the half of the list this person owns - "HR" or "IT"."""
        super().__init__(parent)
        self.username = (username or "").strip()
        self.team = IT if str(team).upper() == IT else HR
        self.service = OnboardingService(db_manager)
        self._people = []
        self._tasks = []
        # A row is a person *and* a direction: somebody joining and leaving
        # has two lists, and merging them mixed both into one 19-line table.
        self._selected_person = None       # (username, direction)

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_5, Gate.SPACE_4, Gate.SPACE_5, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)

        root.addWidget(page_title(
            "Joining and leaving",
            "Your paperwork, and who is still waiting on it" if self.team == HR
            else "Accounts, access and machines - what is still outstanding"))

        self.figures = StatStrip()
        root.addWidget(self.figures)
        self.card_mine = self.figures.add("On your list", 0, caption="tasks for %s" % self.team)
        self.card_joining = self.figures.add("Joining", 0, tone="accent",
                                             caption="people still being set up")
        self.card_leaving = self.figures.add("Leaving", 0, caption="people still being wound down")
        # A machine still out with somebody who has left is money on somebody
        # else's desk. It only earns a card when it is actually happening.
        self.card_kit = self.figures.add("Kit not returned", 0, tone="bad",
                                         caption="issued to people who have left")

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

        self.people_table = QTableWidget(0, len(self.PEOPLE_COLUMNS))
        self.people_table.setHorizontalHeaderLabels(self.PEOPLE_COLUMNS)
        style_table(self.people_table, {
            "Person": "stretch", "Joining / leaving": "contents", "Date": "contents",
            "Your tasks": "numeric", "Total open": "numeric"}, multi_select=False)
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

        # One empty state until somebody is chosen, instead of an empty table
        # and four dead buttons.
        self.pick_empty = EmptyState(
            "Pick somebody on the left",
            "Their checklist appears here, with what is still to do.", glyph="users")
        right_box.addWidget(self.pick_empty, 1)

        self.detail = QWidget()
        detail_box = QVBoxLayout(self.detail)
        detail_box.setContentsMargins(0, 0, 0, 0)
        detail_box.setSpacing(Gate.SPACE_2)

        self.heading = QLabel("")
        self.heading.setStyleSheet(
            f"color: {Gate.TEXT}; font-family: {Gate.FONT_LABEL_STRONG}; font-size: 15px;")
        detail_box.addWidget(self.heading)

        self.holding = QLabel("")
        self.holding.setWordWrap(True)
        self.holding.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 12px;")
        detail_box.addWidget(self.holding)

        # Owner is always this team (each team sees its own lines), so it is
        # not a column; Machine only means anything to IT.
        columns = ["State", "Task", "Done by"] + (["Machine"] if self.team == IT else [])
        self.task_columns = columns
        self.task_table = QTableWidget(0, len(columns))
        self.task_table.setHorizontalHeaderLabels(columns)
        style_table(self.task_table, {"State": "contents", "Task": "stretch",
                                      "Done by": "contents", "Machine": "contents"},
                    multi_select=True)
        self.task_table.itemSelectionChanged.connect(self._sync_buttons)
        detail_box.addWidget(self.task_table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(Gate.SPACE_2)
        self.btn_done = make_button("Mark done", "primary", on_click=lambda: self._tick(True))
        self.btn_undo = make_button("Reopen", "ghost", on_click=lambda: self._tick(False))
        actions.addWidget(self.btn_done)
        actions.addWidget(self.btn_undo)
        actions.addStretch(1)

        self.btn_cancel_list = None
        if self.team == HR:
            # A list started by mistake stayed in 'Everyone outstanding' for ever.
            self.btn_cancel_list = make_button(
                "Cancel this checklist", "danger", on_click=self.cancel_checklist,
                tooltip="Remove the open lines of a list started by mistake")
            actions.addWidget(self.btn_cancel_list)

        # Issuing kit is IT's job and nobody else's.
        if self.team == IT:
            self.btn_issue = make_button("Issue machine", "secondary", on_click=self.issue_machine)
            self.btn_collect = make_button("Collect machine", "secondary", on_click=self.collect_machine)
            actions.addWidget(self.btn_issue)
            actions.addWidget(self.btn_collect)
        detail_box.addLayout(actions)
        right_box.addWidget(self.detail, 1)
        split.addWidget(right)

        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        root.addWidget(split, 1)

        self.refresh()
        # Other people's changes, without a restart (the change feed; a timer if it is missing).
        from slate.gui.components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(self, self.refresh, seconds=30, topics=("onboarding_workflows", "asset_assignments", "hardware_inventory"))

    # ------------------------------------------------------------------ data
    @on_database_error
    def refresh(self, *_):
        outstanding = self.service.open_tasks()
        records = {str(p.get("username") or "").lower(): p for p in self.service.people()}

        wanted = self.filter_direction.currentData()
        by_key = {}
        for task in outstanding:
            direction = task.get("direction") or JOINING
            if wanted in (JOINING, LEAVING) and direction != wanted:
                continue
            user = str(task.get("user_id") or "")
            entry = by_key.setdefault((user.lower(), direction),
                                      {"user_id": user, "direction": direction, "mine": 0, "all": 0})
            entry["all"] += 1
            if (task.get("owner_team") or "").upper() == self.team:
                entry["mine"] += 1

        if wanted == "all":
            # Include people whose list is already finished, so somebody can go
            # back and look at what was done.
            for name, record in records.items():
                for direction in (JOINING, LEAVING):
                    if (name, direction) in by_key:
                        continue
                    if self.service.tasks_for(record.get("username"), direction):
                        by_key[(name, direction)] = {"user_id": record.get("username"),
                                                     "direction": direction, "mine": 0, "all": 0}

        rows = list(by_key.values())
        for row in rows:
            record = records.get(row["user_id"].lower(), {})
            when = record.get("joined_on") if row["direction"] == JOINING else record.get("last_day")
            row["date"] = _as_date(when)
            row["name"] = str(record.get("display_name") or "").strip() or row["user_id"]

        # Yours first, then soonest - the person starting on Monday is the one
        # IT need to get to. No date sorts last.
        rows.sort(key=lambda r: (0 if r["mine"] else 1, r["date"] or date.max, r["name"].casefold()))
        self._people = rows

        self._paint_figures(outstanding)
        self._paint_people(rows)

        keys = {(r["user_id"].lower(), r["direction"]) for r in rows}
        if self._selected_person and (self._selected_person[0].lower(),
                                      self._selected_person[1]) not in keys:
            self._selected_person = None
        self._paint_tasks()
        self.people_empty.refresh()
        self._sync_buttons()

    def _paint_figures(self, outstanding):
        mine = [t for t in outstanding if (t.get("owner_team") or "").upper() == self.team]
        joining = {str(t.get("user_id")).lower() for t in outstanding
                   if (t.get("direction") or JOINING) == JOINING}
        leaving = {str(t.get("user_id")).lower() for t in outstanding
                   if (t.get("direction") or JOINING) == LEAVING}
        stranded = self.service.unreturned()
        self.card_mine.set_value(len(mine))
        self.card_mine.set_tone("warn" if mine else "ok")
        self.card_joining.set_value(len(joining))
        self.card_leaving.set_value(len(leaving))
        self.card_kit.set_value(len(stranded))
        self.card_kit.setVisible(bool(stranded))

    def _paint_people(self, rows):
        key = (self._selected_person[0].lower() + "|" + self._selected_person[1]) \
            if self._selected_person else None
        with KeepSelection(self.people_table):
            self.people_table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                leaving = row["direction"] == LEAVING
                cells = [
                    make_item(row["name"], key=row["user_id"].lower() + "|" + row["direction"],
                              tooltip=row["user_id"]),
                    make_item("Leaving" if leaving else "Joining",
                              foreground=Gate.TEXT_2 if leaving else Gate.ACCENT),
                    make_item(format_date(row["date"]) if row["date"] else "-",
                              sort_value=row["date"].isoformat() if row["date"] else None,
                              tooltip=("Last working day" if leaving else "Joining date")),
                    make_item(str(row["mine"]), sort_value=row["mine"],
                              foreground=Gate.WARN if row["mine"] else None),
                    make_item(str(row["all"]), sort_value=row["all"]),
                ]
                for c, item in enumerate(cells):
                    self.people_table.setItem(r, c, item)
        if key:
            from slate.gui.components.table_tools import select_keys
            self.people_table.blockSignals(True)
            select_keys(self.people_table, [key])
            self.people_table.blockSignals(False)

    def _person_picked(self):
        keys = selected_keys(self.people_table)
        if not keys:
            self._selected_person = None
        else:
            user, _, direction = str(keys[0]).partition("|")
            match = next((r for r in self._people
                          if r["user_id"].lower() == user and r["direction"] == direction), None)
            self._selected_person = (match["user_id"], direction) if match else None
        self._paint_tasks()
        self._sync_buttons()

    def _row_of_selected(self):
        if not self._selected_person:
            return None
        user, direction = self._selected_person
        return next((r for r in self._people
                     if r["user_id"].lower() == user.lower() and r["direction"] == direction), None)

    def _paint_tasks(self):
        chosen = self._selected_person
        self.pick_empty.setVisible(not chosen)
        self.detail.setVisible(bool(chosen))
        if not chosen:
            self.task_table.setRowCount(0)
            self._tasks = []
            return
        person, direction = chosen
        row = self._row_of_selected() or {}
        name = row.get("name") or people.display_name(person)

        tasks = self.service.tasks_for(person, direction)
        # Only this team's half. The other team's lines are not hidden work -
        # they are somebody else's work.
        tasks = [t for t in tasks if (t.get("owner_team") or "").upper() == self.team]
        # Outstanding first; a finished line is a record, not a job.
        tasks.sort(key=lambda t: (bool(t.get("is_completed")), t.get("id") or 0))
        self._tasks = tasks

        done = sum(1 for t in tasks if t.get("is_completed"))
        self.heading.setText("%s – %s – %d of %d done" % (
            name, "leaving" if direction == LEAVING else "joining", done, len(tasks)))

        held = self.service.held_by(person)
        if held:
            self.holding.setText("Currently holding: " + ", ".join(
                "%s (since %s)" % (h["machine_name"], format_date(h.get("issued_on")))
                for h in held))
        else:
            self.holding.setText("Nothing issued to this person.")

        self.task_table.setRowCount(len(tasks))
        for r, task in enumerate(tasks):
            complete = bool(task.get("is_completed"))
            by = task.get("completed_by")
            done_by = ""
            if complete and by:
                done_by = "%s on %s" % (people.display_name(by), format_date(task.get("completed_at")))
            elif complete:
                done_by = "Done"
            cells = [make_item("Done" if complete else "To do", key=task.get("id"),
                               foreground=Gate.OK if complete else Gate.WARN),
                     make_item(task.get("task_name") or "",
                               foreground=Gate.TEXT_DIM if complete else None),
                     make_item(done_by, foreground=Gate.TEXT_DIM)]
            if self.team == IT:
                cells.append(make_item(task.get("asset_name") or ""))
            for c, item in enumerate(cells):
                self.task_table.setItem(r, c, item)

    # --------------------------------------------------------------- actions
    def _selected_tasks(self):
        rows = sorted({i.row() for i in self.task_table.selectedIndexes()})
        return [self._tasks[r] for r in rows if r < len(self._tasks)]

    def _sync_buttons(self, *_):
        picked = self._selected_tasks()
        self.btn_done.setEnabled(any(not t.get("is_completed") for t in picked))
        self.btn_undo.setEnabled(any(t.get("is_completed") for t in picked))
        person = self._selected_person[0] if self._selected_person else None
        if self.btn_cancel_list is not None:
            self.btn_cancel_list.setEnabled(bool(person))
        if self.team == IT:
            self.btn_issue.setEnabled(bool(person))
            self.btn_collect.setEnabled(bool(person) and bool(self.service.held_by(person)))

    def _start(self, direction):
        dialog = StartPersonDialog(self.service, direction, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        username, employment, department, effective = dialog.payload()
        if not username:
            return
        from slate.core.domain.onboarding_service import UnknownPerson
        try:
            made = self.service.start(username, direction, employment, department,
                                      effective_date=effective,
                                      overwrite_joined=getattr(dialog, "overwrite_joined", False))
        except UnknownPerson as missing:
            QMessageBox.warning(self, "Start joining" if direction == JOINING else "Start leaving",
                                str(missing))
            return
        if not made:
            QMessageBox.information(
                self, "Already on the list",
                "%s already has a %s checklist - nothing was added."
                % (people.display_name(username), "joining" if direction == JOINING else "leaving"))
        self._selected_person = (username, direction)
        self.refresh()
        self.changed.emit()

    def cancel_checklist(self):
        if not self._selected_person:
            return
        person, direction = self._selected_person
        name = people.display_name(person)
        kind = "joining" if direction == JOINING else "leaving"
        if QMessageBox.question(
            self, "Cancel this checklist",
            "Remove the open lines of %s's %s checklist? Lines already ticked stay "
            "as the record of what was done." % (name, kind),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return
        clear = False
        if direction == LEAVING and self.service.last_day(person):
            clear = QMessageBox.question(
                self, "Cancel this checklist",
                "Also clear the last working day recorded for %s?" % name,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes
        removed = self.service.cancel_checklist(person, direction, self.username,
                                                clear_last_day=clear)
        from slate.gui.components.feedback import toast
        toast(self, "%s's %s checklist cancelled (%d line%s removed)." % (
            name, kind, removed, "" if removed == 1 else "s"), "success")
        self._selected_person = None
        people.refresh()
        self.refresh()
        self.changed.emit()

    def _tick(self, done: bool):
        picked = [t for t in self._selected_tasks()
                  if bool(t.get("is_completed")) != done]
        if not picked:
            return
        failed = [t for t in picked
                  if not self.service.complete(t.get("id"), done, by=self.username)]
        if failed:
            QMessageBox.warning(self, "Checklist", "%d line(s) could not be saved - "
                                "somebody may have removed them. The list has been refreshed."
                                % len(failed))
        self.refresh()
        self.changed.emit()

    def issue_machine(self):
        if not self._selected_person:
            return
        person = self._selected_person[0]
        name = people.label(person)
        free = self.service.available_machines_detail()
        if not free:
            QMessageBox.information(
                self, "No free machines",
                "Every machine in the inventory is already assigned. Free one up "
                "in Hardware, or add the new one first.")
            return
        override = False
        refusal = self.service.issue_refusal(person)
        if refusal:
            if QMessageBox.question(
                self, "Issue a machine",
                "%s Issue a machine to them anyway?" % refusal,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
            ) != QMessageBox.StandardButton.Yes:
                return
            override = True
        dialog = MachinePickerDialog(free, name, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.machine():
            return
        machine = dialog.machine()
        if self.service.issue_machine(machine, person, self.username, override=override):
            QMessageBox.information(
                self, "Issue a machine",
                "%s is now recorded against %s. When they leave, the leaving list "
                "will ask for it back." % (machine, name))
        else:
            QMessageBox.warning(self, "Issue a machine", "The assignment could not be recorded.")
        self.refresh()
        self.changed.emit()

    def collect_machine(self):
        if not self._selected_person:
            return
        person = self._selected_person[0]
        held = [h["machine_name"] for h in self.service.held_by(person)]
        if not held:
            return
        machine, ok = QInputDialog.getItem(
            self, "Collect a machine", "Which machine has come back?", held, 0, False)
        if not ok or not machine:
            return
        if self.service.return_machine(machine, person, self.username):
            QMessageBox.information(
                self, "Collect a machine",
                "%s is free again and can be issued to somebody else." % machine)
        else:
            # It said nothing at all when this failed.
            QMessageBox.warning(
                self, "Collect a machine",
                "Not saved: %s is not recorded as issued to %s any more. "
                "The list has been refreshed." % (machine, people.display_name(person)))
        self.refresh()
        self.changed.emit()
