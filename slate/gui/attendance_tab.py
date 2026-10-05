"""
Attendance: punch in and out, see your month, and - for HR and for supervisors
with a team - the month of everybody you look after.

What a day means (present, late, missing punch-out, on leave, absent...) is
decided once, in slate.core.domain.attendance_rules, and used by the hero
card, both tables, the legend and the Excel export alike. Four copies of the
lateness rule had given one person three different late counts on one
screen.

Who sees what:
    everybody               their own punches and month
    view_team_attendance    the whole studio's grid; may correct punches,
    (HR, admin)             export, import biometric punches, keep holidays
    approve_leave           the grid for the people who report to them,
    (supervisors, leads)    read-only (studio decision)

Closing Slate or signing out never punches anybody out. Punching in again
after punching out starts a second session the same day.
"""

import calendar
import logging
from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt, QTime, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHeaderView,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QSizePolicy, QSpinBox,
    QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from ..core.infra.app_context import AppContext
from .attendance_export_worker import ExcelExportWorker
from .attendance_metrics import calculate_hours as compute_hours
from slate.core.domain import attendance_rules as rules
from slate.core.domain import leave_policy as lp
from slate.core.domain import people
from slate.core.domain.people import plural
# At module level: setup_ui asks can(...) for the Holidays button, and the
# import used to live inside is_admin() only - so opening the tab as anyone
# died with "name 'can' is not defined" before a single widget was drawn.
from slate.core.domain.access import can
from slate.core.domain.dates import format_date
from slate.gui.core.offline_notice import on_database_error
from slate.gui.core.controls import make_button, style_button
from slate.gui.core.table_style import style_table
from slate.core.infra.gate import Gate


# ------------------------------------------------------------------ colours

def state_styles() -> dict:
    """
    One colour and one label per day state, used by the legend, the personal
    table and the team grid. Built when asked so it follows the theme.

    The legend used to say green for a punch and amber for late while the
    personal table painted late rows amber and overtime rows green, and
    "Auto" and "Working" shared a colour so could not be told apart.
    """
    base = Gate.RAISED
    off = Gate.mix(base, Gate.GROUND, 0.75)
    return {
        rules.PRESENT: (Gate.mix(base, Gate.OK, 0.22), "Present"),
        rules.WORKED_OFF: (Gate.mix(base, Gate.OK, 0.22), "Present"),
        rules.LATE: (Gate.mix(base, Gate.WARN, 0.30), "Late"),
        rules.SHORT: (Gate.mix(base, Gate.WARN, 0.14), "Short day"),
        rules.WORKING: (Gate.mix(base, Gate.ACCENT, 0.30), "Working now"),
        rules.AUTO: (Gate.mix(base, Gate.INFO, 0.30), "Auto punch-out"),
        rules.MISSING_OUT: (Gate.mix(base, Gate.BAD, 0.38), "Missing punch"),
        rules.MISSING_IN: (Gate.mix(base, Gate.BAD, 0.38), "Missing punch"),
        rules.LEAVE: (Gate.mix(base, Gate.IDLE, 0.40), "On leave"),
        rules.ABSENT: (Gate.mix(base, Gate.BAD, 0.14), "Absent"),
        rules.HOLIDAY: (off, "Holiday / weekly off"),
        rules.WEEKLY_OFF: (off, "Holiday / weekly off"),
        rules.FUTURE: (base, ""),
        rules.NONE: (base, ""),
    }


def legend_entries() -> list:
    """(colour, label) once per label, in the order a person reads a day."""
    seen, out = set(), []
    order = (rules.PRESENT, rules.LATE, rules.SHORT, rules.WORKING, rules.AUTO,
             rules.MISSING_OUT, rules.LEAVE, rules.ABSENT, rules.HOLIDAY)
    styles = state_styles()
    for state in order:
        colour, label = styles[state]
        if label and label not in seen:
            seen.add(label)
            out.append((colour, label))
    return out


def legend_tone(label: str) -> str:
    """The state's own colour, to ring its legend swatch (the cell tints are faint in Dark)."""
    return {
        "Present": Gate.OK, "Late": Gate.WARN, "Short day": Gate.WARN, "Working now": Gate.ACCENT,
        "Auto punch-out": Gate.INFO, "Missing punch": Gate.BAD, "On leave": Gate.IDLE,
        "Absent": Gate.BAD, "Holiday / weekly off": Gate.TEXT_DIM,
    }.get(label, Gate.LINE)


class DayHeader(QHeaderView):
    """
    The team grid's day header, with today and the days off marked.

    The themed header stylesheet draws every section the same and ignores
    item colours, so today's column and the holidays looked like any other
    day. marks: {section: "today" | "off"}.
    """

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.marks = {}

    def paintSection(self, painter, rect, index):
        painter.save()
        super().paintSection(painter, rect, index)
        painter.restore()
        mark = self.marks.get(index)
        if mark == "off":
            shade = QColor(Gate.GROUND)
            shade.setAlpha(140)
            painter.fillRect(rect, shade)
        elif mark == "today":
            painter.fillRect(rect.x(), rect.bottom() - 2, rect.width(), 3, QColor(Gate.ACCENT))


def streak_text(days: int) -> str:
    if days <= 0:
        return ""
    if days == 1:
        return "1 day on time"
    return "%d days on time in a row" % days


def status_text(state: dict, hours_now: float = 0.0) -> str:
    """The hero's line for today, from CentralAttendance.today_state()."""
    kind = state.get("state")
    if kind == "working":
        text = "Working %.1f h - in since %s" % (hours_now, state.get("since") or state.get("in"))
        if state.get("sessions", 1) > 1:
            text += " (session %d)" % state["sessions"]
        return text
    if kind == "done":
        return "Punched out at %s" % (state.get("out") or "-")
    return "Not punched in today"


# ------------------------------------------------------------- edit dialog

