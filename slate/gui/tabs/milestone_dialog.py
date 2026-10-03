"""
Add or edit one milestone.

The old "New Milestone" dialog checked nothing until it had closed - an empty
name or an end before the start lost everything typed - its Depends On list
kept the first project's milestones whatever project was picked (the
currentTextChanged signal passed an argument the slot did not take, so it
raised every time), and a dependency on a milestone without ISO dates quietly
set the earliest start to 1900. There was no way to edit or delete at all.

This dialog checks as you type with the same rules the repository enforces
(core/domain/scheduling.check_milestone), keeps Save off while something is
wrong and says what, and only closes once the save has worked.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox, QCompleter, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox, QLabel,
    QLineEdit, QVBoxLayout, QWidget,
)

from slate.core.domain import scheduling as DS
from slate.core.domain.dates import format_date, format_datetime, format_range
from slate.core.infra.db_results import DatabaseUnavailableError
from slate.core.infra.gate import Gate
from slate.gui.core.controls import form_layout, plain, style_button
from slate.gui.core.data_display import from_qdate, setup_date_edit, to_qdate

logger = logging.getLogger(__name__)


def _hint(tone: str = "dim") -> QLabel:
    label = QLabel("")
    label.setWordWrap(True)
    colour = {"dim": Gate.TEXT_DIM, "warn": Gate.WARN, "bad": Gate.BAD}.get(tone, Gate.TEXT_DIM)
    label.setStyleSheet(f"color: {colour}; font-size: {Gate.SIZE_SM}px;")
    label.hide()
    return label


def _set_hint(label: QLabel, text: str) -> None:
    label.setText(text)
    label.setVisible(bool(text))


def project_label(code: str, name: str) -> str:
    """'KLC – Kalki Chapter 2', or the code alone when the name adds nothing."""
    return f"{code} – {name}" if name and name.casefold() != code.casefold() else code


class MilestoneDialog(QDialog):
    """
    repo        a ScheduleRepository (does the saving)
    milestones  the schedule as it stands (every project, archived included)
    projects    [(code, name)] that a milestone may be added to
    milestone   the one being edited, or None to add
    """

    def __init__(self, parent=None, *, repo=None, milestones: Sequence[DS.Milestone] = (),
                 projects: Sequence[Tuple[str, str]] = (), milestone: DS.Milestone = None,
                 default_project: str = "", username: str = "", calendar: DS.WorkCalendar = None,
                 away: Sequence[DS.Away] = (), people_picker=None, read_only: bool = False):
        super().__init__(parent)
        self.read_only = read_only
        self.repo = repo
        self.username = username
        self.editing = milestone
        self.milestones = list(milestones)
        self.by_id = DS.index(self.milestones)
        self.calendar = calendar or DS.WorkCalendar()
        self.away = list(away)
        self.saved_id: Optional[int] = None
        self._start_before_dependency: Optional[QDate] = None

        title = "Milestone" if read_only else ("Edit milestone" if milestone else "Add milestone")
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        outer.setSpacing(Gate.SPACE_3)
        form = form_layout()
        outer.addLayout(form)

        # Project: shows 'CODE – Name', keeps the code as its data, so the
        # text can say more than the code without the code being re-parsed.
        self.proj_cb = QComboBox()
        self.proj_cb.setMinimumContentsLength(24)
        self.proj_cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        codes = []
        for code, name in projects:
            self.proj_cb.addItem(project_label(code, name), code)
            self.proj_cb.setItemData(self.proj_cb.count() - 1, project_label(code, name),
                                     Qt.ItemDataRole.ToolTipRole)
            codes.append(code)
        if milestone and milestone.project_code and milestone.project_code not in codes:
            # A milestone of an archived project can still be edited.
            self.proj_cb.addItem(project_label(milestone.project_code, milestone.project_name)
                                 + " (archived)", milestone.project_code)
        self.proj_cb.view().setMinimumWidth(360)
        form.addRow("Project", self.proj_cb)
        self.no_projects = _hint("warn")
        form.addRow("", self.no_projects)

        self.ms_input = QLineEdit()
        self.ms_input.setMaxLength(DS.MAX_NAME)
        self.ms_input.setPlaceholderText("e.g. Comp first pass")
        form.addRow("Milestone", self.ms_input)
        self.name_hint = _hint("bad")
        form.addRow("", self.name_hint)

        self.dep_cb = QComboBox()
        self.dep_cb.setEditable(True)
        self.dep_cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        completer = QCompleter(self.dep_cb.model(), self.dep_cb)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.dep_cb.setCompleter(completer)
        form.addRow("Depends on", self.dep_cb)
        self.dep_hint = _hint("dim")
        form.addRow("", self.dep_hint)

        today = QDate.currentDate()
        self.start_input = setup_date_edit(QDateEdit(today))
        self.end_input = setup_date_edit(QDateEdit(today.addDays(14)))
        form.addRow("Start", self.start_input)
        self.start_hint = _hint("warn")
        form.addRow("", self.start_hint)
        form.addRow("End", self.end_input)
        self.end_hint = _hint("dim")
        form.addRow("", self.end_hint)

        self.status_cb = QComboBox()
        for status in DS.STATUSES:
            self.status_cb.addItem(status, status)
        if milestone and milestone.status and milestone.status not in DS.STATUSES:
            self.status_cb.addItem(milestone.status, milestone.status)
        form.addRow("Status", self.status_cb)
        self.status_hint = _hint("warn")
        form.addRow("", self.status_hint)

        if people_picker is None:
            from slate.gui.components.person_picker import PersonPicker
            people_picker = PersonPicker(placeholder="Nobody yet - type a name…")
        self.owner_picker = people_picker
        form.addRow("Owner", self.owner_picker)
        self.owner_hint = _hint("warn")
        form.addRow("", self.owner_hint)

        self.dept_cb = QComboBox()
        self.dept_cb.addItem("—", "")
        try:
            from slate.core.domain.departments import load_departments
            for dept in load_departments():
                self.dept_cb.addItem(dept.name or dept.label or dept.key, dept.key)
        except Exception as exc:                     # a broken departments.json
            logger.warning("Departments could not be read: %s", exc)
        form.addRow("Department", self.dept_cb)

        self.effort_input = QDoubleSpinBox()
        self.effort_input.setRange(0, 9999)
        self.effort_input.setDecimals(1)
        self.effort_input.setSingleStep(0.5)
        self.effort_input.setSuffix(" days")
        self.effort_input.setSpecialValueText("Not set")
        self.effort_input.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.effort_input.setToolTip("Artist days of work - used to spot people booked "
                                     "for more than a day's work per day.")
        form.addRow("Effort", self.effort_input)

        self.error_label = _hint("bad")
        outer.addWidget(self.error_label)

        self.footer = QLabel("")
        self.footer.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")
        self.footer.setWordWrap(True)
        outer.addWidget(self.footer)
        outer.addStretch(1)

        self.buttons = QDialogButtonBox()
        self.save_button = self.buttons.addButton(
            "Save changes" if milestone else "Add milestone", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        style_button(self.save_button, "primary")
        style_button(cancel, "secondary")
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        if read_only:
            # Somebody without 'Edit the schedule' can still open a milestone
            # to read who added it, its effort and its old dates.
            for w in (self.proj_cb, self.ms_input, self.dep_cb, self.start_input, self.end_input,
                      self.status_cb, self.owner_picker, self.dept_cb, self.effort_input):
                w.setEnabled(False)
            self.save_button.hide()
            cancel.setText("Close")

        # Fill in, then wire, so nothing fires half way through.
        if milestone:
            self._load(milestone)
        else:
            index = self.proj_cb.findData(default_project) if default_project else -1
            self.proj_cb.setCurrentIndex(index if index >= 0 else (0 if self.proj_cb.count() else -1))
            self._fill_dependencies()

        self.proj_cb.currentIndexChanged.connect(self._project_changed)
        self.dep_cb.currentIndexChanged.connect(self._dependency_changed)
        self.dep_cb.lineEdit().editingFinished.connect(self._dependency_typed)
        for signal in (self.ms_input.textChanged, self.start_input.dateChanged,
                       self.end_input.dateChanged, self.status_cb.currentIndexChanged):
            signal.connect(self._revalidate)
        self.start_input.dateChanged.connect(self._start_moved)
        try:
            self.owner_picker.person_changed.connect(lambda *_: self._revalidate())
        except AttributeError:
            pass
        # Opening a milestone never moves it: a stored start that is before its
        # dependency's end is shown as a problem, not silently re-planned.
        self._dependency_changed(move=False)
        self._revalidate()
        self.ms_input.setFocus()

    # ------------------------------------------------------------ filling
    def _load(self, m: DS.Milestone) -> None:
        index = self.proj_cb.findData(m.project_code)
        self.proj_cb.setCurrentIndex(index)
        self.ms_input.setText(m.name)
        if m.start:
            self.start_input.setDate(to_qdate(m.start))
        if m.end:
            self.end_input.setDate(to_qdate(m.end))
        index = self.status_cb.findData(m.status)
        self.status_cb.setCurrentIndex(max(index, 0))
        if m.owner and not self.owner_picker.set_username(m.owner):
            # Somebody who has since left: keep them rather than clear the owner.
            self.owner_picker.addItem(m.owner, m.owner)
            self.owner_picker.set_username(m.owner)
        index = self.dept_cb.findData(m.department)
        if index < 0 and m.department:
            self.dept_cb.addItem(m.department, m.department)
            index = self.dept_cb.count() - 1
        self.dept_cb.setCurrentIndex(max(index, 0))
        self.effort_input.setValue(float(m.effort_days or 0))
        self._fill_dependencies(select=m.depends_on_id)
        bits = []
        if m.created_by or m.created_at:
            bits.append("Added" + (f" by {self._name(m.created_by)}" if m.created_by else "")
                        + (f" on {format_datetime(m.created_at)}" if m.created_at else ""))
        if m.updated_by and m.updated_at and m.updated_at != m.created_at:
            bits.append(f"last changed by {self._name(m.updated_by)} on "
                        f"{format_datetime(m.updated_at)}")
        if m.legacy_dates:
            bits.append(f"Before the upgrade: {m.legacy_dates}")
        self.footer.setText("; ".join(bits))
        self.footer.setVisible(bool(bits))

    @staticmethod
    def _name(username: str) -> str:
        try:
            from slate.core.domain.people import display_name
            return display_name(username) or username
        except Exception:
            return username

    def project_code(self) -> str:
        return str(self.proj_cb.currentData() or "")

    def _fill_dependencies(self, select=None) -> None:
        """
        The milestones of the chosen project: open ones first, in end-date
        order, with their end date; then the completed ones under a separator.
        """
        code = self.project_code().casefold()
        own_id = self.editing.id if self.editing else None
        candidates = [m for m in self.milestones
                      if m.project_code.casefold() == code and m.id != own_id]
        candidates.sort(key=lambda m: (not m.is_open, m.end or date.max, m.name.casefold()))
        self.dep_cb.blockSignals(True)
        self.dep_cb.clear()
        self.dep_cb.addItem("None", None)
        separator_added = False
        for m in candidates:
            if not m.is_open and not separator_added:
                self.dep_cb.insertSeparator(self.dep_cb.count())
                separator_added = True
            ends = f" — ends {format_date(m.end)}" if m.end else " — no end date"
            self.dep_cb.addItem(f"{m.name}{ends}", m.id)
        index = self.dep_cb.findData(select) if select else 0
        if select and index < 0:
            self.dep_cb.addItem(f"Missing (#{select})", select)
            index = self.dep_cb.count() - 1
        self.dep_cb.setCurrentIndex(max(index, 0))
        self.dep_cb.blockSignals(False)

    # ------------------------------------------------------------ reactions
    def _project_changed(self, *_):
        self._fill_dependencies()
        self._dependency_changed()
        self._revalidate()

    def _dependency_typed(self):
        """Typed text that names no milestone falls back to the list's choice."""
        index = self.dep_cb.findText(self.dep_cb.currentText(), Qt.MatchFlag.MatchFixedString)
        if index >= 0:
            self.dep_cb.setCurrentIndex(index)
        else:
            self.dep_cb.setEditText(self.dep_cb.itemText(self.dep_cb.currentIndex()))

    def dependency_id(self) -> Optional[int]:
        value = self.dep_cb.currentData()
        return int(value) if value not in (None, "") else None

    def _dependency_changed(self, *_, move: bool = True):
        """
        Nothing can start before what it waits on has finished: picking a
        dependency moves the start to the working day after its end, and says
        so. Clearing the dependency puts back the start that was there before.
        move=False (the dialog opening) only shows the hint.
        """
        parent = self.by_id.get(self.dependency_id()) if self.dependency_id() else None
        if parent is None:
            if self._start_before_dependency is not None:
                self.start_input.setDate(self._start_before_dependency)
                self._start_before_dependency = None
            _set_hint(self.dep_hint, "")
            self._revalidate()
            return
        if parent.end is None:
            _set_hint(self.dep_hint, f"\"{parent.name}\" has no end date yet, so it cannot "
                                     "hold this milestone back.")
            self._revalidate()
            return
        # No setMinimumDate: the date box clamped the start to the calendar day
        # after the dependency (a weekly off) before the working day was set.
        if move and from_qdate(self.start_input.date()) <= parent.end:
            if self._start_before_dependency is None:
                self._start_before_dependency = self.start_input.date()
            self.start_input.setDate(to_qdate(self.calendar.add_working_days(parent.end, 1)))
        _set_hint(self.dep_hint, f"Starts after \"{parent.name}\" ends ({format_date(parent.end)}).")
        self._revalidate()

    def _start_moved(self, value):
        if self.end_input.date() < value:
            self.end_input.setDate(value)

    def milestone(self) -> DS.Milestone:
        """The milestone as the form has it now."""
        base = self.editing
        effort = self.effort_input.value()
        return DS.Milestone(
            id=base.id if base else None,
            project_code=self.project_code(),
            name=" ".join(self.ms_input.text().split()),
            start=from_qdate(self.start_input.date()),
            end=from_qdate(self.end_input.date()),
            status=str(self.status_cb.currentData() or DS.SCHEDULED),
            depends_on_id=self.dependency_id(),
            owner=self.owner_picker.username() if hasattr(self.owner_picker, "username") else "",
            department=str(self.dept_cb.currentData() or ""),
            effort_days=Decimal(str(round(effort, 1))) if effort else None,
            project_name=base.project_name if base else "",
        )

    def _revalidate(self, *_):
        if not hasattr(self, "save_button"):
            return
        m = self.milestone()
        problems = DS.check_milestone(m, self.milestones)
        by_field = {}
        for p in problems:
            by_field.setdefault(p.field, str(p))

        _set_hint(self.no_projects, "" if self.proj_cb.count() else
                  "There are no active projects. Create one on the VFX Dashboard first.")
        # An empty name says it is needed, quietly, rather than leaving only a
        # tooltip on a disabled button.
        empty = not self.ms_input.text().strip()
        _set_hint(self.name_hint, "Required." if empty else by_field.get("name", ""))
        self.name_hint.setStyleSheet(f"color: {Gate.TEXT_DIM if empty else Gate.BAD}; "
                                     f"font-size: {Gate.SIZE_SM}px;")
        _set_hint(self.status_hint, self._completion_warning(m))
        start_notes = [by_field.get("start", "")]
        why = self.calendar.why_not_working(m.start)
        if why:
            start_notes.append(f"Starts on a non-working day: {why}.")
        _set_hint(self.start_hint, " ".join(x for x in start_notes if x))
        end_notes = []
        if "end" in by_field:
            end_notes.append(by_field["end"])
        elif m.start and m.end:
            end_notes.append(DS.plural(self.calendar.working_days(m.start, m.end), "working day"))
        why = self.calendar.why_not_working(m.end)
        if why:
            end_notes.append(f"Ends on a non-working day: {why}.")
        _set_hint(self.end_hint, " · ".join(end_notes))
        self.end_hint.setStyleSheet(
            f"color: {Gate.BAD if 'end' in by_field else (Gate.WARN if why else Gate.TEXT_DIM)};"
            f" font-size: {Gate.SIZE_SM}px;")

        # Assigning somebody over their approved leave is allowed - sometimes
        # leave moves - but it should be a decision, not a surprise.
        overlap = DS.leave_overlap(m.owner, m.start, m.end, self.away)
        _set_hint(self.owner_hint, "" if not overlap else
                  "Approved leave in these dates: " + ", ".join(
                      format_range(a.start, a.end) for a in overlap[:3]))
        owner_ok = getattr(self.owner_picker, "is_valid", lambda: True)()
        other = [str(p) for p in problems if p.field in ("project", "dates", "depends")]
        if not owner_ok:
            other.append("The owner must be somebody from the list (or nobody).")
        _set_hint(self.error_label, " ".join(other) if (other and self.proj_cb.count()) else "")
        self.save_button.setEnabled(not problems and owner_ok and bool(self.proj_cb.count()))
        if problems:
            self.save_button.setToolTip(str(problems[0]))
        else:
            self.save_button.setToolTip("")

    def _completion_warning(self, m: DS.Milestone) -> str:
        """The Change status question, for Completed set here."""
        if m.status != DS.COMPLETED or (self.editing and self.editing.status == DS.COMPLETED):
            return ""
        return DS.completion_warning(m, self.by_id)

    # ------------------------------------------------------------ saving
    def _save(self):
        """Save through the repository; the dialog stays open if it is refused."""
        m = self.milestone()
        warning = self._completion_warning(m)
        if warning:
            from slate.gui.components.feedback import confirm
            if not confirm(self, "Mark completed", warning, yes_label="Mark completed anyway",
                           informative="Complete it anyway?"):
                return
        if self.repo is None:
            self.accept()
            return
        try:
            if self.editing:
                self.repo.edit(m, by=self.username)
                self.saved_id = m.id
            else:
                self.saved_id = self.repo.add(m, by=self.username)
        except DatabaseUnavailableError:
            _set_hint(self.error_label, "Can't reach the studio database, so nothing was saved. "
                                        "Try again when it is back.")
            return
        except (DS.ScheduleError, PermissionError) as exc:
            _set_hint(self.error_label, str(exc))
            return
        except Exception as exc:
            logger.exception("Milestone not saved")
            _set_hint(self.error_label, f"The milestone was not saved: {exc}")
            return
        self.saved = m
        self.accept()
