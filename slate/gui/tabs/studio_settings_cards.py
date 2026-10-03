"""
The studio-wide parts of Settings: the attendance and leave policy, and the
money and working hours.

Both used to be (or were never) per machine. "Late after" and "Standard day"
were saved into this workstation's own config file, so every machine could
hold a different studio policy; the rest of the policy - weekly offs, accrual,
carry-forward cap, the sandwich rule, comp-off - had no screen at all, while
the Leave tab told HR to "turn it on in the studio policy". Both editors here
read and write the studio_settings table (slate/core/infra/studio_settings.py),
so every workstation sees the same values, and they say who changed them last.

Only the people who own a setting can change it: the policy belongs to HR (and
admins); the currency, day rates and GST to whoever approves bids
(approve_bid); the working hours and licence warning to studio_settings
holders (admin, developer, IT). Everyone else sees the policy as a plain
summary, with a line saying who may change it.

The studio's working week is one value: the policy's weekly offs. The money
card used to have its own 'Working days' too, and the two could disagree.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QTime, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QSpinBox, QTimeEdit, QVBoxLayout, QWidget,
)

from slate.core.domain import leave_policy as lp
from slate.core.domain import money
from slate.core.domain.dates import WEEKDAYS, format_datetime
from slate.core.infra.db_results import DatabaseUnavailableError
from slate.core.infra.design_tokens import ColorTokens as C
from slate.core.infra.gate import Gate
from ..core.controls import make_button

logger = logging.getLogger(__name__)

ADMIN_ROLES = {"admin", "developer"}


def _roles_of(widget):
    """The signed-in person's roles, from the main window. None when unknown."""
    window = widget.window() if widget is not None else None
    roles = getattr(window, "user_roles", None)
    if roles is None:
        data = getattr(window, "user_data", None) or {}
        roles = data.get("roles", data.get("role"))
    if roles is None:
        return None
    if isinstance(roles, str):
        roles = [roles]
    return [str(r).strip().lower() for r in roles if str(r).strip()]


def _username_of(widget) -> str:
    window = widget.window() if widget is not None else None
    data = getattr(window, "user_data", None) or {}
    return str(getattr(window, "username", "") or data.get("username") or "").strip()


FIELD_WIDTH = 170
# One label column for both studio cards, so their values line up.
LABEL_WIDTH = 210


def _fixed(widget):
    """A value box of the width every other one on these cards has."""
    widget.setFixedWidth(FIELD_WIDTH)
    return widget


def _form() -> QFormLayout:
    form = QFormLayout()
    form.setSpacing(8)
    # Inputs at their natural width, not stretched across the card.
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    return form


def _align_labels(form: QFormLayout) -> None:
    for row in range(form.rowCount()):
        item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        if item is not None and item.widget() is not None:
            item.widget().setMinimumWidth(LABEL_WIDTH)


def number_text(value: float) -> str:
    """12.0 -> '12', 0.25 -> '0.25', 1.5 -> '1.5' (spin boxes showed '12.00')."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


class _Number(QDoubleSpinBox):
    """A decimal box without the trailing zeros."""

    def textFromValue(self, value):
        return number_text(value)


class _Money(QDoubleSpinBox):
    """An amount in the currency's own grouping: '₹ 1,50,000', '$ 300'."""

    def __init__(self, code, parent=None):
        super().__init__(parent)
        self.code = code
        self.setDecimals(0)

    def textFromValue(self, value):
        return money.format_money(value, self.code, symbol=False, decimals=0)

    def valueFromText(self, text):
        try:
            return float(str(text).replace(",", "").replace(self.prefix().strip(), "").strip() or 0)
        except ValueError:
            return 0.0

    def validate(self, text, pos):
        from PySide6.QtGui import QValidator
        body = str(text).replace(self.prefix(), "").replace(",", "").strip()
        return (QValidator.State.Acceptable if body.isdigit() or body in ("", self.specialValueText())
                else QValidator.State.Intermediate), text, pos


def _plural_days(value: float) -> str:
    return "day" if value == 1 else "days"