class EditPunchDialog(QDialog):
    """
    HR correcting one person's day.

    Checked before it closes - the old dialog closed first and then said
    '25:00' in a box, so the input was lost. Times are picked, not typed. An
    out time earlier than the in time is refused unless the shift really
    ended the next day. A reason is required and kept with the day, with who
    made the change and what it said before.

    A day of two sessions (punched out for a break and back in) shows every
    session, and sessions can be added or removed. It used to open as the
    first in and the last out, and saving it - even just to add a reason -
    turned the break into worked hours.
    """

    def __init__(self, name, day, t_in="", t_out="", overnight=False, has_record=False,
                 parent=None, sessions=None, auto_cutoff="", leave=None):
        super().__init__(parent, Qt.WindowType.WindowCloseButtonHint)
        self.setWindowTitle("Edit punch")
        self.setMinimumWidth(380)
        self.cleared = False
        self.leave = leave
        self.setObjectName("editPunch")
        self.setStyleSheet(f"QDialog#editPunch {{ background: {Gate.GROUND}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)
        heading_text = "<b>%s</b><br>%s" % (name, format_date(day, weekday=True))
        if leave:
            heading_text += "<br>Approved leave: %s%s" % (
                leave.get("type") or "Leave", " (%s)" % leave["half"] if leave.get("half") else "")
        heading = QLabel(heading_text)
        heading.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(heading)

        from slate.gui.core.controls import form_layout
        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(Gate.SPACE_2)
        self._grid.setVerticalSpacing(Gate.SPACE_2)
        self._rows = []        # [(label, in edit, out edit, remove button)]
        root.addLayout(self._grid)

        self.no_in = QCheckBox("No in time")
        self.no_out = QCheckBox("No out time")
        self.btn_add = make_button("Add session", "secondary", on_click=lambda: self._add_row())
        self.btn_add.setToolTip("Another in and out the same day (out for a break and back).")
        ticks = QHBoxLayout()
        ticks.addWidget(self.no_in)
        ticks.addWidget(self.no_out)
        ticks.addStretch(1)
        ticks.addWidget(self.btn_add)
        root.addLayout(ticks)

        typed = [(a, b) for a, b in (sessions or []) if a or b]
        if len(typed) < 2:
            typed = [(t_in, t_out)]
        if not has_record and not (t_in or t_out):
            # A missing day is added with both times ready: it opened with
            # both 'No ... time' ticked and HR had to untick them first.
            start = QTime(10, 0)
            typed = [("10:00", start.addSecs(int(lp.standard_day_hours() * 3600)).toString("HH:mm"))]
        for a, b in typed:
            self._add_row(a, b)
        if len(self._rows) == 1:
            self.no_in.setChecked(not rules.parse_time(typed[0][0]))
            self.no_out.setChecked(not rules.parse_time(typed[0][1]))
        self.no_in.toggled.connect(lambda *_: self._sync_rows())
        self.no_out.toggled.connect(lambda *_: self._sync_rows())

        form = form_layout()
        self.overnight = QCheckBox("Ends next day (overnight shift)")
        self.overnight.setChecked(bool(overnight))
        form.addRow("", self.overnight)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Forgot to punch out, machine was down…")
        form.addRow("Reason", self.reason)
        root.addLayout(form)

        if auto_cutoff:
            # The cell's clock icon was the only clue, and saving the day
            # unchanged turned the cutoff into a 'corrected' real time.
            note = QLabel("Out was set by the automatic punch-out at %s - enter the real "
                          "time if it is known." % rules.hhmm(auto_cutoff))
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {Gate.WARN}; font-size: 12.5px;")
            root.addWidget(note)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {Gate.BAD}; font-size: 12.5px;")
        self.error.hide()
        root.addWidget(self.error)

        buttons = QHBoxLayout()
        self.btn_clear = make_button("Clear day", "danger", on_click=self._clear)
        self.btn_clear.setVisible(bool(has_record))
        self.btn_clear.setToolTip("Remove this day's record. The reason is kept in the audit log.")
        buttons.addWidget(self.btn_clear)
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        self.btn_save = make_button("Save", "primary", on_click=self._save)
        buttons.addWidget(self.btn_save)
        root.addLayout(buttons)
        self._sync_rows()

    # ----------------------------------------------------------- sessions
    def _add_row(self, t_in="", t_out=""):
        if not t_in and not t_out and self._rows:
            # A new session starts an hour after the last one ends.
            last = self._rows[-1][2].time()
            t_in = last.addSecs(3600).toString("HH:mm")
            t_out = last.addSecs(2 * 3600).toString("HH:mm")
        label = QLabel("")
        e_in, e_out = QTimeEdit(), QTimeEdit()
        for edit, value in ((e_in, t_in), (e_out, t_out)):
            edit.setDisplayFormat("HH:mm")
            parsed = rules.parse_time(value)
            edit.setTime(QTime(parsed.hour, parsed.minute) if parsed else QTime(10, 0))
        remove = make_button("Remove", "ghost")
        remove.setToolTip("Remove this session")
        row = (label, e_in, e_out, remove)
        remove.clicked.connect(lambda *_: self._remove_row(row))
        self._rows.append(row)
        if len(self._rows) > 1:
            # Only a one-session day can be an in or an out on its own.
            self.no_in.setChecked(False)
            self.no_out.setChecked(False)
        self._sync_rows()

    def _remove_row(self, row):
        if len(self._rows) > 1 and row in self._rows:
            self._rows.remove(row)
            for widget in row:
                self._grid.removeWidget(widget)
                widget.deleteLater()
            self._sync_rows()

    def _sync_rows(self):
        single = len(self._rows) == 1
        for i, (label, e_in, e_out, remove) in enumerate(self._rows):
            label.setText("In - out" if single else "Session %d" % (i + 1))
            self._grid.addWidget(label, i, 0)
            self._grid.addWidget(e_in, i, 1)
            self._grid.addWidget(e_out, i, 2)
            self._grid.addWidget(remove, i, 3)
            remove.setVisible(not single)
            e_in.setEnabled(not (single and self.no_in.isChecked()))
            e_out.setEnabled(not (single and self.no_out.isChecked()))
        self.no_in.setVisible(single)
        self.no_out.setVisible(single)
        self.e_in = self._rows[0][1]
        self.e_out = self._rows[-1][2]

    def _fail(self, text):
        self.error.setText(text)
        self.error.show()

    def values(self) -> dict:
        sessions = [(e_in.time().toString("HH:mm"), e_out.time().toString("HH:mm"))
                    for _, e_in, e_out, _ in self._rows]
        if len(sessions) == 1:
            sessions = [("" if self.no_in.isChecked() else sessions[0][0],
                         "" if self.no_out.isChecked() else sessions[0][1])]
        return {
            "in": sessions[0][0],
            "out": sessions[-1][1],
            "sessions": sessions,
            "overnight": self.overnight.isChecked(),
            "reason": self.reason.text().strip(),
        }

    def problem(self) -> str:
        """Why the values cannot be saved, or ''."""
        v = self.values()
        why = rules.sessions_problem(v["sessions"], v["overnight"])
        if why:
            if why.startswith("Enter an in time"):
                why += " To remove the day, use Clear day."
            return why
        if not v["reason"]:
            return "Say why the day is being corrected - it is kept with the record."
        return ""

    def _save(self):
        why = self.problem()
        if why:
            self._fail(why)
            return
        if self.leave and QMessageBox.question(
                self, "Edit punch",
                "This is a day of approved leave (%s). Add punches anyway?\n\n"
                "The leave stays charged until HR revoke it." % (self.leave.get("type") or "Leave"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return
        self.accept()

    def _clear(self):
        if not self.reason.text().strip():
            self._fail("Say why the day is being cleared - it is kept in the audit log.")
            return
        if QMessageBox.question(
                self, "Clear day", "Remove this day's punches?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return
        self.cleared = True
        self.accept()


# ---------------------------------------------------------------- the tab

class AttendanceTab(QWidget):
    """
    Dedicated Attendance Dashboard.
    - Personal: punch in/out, my month (any month).
    - Team: HR/admin edit the whole studio; supervisors see their reports.
    """

    TEAM_STATS = ["Days", "Late", "Absent", "Missing", "Hours", "WFH"]
    PERSONAL_COLUMNS = ["Date", "In", "Out", "Hours", "OT", "Status"]
    SETTINGS = ("UTStudio", "Slate")

    def __init__(self, user_data, attendance=None, user_manager=None, app_context=None, sync_enabled=True):
        super().__init__()
        self.app_context = app_context or AppContext()
        self.sync_enabled = bool(sync_enabled)
        # Standalone / tests may open MainWindow without login; match main_window debug_user.
        if not user_data:
            user_data = {
                "user_id": "debug_user",
                "username": "debug_user",
                "display_name": "Local user",
                "roles": ["Artist"],
            }
        self.user_data = user_data
        self.username = user_data.get('user_id', user_data.get('username', 'Unknown'))
        self._team_row_user_ids = []
        self._team_users = {}
        self._team_data = {}
        self._holiday_cache = {}
        self._location_cache = {}
        self._unread = None      # what could not be read this time (_holidays, _leave)

        # Handle roles array (new format) and role string (legacy)
        roles_data = user_data.get('roles', user_data.get('role', ['Artist']))
        if isinstance(roles_data, list):
            self.roles = roles_data
            self.role = roles_data[0] if roles_data else 'Artist'  # Keep for display compatibility
        else:
            self.roles = [roles_data]
            self.role = roles_data

        self.display_name = user_data.get('display_name', self.username)

        self.attendance = attendance or self.app_context.attendance()
        self.user_manager = user_manager or self.app_context.user_manager()
        self._export_worker = None

        self.setup_ui()
        self.refresh_personal_view()
        if self.has_team_view() and self.sync_enabled:
            self.refresh_team_view()

        # Today's running hours, every minute while the page is on screen:
        # one person's day, not the month, the streak and every user.
        self.personal_refresh_timer = QTimer(self)
        self.personal_refresh_timer.timeout.connect(self._tick_today)
        self.personal_refresh_timer.start(60000)
        # Everything else when somebody's attendance, leave or the holidays
        # change (the change feed), and only while the page is shown. It used
        # to re-read the studio's month every 30 s on whatever tab HR was on.
        from slate.gui.components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(
            self, self._refresh_all, seconds=60,
            topics=("attendance_log", "leave_requests", "holiday_calendar"))

    def _tick_today(self):
        if self.isVisible():
            self._refresh_today(datetime.now())

    def _refresh_all(self):
        self._holiday_cache = {}
        self._my_record = None
        self.refresh_personal_view()
        if self.has_team_view() and self.sync_enabled:
            self.refresh_team_view()

    # ------------------------------------------------------------ who sees
    def is_admin(self):
        """
        Whether this person may see the whole studio's grid and correct punches.

        Decided by access.json, not by a list in this file. The list that was
        here (supervisor, developer, admin) left HR out, so the people who
        actually keep the attendance record could not see it.
        """
        return can(self.roles, "view_team_attendance")

    def is_team_lead(self):
        """Approves somebody's leave, so sees their attendance - read-only."""
        return not self.is_admin() and can(self.roles, "approve_leave")

    def has_team_view(self):
        return self.is_admin() or self.is_team_lead()

    def team_scope(self, users=None):
        """
        None: everybody. A set of usernames (lower-case): only those - the
        people under this person, all the way down: a supervisor whose
        artists report to a lead saw only the lead.
        """
        if self.is_admin():
            return None
        if users is None:
            try:
                users = self.user_manager.get_all_users() or {}
            except Exception as exc:
                logging.warning("Could not read who reports to %s: %s", self.username, exc)
                return set()
        return people.reports_under(users, self.username, all_the_way_down=True)

    def _notify(self, message: str, level: str = "info", details: str = ""):
        """Use host feedback API when available, fallback to dialogs."""
        host = self.window()
        if host and hasattr(host, "show_feedback"):
            try:
                host.show_feedback(message=message, level=level, duration=4500, details=details)
                return
            except Exception:
                pass

        if level == "error":
            QMessageBox.critical(self, "Attendance", details or message)
        elif level == "warning":
            QMessageBox.warning(self, "Attendance", message)
        else:
            QMessageBox.information(self, "Attendance", message)

    # --------------------------------------------------------------- layout
    def _create_stat_card(self, label, value, color):
        """A compact figure for the hero card. Shrinks with the window."""
        card = QWidget()
        card.setObjectName("attStat")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setMinimumSize(78, 62)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        card.setStyleSheet(
            f"QWidget#attStat {{ background: {Gate.overlay(0.03)}; "
            f"border: 1px solid {Gate.overlay(0.06)}; border-radius: {Gate.RADIUS_LG}px; }}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        val = QLabel(value)
        val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        val.setStyleSheet(f"color: {color}; font-size: 20px; font-weight: 700; background: transparent;")
        val.setObjectName(f"stat_value_{label.lower()}")
        lbl = QLabel(label)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 11px; font-weight: 600; background: transparent;")
        setattr(self, f"lbl_monthly_{label.lower()}_value", val)
        layout.addWidget(val)
        layout.addWidget(lbl)
        return card

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(12)

        self.setStyleSheet(f"""
            QFrame#PersonalCard {{
                background: {Gate.ACCENT_SURFACE};
                border: 1px solid {Gate.LINE};
                border-radius: 12px;
            }}
            QFrame#CompactLegend {{
                background: {Gate.PANEL};
                border-radius: 14px;
                border: 1px solid {Gate.LINE};
            }}
        """)

        # 1. HERO --------------------------------------------------------
        self.personal_card = QFrame()
        self.personal_card.setObjectName("PersonalCard")
        self.personal_card.setMinimumHeight(110)
        pc_layout = QHBoxLayout(self.personal_card)
        pc_layout.setContentsMargins(20, 10, 20, 10)
        pc_layout.setSpacing(20)

        # A. Profile and status. A fixed width, so the cards beside it do not
        # slide sideways every time the status line changes length.
        left_box = QWidget()
        left_box.setFixedWidth(330)
        left_box.setStyleSheet("background: transparent;")
        lb_layout = QVBoxLayout(left_box)
        lb_layout.setContentsMargins(0, 0, 0, 0)
        lb_layout.setSpacing(4)
        lb_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        name_row = QHBoxLayout()
        name_row.setSpacing(10)
        avatar = QLabel((self.display_name or "?")[:1].upper())
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            f"background: {Gate.ACCENT}; color: {Gate.TEXT_ON_ACCENT}; font-weight: bold; "
            f"border-radius: 16px; font-size: 14px;")
        self.lbl_welcome = QLabel(self.display_name)
        self.lbl_welcome.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {Gate.TEXT}; background: transparent;")
        name_row.addWidget(avatar)
        name_row.addWidget(self.lbl_welcome, 1)
        lb_layout.addLayout(name_row)

        # The status sits under the name, indented past the avatar by the
        # layout - a stylesheet margin was lost the first time the colour
        # changed.
        status_row = QVBoxLayout()
        status_row.setContentsMargins(42, 0, 0, 0)
        status_row.setSpacing(2)
        self.lbl_status = QLabel("Checking\u2026")
        self.lbl_status.setObjectName("attStatus")
        self.lbl_status.setProperty("tone", "idle")
        self.lbl_status.setStyleSheet(
            f"QLabel#attStatus {{ font-size: 15px; font-weight: 600; background: transparent; color: {Gate.TEXT_2}; }}"
            f"QLabel#attStatus[tone=\"ok\"] {{ color: {Gate.OK}; }}"
            f"QLabel#attStatus[tone=\"done\"] {{ color: {Gate.TEXT}; }}")
        self.lbl_streak = QLabel("")
        self.lbl_streak.setStyleSheet(f"color: {Gate.WARN}; font-weight: 600; font-size: 12px; background: transparent;")
        status_row.addWidget(self.lbl_status)
        status_row.addWidget(self.lbl_streak)
        lb_layout.addLayout(status_row)

        # B. The month's figures. They shrink, and fold into two rows when
        # the window is narrow - at 1280 px the fourth one was cut in half.
        self.center_box = QWidget()
        self.center_box.setStyleSheet("background: transparent;")
        self.cards_grid = QGridLayout(self.center_box)
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setSpacing(8)
        self.lbl_monthly_present = self._create_stat_card("Present", "-", Gate.OK)
        self.lbl_monthly_late = self._create_stat_card("Late", "-", Gate.WARN)
        self.lbl_monthly_hours = self._create_stat_card("Hours", "-", Gate.ACCENT)
        self.lbl_monthly_wfh = self._create_stat_card("WFH", "-", Gate.ACCENT)
        self._stat_cards = [self.lbl_monthly_present, self.lbl_monthly_late,
                            self.lbl_monthly_hours, self.lbl_monthly_wfh]
        self._cards_columns = 0
        self._arrange_cards(4)

        # C. Actions: one valid next step at a time.
        right_box = QWidget()
        right_box.setStyleSheet("background: transparent;")
        rb = QVBoxLayout(right_box)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.setSpacing(6)
        rb.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.chk_wfh_box = QCheckBox("Working from home today")
        self.chk_wfh_box.setToolTip(
            "Recorded with today's punch-in. Change it later in the day and "
            "today's record follows.")
        self.chk_wfh_box.toggled.connect(self._on_wfh_toggled)
        self.btn_punch_in = make_button("Punch in", "primary", on_click=lambda: self.manual_punch("in"))
        self.btn_punch_out = make_button("Punch out", "danger", on_click=lambda: self.manual_punch("out"))
        actions.addWidget(self.chk_wfh_box)
        actions.addWidget(self.btn_punch_in)
        actions.addWidget(self.btn_punch_out)
        rb.addLayout(actions)
        # The confirmation stays next to the buttons; the status bar line
        # was 9 px and gone in four seconds.
        self.lbl_punch_note = QLabel("")
        self.lbl_punch_note.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.lbl_punch_note.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 12px; background: transparent;")
        rb.addWidget(self.lbl_punch_note)

        pc_layout.addWidget(left_box)
        pc_layout.addWidget(self.center_box, 1)
        pc_layout.addWidget(right_box)
        layout.addWidget(self.personal_card)
        # An account that is not a person (admin) has no day to punch.
        me = people.person(self.username)
        self.is_service = bool(me is not None and me.is_service)
        team_page = self.has_team_view() and self.sync_enabled
        self.personal_card.setVisible(not (self.is_service and team_page))

        # 2. LEGEND -------------------------------------------------------
        legend_row = QHBoxLayout()
        legend_row.setContentsMargins(5, 0, 5, 0)
        legend_row.addStretch(1)
        legend_frame = QFrame()
        legend_frame.setObjectName("CompactLegend")
        legend_frame.setMinimumHeight(30)
        lf_layout = QHBoxLayout(legend_frame)
        lf_layout.setContentsMargins(12, 0, 12, 0)
        lf_layout.setSpacing(14)
        for colour, text in legend_entries():
            item = QWidget()
            item.setStyleSheet("background: transparent;")
            il = QHBoxLayout(item)
            il.setContentsMargins(0, 0, 0, 0)
            il.setSpacing(5)
            swatch = QLabel()
            swatch.setFixedSize(12, 12)
            # The cell's tint, ringed in the state's own colour: in Dark the
            # tints alone were five near-black squares.
            swatch.setStyleSheet(f"background: {colour}; border: 2px solid {legend_tone(text)}; "
                                 "border-radius: 3px;")
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 11px; background: transparent;")
            il.addWidget(swatch)
            il.addWidget(lbl)
            lf_layout.addWidget(item)
        legend_row.addWidget(legend_frame)
        layout.addLayout(legend_row)

        # 3. MY MONTH ----------------------------------------------------
        personal_page = QWidget()
        pp = QVBoxLayout(personal_page)
        pp.setContentsMargins(0, 8, 0, 0)
        pp.setSpacing(8)
        mid_row = QHBoxLayout()
        mid_row.setContentsMargins(5, 0, 5, 0)
        mid_row.setSpacing(8)
        mid_row.addWidget(QLabel("My month"))
        now = datetime.now()
        self.my_month = QComboBox()
        self.my_month.addItems([calendar.month_name[i] for i in range(1, 13)])
        self.my_month.setCurrentIndex(now.month - 1)
        self.my_year = QSpinBox()
        self.my_year.setRange(now.year - 6, now.year + 1)
        self.my_year.setValue(now.year)
        self.my_month.currentIndexChanged.connect(lambda *_: self.refresh_personal_view())
        self.my_year.valueChanged.connect(lambda *_: self.refresh_personal_view())
        mid_row.addWidget(self.my_month)
        mid_row.addWidget(self.my_year)
        mid_row.addStretch(1)
        pp.addLayout(mid_row)
        self.my_table = QTableWidget()
        self.setup_table(self.my_table)
        pp.addWidget(self.my_table, 1)

        # 4. THE TEAM ----------------------------------------------------
        # 'My month' and the team grid are two tabs for anybody with a team:
        # stacked, at 1280x720 each showed one and a half rows and the
        # splitter had to be dragged on every machine. The tab is remembered.
        self.team_table = None
        self.pages = None
        if team_page:
            from PySide6.QtWidgets import QTabWidget
            self.pages = QTabWidget()
            self.pages.setDocumentMode(True)
            if self.is_service:
                personal_page.setParent(self)        # kept, never shown
                personal_page.hide()
            else:
                self.pages.addTab(personal_page, "My month")
            self.pages.addTab(self._build_team_section(),
                              "Team overview" if self.is_admin() else "My team")
            self.pages.setCurrentIndex(self._stored_page())
            self.pages.currentChanged.connect(self._save_page)
            layout.addWidget(self.pages, 1)
        else:
            layout.addWidget(personal_page, 1)
            if self.has_team_view():
                sync_notice = QLabel(
                    "Local mode: the team's attendance is not available.\n"
                    "It comes back when Slate is connected to the studio database.")
                sync_notice.setWordWrap(True)
                sync_notice.setStyleSheet(
                    f"color: {Gate.WARN}; background: {Gate.WARN_SURFACE}; border: 1px solid {Gate.LINE}; "
                    "border-radius: 8px; padding: 10px; font-weight: 600;")
                layout.addWidget(sync_notice)

    def _build_team_section(self):
        admin_widget = QWidget()
        aw_layout = QVBoxLayout(admin_widget)
        aw_layout.setContentsMargins(0, 8, 0, 0)
        aw_layout.setSpacing(8)

        ah_layout = QHBoxLayout()
        ah_layout.setContentsMargins(5, 0, 5, 0)
        # The tab names the section; only when it was read is said here.
        self.lbl_last_refresh = QLabel("Updated -")
        self.lbl_last_refresh.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 11px;")
        ah_layout.addWidget(self.lbl_last_refresh)
        ah_layout.addStretch()

        self.combo_month = QComboBox()
        self.combo_month.addItems([calendar.month_name[i] for i in range(1, 13)])
        self.combo_month.setCurrentIndex(datetime.now().month - 1)
        self.spin_year = QSpinBox()
        # Relative to now. A fixed 2020-2030 is a date this screen stops
        # working on, written five years before anybody would notice.
        this_year = datetime.now().year
        self.spin_year.setRange(this_year - 6, this_year + 2)
        self.spin_year.setValue(this_year)
        self.combo_month.currentIndexChanged.connect(lambda *_: self.refresh_team_view())
        self.spin_year.valueChanged.connect(lambda *_: self.refresh_team_view())

        btn_ref = make_button("Refresh", "secondary", on_click=self.refresh_team_view)
        btn_exp = make_button("Export", "secondary", on_click=self.export_csv,
                              tooltip="The month as an Excel workbook, for the people shown")

        ah_layout.addWidget(self.combo_month)
        ah_layout.addWidget(self.spin_year)
        ah_layout.addWidget(btn_ref)
        ah_layout.addWidget(btn_exp)

        # The holidays this grid shades are HR's to keep, and this is where
        # a wrong one gets noticed - by somebody looking at the month.
        self.btn_holidays = None
        self.btn_import = None
        if self.is_admin():
            # The biometric machine's export. Any machine: the columns are
            # worked out from the file and remembered per studio.
            self.btn_import = make_button(
                "Import biometric", "secondary", on_click=self.import_biometric,
                tooltip="Import punches from the biometric machine's CSV or Excel export. "
                        "Importing the same file again changes nothing.")
            ah_layout.addWidget(self.btn_import)
        if can(self.roles, "manage_leave"):
            self.btn_holidays = make_button(
                "Holidays", "secondary", on_click=self.edit_holidays,
                tooltip="The studio's public holidays: the days shaded here, and "
                        "the days every leave request is charged against.")
            ah_layout.addWidget(self.btn_holidays)
        aw_layout.addLayout(ah_layout)

        # Finding one person among 150 meant scrolling.
        filters = QHBoxLayout()
        filters.setContentsMargins(5, 0, 5, 0)
        filters.setSpacing(8)
        self.team_search = QLineEdit()
        self.team_search.setPlaceholderText("Search people\u2026")
        # Typing filters the people already read, a moment after the last
        # key: every keystroke re-read all users and the whole month.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(lambda: self.refresh_team_view(reload=False))
        self.team_search.textChanged.connect(lambda *_: self._search_timer.start())
        self.filter_department = QComboBox()
        self.filter_manager = QComboBox()
        self.filter_location = QComboBox()
        for combo, everyone in ((self.filter_department, "All departments"),
                                (self.filter_manager, "All managers"),
                                (self.filter_location, "All locations")):
            combo.addItem(everyone, "")
            combo.currentIndexChanged.connect(lambda *_: self.refresh_team_view(reload=False))
        self.show_system = QCheckBox("Show system accounts")
        self.show_system.toggled.connect(lambda *_: self.refresh_team_view(reload=False))
        # A team lead's grid is their own people: no system accounts to show.
        self.show_system.setVisible(self.is_admin())
        filters.addWidget(self.team_search, 1)
        filters.addWidget(self.filter_department)
        filters.addWidget(self.filter_manager)
        filters.addWidget(self.filter_location)
        filters.addWidget(self.show_system)
        aw_layout.addLayout(filters)

        self.team_table = QTableWidget()
        # Today and the days off marked in the header itself: the themed
        # header stylesheet ignored the item colours that tried to.
        self.team_table.setHorizontalHeader(DayHeader(self.team_table))
        self.team_table.cellDoubleClicked.connect(self.on_cell_double_click)
        # The names down the side get a heading like every other column.
        self.team_corner = QLabel("Person", self.team_table)
        self.team_corner.setStyleSheet(
            f"color: {Gate.TEXT_2}; font-weight: 600; font-size: {Gate.SIZE_SM}px; "
            f"padding-left: {Gate.CELL_PADDING}px; background: transparent;")
        aw_layout.addWidget(self.team_table, 1)

        from slate.gui.core.empty_state import EmptyState
        self.team_empty = EmptyState(
            "Nobody reports to you yet" if self.is_team_lead() else "Nobody matches",
            "People appear here once their record names you as their manager. "
            "HR set that on Users & Roles." if self.is_team_lead()
            else "Change the search or the filters.", glyph="users")
        aw_layout.addWidget(self.team_empty, 1)
        self.team_empty.attach_to(self.team_table)
        return admin_widget

    def _arrange_cards(self, columns):
        if columns == self._cards_columns:
            return
        self._cards_columns = columns
        for card in self._stat_cards:
            self.cards_grid.removeWidget(card)
        for i, card in enumerate(self._stat_cards):
            self.cards_grid.addWidget(card, i // columns, i % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            # One row from 1100 px: two rows made the hero 150 px tall and
            # left the tables a few rows at 1280x720.
            self._arrange_cards(4 if self.width() >= 1100 else 2)
        except Exception:
            pass

    def _stored_page(self) -> int:
        """The tab last used; the team for a first visit (that is HR's work area)."""
        try:
            from PySide6.QtCore import QSettings
            stored = int(QSettings(*self.SETTINGS).value("attendance/page", -1))
        except Exception:
            stored = -1
        last = self.pages.count() - 1
        return stored if 0 <= stored <= last else last

    def _save_page(self, index):
        try:
            from PySide6.QtCore import QSettings
            QSettings(*self.SETTINGS).setValue("attendance/page", int(index))
        except Exception as exc:
            logging.debug("Attendance tab not remembered: %s", exc)

    def setup_table(self, table):
        # The shared table style (it had its own: grey upper-case headers on a
        # different grey from every other table in the module).
        style_table(table)
        table.setEditTriggers(QTableWidget.NoEditTriggers)

    # --------------------------------------------------------------- data
    def _holidays(self, year: int, location: str = None) -> set:
        """
        The studio's public holidays for a year, for one location.

        Attendance used to ask for every location's holidays, so a Mumbai
        artist got Chennai's Onam off and a Chennai artist got Mumbai's
        Ganesh Chaturthi. Each person is judged by their own location; a
        person with no location gets the holidays that apply to everybody.
        """
        if location is None:
            location = self._location_of(self.username)
        place = str(location or "").strip() or "All"
        key = (int(year), place.lower())
        if key not in self._holiday_cache:
            try:
                from slate.core.infra.leave_repository import LeaveRepository
                repo = LeaveRepository()
                self._holiday_cache[key] = (repo.holidays(year, place),
                                            repo.holiday_names(year, None if place == "All" else place))
            except Exception as exc:
                # Not cached: an empty set showed every holiday as Absent for
                # the rest of the session, and in the export.
                logging.warning("Attendance could not read the holidays: %s", exc)
                self._unread = "the holiday calendar"
                return set()
        return self._holiday_cache[key][0]

    def _holiday_name(self, day, location=None) -> str:
        self._holidays(day.year, location)
        place = str((self._location_of(self.username) if location is None else location) or "").strip() or "All"
        return self._holiday_cache.get((day.year, place.lower()), (set(), {}))[1].get(day, "")

    def _location_of(self, username) -> str:
        key = str(username or "").lower()
        if key not in self._location_cache:
            try:
                from slate.core.infra.leave_repository import LeaveRepository
                self._location_cache[key] = LeaveRepository().location_of(username)
            except Exception:
                self._location_cache[key] = ""
        return self._location_cache[key]

    def _expected(self, username, record=None) -> tuple:
        """
        (first, last) day somebody was expected in (rules.expected_window):
        nobody is 'absent' before they joined or after they left, and an
        account that is not a person (admin, tester) never is.
        """
        person = people.person(username)
        if record is None:
            record = getattr(self, "_my_record", None)
            if record is None:
                try:
                    record = (self.user_manager.get_all_users() or {}).get(username, {})
                except Exception:
                    record = {}
                self._my_record = record
        return rules.expected_window(record, bool(person is not None and person.is_service))

    def _is_non_working(self, day) -> bool:
        """A day nobody was expected in: a weekly off or a public holiday."""
        return not lp.is_working_day(day, self._holidays(day.year))

    def _leave(self, start, end, users=None) -> dict:
        try:
            from slate.core.infra.leave_repository import LeaveRepository
            return LeaveRepository().approved_leave(start, end, users)
        except Exception as exc:
            logging.warning("Attendance could not read approved leave: %s", exc)
            self._unread = "approved leave"
            return {}

    def _report_unread(self) -> bool:
        """Say so when holidays or approved leave could not be read; True if so."""
        what, self._unread = self._unread, None
        if what:
            self._notify("Could not read %s, so some days may show as Absent that are not. "
                         "Refresh to try again; if it keeps happening, tell IT." % what, "warning")
        return bool(what)

    def calculate_streak(self, user_log=None, year=None, month=None):
        """On-time days in a row, across month ends, leave and days off neutral."""
        today = date.today()
        start = today - timedelta(days=120)
        try:
            days = self.attendance.get_user_days(self.username, start, today)
        except Exception:
            days = {}
        holidays = self._holidays(today.year) | self._holidays(start.year)
        leave = self._leave(start, today, [self.username]).get(self.username.lower(), {})
        return rules.calculate_streak(days, today, holidays, set(leave))

    def _calculate_hours(self, in_time: str, out_time: str = "", now_ref: datetime = None) -> float:
        return compute_hours(in_time=in_time, out_time=out_time, now_ref=now_ref)

    # ------------------------------------------------------------- personal
    def personal_month(self):
        return self.my_year.value(), self.my_month.currentIndex() + 1

    # __init__ calls this, so without the decorator an unreachable database
    # does not produce an empty tab - it produces no tab at all, and the
    # error surfaces as a failure to open rather than as an explanation.
    @on_database_error
    def refresh_personal_view(self):
        """Today's status, and the chosen month for self (one person's rows only)."""
        now = datetime.now()
        today = now.date()
        self._refresh_today(now)

        year, month = self.personal_month()
        user_log = self.attendance.get_user_month(self.username, year, month)
        days_in_month = calendar.monthrange(year, month)[1]
        first, last = date(year, month, 1), date(year, month, days_in_month)
        leave = self._leave(first, last, [self.username]).get(self.username.lower(), {})
        holidays = self._holidays(year)

        streak = self.calculate_streak()
        self.lbl_streak.setText(streak_text(streak))
        self.lbl_streak.setVisible(bool(streak))

        self.my_table.clear()
        self.my_table.setColumnCount(len(self.PERSONAL_COLUMNS))
        self.my_table.setHorizontalHeaderLabels(self.PERSONAL_COLUMNS)
        # Status takes the room, last - OT sat a thousand pixels from the
        # Hours it qualifies when Status stretched between them.
        style_table(self.my_table, {
            "Date": "contents", "In": "contents", "Out": "contents",
            "Hours": "numeric", "OT": "contents", "Status": "stretch",
        })
        self.my_table.setRowCount(days_in_month)
        styles = state_styles()
        standard = lp.standard_day_hours()
        expected = self._expected(self.username)

        # The hero's figures and the table come from one count, the one the
        # team grid and the export use, so 'Late 1' is a late day you can find.
        summary = rules.month_summary(user_log, year, month, holidays, leave, expected, now, today)
        for i, d in enumerate(summary["days"], 1):
            day, entry, state, hours = d["day"], d["entry"], d["state"], d["hours"]
            day_leave, was_late = d["leave"], d["late"]
            sessions = rules.sessions_of(entry)
            parts = [styles[state][1] if state not in (rules.HOLIDAY, rules.WEEKLY_OFF) else ""]
            if state == rules.HOLIDAY:
                parts = ["Holiday: %s" % (self._holiday_name(day) or "public holiday")]
            elif state == rules.WEEKLY_OFF:
                parts = ["Weekly off"]
            elif state == rules.WORKED_OFF:
                parts = ["Worked on a %s" % ("holiday" if day in holidays else "weekly off")]
            if was_late and state != rules.LATE:
                parts.append("late")
            if day_leave and state != rules.LEAVE:
                parts.append("on approved leave (%s)" % day_leave["type"])
            elif state == rules.LEAVE:
                parts = ["On leave (%s%s)" % (day_leave["type"],
                                             ", " + day_leave["half"].lower() if day_leave.get("half") else "")]
            if entry.get("wfh"):
                parts.append("WFH")
            if len(sessions) > 1:
                parts.append("%d sessions" % len(sessions))
            if entry.get("edited_by"):
                parts.append("corrected by %s" % people.display_name(entry["edited_by"]))
            ot = ""
            if hours > standard and sessions and sessions[-1][1] is not None:
                ot = "+%.1f h" % (hours - standard)
            out_text = entry.get("out", "")
            if entry.get("overnight") and out_text:
                out_text += " (+1)"
            cells = [
                format_date(day, weekday=True),
                entry.get("in", ""),
                out_text,
                ("%.1f" % hours) if hours else "",
                ot,
                " / ".join(p for p in parts if p),
            ]
            colour = QColor(styles[state][0])
            for c, txt in enumerate(cells):
                it = QTableWidgetItem(txt)
                it.setBackground(colour)
                if c in (3, 4):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if day == today:
                    font = it.font()
                    font.setBold(True)
                    it.setFont(font)
                if c == 5:
                    it.setToolTip(self._day_tooltip(entry, day, state, day_leave, late=was_late))
                self.my_table.setItem(i - 1, c, it)

        self.lbl_monthly_present_value.setText(f"{summary['present']}")
        self.lbl_monthly_late_value.setText(f"{summary['late']}")
        self.lbl_monthly_hours_value.setText(f"{summary['hours']:.1f}")
        self.lbl_monthly_wfh_value.setText(f"{summary['wfh']}")
        self._report_unread()

    def _refresh_today(self, now):
        try:
            state = self.attendance.today_state(self.username)
        except Exception as exc:
            logging.debug("Today's attendance not read: %s", exc)
            state = {"state": "out"}
        self._today = state
        hours_now = 0.0
        if state.get("state") == "working":
            try:
                entry = self.attendance.get_user_month(self.username, now.year, now.month).get(
                    f"{now.day:02d}", {})
                hours_now = rules.day_hours(entry, now, open_counts=True)
            except Exception:
                hours_now = 0.0
        self.lbl_status.setText(status_text(state, hours_now))
        self.lbl_status.setToolTip(self.lbl_status.text())
        tone = {"working": "ok", "done": "done"}.get(state.get("state"), "idle")
        self.lbl_status.setProperty("tone", tone)
        self.lbl_status.style().unpolish(self.lbl_status)
        self.lbl_status.style().polish(self.lbl_status)

        # Only the valid next step is offered.
        kind = state.get("state")
        self.btn_punch_in.setEnabled(kind in ("out", "done"))
        self.btn_punch_in.setText("Punch in again" if kind == "done" else "Punch in")
        self.btn_punch_out.setEnabled(kind == "working")
        self.btn_punch_in.setToolTip(
            "Starts a second session today." if kind == "done" else
            ("You are punched in." if kind == "working" else ""))
        self.btn_punch_out.setToolTip(
            "" if kind == "working" else "Punch in first.")

        # The WFH box shows today's record once there is one. Before the
        # punch-in it is the person's choice for it, kept - the refresh used
        # to clear it while the note still promised working from home.
        if kind in ("working", "done"):
            self.chk_wfh_box.blockSignals(True)
            self.chk_wfh_box.setChecked(bool(state.get("wfh")))
            self.chk_wfh_box.blockSignals(False)

    def _on_wfh_toggled(self, checked):
        state = getattr(self, "_today", {}) or {}
        if state.get("state") in ("working", "done"):
            try:
                ok = self.attendance.set_wfh(self.username, date.fromisoformat(state.get("date")), checked)
            except Exception as exc:
                logging.warning("WFH not saved: %s", exc)
                ok = False
            if ok:
                self.lbl_punch_note.setText(
                    "Marked as working from home today." if checked
                    else "No longer marked as working from home today.")
                self.refresh_personal_view()
            else:
                self.lbl_punch_note.setText("Working from home could not be saved.")
        else:
            self.lbl_punch_note.setText(
                "You will be marked as working from home when you punch in." if checked else "")

    def manual_punch(self, action):
        """Punch in or out, and say what was stored."""
        if action == "out":
            # No time in the question: the punch is stored at the database
            # server's time, which the workstation's clock may not match.
            if QMessageBox.question(
                    self, "Punch out", "Punch out now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
            ) != QMessageBox.StandardButton.Yes:
                return
        else:
            today = date.today()
            on_leave = self._leave(today, today, [self.username]).get(
                self.username.lower(), {}).get(today)
            # A half day is meant to be worked for the other half.
            if on_leave and not on_leave.get("half") and QMessageBox.question(
                    self, "Punch in",
                    "You are on approved leave today (%s). Punch in anyway?\n\n"
                    "Ask HR to cancel the leave if you are working." % on_leave.get("type", "Leave"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
            ) != QMessageBox.StandardButton.Yes:
                return
        try:
            meta = {}
            if action == 'in' and self.chk_wfh_box.isChecked():
                meta['wfh'] = True
            stored = self.attendance.log_action(self.username, action, metadata=meta) or {}
            self.refresh_personal_view()
            if self.has_team_view() and self.sync_enabled and self.team_table is not None:
                self.refresh_team_view()
            at = str(stored.get("time") or datetime.now().strftime("%H:%M:%S"))[:5]
            if action == "in":
                session = stored.get("session") or 1
                msg = "Punched in at %s." % at if session == 1 else \
                    "Punched in again at %s (session %d)." % (at, session)
            else:
                msg = "Punched out at %s." % at
            self.lbl_punch_note.setText(msg)
            self._notify(msg, "success")
        except ValueError as ve:
            # Refused: already in, not punched in, already out.
            self.lbl_punch_note.setText(str(ve))
            self._notify(str(ve), "warning")
            self.refresh_personal_view()
        except Exception as e:
            self.lbl_punch_note.setText("The punch was not saved.")
            self._notify("The punch was not saved.", "error", details=f"Failed to punch: {e}")

    def _day_tooltip(self, entry, day, state, leave=None, location=None, late=False) -> str:
        lines = [format_date(day, weekday=True)]
        label = state_styles()[state][1]
        if state == rules.HOLIDAY:
            label = "Holiday: %s" % (self._holiday_name(day, location) or "public holiday")
        elif state == rules.WEEKLY_OFF:
            label = "Weekly off"
        if label:
            lines.append(label)
        if late and state != rules.LATE:
            lines.append("Arrived late")
        sessions = rules.sessions_of(entry)
        for n, (start, end) in enumerate(sessions, 1):
            prefix = "Session %d: " % n if len(sessions) > 1 else ""
            lines.append("%s%s - %s" % (prefix, rules.hhmm(start) or "?", rules.hhmm(end) or "still in"))
        if not sessions and entry.get("out"):
            lines.append("Out %s, no punch-in" % entry.get("out"))
        if entry.get("overnight"):
            lines.append("Ends the next day")
        if entry.get("wfh"):
            lines.append("Working from home")
        if entry.get("auto_logout"):
            lines.append("Closed by the automatic punch-out")
        if entry.get("missing_punch_out"):
            lines.append("Punch-out missing - HR to correct")
        if leave:
            lines.append("Approved leave: %s%s" % (leave["type"],
                                                   " (%s)" % leave["half"] if leave.get("half") else ""))
            if sessions:
                lines.append("Punched in on a day of approved leave - check which is right.")
        if entry.get("edited_by"):
            history = entry.get("edit_history") or []
            was = history[-1] if history else {}
            lines.append("Corrected by %s on %s: %s%s" % (
                people.display_name(entry["edited_by"]), str(entry.get("edited_at") or "")[:10],
                entry.get("edit_reason") or "",
                " (was %s-%s)" % (was.get("was_in") or "-", was.get("was_out") or "-") if was else ""))
        return "\n".join(lines)

    # ----------------------------------------------------------------- team
    def team_month(self):
        return self.spin_year.value(), self.combo_month.currentIndex() + 1

    def team_people(self, year, month, users=None) -> list:
        """
        The people the grid shows, sorted by name: everybody for HR, the
        viewer's people for a supervisor; leavers only for months they were
        here; system accounts only when asked; the search and filters applied.
        """
        if users is None:
            users = self.user_manager.get_all_users() or {}
        month_start = f"{year:04d}-{month:02d}-01"
        scope = self.team_scope(users)

        def still_here(record):
            if record.get('active', True):
                return True
            ended = str(record.get('last_day') or record.get('deactivated_on') or '')[:10]
            return bool(ended) and ended >= month_start

        chosen = []
        for uid, rec in users.items():
            rec = rec or {}
            if scope is not None and uid.lower() not in scope:
                continue
            if not still_here(rec):
                continue
            person = people.person(uid)
            if person is not None and person.is_service and not self.show_system.isChecked():
                continue
            chosen.append((uid, rec))
        self._fill_filter_choices(chosen)

        needle = self.team_search.text().strip().lower()
        dept = self.filter_department.currentData() or ""
        mgr = self.filter_manager.currentData() or ""
        loc = self.filter_location.currentData() or ""
        out = []
        for uid, rec in chosen:
            name = (rec.get("display_name") or uid)
            if needle and needle not in name.lower() and needle not in uid.lower():
                continue
            if dept and (rec.get("job_title") or "") != dept:
                continue
            if mgr and (rec.get("reports_to") or "").lower() != mgr.lower():
                continue
            if loc and (rec.get("location") or "").lower() != loc.lower():
                continue
            out.append((uid, rec))
        out.sort(key=lambda pair: ((pair[1].get("display_name") or pair[0]).casefold(), pair[0]))
        return out

    def _fill_filter_choices(self, chosen):
        def refill(combo, values, label=lambda v: v):
            current = combo.currentData() or ""
            wanted = sorted({v for v in values if v}, key=str.casefold)
            existing = [combo.itemData(i) for i in range(1, combo.count())]
            if existing == wanted:
                return
            combo.blockSignals(True)
            while combo.count() > 1:
                combo.removeItem(1)
            for v in wanted:
                combo.addItem(label(v), v)
            index = combo.findData(current)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)
        refill(self.filter_department, [r.get("job_title") for _, r in chosen])
        refill(self.filter_manager, [r.get("reports_to") for _, r in chosen],
               label=lambda v: people.display_name(v))
        refill(self.filter_location, [r.get("location") for _, r in chosen])
        # A filter with one choice filters nothing (a lead saw only themselves).
        self.filter_manager.setVisible(self.filter_manager.count() > 2)

    @on_database_error
    def refresh_team_view(self, reload=True):
        """
        The month grid. Keeps the selected people and the scroll position
        across the refresh: HR used to lose their place twice a minute.
        reload=False: only the search or a filter changed - the people, the
        month and the leave already read are used again.
        """
        if self.team_table is None:
            return
        from slate.gui.components.table_tools import KeepSelection
        with KeepSelection(self.team_table):
            self._fill_team_view(reload)
        self.team_empty.refresh()
        self._report_unread()

    def _fill_team_view(self, reload=True):
        from slate.gui.components.table_tools import KEY_ROLE
        from slate.gui.core.icons import icon
        year, month = self.team_month()
        days = calendar.monthrange(year, month)[1]
        now_dt = datetime.now()
        today = now_dt.date()
        first, last = date(year, month, 1), date(year, month, days)

        cached = getattr(self, "_team_read", None)
        if reload or not cached or cached[0] != (year, month):
            self.lbl_last_refresh.setText("Updated %s" % now_dt.strftime("%H:%M"))
            cached = ((year, month), self.user_manager.get_all_users() or {},
                      self.attendance.get_full_month_data(year, month),
                      self._leave(first, last))
            # A failed leave read is not kept for the next filter change.
            self._team_read = None if self._unread else cached
        _, users, data, leave = cached
        chosen = self.team_people(year, month, users)
        self._team_users = dict(chosen)
        user_ids = [uid for uid, _ in chosen]
        self._team_row_user_ids = list(user_ids)
        self._team_data = data
        self._team_leave = leave
        styles = state_styles()

        stats_cols = list(self.TEAM_STATS)
        day_cols = ["%02d\n%s" % (i, date(year, month, i).strftime("%a")) for i in range(1, days + 1)]
        self.team_table.clear()
        self.team_table.setColumnCount(len(stats_cols) + len(day_cols))
        self.team_table.setHorizontalHeaderLabels(stats_cols + day_cols)
        self.team_table.setRowCount(len(user_ids))
        style_table(self.team_table, {c: ("fixed", 62) for c in range(len(stats_cols))},
                    multi_select=True, select_rows=False, row_height=40)
        # The names stay down the side while the days scroll - at a fixed
        # width, cut with "...", the full name in the tooltip. Sized to the
        # longest name it took 1,030 px for one long name.
        header = self.team_table.verticalHeader()
        header.setVisible(True)
        header.setFixedWidth(200)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.team_table.setStyleSheet(f"QTableWidget {{ font-size: {Gate.SIZE_SM}px; }}")
        from PySide6.QtGui import QFontMetrics
        metrics = QFontMetrics(header.font())

        # Days nobody is expected in, and today, marked in the header.
        studio_holidays = self._holidays(year, "All")
        marks = {}
        for i in range(1, days + 1):
            d = date(year, month, i)
            col = len(stats_cols) + i - 1
            head = self.team_table.horizontalHeaderItem(col)
            if d == today:
                marks[col] = "today"
                if head is not None:
                    head.setToolTip("Today")
            elif not lp.is_working_day(d, studio_holidays):
                marks[col] = "off"
                if head is not None:
                    head.setToolTip("Weekly off" if lp.is_weekly_off(d) else
                                    "Holiday: %s" % (self._holiday_name(d, "All") or "public holiday"))
        self.team_table.horizontalHeader().marks = marks

        for r, uid in enumerate(user_ids):
            rec = self._team_users[uid] or {}
            name = rec.get('display_name') or uid
            # Cut with "..." here: the themed header draws its text unclipped.
            shown = metrics.elidedText(name, Qt.TextElideMode.ElideRight, 200 - 2 * Gate.CELL_PADDING - 4)
            name_item = QTableWidgetItem(shown)
            name_item.setToolTip("%s (%s)" % (name, uid))
            self.team_table.setVerticalHeaderItem(r, name_item)

            # Attendance is stored with lower-case user ids.
            user_log = data.get(uid.lower(), {})
            location = rec.get("location") or ""
            holidays = self._holidays(year, location)
            person_leave = leave.get(uid.lower(), {})
            summary = rules.month_summary(user_log, year, month, holidays, person_leave,
                                          self._expected(uid, rec), now_dt, today)
            for d, cell in enumerate(summary["days"], 1):
                day, entry, state = cell["day"], cell["entry"], cell["state"]
                day_leave = cell["leave"]
                col = len(stats_cols) + d - 1
                sessions = rules.sessions_of(entry)

                text = ""
                if state == rules.MISSING_IN:
                    text = "no in\n%s" % entry.get("out", "")
                elif sessions:
                    first_in = rules.hhmm(sessions[0][0])
                    last_out = entry.get("out") or ""
                    text = first_in + ("\n" + last_out if last_out else
                                       ("\nno out" if state == rules.MISSING_OUT else ""))
                elif state == rules.LEAVE:
                    text = "Leave\n%s" % day_leave["type"]
                elif state == rules.ABSENT:
                    text = "Absent"
                item = QTableWidgetItem(text)
                item.setBackground(QColor(styles[state][0]))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if entry.get("wfh"):
                    # Working from home: italic (and said in the tooltip) -
                    # a "WFH" word pushed the out time out of the cell.
                    font = item.font()
                    font.setItalic(True)
                    item.setFont(font)
                if state == rules.ABSENT:
                    item.setForeground(QColor(Gate.BAD))
                if day_leave and sessions:
                    # A punch on a day of approved leave: one of them is wrong.
                    item.setForeground(QColor(Gate.WARN))
                if entry.get("edited_by") or entry.get("admin_edit"):
                    item.setIcon(icon("edit", Gate.TEXT_DIM, 12))
                elif entry.get("auto_logout"):
                    item.setIcon(icon("clock", Gate.TEXT_DIM, 12))
                item.setToolTip(self._day_tooltip(entry, day, state, day_leave, location,
                                                  late=cell["late"]))
                self.team_table.setItem(r, col, item)

            def stat(value, kind=None, text=None):
                it = QTableWidgetItem(text if text is not None else str(value))
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                # Coloured only when there is something to see - 'Days' was
                # green for people with 0 days.
                if kind and value:
                    colour = {"ok": Gate.OK, "bad": Gate.BAD, "warn": Gate.WARN,
                              "accent": Gate.ACCENT}[kind]
                    it.setForeground(QColor(colour))
                return it

            first_item = stat(summary["present"], "ok")
            first_item.setData(KEY_ROLE, uid)          # the row is this person, whatever order
            self.team_table.setItem(r, 0, first_item)
            self.team_table.setItem(r, 1, stat(summary["late"], "warn"))
            self.team_table.setItem(r, 2, stat(summary["absent"], "bad"))
            self.team_table.setItem(r, 3, stat(summary["missing"], "bad"))
            self.team_table.setItem(r, 4, stat(summary["hours"], None, "%.1f" % summary["hours"]))
            self.team_table.setItem(r, 5, stat(summary["wfh"], "accent"))

        # Wide enough for "10:00" beside the corrected / auto punch-out icon:
        # at 64 px those days - the ones HR check - read "10...".
        self.team_table.horizontalHeader().setDefaultSectionSize(76)
        for c in range(len(stats_cols)):
            self.team_table.setColumnWidth(c, 62)
        hh = self.team_table.horizontalHeader()
        frame = self.team_table.frameWidth()
        self.team_corner.setGeometry(frame, frame, header.width(),
                                     max(hh.height(), hh.sizeHint().height()))
        self.team_corner.raise_()

    def on_cell_double_click(self, row, col):
        """HR correcting a day. Supervisors see their team read-only."""
        if not self.is_admin():
            return
        stats = len(self.TEAM_STATS)
        if col < stats:
            return
        day_idx = col - stats + 1
        year, month = self.team_month()
        if day_idx < 1 or day_idx > calendar.monthrange(year, month)[1]:
            return
        target = date(year, month, day_idx)
        if target > date.today():
            self._notify("A day that has not happened yet cannot be edited.", "warning")
            return
        if row >= len(self._team_row_user_ids):
            return
        uid = self._team_row_user_ids[row]
        rec = self._team_users.get(uid) or {}
        entry = self._team_data.get(uid.lower(), {}).get(f"{day_idx:02d}", {})
        sessions = [(rules.hhmm(a), rules.hhmm(b)) for a, b in rules.sessions_of(entry)]
        t_in = sessions[0][0] if sessions else ""
        self.show_edit_dialog(uid, rec.get('display_name') or uid, year, month, day_idx,
                              t_in, entry.get("out", ""), overnight=entry.get("overnight", False),
                              has_record=bool(entry), sessions=sessions,
                              auto_cutoff=(entry.get("cutoff") or entry.get("out", ""))
                              if entry.get("auto_logout") else "",
                              leave=getattr(self, "_team_leave", {}).get(uid.lower(), {}).get(target))

    def show_edit_dialog(self, uid, name, year, month, day, t_in, t_out, overnight=False,
                         has_record=False, sessions=None, auto_cutoff="", leave=None):
        target = date(year, month, day)
        dialog = EditPunchDialog(name, target, t_in, t_out, overnight, has_record, self,
                                 sessions=sessions, auto_cutoff=auto_cutoff, leave=leave)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        values = dialog.values()
        if dialog.cleared:
            ok, msg = self.attendance.clear_day(uid, target, self.username, values["reason"])
        else:
            ok, msg = self.attendance.update_record(
                uid, year, month, day, values["in"], values["out"],
                editor=self.username, reason=values["reason"], overnight=values["overnight"],
                sessions=values["sessions"])
        if ok:
            self.refresh_team_view()
            self._notify("%s's %s %s." % (name, format_date(target),
                                         "cleared" if dialog.cleared else "corrected"), "success")
        else:
            self._notify(msg or "The change was not saved.", "error", details=msg)
        return ok

    def status_bar_msg(self, msg):
        try:
            self.window().statusBar().showMessage(msg, 3000)
        except (AttributeError, RuntimeError):
            pass  # Status bar not available

    # ---------------------------------------------------------- HR actions
    def edit_holidays(self):
        """
        Open the studio's holiday calendar, on the year being looked at, and
        show its changes here as soon as it closes - the grid used to keep the
        old shading until Slate was restarted.
        """
        from slate.gui.tabs.leave_admin import HolidayCalendarDialog
        dialog = HolidayCalendarDialog(parent=self, year=self.spin_year.value())
        dialog.exec()
        self._holiday_cache = {}
        try:
            self.refresh_team_view()
            self.refresh_personal_view()
        except Exception as exc:
            logging.debug("Could not refresh after editing the holidays: %s", exc)

    def import_biometric(self):
        """
        Bring in the biometric machine's export.

        Any machine: the dialog works out the columns from the file and keeps
        the answer in the studio's shared folder, so the second import from
        the same machine is one click. Every code the machine uses that Slate
        does not know is listed for HR to match, never guessed.
        """
        from slate.gui.dialogs.biometric_import_dialog import BiometricImportDialog
        try:
            known_ids = list(self.user_manager.get_all_users().keys())
        except Exception as exc:
            logging.warning("Could not list users for the biometric import: %s", exc)
            known_ids = []
        dialog = BiometricImportDialog(self.attendance, known_ids, parent=self)
        if dialog.exec() and dialog.result_summary:
            result = dialog.result_summary
            written = result.get("written", 0)
            text = "Imported %s of attendance from the machine" % plural(written, "day")
            if result.get("unchanged"):
                text += "; %s already correct" % plural(result["unchanged"], "day")
            self._notify(text + ".", "success" if written else "info")
        self.refresh_team_view()

    # ---------------------------------------------------------------- export
    def export_csv(self):
        """The month as an Excel workbook, for the people the grid shows (async)."""
        year, month = self.team_month()
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self._notify("Excel export needs the openpyxl package, which this install does not have.",
                         "error")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export attendance", f"Attendance_{year}_{month:02d}.xlsx", "Excel Files (*.xlsx)")
        if not path:
            return
        chosen = self.team_people(year, month)
        days = calendar.monthrange(year, month)[1]
        first, last = date(year, month, 1), date(year, month, days)
        self._unread = None
        rows = []
        for uid, rec in chosen:
            location = rec.get("location") or ""
            rows.append({"username": uid, "name": rec.get("display_name") or uid,
                         "holidays": self._holidays(year, location),
                         "expected": self._expected(uid, rec)})
        data = self.attendance.get_full_month_data(year, month)
        leave = self._leave(first, last, [u for u, _ in chosen])
        studio_holidays = self._holidays(year, "All")
        what, self._unread = self._unread, None
        if what:
            self._notify("The export was not made: %s could not be read, so those days would "
                         "have gone into the file as Absent. Try again in a moment." % what,
                         "error")
            return

        self._cleanup_export_worker()
        self._export_worker = ExcelExportWorker(path, year, month, rows, data, leave,
                                                now=datetime.now(),
                                                studio_holidays=studio_holidays)
        self._export_worker.finished_export.connect(self._on_export_finished)
        self._export_worker.finished.connect(self._on_export_worker_done)
        self._export_worker.finished.connect(self._export_worker.deleteLater)
        self._export_worker.start()
        self.status_bar_msg("Exporting attendance\u2026")

    def _on_export_finished(self, success, message):
        if self.sender() is not self._export_worker and self.sender() is not None:
            return
        if not success:
            self._notify("The attendance export failed.", "error", details=message)
            return
        self.show_export_done(message)

    def show_export_done(self, path):
        """'Saved Attendance_2026_09.xlsx' with Open and Show in folder."""
        import os
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Export attendance")
        box.setText("Saved %s" % os.path.basename(path))
        box.setInformativeText(os.path.dirname(path))
        open_btn = box.addButton("Open", QMessageBox.ButtonRole.AcceptRole)
        folder_btn = box.addButton("Show in folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        elif clicked is folder_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def _on_export_worker_done(self):
        if self.sender() is self._export_worker:
            self._export_worker = None

    def _cleanup_export_worker(self, timeout_ms: int = 2000):
        worker = self._export_worker
        if worker is None:
            return
        if worker.isRunning():
            worker.requestInterruption()
            worker.wait(timeout_ms)
        try:
            worker.deleteLater()
        except RuntimeError as exc:
            logging.debug("Attendance export worker deleteLater skipped: %s", exc)
        self._export_worker = None

    def closeEvent(self, event):
        if hasattr(self, "personal_refresh_timer") and self.personal_refresh_timer.isActive():
            self.personal_refresh_timer.stop()
        self._cleanup_export_worker(timeout_ms=2000)
        super().closeEvent(event)
