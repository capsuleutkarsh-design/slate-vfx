"""
Scheduling: the studio's milestones, as a table, a Gantt timeline and people.

What it used to be: a seven-column table with Add / Update Status / Shift.
Cells could be typed into and nothing was saved, there was no way to edit or
delete a milestone, the "In Progress" card counted every unfinished row, the
shift moved completed work and could stop half way, and the sidebar promised
Gantt charts that did not exist.

The rules live in core/domain/scheduling.py and the SQL in
core/infra/schedule_repository.py. This file is the screen:

    Table | Timeline | People   one set of filters and one selection for all
                                three views (schedule_timeline.py draws the
                                other two)
    Add / Edit / Delete         milestone_dialog.py; delete detaches what
                                waited on the milestone
    Change status               with Undo; completing work whose dependency
                                is still open asks first
    Shift dates                 shift_dates_dialog.py: a preview of what
                                moves, what stays and what would break,
                                then one transaction and an Undo

Changing anything needs the schedule_write ability; everybody else who can
open the tab sees it read-only.
"""

import logging
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QPushButton, QStackedWidget, QTableWidget, QVBoxLayout, QWidget,
)

from slate.core.domain import scheduling as DS
from slate.core.domain.dates import format_date
from slate.core.infra.gate import Gate
from slate.gui.core.offline_notice import on_database_error
from slate.gui.core.controls import make_button, page_title, style_button
from slate.gui.core.empty_state import EmptyState
from slate.gui.core.stat_card import StatStrip
from slate.gui.core.table_style import dim_cell, set_cell_status, style_table
from slate.gui.core.data_display import SORT_ROLE, date_item, select_row_by_id
from slate.gui.components.table_tools import (
    KEY_ROLE, KeepSelection, TableToolbar, make_item, select_keys, selected_keys, setup_table,
)

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else is reported as a failed read.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""

logger = logging.getLogger(__name__)

# Columns of the table.
C_ID, C_PROJECT, C_NAME, C_DEPENDS, C_START, C_END, C_STATUS, C_OWNER, C_DEPT = range(9)
HEADERS = ["ID", "Project", "Milestone", "Depends on", "Start", "End", "Status", "Owner",
           "Department"]
OVERDUE = "__overdue__"

VIEW_TABLE, VIEW_TIMELINE, VIEW_PEOPLE = 0, 1, 2