class _StudioEditor(QWidget):
    """Common parts: who may edit, the note line, the last-changed line."""

    # Any field was edited - Settings shows "Unsaved changes" from this.
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checked_roles = False
        self._editable = True
        self._baseline = None
        self._watching = False
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(10)
        self.lbl_who = QLabel("")
        self.lbl_who.setWordWrap(True)
        self.lbl_who.setStyleSheet(f"font-size: 11px; color: {C.TEXT_GRAY_LIGHTER};")

    def may_edit(self, roles) -> bool:
        raise NotImplementedError

    def showEvent(self, event):
        super().showEvent(event)
        if not self._checked_roles:
            self._checked_roles = True
            roles = _roles_of(self)
            # Unknown roles (the widget shown on its own, in a test) leave it
            # editable; a signed-in person without the right sees it read-only.
            self.set_editable(True if roles is None else self.may_edit(roles))
            self.load()
            self._watch_inputs()

    def _watch_inputs(self):
        """Every field reports an edit, so the page can say something is unsaved."""
        if self._watching:
            return
        self._watching = True
        for child in self.findChildren(QWidget):
            if isinstance(child, QTimeEdit):
                child.timeChanged.connect(self._on_input)
            elif isinstance(child, QAbstractSpinBox) and hasattr(child, "valueChanged"):
                child.valueChanged.connect(self._on_input)
            elif isinstance(child, QCheckBox):
                child.toggled.connect(self._on_input)
            elif isinstance(child, QComboBox):
                child.currentIndexChanged.connect(self._on_input)
            elif isinstance(child, QLineEdit) and not isinstance(child.parent(), QAbstractSpinBox):
                child.textChanged.connect(self._on_input)

    def _on_input(self, *_):
        self.changed.emit()

    def _mark_clean(self):
        """The values on screen are the saved ones."""
        try:
            self._baseline = self.values()
        except Exception:
            self._baseline = None
        self.changed.emit()

    def is_dirty(self) -> bool:
        if self._baseline is None or not self._editable:
            return False
        try:
            return self.values() != self._baseline
        except Exception:
            return False

    def set_editable(self, editable: bool) -> None:
        self._editable = bool(editable)
        for child in self.findChildren(QWidget):
            if child is self.lbl_who or isinstance(child, QLabel):
                continue
            child.setEnabled(self._editable)

    def _show_meta(self, store, key: str) -> None:
        try:
            meta = store.meta(key)
        except Exception:
            meta = {}
        if meta.get("updated_by") or meta.get("updated_at"):
            from slate.core.domain.people import display_name
            # The person's name, as everywhere else (it showed the login).
            self.lbl_who.setText("Last changed by %s, %s. The same for every workstation."
                                 % (display_name(meta.get("updated_by"), empty="someone"),
                                    format_datetime(meta.get("updated_at"))))
        else:
            self.lbl_who.setText("Studio defaults - nobody has changed these yet. "
                                 "The same for every workstation.")


class StudioPolicyEditor(_StudioEditor):
    """Every studio_policy rule: the day, the week, leave accrual and comp-off."""

    # After a successful save, so Settings can recount an open Attendance tab.
    saved = Signal()

    # A standard day outside these hours is a typing mistake, not a policy;
    # a late cut-off outside these times is asked about.
    DAY_HOURS = (4.0, 12.0)
    USUAL_CUTOFF = (QTime(7, 0), QTime(13, 0))
    # A forgotten day is closed at least this long after the late cut-off;
    # earlier would close days before they had begun.
    MIN_FORGOTTEN_DAY_HOURS = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        note = QLabel(
            "When somebody counts as late, how long a day is, which days are off and "
            "how leave and comp-off are earned. Saved for the whole studio: Attendance "
            "and Leave on every workstation use these.")
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: 11px; color: {C.TEXT_GRAY_LIGHTER};")
        self.root.addWidget(note)

        # The form for people who may change it; a plain summary for everyone
        # else (a disabled form with spin arrows read as broken).
        self.form_box = QWidget()
        form = _form()
        self.form_box.setLayout(form)
        form.setContentsMargins(0, 0, 0, 0)
        self.lbl_summary = QLabel("")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.hide()

        self.late_cutoff = _fixed(QTimeEdit())
        self.late_cutoff.setDisplayFormat("HH:mm")
        form.addRow("Late after", self.late_cutoff)

        self.standard_day = _fixed(_Number())
        self.standard_day.setRange(*self.DAY_HOURS)
        self.standard_day.setSingleStep(0.5)
        self.standard_day.setSuffix(" hours")
        form.addRow("Standard day", self.standard_day)

        self.auto_logout = _fixed(QTimeEdit())
        self.auto_logout.setDisplayFormat("HH:mm")
        self.auto_logout.setToolTip(
            "A day somebody forgot to punch out of is closed at this time "
            "when they next punch in.")
        self.lbl_forgotten = QLabel("")
        self.lbl_forgotten.setStyleSheet(f"font-size: 11px; color: {C.TEXT_GRAY_LIGHTER};")
        auto_row = QHBoxLayout()
        auto_row.addWidget(self.auto_logout)
        auto_row.addWidget(self.lbl_forgotten)
        auto_row.addStretch(1)
        form.addRow("Forgotten punch-out closes at", auto_row)
        self.auto_logout.timeChanged.connect(self._show_forgotten_day)
        self.late_cutoff.timeChanged.connect(self._show_forgotten_day)

        offs = QHBoxLayout()
        self.weekly_offs = []
        for index, name in enumerate(WEEKDAYS):
            box = QCheckBox(name)
            box.setProperty("weekday", index)
            self.weekly_offs.append(box)
            offs.addWidget(box)
        offs.addStretch(1)
        # The studio's one working week: Attendance, Leave and IT's response
        # clocks all count from it.
        form.addRow("Weekly off", offs)

        self.accrual = _fixed(_Number())
        self.accrual.setRange(0, 10)
        self.accrual.setSingleStep(0.25)
        self.accrual.setSuffix(" days a month")
        form.addRow("Leave earned", self.accrual)

        self.carry_cap = _fixed(_Number())
        self.carry_cap.setRange(0, 365)
        self.carry_cap.setSuffix(" days")
        form.addRow("Carry into next year, at most", self.carry_cap)

        self.sandwich = QCheckBox("A lone working day between days off, taken as leave, "
                                  "also costs the days either side")
        form.addRow("Sandwich rule", self.sandwich)

        self.comp_off = QCheckBox("Working a weekly off or a holiday earns comp-off")
        form.addRow("Comp-off", self.comp_off)

        comp = QGridLayout()
        comp.setHorizontalSpacing(8)
        comp.setColumnStretch(2, 1)
        self.comp_weekly = self._days_box(comp, 0, "For a weekly off worked")
        self.comp_holiday = self._days_box(comp, 1, "For a holiday worked")
        self.comp_half = self._hours_box(comp, 2, "Half a day for working at least")
        self.comp_full = self._hours_box(comp, 3, "A whole day for working at least")
        self.comp_expiry = _fixed(QSpinBox())
        self.comp_expiry.setRange(0, 3650)
        self.comp_expiry.setSuffix(" days")
        comp.addWidget(QLabel("Unused comp-off lapses after"), 4, 0)
        comp.addWidget(self.comp_expiry, 4, 1)
        form.addRow("", comp)
        self.comp_off.toggled.connect(self._comp_sync)

        self.project_rest = QCheckBox("HR may grant project rest at the end of a project")
        form.addRow("Project rest", self.project_rest)
        _align_labels(form)

        self.root.addWidget(self.form_box)
        self.root.addWidget(self.lbl_summary)
        self.root.addWidget(self.lbl_who)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_save = make_button("Save policy", "primary", on_click=self.save)
        row.addWidget(self.btn_save)
        self.root.addLayout(row)

    @staticmethod
    def _days_box(grid, row, label):
        box = _fixed(_Number())
        box.setRange(0, 5)
        box.setSingleStep(0.5)
        box.setSuffix(" days")
        grid.addWidget(QLabel(label), row, 0)
        grid.addWidget(box, row, 1)
        return box

    @staticmethod
    def _hours_box(grid, row, label):
        box = _fixed(_Number())
        box.setRange(0, 24)
        box.setSingleStep(0.5)
        box.setSuffix(" hours")
        grid.addWidget(QLabel(label), row, 0)
        grid.addWidget(box, row, 1)
        return box

    def _comp_sync(self, *_):
        on = self.comp_off.isChecked() and self._editable
        for box in (self.comp_weekly, self.comp_holiday, self.comp_half,
                    self.comp_full, self.comp_expiry):
            box.setEnabled(on)

    def may_edit(self, roles) -> bool:
        from slate.core.domain.access import can
        return bool(set(roles) & ADMIN_ROLES) or can(roles, "manage_leave")

    def set_editable(self, editable: bool) -> None:
        super().set_editable(editable)
        self.form_box.setVisible(bool(editable))
        self.lbl_summary.setVisible(not editable)
        if not editable:
            self.lbl_who.setText("Only HR and admins can change the studio policy.")
        self._comp_sync()

    def forgotten_day_hours(self) -> float:
        """How long a forgotten day counts: from the late cut-off to the auto punch-out."""
        return self.late_cutoff.time().secsTo(self.auto_logout.time()) / 3600.0

    def _show_forgotten_day(self, *_):
        hours = self.forgotten_day_hours()
        if hours <= 0:
            self.lbl_forgotten.setText("before the day starts")
            self.lbl_forgotten.setStyleSheet(f"font-size: 11px; color: {Gate.BAD};")
            return
        whole, minutes = divmod(int(round(hours * 60)), 60)
        self.lbl_forgotten.setText(f"a forgotten day counts {whole} h" + (f" {minutes} min" if minutes else "")
                                   + f" from {self.late_cutoff.time().toString('HH:mm')}")
        self.lbl_forgotten.setStyleSheet(f"font-size: 11px; color: {C.TEXT_GRAY_LIGHTER};")

    def summary_text(self) -> str:
        """The policy as plain lines, for the people who cannot change it."""
        from slate.core.domain.dates import WEEKDAYS as names
        v = self.values()
        offs = ", ".join(names[d] for d in v["weekly_offs"]) or "none"
        lines = [
            f"Late after: {v['late_cutoff']}",
            f"Standard day: {number_text(v['standard_day_hours'])} hours",
            f"Forgotten punch-out closes at: {v['auto_logout_time']}",
            f"Weekly off: {offs}",
            f"Leave earned: {number_text(v['accrual_days_per_month'])} days a month",
            f"Carried into next year, at most: {number_text(v['carry_forward_cap'])} days",
            f"Sandwich rule: {'on' if v['sandwich_rule'] else 'off'}",
        ]
        if v["comp_off_enabled"]:
            lines.append(
                f"Comp-off: {number_text(v['comp_off_for_weekly_off'])} "
                f"{_plural_days(v['comp_off_for_weekly_off'])} for a weekly off worked, "
                f"{number_text(v['comp_off_for_holiday'])} {_plural_days(v['comp_off_for_holiday'])} "
                f"for a holiday; lapses after {v['comp_off_expiry_days']} days")
        else:
            lines.append("Comp-off: off")
        lines.append(f"Project rest: {'HR may grant it' if v['project_rest_enabled'] else 'off'}")
        return "\n".join(lines)

    def load(self) -> None:
        """Show the policy in force (the studio's values over the defaults)."""
        try:
            from slate.core.infra import studio_policy
            from slate.core.infra.studio_settings import StudioSettings
            saved = studio_policy.studio_rules()
            store = StudioSettings()
        except DatabaseUnavailableError:
            saved, store = lp.overrides(), None
            self.lbl_who.setText("The database is not reachable - showing this "
                                 "workstation's last known policy. It cannot be saved now.")
            self.btn_save.setEnabled(False)
        rules = lp.policy(saved)
        hour, minute = lp.late_cutoff(rules)
        self.late_cutoff.setTime(QTime(hour, minute))
        self.standard_day.setValue(lp.standard_day_hours(rules))
        auto = str(rules.get("auto_logout_time") or "19:30").split(":")
        self.auto_logout.setTime(QTime(int(auto[0]), int(auto[1]) if len(auto) > 1 else 0))
        offs = set(rules.get("weekly_offs") or [])
        for box in self.weekly_offs:
            box.setChecked(box.property("weekday") in offs)
        self.accrual.setValue(float(rules.get("accrual_days_per_month") or 0))
        self.carry_cap.setValue(float(rules.get("carry_forward_cap") or 0))
        self.sandwich.setChecked(bool(rules.get("sandwich_rule")))
        self.comp_off.setChecked(bool(rules.get("comp_off_enabled")))
        self.comp_weekly.setValue(float(rules.get("comp_off_for_weekly_off") or 0))
        self.comp_holiday.setValue(float(rules.get("comp_off_for_holiday") or 0))
        self.comp_half.setValue(float(rules.get("comp_off_hours_half") or 0))
        self.comp_full.setValue(float(rules.get("comp_off_hours_full") or 0))
        self.comp_expiry.setValue(int(rules.get("comp_off_expiry_days") or 0))
        self.project_rest.setChecked(bool(rules.get("project_rest_enabled")))
        self._comp_sync()
        self._show_forgotten_day()
        self.lbl_summary.setText(self.summary_text())
        if store is not None and self._editable:
            self._show_meta(store, "attendance_policy")
        self._mark_clean()

    def values(self) -> dict:
        return {
            "late_cutoff": self.late_cutoff.time().toString("HH:mm"),
            "standard_day_hours": float(self.standard_day.value()),
            "auto_logout_time": self.auto_logout.time().toString("HH:mm"),
            "weekly_offs": [b.property("weekday") for b in self.weekly_offs if b.isChecked()],
            "accrual_days_per_month": float(self.accrual.value()),
            "carry_forward_cap": float(self.carry_cap.value()),
            "sandwich_rule": self.sandwich.isChecked(),
            "comp_off_enabled": self.comp_off.isChecked(),
            "comp_off_for_weekly_off": float(self.comp_weekly.value()),
            "comp_off_for_holiday": float(self.comp_holiday.value()),
            "comp_off_hours_half": float(self.comp_half.value()),
            "comp_off_hours_full": float(self.comp_full.value()),
            "comp_off_expiry_days": int(self.comp_expiry.value()),
            "project_rest_enabled": self.project_rest.isChecked(),
        }

    def save(self, quiet: bool = False) -> bool:
        """
        Save for the whole studio and apply it here at once - no restart, and
        no "re-open the tab": somebody changing the start time is usually
        looking at a grid they believe is wrong. quiet: the Settings page says
        what was saved, in one message.
        """
        if not self._editable:
            return False
        values = self.values()
        if len(values["weekly_offs"]) >= 7:
            QMessageBox.warning(self, "Not saved", "At least one day of the week has to be a working day.")
            return False
        cutoff = self.late_cutoff.time()
        low, high = self.USUAL_CUTOFF
        if (cutoff < low or cutoff > high) and QMessageBox.question(
                self, "Save policy",
                "People will count as late only after %s, which is unusual for a start time. "
                "Save it anyway?" % values["late_cutoff"],
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return False
        forgotten = self.forgotten_day_hours()
        if forgotten < self.MIN_FORGOTTEN_DAY_HOURS:
            self.auto_logout.setFocus()
            when = ("no later than the late cut-off" if forgotten <= 0
                    else f"only {number_text(forgotten)} hours after the late cut-off")
            QMessageBox.warning(
                self, "Not saved",
                f"A forgotten punch-out would be closed at {values['auto_logout_time']}, {when} "
                f"({values['late_cutoff']}), so a forgotten day would count next to nothing. "
                f"Choose a time at least {self.MIN_FORGOTTEN_DAY_HOURS} hours after the cut-off.")
            return False
        from slate.core.infra import studio_policy
        try:
            result = studio_policy.save_rules(values, by=_username_of(self) or "Settings")
        except DatabaseUnavailableError as exc:
            QMessageBox.warning(self, "Not saved", str(exc))
            return False
        if not result:
            QMessageBox.warning(self, "Not saved", "The studio policy was not saved:\n\n%s"
                                % (result.error or "the database refused it"))
            return False
        self.load()
        if not quiet:
            QMessageBox.information(
                self, "Save policy",
                "Saved. Attendance and Leave on every workstation use the new policy from now on.")
        self.saved.emit()
        return True


def _renewal_default() -> int:
    """The licence renewal window's default; importing the module registers the key."""
    try:
        from slate.core.domain import licence_compliance
        return int(licence_compliance.RENEWAL_SOON_DAYS)
    except Exception:
        return 45


class StudioMoneyEditor(_StudioEditor):
    """The studio's currency, day rates, GST and working hours."""

    # Who may change which part (abilities, so a custom role holding one counts).
    MONEY_ABILITY = "approve_bid"
    HOURS_ABILITY = "studio_settings"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.can_money = self.can_hours = True
        note = QLabel(
            "What bids are written in unless a client needs another currency, the artist "
            "day rate per currency, GST on rupee bids, the studio's working hours "
            "(IT's response clocks count these, on the working days of the studio policy) "
            "and how early a licence renewal is flagged. Saved for the whole studio.")
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: 11px; color: {C.TEXT_GRAY_LIGHTER};")
        self.root.addWidget(note)

        form = _form()

        self.currency = QComboBox()
        for code, cur in money.CURRENCIES.items():
            self.currency.addItem(f"{cur.symbol}  {code} - {cur.name}", code)
        form.addRow("Studio currency", self.currency)

        self.rates = {}
        for code, cur in money.CURRENCIES.items():
            # Grouped as the currency is ('₹ 1,50,000'), whole amounts only.
            box = _fixed(_Money(code))
            box.setRange(0, 10000000)
            box.setPrefix(cur.symbol + " ")
            box.setSpecialValueText("not set")
            box.setToolTip("0 means no rate: bids in %s have to type one." % code)
            self.rates[code] = box
            form.addRow(f"Day rate, {code}", box)

        self.gst = _fixed(_Number())
        self.gst.setRange(0, 100)
        self.gst.setSuffix(" %")
        self.gst.setToolTip("Added to new rupee bids; editable on each bid. "
                            "Foreign-currency bids start with no tax.")
        form.addRow("GST on rupee bids", self.gst)

        hours = QHBoxLayout()
        self.day_start = _fixed(QTimeEdit())
        self.day_start.setDisplayFormat("HH:mm")
        self.day_end = _fixed(QTimeEdit())
        self.day_end.setDisplayFormat("HH:mm")
        hours.addWidget(self.day_start)
        hours.addWidget(QLabel("to"))
        hours.addWidget(self.day_end)
        hours.addStretch(1)
        form.addRow("Working hours", hours)
        # No 'Working days' here: the week is the studio policy's weekly offs.

        # How many days before a licence expires it counts as "renewal due"
        # (the Licences screen, its reminders and the Home figure).
        self.renewal_days = _fixed(QSpinBox())
        self.renewal_days.setRange(1, 365)
        self.renewal_days.setValue(_renewal_default())      # also registers the key
        self.renewal_days.setSuffix(" days before expiry")
        self.renewal_days.setToolTip("Licences inside this window show as due for renewal, and "
                                     "IT is reminded.")
        form.addRow("Licence renewal warning", self.renewal_days)
        _align_labels(form)
        self.form = form
        self.money_fields = [self.currency, self.gst] + list(self.rates.values())
        self.hours_fields = [self.day_start, self.day_end, self.renewal_days]

        self.root.addLayout(form)
        self.root.addWidget(self.lbl_who)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_save = make_button("Save", "primary", on_click=self.save)
        row.addWidget(self.btn_save)
        self.root.addLayout(row)

    def may_edit(self, roles) -> bool:
        """
        By ability, not role name: the money part for whoever approves bids,
        the hours for studio_settings holders (IT owns the SLA hours, and was
        shown this card disabled).
        """
        from slate.core.domain.access import can
        self.can_money = can(roles, self.MONEY_ABILITY)
        self.can_hours = can(roles, self.HOURS_ABILITY)
        return self.can_money or self.can_hours

    def set_editable(self, editable: bool) -> None:
        super().set_editable(editable)
        if editable:
            for field in self.money_fields:
                field.setEnabled(self.can_money)
            for field in self.hours_fields:
                field.setEnabled(self.can_hours)
            if not (self.can_money and self.can_hours):
                self.lbl_who.setText("You can change the " + (
                    "currency, day rates and GST" if self.can_money else "working hours and licence warning")
                    + "; the rest belongs to " + ("admins and IT." if self.can_money else
                                                  "the people who approve bids."))
        else:
            self.lbl_who.setText("Only the people who approve bids (currency and rates) and studio "
                                 "admins and IT (hours) can change these.")

    def load(self) -> None:
        from slate.core.infra.studio_settings import StudioSettings
        try:
            store = StudioSettings()
            values = store.all()
        except DatabaseUnavailableError:
            self.lbl_who.setText("The database is not reachable, so these cannot be shown or saved now.")
            self.btn_save.setEnabled(False)
            return
        index = self.currency.findData(money.normalise_code(values.get("currency"), "INR"))
        self.currency.setCurrentIndex(max(index, 0))
        rates = values.get("day_rates") or {}
        for code, box in self.rates.items():
            try:
                box.setValue(float(money.to_decimal(rates.get(code))) if rates.get(code) else 0.0)
            except ValueError:
                box.setValue(0.0)
        self.gst.setValue(float(values.get("gst_rate") or 0))
        hours = values.get("working_hours") or {}
        self.day_start.setTime(QTime.fromString(hours.get("start", "10:00"), "HH:mm"))
        self.day_end.setTime(QTime.fromString(hours.get("end", "19:00"), "HH:mm"))
        # No working days here: the week is the studio policy's weekly offs
        # (the SLA reads them there). A stored 'days' is dropped on the next save.
        try:
            renewal = int(values.get("licence_renewal_days") or _renewal_default())
        except (TypeError, ValueError):
            renewal = _renewal_default()
        self.renewal_days.setValue(min(max(renewal, 1), 365))
        if self._editable and self.can_money and self.can_hours:
            self._show_meta(store, "day_rates")
        self._mark_clean()

    def values(self) -> dict:
        return {
            "currency": self.currency.currentData(),
            "day_rates": {code: box.value() for code, box in self.rates.items() if box.value() > 0},
            "gst_rate": float(self.gst.value()),
            "working_hours": {
                "start": self.day_start.time().toString("HH:mm"),
                "end": self.day_end.time().toString("HH:mm"),
            },
            "licence_renewal_days": int(self.renewal_days.value()),
        }

    def _labelled(self):
        """setting key -> (the label people read, the field to point at)."""
        return {"currency": ("Studio currency", self.currency),
                "day_rates": ("Day rates", next(iter(self.rates.values()))),
                "gst_rate": ("GST on rupee bids", self.gst),
                "working_hours": ("Working hours", self.day_start),
                "licence_renewal_days": ("Licence renewal warning", self.renewal_days)}

    def _refuse(self, key, reason) -> bool:
        label, field = self._labelled().get(key, (key, None))
        if field is not None:
            field.setStyleSheet(f"border: 1px solid {Gate.BAD};")
            field.setFocus()
        QMessageBox.warning(self, "Not saved", f"Nothing was saved.\n\n{label}: {reason}")
        return False

    def save(self, quiet: bool = False) -> bool:
        if not self._editable:
            return False
        from slate.core.infra.studio_settings import StudioSettings, VALIDATORS
        for _label, field in self._labelled().values():
            field.setStyleSheet("")
        values = self.values()
        # Each value checked here first, so a refusal names its field (it
        # read 'working_hours: ...') and the field is outlined.
        for key, value in values.items():
            check = VALIDATORS.get(key)
            try:
                if check:
                    check(value)
            except (TypeError, ValueError, KeyError) as exc:
                return self._refuse(key, str(exc) or "that value is not allowed.")
        try:
            result = StudioSettings().set_many(values, by=_username_of(self) or "Settings")
        except DatabaseUnavailableError as exc:
            QMessageBox.warning(self, "Not saved", str(exc))
            return False
        if not result:
            QMessageBox.warning(self, "Not saved", "Nothing was saved:\n\n%s"
                                % (result.error or "the database refused it"))
            return False
        self.load()
        if not quiet:
            QMessageBox.information(self, "Saved", "Saved for the whole studio.")
        return True