class StatusDialog(QDialog):
    """
    Change the status of the selected milestones. Says which ones, starts on
    their current status (or asks for a choice when they differ), and has a
    Cancel - the old one was a 113 px box with only "Update", always starting
    on "Scheduled" so a quick OK reset work in progress.
    """

    def __init__(self, parent, milestones):
        super().__init__(parent)
        count = len(milestones)
        self.setWindowTitle("Change status")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setSpacing(Gate.SPACE_3)
        heading = QLabel(f"Change the status of {DS.plural(count, 'milestone')}:")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)
        names = [f"• {m.name} ({m.project_code})" for m in milestones[:6]]
        if count > 6:
            names.append(f"and {count - 6} more")
        listing = QLabel("\n".join(names))
        listing.setWordWrap(True)
        listing.setStyleSheet(f"color: {Gate.TEXT_2};")
        layout.addWidget(listing)

        self.combo = QComboBox()
        current = {m.status for m in milestones}
        if len(current) != 1:
            self.combo.addItem("— choose —", "")
        for status in DS.STATUSES:
            self.combo.addItem(status, status)
        if len(current) == 1:
            index = self.combo.findData(next(iter(current)))
            self.combo.setCurrentIndex(max(index, 0))
        layout.addWidget(self.combo)

        buttons = QDialogButtonBox()
        self.ok_button = buttons.addButton("Change status", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        style_button(self.ok_button, "primary")
        style_button(cancel, "secondary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.combo.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self, *_):
        self.ok_button.setEnabled(bool(self.combo.currentData()))

    def status(self) -> str:
        return str(self.combo.currentData() or "")


class ProdSchedulingTab(QWidget):
    def __init__(self, parent=None, user_data=None, repo=None):
        super().__init__(parent)
        # Who is looking, so the tab can ask access.can(self.user_roles, ...).
        self.user_data = dict(user_data or {})
        roles = self.user_data.get("roles") or self.user_data.get("role") or []
        self.user_roles = [roles] if isinstance(roles, str) else list(roles)
        self.username = str(self.user_data.get("username") or "")
        from slate.core.domain import access
        self.can_write = access.can(self.user_roles, "schedule_write")
        if repo is None:
            from slate.core.infra.schedule_repository import ScheduleRepository
            repo = ScheduleRepository(roles=self.user_roles)
        self.repo = repo
        self.all_milestones = []          # every milestone, archived projects included
        self.milestones = []              # what the table holds
        self.by_id = {}
        self.calendar = DS.WorkCalendar()
        self._names = {}
        self._timeline_dirty = True
        self._people_dirty = True

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        self.build_ui(main_layout)

    # ------------------------------------------------------------------ layout
    def build_ui(self, main_layout):
        main_layout.addWidget(page_title("Scheduling", "Milestones, dependencies, who owns them "
                                                       "and the Gantt timeline"))

        # Counted from the status column; Overdue is its own card and opens
        # the overdue ones.
        strip = StatStrip(compact=True)
        self.card_projects = strip.add("Projects", "0", tone="accent",
                                       tooltip="Projects with open milestones in what is shown")
        self.card_scheduled = strip.add("Scheduled", "0", tone="info")
        self.card_progress = strip.add("In progress", "0", tone="warn")
        self.card_paused = strip.add("On hold / blocked", "0", tone="idle")
        self.card_overdue = strip.add("Overdue", "0", tone="ok", on_click=self.show_overdue,
                                      tooltip="Open milestones past their end date - click to list them")
        self.card_completed = strip.add("Completed", "0", tone="ok")
        main_layout.addWidget(strip)

        controls = QHBoxLayout()
        controls.setSpacing(Gate.SPACE_2)
        self.add_button = make_button("Add milestone", "primary", icon="plus", on_click=self.add_milestone)
        self.edit_button = make_button("Edit…", icon="edit", on_click=self.edit_milestone,
                                       tooltip="Change the selected milestone (or double-click it)")
        self.status_button = make_button("Change status…", on_click=self.update_status)
        self.shift_button = make_button("Shift dates…", on_click=self.shift_dates,
                                        tooltip="Move this milestone and everything that depends on it")
        self.delete_button = make_button("Delete…", "danger", icon="trash", on_click=self.delete_milestones)
        self.write_buttons = (self.add_button, self.edit_button, self.status_button,
                              self.shift_button, self.delete_button)
        for button in self.write_buttons:
            controls.addWidget(button)
            button.setVisible(self.can_write)
        self.read_only_badge = QLabel("Read-only")
        self.read_only_badge.setToolTip("You can look at the schedule. Changing it needs the "
                                        "'Edit the schedule' ability on your role.")
        self.read_only_badge.setStyleSheet(
            f"color: {Gate.TEXT_2}; background: {Gate.RAISED}; border: 1px solid {Gate.LINE}; "
            f"border-radius: {Gate.RADIUS_SM}px; padding: 3px 8px;")
        self.read_only_badge.setVisible(not self.can_write)
        controls.addWidget(self.read_only_badge)
        controls.addStretch()
        self.archived_box = QCheckBox("Show archived projects")
        self.archived_box.setToolTip("Include milestones of projects that are no longer active")
        self.archived_box.toggled.connect(lambda *_: self.load_data())
        controls.addWidget(self.archived_box)

        # Table | Timeline | People
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        switch = QHBoxLayout()
        switch.setSpacing(0)
        for index, (label, icon) in enumerate((("Table", "list"), ("Timeline", "timeline"),
                                               ("People", "users"))):
            button = make_button(label, "secondary", icon=icon)
            button.setCheckable(True)
            button.setProperty("segment", "first" if index == 0 else ("last" if index == 2 else "mid"))
            self.view_group.addButton(button, index)
            switch.addWidget(button)
        self.view_group.button(VIEW_TABLE).setChecked(True)
        self.view_group.idClicked.connect(self.set_view)
        self._style_switch()
        controls.addSpacing(Gate.SPACE_3)
        controls.addLayout(switch)
        main_layout.addLayout(controls)

        self.grid = QTableWidget(0, len(HEADERS))
        self.grid.setHorizontalHeaderLabels(HEADERS)
        self.style_table(self.grid)
        # Read-only cells: typing a new End Date into a cell looked like a
        # re-plan and saved nothing. Double-click (or Enter) edits instead.
        setup_table(self.grid)
        self.grid.setWordWrap(False)
        self.grid.doubleClicked.connect(lambda _index: self.edit_milestone())
        self.grid.itemSelectionChanged.connect(self._selection_changed)

        self.toolbar = TableToolbar(self.grid, placeholder="Search milestone, project, owner or dependency…",
                                    columns=(C_PROJECT, C_NAME, C_DEPENDS, C_STATUS, C_OWNER, C_DEPT),
                                    on_refresh=self.load_data)
        self.project_filter = self.toolbar.add_filter("Project", [("All projects", "")], column=C_PROJECT)
        self.status_filter = self.toolbar.add_filter(
            "Status", [("All statuses", "")] + [(s, s) for s in DS.STATUSES]
            + [("Overdue", OVERDUE), ("Open (not finished)", "__open__")],
            column=C_STATUS, match=self._status_matches)
        self.owner_filter = self.toolbar.add_filter("Owner", [("Anyone", "")], column=C_OWNER,
                                                    match=self._owner_matches)
        # A long project name must not widen the page past a 1280 px screen.
        for combo, width in ((self.project_filter, 22), (self.status_filter, 12), (self.owner_filter, 12)):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(width)
            combo.view().setMinimumWidth(280)
        self.toolbar.filter.counted.connect(self._filtered)
        main_layout.addWidget(self.toolbar)

        from slate.gui.components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(self, self.load_data, seconds=30,
                                         topics=("prod_scheduling", "holiday_calendar"))

        self.grid.hideColumn(C_ID)
        self.stack = QStackedWidget()
        table_page = QWidget()
        table_layout = QVBoxLayout(table_page)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self.grid)
        self.stack.addWidget(table_page)

        from slate.gui.tabs.schedule_timeline import ScheduleTimeline
        self.timeline = ScheduleTimeline(self)
        self.timeline.gantt.activated.connect(self._timeline_activated)
        self.timeline.gantt.selected.connect(self._timeline_selected)
        self.timeline.gantt.dragged.connect(self._timeline_dragged)
        self.stack.addWidget(self.timeline)
        self.people = ScheduleTimeline(self, people_mode=True)
        self.people.gantt.selected.connect(self._timeline_selected)
        self.people.gantt.activated.connect(self._timeline_activated)
        self.stack.addWidget(self.people)
        main_layout.addWidget(self.stack, 1)

        self.empty = EmptyState.over(
            self.grid, "No milestones yet",
            "Add the first milestone of a project - a turnover, a first pass, a delivery.",
            primary=("Add milestone", self.add_milestone) if self.can_write else None,
            glyph="calendar")

        self._sync_buttons()
        # First read only now that the table is in the layout: a notice for a
        # failed read takes the table's place.
        self.load_data()

    def _style_switch(self):
        """The three view buttons read as one control, the chosen one filled."""
        for button in self.view_group.buttons():
            segment = button.property("segment")
            left = Gate.RADIUS_MD if segment == "first" else 0
            right = Gate.RADIUS_MD if segment == "last" else 0
            button.setStyleSheet(button.styleSheet() + Gate.sheet(
                "QPushButton { border-top-left-radius: %dpx; border-bottom-left-radius: %dpx; "
                "border-top-right-radius: %dpx; border-bottom-right-radius: %dpx; } "
                "QPushButton:checked { background: @ACCENT_SURFACE; border-color: @ACCENT; "
                "color: @TEXT; }" % (left, left, right, right)))

    def style_table(self, table: QTableWidget):
        """The milestone name takes the spare width; codes, dates and status their content."""
        style_table(table, {
            "Project": ("interactive", 150),
            "Milestone": "stretch",
            "Depends on": ("interactive", 220),
            "Start": "contents",
            "End": "contents",
            "Status": "contents",
            "Owner": ("interactive", 150),
            "Department": "contents",
        }, multi_select=True)

    # ------------------------------------------------------------------ filters
    def _milestone_of_row(self, row):
        item = self.grid.item(row, C_ID)
        key = item.data(KEY_ROLE) if item else None
        return self.by_id.get(key)

    def _status_matches(self, cell, value, row):
        m = self._milestone_of_row(row)
        if value == OVERDUE:
            return bool(m) and DS.is_overdue(m)
        if value == "__open__":
            return bool(m) and m.is_open
        return cell.casefold() == str(value).casefold()

    def _owner_matches(self, cell, value, row):
        m = self._milestone_of_row(row)
        return bool(m) and m.owner.casefold() == str(value).casefold()

    def show_overdue(self):
        index = self.status_filter.findData(OVERDUE)
        if index >= 0:
            self.status_filter.setCurrentIndex(index)

    def clear_filters(self):
        self.toolbar.filter.clear()

    def _refill_combo(self, combo, entries, all_label):
        keep = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(all_label, "")
        for text, value in entries:
            combo.addItem(text, value)
        index = combo.findData(keep)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _filtered(self, visible: int, total: int):
        """A filter that hides every row is 'no match', not 'no milestones'."""
        if not hasattr(self, "empty"):
            return              # still being built
        narrowed = bool(total) and visible == 0
        self.empty.set_filtered(narrowed, on_clear=self.clear_filters, noun="milestones")
        if narrowed:
            self.empty.setVisible(True)
            self.empty.raise_()
        else:
            self.empty.refresh()
        self._timeline_dirty = self._people_dirty = True
        self._update_cards()
        self._refresh_visible_view()

    def visible_milestones(self):
        out = []
        for row in range(self.grid.rowCount()):
            if self.grid.isRowHidden(row):
                continue
            m = self._milestone_of_row(row)
            if m is not None:
                out.append(m)
        return out

    # ------------------------------------------------------------------ data
    @on_database_error
    def load_data(self, *_):
        try:
            everything = self.repo.list(include_archived=True)
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            # A failed read is not an empty schedule.
            from slate.gui.components.state_notice import show_load_error
            logger.exception("Schedule could not be read")
            show_load_error(self, e, retry=self.load_data, what="the schedule")
            return
        from slate.gui.components.state_notice import clear_state
        clear_state(self)

        self.all_milestones = everything
        show_archived = self.archived_box.isChecked()
        self.milestones = [m for m in everything if show_archived or not m.archived]
        self.by_id = DS.index(everything)
        first, last = DS.timeline_range(self.milestones)
        try:
            self.calendar = self.repo.calendar(first, last)
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("Holidays could not be read; weekends only")
            self.calendar = DS.WorkCalendar()
        self._names = self._display_names({m.owner for m in everything if m.owner})

        projects = {}
        for m in self.milestones:
            projects.setdefault(m.project_code, m.project_name)
        from slate.gui.tabs.milestone_dialog import project_label
        self._refill_combo(self.project_filter,
                           [(project_label(c, n), c) for c, n in sorted(projects.items(),
                                                                         key=lambda x: x[0].casefold())],
                           "All projects")
        owners = sorted({m.owner for m in self.milestones if m.owner},
                        key=lambda u: self._names.get(u, u).casefold())
        self._refill_combo(self.owner_filter, [(self._names.get(u, u), u) for u in owners], "Anyone")

        # The selection follows the milestone (by id), not the row number.
        with KeepSelection(self.grid):
            self._fill(self.milestones)
        self.toolbar.filter.apply()
        self.empty.refresh()
        self._timeline_dirty = self._people_dirty = True
        self._update_cards()
        self._sync_buttons()
        self._refresh_visible_view()

    @staticmethod
    def _display_names(usernames):
        try:
            from slate.core.domain.people import display_names
            return {u: (n or u) for u, n in display_names(usernames).items()}
        except DatabaseUnavailableError:
            raise
        except Exception:
            return {u: u for u in usernames}

    def _update_cards(self):
        s = DS.summary(self.visible_milestones())
        self.card_projects.set_value(s.projects)
        self.card_scheduled.set_value(s.scheduled)
        self.card_progress.set_value(s.in_progress)
        self.card_paused.set_value(s.paused)
        self.card_overdue.set_value(s.overdue)
        self.card_overdue.set_tone("bad" if s.overdue else "ok")
        self.card_completed.set_value(s.completed)

    def _fill(self, milestones):
        today = date.today()
        self.grid.setRowCount(0)
        self.grid.setRowCount(len(milestones))
        for r, m in enumerate(milestones):
            self.grid.setItem(r, C_ID, make_item(str(m.id), sort_value=m.id, key=m.id))
            project = make_item(m.project_code, tooltip=(f"{m.project_code} – {m.project_name}"
                                                         if m.project_name else m.project_code)
                                + (" (archived project)" if m.archived else ""))
            if m.archived:
                dim_cell(project)
            self.grid.setItem(r, C_PROJECT, project)
            name = make_item(m.name, tooltip=m.name + (f"\nBefore the upgrade: {m.legacy_dates}"
                                                       if m.legacy_dates else ""))
            self.grid.setItem(r, C_NAME, name)

            dep_text, dep_tone = DS.dependency_label(m, self.by_id)
            dep = make_item(dep_text or "—", tooltip=dep_text)
            if dep_tone:
                set_cell_status(dep, dep_tone, background=False)
            elif not dep_text:
                dim_cell(dep)
            self.grid.setItem(r, C_DEPENDS, dep)

            for column, value, raw in ((C_START, m.start, m.start_text), (C_END, m.end, m.end_text)):
                if value is None:
                    text = "Invalid date" if raw.strip() and raw.strip().lower() != "none" else "—"
                    cell = make_item(text, tooltip=f"Stored as '{raw}'" if text == "Invalid date" else "")
                    if text == "Invalid date":
                        set_cell_status(cell, "warn", background=False)
                    else:
                        dim_cell(cell)
                else:
                    cell = date_item(value)
                self.grid.setItem(r, column, cell)
            end_item = self.grid.item(r, C_END)
            if DS.is_overdue(m, today):
                set_cell_status(end_item, DS.OVERDUE_TONE, background=False)
                end_item.setToolTip(f"Overdue - {DS.plural(DS.days_late(m, today), 'day')} late")
            elif m.dates_reversed:
                set_cell_status(end_item, "warn", background=False)
                end_item.setToolTip("The end date is before the start date.")
            else:
                why = self.calendar.why_not_working(m.end)
                if why:
                    end_item.setToolTip(f"Ends on a non-working day: {why}")

            status = make_item(m.status or "—")
            if m.status:
                set_cell_status(status, DS.status_tone(m.status), background=False)
            else:
                dim_cell(status)
            self.grid.setItem(r, C_STATUS, status)
            owner = make_item(self._names.get(m.owner, m.owner) if m.owner else "—",
                              tooltip=m.owner)
            if not m.owner:
                dim_cell(owner)
            self.grid.setItem(r, C_OWNER, owner)
            dept = make_item(self._department_name(m.department) if m.department else "—")
            if not m.department:
                dim_cell(dept)
            self.grid.setItem(r, C_DEPT, dept)

    @staticmethod
    def _department_name(key):
        try:
            from slate.core.domain.departments import get_department
            dept = get_department(key)
            return (dept.name or dept.label) if dept else key
        except Exception:
            return key

    # ------------------------------------------------------------------ views
    def set_view(self, view: int):
        self.stack.setCurrentIndex(view)
        button = self.view_group.button(view)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        self._refresh_visible_view()

    def _refresh_visible_view(self):
        view = self.stack.currentIndex()
        if view == VIEW_TIMELINE and self._timeline_dirty:
            self._timeline_dirty = False
            self.timeline.gantt.set_selected(self._selected_ids())
            self.timeline.gantt.show_milestones(self.visible_milestones(), self.calendar,
                                                all_by_id=self.by_id, editable=self.can_write)
        elif view == VIEW_PEOPLE and self._people_dirty:
            self._people_dirty = False
            self._show_people()

    @on_database_error
    def _show_people(self, *_):
        shown = self.visible_milestones()
        first, last = DS.timeline_range(shown)
        project = self.project_filter.currentData()
        try:
            tasks, away = self.repo.people_data(first, last, [project] if project else None)
            from slate.core.infra.schedule_repository import person_resolver
            resolve = person_resolver()
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.exception("People view could not be read")
            from slate.gui.components.feedback import toast
            toast(self, f"The people view could not read the dashboard work: {exc}", "error")
            tasks, away, resolve = [], [], (lambda n: n)
        items, skipped = DS.task_items(tasks, self.calendar, resolve)
        items += DS.milestone_items(shown)
        people = {i.person for i in items}
        plan = DS.people_plan(items, [a for a in away if a.person in people], self.calendar)
        plan.skipped_tasks = skipped
        names = self._display_names(set(plan.people))
        tones = {m.id: (DS.OVERDUE_TONE if DS.is_overdue(m) else DS.status_tone(m.status))
                 for m in shown}
        self.people.gantt.show_people(plan, self.calendar, names=names, milestone_tone=tones)
        self.people.set_people_problems(plan, names)
        self.people_plan = plan

    def _selected_ids(self):
        return [int(k) for k in selected_keys(self.grid) if k is not None]

    def _selection_changed(self):
        self._sync_buttons()
        ids = self._selected_ids()
        self.timeline.gantt.set_selected(ids)
        self.people.gantt.set_selected(ids)

    def _timeline_selected(self, milestone_id: int):
        select_keys(self.grid, [milestone_id])

    def _timeline_activated(self, milestone_id: int):
        select_keys(self.grid, [milestone_id])
        self.edit_milestone()

    def _sync_buttons(self):
        ids = self._selected_ids()
        one = len(ids) == 1
        self.edit_button.setEnabled(one)
        self.shift_button.setEnabled(one)
        self.status_button.setEnabled(bool(ids))
        self.delete_button.setEnabled(bool(ids))
        self.add_button.setEnabled(True)

    # ------------------------------------------------------------------ actions
    def _projects(self):
        try:
            return self.repo.projects()
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.exception("Projects could not be read")
            from slate.gui.components.feedback import warn
            warn(self, "Add milestone", f"The project list could not be read: {exc}")
            return None

    def _away(self):
        try:
            first, last = DS.timeline_range(self.all_milestones)
            return self.repo.people_data(first, last)[1]
        except DatabaseUnavailableError:
            raise
        except Exception:
            return []

    def _dialog(self, milestone=None):
        from slate.gui.tabs.milestone_dialog import MilestoneDialog
        projects = self._projects()
        if projects is None:
            return None
        if not projects and milestone is None:
            from slate.gui.components.feedback import inform
            inform(self, "Add milestone", "There are no active projects yet.",
                   "Create a project on the VFX Dashboard first, then add its milestones here.")
            return None
        return MilestoneDialog(self, repo=self.repo, milestones=self.all_milestones,
                               projects=projects, milestone=milestone,
                               default_project=self.project_filter.currentData() or "",
                               username=self.username, calendar=self.calendar, away=self._away())

    @on_database_error
    def add_milestone(self, *_):
        if not self.can_write:
            return
        dialog = self._dialog()
        if dialog is None or dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.load_data()
        if dialog.saved_id is not None:
            select_keys(self.grid, [dialog.saved_id])
        from slate.gui.components.feedback import toast
        toast(self, f"Added \"{dialog.saved.name}\" to {dialog.saved.project_code}.", "success")

    @on_database_error
    def edit_milestone(self, *_):
        ids = self._selected_ids()
        if len(ids) != 1 or not self.can_write:
            return
        m = self.by_id.get(ids[0])
        if m is None:
            return
        dialog = self._dialog(m)
        if dialog is None or dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.load_data()
        select_keys(self.grid, [m.id])
        from slate.gui.components.feedback import toast
        toast(self, f"Saved \"{dialog.saved.name}\".", "success")

    @on_database_error
    def delete_milestones(self, *_):
        ids = self._selected_ids()
        if not ids or not self.can_write:
            return
        doomed = [self.by_id[i] for i in ids if i in self.by_id]
        waiting = self.repo.dependents(ids)
        from slate.gui.components.feedback import confirm, toast, warn
        names = ", ".join(f"\"{m.name}\"" for m in doomed[:4]) + (
            f" and {len(doomed) - 4} more" if len(doomed) > 4 else "")
        detail = ""
        if waiting:
            detail = (f"{DS.plural(len(waiting), 'milestone')} "
                      f"{'waits' if len(waiting) == 1 else 'wait'} on "
                      f"{'it' if len(doomed) == 1 else 'them'} ("
                      + ", ".join(m.name for m in waiting[:4])
                      + (" …" if len(waiting) > 4 else "")
                      + ("). It is kept and will no longer depend on anything." if len(waiting) == 1
                         else "). They are kept and will no longer depend on anything."))
        if not confirm(self, "Delete milestones",
                       f"Delete {DS.plural(len(doomed), 'milestone')}: {names}? This cannot be undone.",
                       yes_label=f"Delete {DS.plural(len(doomed), 'milestone')}",
                       destructive=True, informative=detail):
            return
        try:
            count = self.repo.delete(ids, by=self.username)
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.exception("Milestones not deleted")
            warn(self, "Delete milestones", f"Nothing was deleted: {exc}")
            return
        self.load_data()
        toast(self, f"Deleted {DS.plural(count, 'milestone')}"
              + (f"; {DS.plural(len(waiting), 'dependent')} detached." if waiting else "."), "success")

    @on_database_error
    def update_status(self, *_):
        # By milestone id, never by row number (see KeepSelection).
        ids = self._selected_ids()
        if not ids or not self.can_write:
            return
        chosen = [self.by_id[i] for i in ids if i in self.by_id]
        dialog = StatusDialog(self, chosen)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_status = dialog.status()
        from slate.gui.components.feedback import confirm, toast, warn
        if new_status == DS.COMPLETED:
            waits = [DS.completion_warning(m, self.by_id) for m in chosen]
            waits = [w for w in waits if w]
            if waits and not confirm(self, "Change status", "\n".join(waits[:5]),
                                     yes_label="Mark completed anyway",
                                     informative="Complete it anyway?"):
                return
        try:
            previous = self.repo.set_status(ids, new_status, by=self.username)
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.exception("Status not changed")
            warn(self, "Change status", f"No status was changed: {exc}")
            self.load_data()
            return
        self.load_data()
        changed = sum(1 for old, _d in previous.values() if old != new_status)

        def undo():
            try:
                self.repo.restore_statuses(previous, by=self.username)
            except DatabaseUnavailableError:
                raise
            except Exception as exc:
                warn(self, "Undo status change", f"The statuses could not be put back: {exc}")
            self.load_data()
            toast(self, "Status change undone.", "info")

        if changed:
            toast(self, f"{DS.plural(changed, 'milestone')} set to {new_status}.", "success",
                  action=("Undo", undo))
        else:
            toast(self, f"Nothing changed - already {new_status}.", "info")

    @on_database_error
    def shift_dates(self, *_, root_to=None, milestone_id=None):
        ids = [milestone_id] if milestone_id is not None else self._selected_ids()
        if len(ids) != 1 or not self.can_write:
            return
        from slate.gui.tabs.shift_dates_dialog import ShiftDatesDialog
        dialog = ShiftDatesDialog(self, milestones=self.all_milestones, root_id=ids[0],
                                  calendar=self.calendar, root_to=root_to)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.plan is None:
            return
        self.apply_plan(dialog.plan)

    def apply_plan(self, plan: DS.ShiftPlan):
        from slate.gui.components.feedback import toast, warn
        root = self.by_id.get(plan.root_id)
        try:
            self.repo.apply_dates(plan.changes(), by=self.username)
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.exception("Shift not applied")
            warn(self, "Shift dates", f"Nothing was moved: {exc}")
            self.load_data()
            return
        self.load_data()
        select_keys(self.grid, [plan.root_id])

        def undo():
            try:
                self.repo.apply_dates(plan.undo_changes(), by=self.username, action="UNDO SHIFT")
            except DatabaseUnavailableError:
                raise
            except Exception as exc:
                warn(self, "Undo shift", f"The dates could not be put back: {exc}")
            self.load_data()
            toast(self, "Shift undone.", "info")

        toast(self, plan.message(root.name if root else ""), "success", action=("Undo", undo))

    def _timeline_dragged(self, milestone_id, start, end):
        """A bar dragged or stretched: the same preview as Shift dates, then save."""
        if not self.can_write:
            return
        select_keys(self.grid, [milestone_id])
        self.shift_dates(root_to=(start, end), milestone_id=milestone_id)
