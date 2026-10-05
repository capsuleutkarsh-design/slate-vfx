"""
Home: the first screen of the day.

Your shots (or your punch and the leave pulse), the studio figures you can act
on, and quick links to the screens you actually have - over the animated
background, which gets out of the way once the data is in.
"""
import json
import logging
import os
import time
from datetime import date, datetime, timedelta

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QStackedLayout, QGraphicsDropShadowEffect, QFrame, QSizePolicy, QScrollArea,
)
from PySide6.QtCore import Qt, QUrl, Signal, QThread, Slot
from PySide6.QtGui import QColor
from slate.core.infra.gate import Gate as _ThemeGate


class _HomeGate:
    """
    Home's colours: always the Dark palette.

    Home draws over the animated sky, which is dark in every theme. With the
    Light theme's tokens the greeting and the panel text came out as dark text
    on that dark sky and could hardly be read. Everything else (tint, mix,
    sizes, fonts) is the real Gate; overlay() is a white wash, as on Dark.
    """

    def __init__(self):
        self._dark = _ThemeGate.palette("Dark")

    def __getattr__(self, name):
        if name in self._dark:
            return self._dark[name]
        return getattr(_ThemeGate, name)

    def overlay(self, alpha: float) -> str:
        return _ThemeGate.tint("#" + "F" * 6, alpha)

    @property
    def STATUS(self):
        current = _ThemeGate.palette(_ThemeGate.MODE)
        by_value = {}
        for key, value in current.items():
            by_value.setdefault(value, key)
        return {status: self._dark.get(by_value.get(colour, ""), colour)
                for status, colour in _ThemeGate.STATUS.items()}


Gate = _HomeGate()

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False

from ..components.qt_safety import safe_single_shot
from slate.core.domain.central_attendance import CentralAttendance
from slate.core.infra.database_manager import database_manager
from slate.gui.core.offline_notice import TITLE as OFFLINE_TITLE, BODY as OFFLINE_BODY, on_database_error

try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""


# ----------------------------------------------------------------- quick links
#
# Every quick link names the screen it opens by its sidebar label - one list,
# so a tile can never point at a screen called something else ("Leave
# Management" and "Ticketing" did, and did nothing). A tile is only built when
# that screen is in this person's sidebar.
VFX_TILES = (
    ("VFX Dashboard", "Every shot, status and version", "chart"),
    ("CAP Rename", "Rename delivered files", "tag"),
    ("Build & Ingest", "Project structure and scans", "folder"),
    ("Timeline Viewer", "Reel lineup in RV", "clapper"),
    ("Stock Viewer", "Browse the stock library", "film"),
    ("Scheduling", "Milestones and the schedule", "calendar"),
)
OPS_TILES = (
    ("Attendance", "Your punches and your month", "clock"),
    ("Leave", "Ask for leave and see your balance", "leave"),
    ("IT Support", "Report a problem and follow it up", "ticket"),
    ("Users & Roles", "People, roles and permissions", "users"),
    ("Hardware", "Machines and who has them", "monitor"),
    ("Joining & Leaving", "Checklists for joiners and leavers", "handshake"),
)
MAX_TILES = 6
# For the people who work the other side of those two screens.
DECIDER_SUBTITLES = {"Leave": "Ask for leave, or decide requests",
                     "IT Support": "Report a problem, or work the queue"}

# Shot statuses that are finished, and those that wait for a review.
DONE_STATUSES = {"APPROVED", "DONE", "FINAL", "DELIVERED", "OMIT", "OMITTED"}
REVIEW_STATUSES = {"REVIEW", "SENT FOR REVIEW", "PENDING REVIEW", "INTERNAL REVIEW", "CLIENT REVIEW"}

# How fresh a workstation's heartbeat must be for its person to count as online.
ONLINE_WITHIN_SECONDS = 120

UPCOMING_LEAVE_DAYS = 14


def _value(row, key, index=0):
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[index]
    except (IndexError, TypeError, KeyError):
        return None


def _count(row, index=0):
    value = _value(row, "c", index)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def shot_artists(data: dict) -> set:
    """Everybody named on a shot: the lead, every department's artist, the history."""
    names = set()
    if not isinstance(data, dict):
        return names
    for key in ("assigned_artist", "artist"):
        if data.get(key):
            names.add(str(data[key]))
    departments = data.get("departments") or {}
    if isinstance(departments, dict):
        for dept in departments.values():
            if isinstance(dept, dict) and dept.get("artist"):
                names.add(str(dept["artist"]))
    for entry in data.get("artist_history") or []:
        if isinstance(entry, dict) and entry.get("artist"):
            names.add(str(entry["artist"]))
    return {n.strip().lower() for n in names if n.strip()}


def db_today(db=None) -> date:
    """Today by the database's clock - the one punches are written with."""
    # CentralAttendance's clock, not a copy of it: the copy read UTC and from
    # midnight to 05:30 IST Home read yesterday's punch.
    try:
        return CentralAttendance(db or database_manager).server_today() or date.today()
    except RuntimeError:                  # the database gave no time back
        return date.today()


def people_online(report_dir, now=None) -> int:
    """
    People with Slate open: the workstations whose heartbeat (LiveReporter)
    is fresh, counted by person. A locked screen is not somebody working.
    It used to count attendance punches, which the VFX app never makes.
    """
    now = now if now is not None else time.time()
    names = set()
    for name in os.listdir(report_dir):
        if not name.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(report_dir, name), encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        user = str(data.get("user") or "").strip()
        if not user or user.lower() in ("locked", "unknown"):
            continue
        try:
            fresh = now - float(data.get("last_seen") or 0) <= ONLINE_WITHIN_SECONDS
        except (TypeError, ValueError):
            fresh = False
        if fresh:
            names.add(user.lower())
    return len(names)


def _time_text(value) -> str:
    """'19:04' from a time, a datetime or 'HH:MM:SS'."""
    if value is None or value == "":
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return str(value)[:5]


def _minutes_since(value, now=None) -> int:
    now = now or datetime.now()
    try:
        if hasattr(value, "hour"):
            start = now.replace(hour=value.hour, minute=value.minute, second=0, microsecond=0)
        else:
            hh, mm = str(value).split(":")[:2]
            start = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except (TypeError, ValueError):
        return 0
    return max(0, int((now - start).total_seconds() // 60))


def punch_text(punch_in, punch_out, now=None) -> str:
    """'In since 19:04 (2 h 10 min)', 'Punched out at 18:30', 'Not punched in today'."""
    if punch_in and not punch_out:
        minutes = _minutes_since(punch_in, now)
        hours, mins = divmod(minutes, 60)
        so_far = f"{hours} h {mins} min" if hours else f"{mins} min"
        return f"In since {_time_text(punch_in)} ({so_far})"
    if punch_in and punch_out:
        return f"Punched out at {_time_text(punch_out)}"
    return "Not punched in today"


def greeting(name: str, now=None, whole_name: bool = False) -> str:
    """
    'Good morning, Priya' - first name, by the time of day. whole_name for an
    account that is not a person ('System Admin' was greeted as 'System').
    """
    hour = (now or datetime.now()).hour
    part = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    words = str(name or "").split()
    if not words:
        return f"Good {part}"
    return f"Good {part}, {' '.join(words) if whole_name else words[0]}"


class QuickActionBtn(QFrame):
    """
    A quick link. One stylesheet with a :hover and a [pressed] rule: a click
    used to replace the sheet and the tile stayed lit with no hover after.
    Reachable from the keyboard (Tab, then Enter or Space).
    """
    clicked = Signal()

    def __init__(self, title, subtitle, glyph=""):
        super().__init__()
        self.title = title
        self.glyph = glyph
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setMinimumHeight(54)
        self.setObjectName("QuickBtn")
        self.setProperty("pressed", False)
        self.setStyleSheet(f"""
            QFrame#QuickBtn {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {Gate.overlay(0.08)}, stop:1 {Gate.overlay(0.02)});
                border: 1px solid {Gate.overlay(0.1)};
                border-top: 1px solid {Gate.overlay(0.2)};
                border-radius: 10px;
            }}
            QFrame#QuickBtn:hover {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {Gate.overlay(0.15)}, stop:1 {Gate.overlay(0.06)});
                border: 1px solid {Gate.overlay(0.3)};
                border-top: 1px solid {Gate.overlay(0.5)};
            }}
            QFrame#QuickBtn:focus {{ border: 1px solid {Gate.ACCENT}; }}
            QFrame#QuickBtn[pressed="true"] {{ background: {Gate.overlay(0.05)}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(9)
        if glyph:
            from ..core.icons import icon as draw_icon, has_icon
            if has_icon(glyph):
                mark = QLabel()
                mark.setPixmap(draw_icon(glyph, Gate.TEXT, 15).pixmap(15, 15))
                mark.setStyleSheet("background: transparent; border: none;")
                mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                title_row.addWidget(mark)

        lbl_title = QLabel(title)
        lbl_title.setStyleSheet(f"background: transparent; border: none; color: {Gate.TEXT}; font-size: 13px; font-weight: 700;")
        lbl_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        title_row.addWidget(lbl_title)
        title_row.addStretch(1)

        lbl_sub = QLabel(subtitle)
        lbl_sub.setWordWrap(True)
        lbl_sub.setStyleSheet(f"background: transparent; border: none; color: {Gate.TEXT_2}; font-size: 11px;")
        lbl_sub.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        layout.addLayout(title_row)
        layout.addWidget(lbl_sub)

    def _set_pressed(self, pressed):
        self.setProperty("pressed", bool(pressed))
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_pressed(True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_pressed(False)
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class ClickableRow(QFrame):
    """A task row that opens its shot."""
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("TaskRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(
            f"QFrame#TaskRow {{ background: transparent; border-radius: 6px; }}"
            f"QFrame#TaskRow:hover {{ background: {Gate.overlay(0.06)}; }}"
            f"QFrame#TaskRow:focus {{ border: 1px solid {Gate.ACCENT}; }}")

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class HomeLoaderWorker(QThread):
    """
    Reads what Home shows, off the UI thread. No pretend steps and no sleeps:
    the progress messages are what it is actually doing.
    """
    progress = Signal(int, str)
    data_loaded = Signal(list, dict)
    telemetry_loaded = Signal(dict)
    load_failed = Signal(str)

    def __init__(self, username, app_context, parent=None, mode="vfx", identities=(),
                 figures=(), db=None, report_dir=None, leave_stage=""):
        super().__init__(parent)
        # "HR", "Supervisor" or "": whose leave this person may see on Home
        # (workplace_access.leave_stage, the Leave queue's own rule).
        self.leave_stage = leave_stage
        # The login, not the name on screen. Attendance rows are keyed by the
        # username; looking them up by display name meant Home and the
        # Attendance tab kept two separate records for one person.
        self.username = username
        self.app_context = app_context
        self.mode = mode
        self.identities = {str(i).strip().lower() for i in identities if str(i or "").strip()}
        self.figures = tuple(figures)
        self.db = db
        self.report_dir = report_dir
        self._shot_rows_cache = None

    def _db(self):
        return self.db or database_manager

    def _stopped(self):
        return self.isInterruptionRequested()

    def run(self):
        try:
            self._run()
        except DatabaseUnavailableError as exc:
            # Said on screen, not raised into the thread where nobody hears it
            # (Home used to sit on its intro with "Loading..." for ever).
            self.load_failed.emit(str(exc))

    def _run(self):
        db = self._db()
        self._shot_rows_cache = None
        self.progress.emit(15, "Checking the database")
        if hasattr(db, "ping_sync"):
            try:
                db.ping_sync()
            except DatabaseUnavailableError:
                raise
            except Exception as exc:
                logging.debug("Home: ping failed: %s", exc)
        if self._stopped():
            return

        items, punch_status = [], {}
        if "punch" in self.figures:
            self.progress.emit(35, "Reading today's punch")
            punch_status = self.todays_punch()
        if "pulse" in self.figures:
            self.progress.emit(50, "Reading leave")
            items = self.leave_pulse()
        if "shots" in self.figures:
            self.progress.emit(50, "Loading your shots")
            items = self.my_shots()
        if self._stopped():
            return

        self.progress.emit(75, "Loading studio figures")
        self.telemetry_loaded.emit(self.telemetry())
        if self._stopped():
            return
        self.progress.emit(100, "Ready")
        self.data_loaded.emit(items, punch_status)

    # ------------------------------------------------------------ readers
    def todays_punch(self) -> dict:
        uid = (self.username or "").lower().strip()
        if not uid:
            return {}
        # CentralAttendance.today_state - the Attendance tab's reading of the
        # day, by the database's date - rather than Home's own copy of its SQL.
        try:
            state = CentralAttendance(self._db()).today_state(uid)
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logging.debug("Home: today's punch not read: %s", exc)
            return {}
        if state.get("state") not in ("working", "done"):
            return {}
        # A second punch-in the same day is a new session: "In since" is the
        # open session's start, not the first arrival - lunch is not time worked.
        return {"punch_in": state["since"], "punch_out": state["out"] or None,
                "first_in": state["in"], "sessions": state["sessions"]}

    def _leave_rows(self) -> list:
        """
        The leave this person may see: HR everybody's, a supervisor their
        reports', everybody else only their own - the Leave queue's rule. It
        used to be the whole studio's for every artist, sick leave included.
        """
        from slate.core.infra.leave_repository import LeaveRepository
        repo = LeaveRepository(self._db())
        if self.leave_stage:
            return repo.stage_requests(self.username, self.leave_stage)
        return repo.for_user(self.username)

    def leave_pulse(self) -> list:
        """Current and coming leave (not rejected or cancelled), soonest first."""
        from slate.core.domain import leave_policy as lp
        from slate.core.domain.dates import format_range
        from slate.core.infra.leave_repository import as_date
        db = self._db()
        try:
            today = db_today(db)
            rows = [r for r in self._leave_rows()
                    if lp.normalise_status(r.get("status")) in lp.LIVE_STATUSES
                    and (as_date(r.get("end_date")) or today) >= today]
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logging.debug("Home: leave pulse not read: %s", exc)
            return []
        rows.sort(key=lambda r: (str(as_date(r.get("start_date")) or ""), r.get("id") or 0))
        from slate.core.domain import people
        me = self.username.strip().lower()
        items = []
        for row in rows[:5]:
            uid = str(row.get("user_id") or "")
            own = uid.strip().lower() == me
            when = format_range(row.get("start_date"), row.get("end_date"))
            # The kind of leave (Sick, Medical) is the person's own business
            # and HR's - a supervisor sees that a report is away, not why.
            if own:
                title = f"You - {row.get('type') or 'Leave'}, {when}"
            elif self.leave_stage == "HR":
                title = f"{people.display_name(uid, db)} - {row.get('type') or 'Leave'}, {when}"
            else:
                title = f"{people.display_name(uid, db)} - away, {when}"
            items.append({"title": title, "status": lp.normalise_status(row.get("status"))})
        return items

    def _my_shot_rows(self):
        """Every shot (in an active project) this person is named on, newest first."""
        if self._shot_rows_cache is not None:
            return self._shot_rows_cache
        rows = self._db().execute_query(
            "SELECT s.shot_name, s.status, s.data_json, s.project_code FROM tracking_shots s "
            "JOIN tracking_projects p ON p.code = s.project_code "
            "WHERE p.active = 1 ORDER BY s.last_updated DESC LIMIT 3000",
            fetch="all") or []
        mine = []
        for row in rows:
            raw = _value(row, "data_json", 2)
            try:
                data = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
            except (TypeError, ValueError):
                data = {}
            if shot_artists(data) & self.identities:
                status = _value(row, "status", 1) or data.get("status") or ""
                # The project goes with the shot: the dashboard opens it
                # before selecting the shot, and two shows can both have SH010.
                mine.append({"title": _value(row, "shot_name", 0), "status": str(status), "shot": True,
                             "project": str(_value(row, "project_code", 3) or "")})
        self._shot_rows_cache = mine
        return mine

    def my_shots(self) -> list:
        """
        The five shots most recently changed that name this person. It used to
        be the first five rows of the table, the same for everybody.
        """
        try:
            return self._my_shot_rows()[:5]
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logging.debug("Home: shots not read: %s", exc)
            return []

    def telemetry(self) -> dict:
        """Each figure on its own: one failing shows 'n/a', the rest still show."""
        out = {}
        for key in self.figures:
            reader = getattr(self, f"_figure_{key}", None)
            if reader is None:
                continue
            try:
                out[key] = reader()
            except DatabaseUnavailableError:
                raise
            except Exception as exc:
                logging.debug("Home: figure %s not read: %s", key, exc)
                out[key] = None
        return out

    def _figure_active_projects(self):
        return _count(self._db().execute_query(
            "SELECT COUNT(*) AS c FROM tracking_projects WHERE active = 1", fetch="one"))

    def _figure_my_open_shots(self):
        return sum(1 for s in self._my_shot_rows() if s["status"].strip().upper() not in DONE_STATUSES)

    def _figure_my_in_review(self):
        return sum(1 for s in self._my_shot_rows() if s["status"].strip().upper() in REVIEW_STATUSES)

    def _figure_waiting_review(self):
        placeholders = ", ".join(["%s"] * len(REVIEW_STATUSES))
        # Active projects only, like every other figure here: a finished show
        # with shots left in Review inflated this for ever.
        return _count(self._db().execute_query(
            "SELECT COUNT(*) AS c FROM tracking_shots s JOIN tracking_projects p ON p.code = s.project_code "
            "WHERE p.active = 1 AND UPPER(COALESCE(s.status, '')) IN (%s)"
            % placeholders, tuple(sorted(REVIEW_STATUSES)), fetch="one"))

    def _figure_open_tickets(self):
        # The IT desk's own count (TicketRepository), so Home and the queue
        # agree on "open" whatever the casing a status was stored in.
        from slate.core.infra.ticket_repository import TicketRepository
        return TicketRepository(self._db()).open_count()

    def _figure_licence_renewals(self):
        """
        Licences expired or renewing inside the studio's renewal window, so a
        renewal is not missed because nobody opened the Licences screen.
        The names go in the tooltip.
        """
        from slate.core.domain import licence_compliance as lc
        from slate.core.infra.licence_repository import LicenceRepository
        db = self._db()
        window = lc.renewal_window(db)
        today = db_today(db)
        due = []
        for row in LicenceRepository(db).licences():
            left = lc.days_until(row.get("expiration_date"), today)
            if lc.is_renewal_due(left, window):
                due.append((left, str(row.get("software_name") or "A licence")))
        due.sort()
        lines = ["%s - %s" % (name, lc.renewal_phrase(left)) for left, name in due[:6]]
        if len(due) > 6:
            lines.append("… and %d more on the Licences screen" % (len(due) - 6))
        tip = "\n".join(lines)
        return {"value": len(due), "tip": tip, "warn": bool(due)}

    def _figure_upcoming_leave(self):
        # Approved, starting after today and within two weeks - not leave
        # already running, nor leave a year away. Of the people this person sees.
        from slate.core.domain import leave_policy as lp
        from slate.core.infra.leave_repository import as_date
        today = db_today(self._db())
        last = today + timedelta(days=UPCOMING_LEAVE_DAYS)
        return sum(1 for r in self._leave_rows()
                   if lp.normalise_status(r.get("status")) == lp.STATUS_APPROVED
                   and today < (as_date(r.get("start_date")) or today) <= last)

    def _figure_pending_leave(self):
        """What waits on this person's decision - as their Leave queue counts it."""
        from slate.core.domain import leave_policy as lp
        waiting = lp.STAGE_DECIDES.get(self.leave_stage, ())
        me = self.username.strip().lower()
        return sum(1 for r in self._leave_rows()
                   if lp.normalise_status(r.get("status")) in waiting
                   and str(r.get("user_id") or "").strip().lower() != me)

    def _figure_people_online(self):
        report_dir = self.report_dir
        if report_dir is None:
            from slate.core.infra.server_hub import ServerHub
            report_dir = ServerHub().get_livestatus_dir()
        return people_online(report_dir)


# What each figure is called on screen.
FIGURE_LABELS = {
    "my_open_shots": "My open shots",
    "my_in_review": "My shots in review",
    "active_projects": "Active projects",
    "waiting_review": "Shots waiting for review",
    "people_online": "People online",
    "pending_leave": "Leave to decide",
    "open_tickets": "Open IT tickets",
    "licence_renewals": "Licences to renew",
    "upcoming_leave": "Leave in the next 2 weeks",
}
# Figures that mean somebody has something to do while they are above zero.
ACTION_FIGURES = {"pending_leave", "open_tickets", "licence_renewals"}


class HomeTab(QWidget):
    """
    Cinematic Home: the animated background with the day's information over
    it. mode='vfx' (production), 'ops' (operations, with the punch panel) or
    'all' (the full suite: built from what this person has).
    """
    AUTO_REFRESH_SECONDS = 60

    def __init__(self, user_data=None, app_context=None, main_window=None, parent=None, mode="vfx"):
        super().__init__(parent)
        self.mode = (mode or "vfx").lower()
        self.main_window = main_window
        self.user_data = user_data or {}
        self.app_context = app_context
        # The name to greet, from the account - no "Artist" / "Operator".
        self.user_display_name = str(self.user_data.get('display_name')
                                     or self.user_data.get('username') or "").strip()
        # What the attendance record is keyed by, which is not what is greeted
        # on screen. The Attendance tab uses exactly this.
        self.username = str(self.user_data.get('user_id')
                            or self.user_data.get('username') or '').strip()
        # An account that is not a person (admin, tester) has no working day
        # to punch, and is greeted by its whole name.
        self.is_service = bool(self.user_data.get("is_service"))
        # The full suite used to build the VFX Home for everybody, so HR and
        # IT had no punch panel there.
        self.has_punch_panel = not self.is_service and (self.mode == "ops" or (
            self.mode == "all" and "Attendance" in self._available_tabs()))
        # Whose leave this person may see (the Leave queue's rule).
        from slate.core.domain.workplace_access import leave_stage
        self.leave_stage = leave_stage(self._roles(), self._allowed())
        # Made when it is first needed (a punch): building it connects to
        # the database, which slowed every window that opened on Home.
        self.attendance = None
        self._punch_worker = None

        self._is_loaded = False
        self._cinematic_ended = False
        self.loader_worker = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.figures = self._choose_figures()
        self.init_ui()

        # Every minute while Home is on screen (not while it is hidden).
        from ..components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(self, self.refresh, seconds=self.AUTO_REFRESH_SECONDS)

        if os.getenv("HEADLESS_TESTING") != "1":
            if HAS_WEBENGINE:
                self.web_view.loadFinished.connect(lambda ok: self._start_loader())
            else:
                self._start_loader()

    # ------------------------------------------------------------ who / what
    def _roles(self):
        roles = self.user_data.get("roles") or self.user_data.get("role") or []
        return [roles] if isinstance(roles, str) else list(roles)

    def _available_tabs(self):
        """The sidebar labels this person has (empty without a main window)."""
        tc = getattr(self.main_window, "tab_coordinator", None)
        if tc is None:
            return []
        return [e.get("label") for e in tc.nav_items
                if e.get("label") and e.get("permitted", True)
                and not str(e.get("label")).startswith("__HEADER__")]

    def _allowed(self):
        return list(getattr(self.main_window, "allowed_tabs", []) or [])

    def identities(self):
        names = {self.username, self.user_data.get("username"), self.user_data.get("user_id"),
                 self.user_data.get("display_name")}
        return {str(n).strip() for n in names if str(n or "").strip()}

    def _choose_figures(self):
        """
        What Home reads, and which figures it shows: only what this person can
        act on. An artist does not need the studio's open IT tickets; HR does
        not need shots pending review.
        """
        from slate.core.domain import access
        roles = self._roles()
        tabs = set(self._available_tabs())
        no_window = not tabs
        figures = []
        wants_vfx = self.mode == "vfx" or (self.mode == "all" and ("VFX Dashboard" in tabs or no_window))
        wants_ops = self.mode == "ops" or (self.mode == "all" and ("Attendance" in tabs or no_window))
        if wants_vfx:
            figures.append("shots")
            if access.can_view_all_shots(roles):
                figures += ["waiting_review", "active_projects"]
            else:
                figures += ["my_open_shots", "my_in_review", "active_projects"]
        if wants_ops:
            if self.has_punch_panel:
                figures.append("punch")
            if not wants_vfx:
                figures.append("pulse")
            figures.append("people_online")
            if self.leave_stage:
                figures += ["pending_leave", "upcoming_leave"]
            try:
                from slate.core.domain.workplace_access import manages_it
                if manages_it(roles, getattr(self.main_window, "allowed_tabs", []) or []):
                    figures.append("open_tickets")
            except Exception as exc:
                logging.debug("Home: IT check skipped: %s", exc)
        # Licence renewals coming up, for whoever can see Licences (IT, or a
        # Production Head with view_licences) - in any app that has the screen.
        try:
            from slate.core.domain.workplace_access import can_view_licences
            sees = "Licences" in tabs if not no_window else can_view_licences(roles, [])
        except Exception as exc:
            logging.debug("Home: licence check skipped: %s", exc)
            sees = False
        if sees:
            figures.append("licence_renewals")
        return figures

    def stat_figures(self):
        return [f for f in self.figures if f in FIGURE_LABELS]

    # ------------------------------------------------------------ loading
    def _start_loader(self):
        if self.loader_worker is not None and self.loader_worker.isRunning():
            return False
        worker = HomeLoaderWorker(self.username, self.app_context, self, mode=self.mode,
                                  identities=self.identities(), figures=self.figures,
                                  leave_stage=self.leave_stage)
        worker.progress.connect(self._on_load_progress)
        worker.data_loaded.connect(self._on_data_loaded)
        worker.telemetry_loaded.connect(self._update_telemetry_ui)
        worker.load_failed.connect(self._on_load_failed)
        self.loader_worker = worker
        worker.start()
        return True

    def refresh(self):
        """F5, Try again and the minute timer: read everything again."""
        return self._start_loader()

    @Slot(dict)
    def _update_telemetry_ui(self, values: dict):
        for key, label in self.stat_labels.items():
            if key not in (values or {}):
                continue
            value = values[key]
            if value is None:
                self._set_figure(label, "-", Gate.TEXT_DIM, "This figure could not be read just now.")
                continue
            # A figure may come with a tooltip and a warning tone.
            tip, warn = "", False
            if isinstance(value, dict):
                tip, warn = value.get("tip") or "", bool(value.get("warn"))
                value = value.get("value")
            # A count is plain text; amber only for something waiting to be
            # done. Every figure was the success green, a backlog and a zero alike.
            try:
                waiting = key in ACTION_FIGURES and int(value or 0) > 0
            except (TypeError, ValueError):
                waiting = False
            self._set_figure(label, str(value), Gate.WARN if (warn or waiting) else Gate.TEXT, tip)

    @staticmethod
    def _set_figure(label, text, colour, tip=""):
        label.setText(text)
        label.setToolTip(tip)
        label.setStyleSheet(f"background: transparent; border: none; color: {colour}; "
                            "font-size: 21px; font-weight: 800;")

    # ------------------------------------------------------------ build
    def init_ui(self):
        self.stack = QStackedLayout(self)
        self.stack.setStackingMode(QStackedLayout.StackingMode.StackAll)

        # --- LAYER 0: the animated background ---
        if HAS_WEBENGINE:
            self.web_view = QWebEngineView()
            self.web_view.page().setBackgroundColor(QColor(Gate.GROUND))
            self.web_view.setStyleSheet(f"background-color: {Gate.GROUND};")
            # Operations runs its own sky so the two modes are told apart
            # before any text is read.
            assets_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets")
            html_path = os.path.join(assets_dir, "cinematic_bg.html")
            if self.mode == "ops":
                ops_path = os.path.join(assets_dir, "cinematic_bg_ops.html")
                if os.path.exists(ops_path):
                    html_path = ops_path
            if os.path.exists(html_path):
                self.web_view.setUrl(QUrl.fromLocalFile(html_path))
            else:
                self.web_view.setHtml(f"<html><body style='background:{Gate.GROUND};'></body></html>")
            self.web_view.titleChanged.connect(self._on_title_changed)
            self.stack.addWidget(self.web_view)
        else:
            self.web_view = QWidget()
            self.web_view.setStyleSheet(f"background-color: {Gate.GROUND};")
            self.stack.addWidget(self.web_view)

        # --- LAYER 1: the information, in a scroll area so a 768 px window
        # shows all of it (the big figures were cut in half there).
        self.overlay_widget = QScrollArea()
        self.overlay_widget.setObjectName("HomeOverlay")
        self.overlay_widget.setWidgetResizable(True)
        self.overlay_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.overlay_widget.setStyleSheet("QScrollArea#HomeOverlay { background: transparent; border: none; }")
        self.overlay_widget.viewport().setAutoFillBackground(False)
        content = QWidget()
        content.setObjectName("HomeContent")
        content.setStyleSheet("QWidget#HomeContent { background: transparent; }")
        self.overlay_widget.setWidget(content)
        self.overlay_widget.hide()

        # --- LAYER 1.5: click anywhere to skip the intro ---
        self.click_catcher = QPushButton()
        self.click_catcher.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.click_catcher.setStyleSheet("background: rgba(0, 0, 0, 1); border: none;")
        self.click_catcher.setCursor(Qt.CursorShape.PointingHandCursor)
        self.click_catcher.clicked.connect(self._end_cinematic_mode)
        self.stack.addWidget(self.click_catcher)

        overlay_layout = QVBoxLayout(content)
        overlay_layout.setContentsMargins(32, 28, 32, 28)
        overlay_layout.setSpacing(20)

        # Top bar
        top_bar = QHBoxLayout()
        self.greeting_label = QLabel(greeting(self.user_display_name, whole_name=self.is_service))
        self.greeting_label.setStyleSheet(
            f"color: {Gate.TEXT}; font-size: 24px; font-weight: 300; letter-spacing: 1px; background: transparent;")
        top_bar.addWidget(self.greeting_label)
        top_bar.addStretch()

        if self.has_punch_panel:
            self.attendance_panel = self._build_glass_panel()
            att_layout = QHBoxLayout(self.attendance_panel)
            att_layout.setContentsMargins(20, 10, 20, 10)

            status_box = QVBoxLayout()
            self.lbl_punch_status = QLabel("Reading today's punch…")
            self.lbl_punch_status.setStyleSheet(f"color: {Gate.TEXT}; font-size: 14px; background: transparent;")
            fix = QLabel('<a href="fix">Fix a punch</a>')
            fix.setStyleSheet(f"color: {Gate.ACCENT}; font-size: 11px; background: transparent;")
            fix.linkActivated.connect(lambda _href: self._trigger_tab("Attendance"))
            fix.setToolTip("Corrections are made on the Attendance screen")
            self.fix_punch_link = fix
            status_box.addWidget(self.lbl_punch_status)
            status_box.addWidget(fix)

            self.btn_punch_in = self._build_glass_button("PUNCH IN", Gate.OK)
            self.btn_punch_out = self._build_glass_button("PUNCH OUT", Gate.BAD)
            self.btn_punch_in.clicked.connect(lambda: self.do_punch("in"))
            self.btn_punch_out.clicked.connect(lambda: self.do_punch("out"))

            att_layout.addLayout(status_box)
            att_layout.addSpacing(20)
            att_layout.addWidget(self.btn_punch_in)
            att_layout.addWidget(self.btn_punch_out)
            top_bar.addWidget(self.attendance_panel)
        else:
            # Today's date, as plain text. It was a pill drawn like a button
            # ("VFX PRODUCTION HUB") that did nothing and repeated the header.
            from slate.core.domain.dates import format_date
            self.date_label = QLabel(format_date(date.today(), weekday=True))
            self.date_label.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 14px; background: transparent;")
            top_bar.addWidget(self.date_label)

        overlay_layout.addLayout(top_bar)

        # --- MIDDLE: quick launch and the right-hand panels. Side by side on
        # a wide window, one under the other below 1200 px.
        self.middle = QGridLayout()
        self.middle.setHorizontalSpacing(20)
        self.middle.setVerticalSpacing(14)

        self.quick_launch_panel = self._build_glass_panel()
        self.quick_launch_panel.setMinimumWidth(340)
        ql_layout = QVBoxLayout(self.quick_launch_panel)
        ql_layout.setContentsMargins(16, 14, 16, 14)
        ql_title = QLabel("QUICK LAUNCH")
        ql_title.setStyleSheet(f"color: {Gate.ACCENT}; font-size: 12px; font-weight: 800; letter-spacing: 2px; background: transparent; border: none;")
        ql_layout.addWidget(ql_title)

        # One grid with equal columns, so the tiles line up.
        self.tiles = []
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, (label, subtitle, glyph) in enumerate(self.tile_specs()):
            tile = self._build_quick_action_btn(label, subtitle, glyph)
            tile.clicked.connect(lambda label=label: self._trigger_tab(label))
            grid.addWidget(tile, i // 2, i % 2)
            self.tiles.append(tile)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        ql_layout.addLayout(grid)
        if not self.tiles:
            none = QLabel("Your screens are in the sidebar on the left.")
            none.setStyleSheet(f"color: {Gate.TEXT_DIM}; background: transparent;")
            ql_layout.addWidget(none)
        # The title at the top, not floating in the middle of the panel.
        ql_layout.addStretch(1)

        right = QWidget()
        right.setStyleSheet("background: transparent;")
        right_panel_layout = QVBoxLayout(right)
        right_panel_layout.setContentsMargins(0, 0, 0, 0)
        right_panel_layout.setSpacing(14)

        # A. Your shots, or the leave pulse
        tasks_panel = self._build_glass_panel()
        tasks_panel.setMinimumWidth(320)
        tasks_layout = QVBoxLayout(tasks_panel)
        tasks_layout.setContentsMargins(16, 14, 16, 14)
        shows_shots = "shots" in self.figures
        title_row = QHBoxLayout()
        tasks_title = QLabel("MY RECENT SHOTS" if shows_shots
                             else "LEAVE: NOW AND NEXT" if self.leave_stage else "MY LEAVE")
        tasks_title.setStyleSheet(f"color: {Gate.ACCENT}; font-size: 12px; font-weight: 800; letter-spacing: 2px; background: transparent; border: none;")
        title_row.addWidget(tasks_title)
        title_row.addStretch(1)
        if shows_shots:
            tasks_title.setToolTip("Your shots in every active project, newest first")
            see_all = QLabel('<a href="all">See all my shots</a>')
            see_all.setStyleSheet(f"color: {Gate.ACCENT}; font-size: 12px; background: transparent;")
            see_all.linkActivated.connect(lambda _h: self.see_all_shots())
            self.see_all_link = see_all
            title_row.addWidget(see_all)
        tasks_layout.addLayout(title_row)

        self.tasks_container_layout = QVBoxLayout()
        self.tasks_container_layout.setSpacing(4)
        self._note_widget("Loading…")
        tasks_layout.addLayout(self.tasks_container_layout)
        right_panel_layout.addWidget(tasks_panel)

        # B. Studio figures, three to a row so the rows line up.
        stats_panel = self._build_glass_panel()
        stats_panel.setMinimumWidth(320)
        stats_layout = QVBoxLayout(stats_panel)
        stats_layout.setContentsMargins(16, 14, 16, 14)
        stats_title = QLabel("AT A GLANCE")
        stats_title.setStyleSheet(f"color: {Gate.ACCENT}; font-size: 12px; font-weight: 800; letter-spacing: 2px; background: transparent; border: none;")
        stats_layout.addWidget(stats_title)
        stat_grid = QGridLayout()
        stat_grid.setHorizontalSpacing(10)
        stat_grid.setVerticalSpacing(10)
        self.stat_labels = {}
        keys = self.stat_figures()
        columns = self.figure_columns(len(keys))
        for i, key in enumerate(keys):
            box, value = self._build_stat_item(FIGURE_LABELS[key], "-")
            stat_grid.addWidget(box, i // columns, i % columns)
            self.stat_labels[key] = value
        for col in range(columns):
            stat_grid.setColumnStretch(col, 1)
        stats_layout.addLayout(stat_grid)
        right_panel_layout.addWidget(stats_panel)
        right_panel_layout.addStretch(1)
        self.right_column = right

        self._arrange(wide=True)
        overlay_layout.addLayout(self.middle)
        # Panels sit under the greeting; no empty band above, no 100 px
        # spacer below.
        overlay_layout.addStretch(1)
        self.stack.addWidget(self.overlay_widget)

    @staticmethod
    def figure_columns(count: int) -> int:
        """Rows that fill: 4 as 2x2, 7 as 4+3, up to three in one row."""
        if count <= 3:
            return max(1, count)
        return 2 if count == 4 else (count + 1) // 2

    def tile_specs(self):
        """
        The quick links for the screens this person has (at most six). In the
        full suite four production and two operations links, so Leave and IT
        Support are not crowded out by six production screens.
        """
        have = set(self._available_tabs())
        vfx = [s for s in VFX_TILES if s[0] in have]
        ops = [s for s in OPS_TILES if s[0] in have]
        if self.mode == "ops":
            specs = ops
        elif self.mode == "all":
            ops_room = min(len(ops), max(2, MAX_TILES - len(vfx)))
            specs = vfx[:MAX_TILES - ops_room] + ops[:ops_room]
        else:
            specs = vfx
        # The manager's half of a screen is described only to its managers.
        from slate.core.domain.workplace_access import manages_it
        deciders = {"Leave": bool(self.leave_stage),
                    "IT Support": manages_it(self._roles(), self._allowed())}
        return [(label, DECIDER_SUBTITLES[label] if deciders.get(label) else sub, glyph)
                for label, sub, glyph in specs[:MAX_TILES]]

    def _arrange(self, wide: bool):
        """Quick launch beside the panels, or above them on a narrow window."""
        self._wide = wide
        self.middle.removeWidget(self.quick_launch_panel)
        self.middle.removeWidget(self.right_column)
        if wide:
            self.middle.addWidget(self.quick_launch_panel, 0, 0, Qt.AlignmentFlag.AlignTop)
            self.middle.addWidget(self.right_column, 0, 1, Qt.AlignmentFlag.AlignTop)
            self.middle.setColumnStretch(0, 1)
            self.middle.setColumnStretch(1, 1)
        else:
            self.middle.addWidget(self.quick_launch_panel, 0, 0)
            self.middle.addWidget(self.right_column, 1, 0)
            self.middle.setColumnStretch(0, 1)
            self.middle.setColumnStretch(1, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        wide = self.width() >= 1200
        if wide != getattr(self, "_wide", True):
            self._arrange(wide)

    def showEvent(self, event):
        super().showEvent(event)
        if self._cinematic_ended:
            self._run_js("if (window.resumeBackground) { window.resumeBackground(); }")

    def hideEvent(self, event):
        # Nothing to draw while another screen is in front.
        self._run_js("if (window.pauseBackground) { window.pauseBackground(); }")
        super().hideEvent(event)

    def _run_js(self, script):
        if HAS_WEBENGINE and isinstance(getattr(self, "web_view", None), QWebEngineView):
            try:
                self.web_view.page().runJavaScript(script)
            except RuntimeError:
                pass

    def _build_glass_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("GlassPanel")
        # A dark scrim, so the text stays readable whatever the sky does.
        frame.setStyleSheet(f"""
            QFrame#GlassPanel {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                            stop:0 {Gate.tint(Gate.GROUND, 0.78)}, stop:1 {Gate.tint(Gate.GROUND, 0.62)});
                border: 1px solid {Gate.overlay(0.14)};
                border-top: 1px solid {Gate.overlay(0.22)};
                border-radius: 16px;
            }}
        """)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(25)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 8)
        frame.setGraphicsEffect(shadow)
        return frame

    def _build_glass_button(self, text: str, color_hex: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Gate.overlay(0.05)};
                border: 1px solid {color_hex};
                color: {color_hex};
                border-radius: 6px;
                padding: 6px 16px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background-color: {color_hex};
                color: {Gate.TEXT_ON_ACCENT};
            }}
            QPushButton:pressed {{
                background-color: {Gate.overlay(0.2)};
            }}
            QPushButton:disabled {{
                background-color: transparent;
                border: 1px solid {Gate.LINE};
                color: {Gate.TEXT_DIM};
            }}
        """)
        return btn

    def _build_quick_action_btn(self, title: str, subtitle: str, glyph: str = "") -> QFrame:
        return QuickActionBtn(title, subtitle, glyph)

    def _build_stat_item(self, label: str, value: str):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        v = QLabel(value)
        v.setStyleSheet(f"background: transparent; border: none; color: {Gate.OK}; font-size: 21px; font-weight: 800;")
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)

        l = QLabel(label)
        l.setWordWrap(False)          # one line: wrapped labels were cut in half
        l.setStyleSheet(f"background: transparent; border: none; color: {Gate.TEXT_2}; font-size: 11px; font-weight: 600;")
        l.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay.addWidget(v)
        lay.addWidget(l)
        return w, v

    # ------------------------------------------------------------ actions
    def _host(self):
        return self.main_window or self.window()

    def _trigger_tab(self, tab_label: str) -> bool:
        host = self._host()
        opened = False
        if hasattr(host, "_switch_to_tab_label"):
            opened = bool(host._switch_to_tab_label(tab_label))
        if not opened and hasattr(host, "show_feedback"):
            host.show_feedback(f"{tab_label} is not available for you here.", "warning", 3500)
        return opened

    def open_shot(self, shot_name: str, project: str = ""):
        """A task row: the shot's project opened in the VFX Dashboard, the shot selected."""
        jump = getattr(self._host(), "_jump_to_shot_in_review", None)
        return jump(shot_name, project_code=project) if callable(jump) else False

    def see_all_shots(self):
        """
        The dashboard in its own 'my shots' scope. The scope is per project, so
        the project of the newest shot is opened first - with none open the
        dashboard said 'No project selected'.
        """
        host = self._host()
        if not self._trigger_tab("VFX Dashboard"):
            return False
        tab = host._get_tab_instance("VFX Dashboard", create=True) if hasattr(host, "_get_tab_instance") else None
        newest = next((it.get("project") for it in getattr(self, "_shown_items", None) or []
                       if it.get("project")), "")
        opener = getattr(host, "_open_dashboard_project", None)
        if newest and callable(opener):
            opener(newest)
        show = getattr(tab, "show_my_shots", None)
        if callable(show) and show():
            return True
        # A dashboard without the scope: fall back to searching for the name.
        search = getattr(tab, "search_input", None)
        if search is not None:
            search.setText(self.user_display_name or self.username)
        return True

    def _on_load_progress(self, pct, msg):
        safe_msg = str(msg or "").replace("\\", "\\\\").replace("'", "\\'")
        self._run_js(
            f"if (typeof window.setLoadingProgress === 'function') {{ window.setLoadingProgress({pct}, '{safe_msg}'); }}")

    def closeEvent(self, event):
        # Asked to stop and waited for: terminate() could kill the thread in
        # the middle of a database call holding a pooled connection.
        worker = getattr(self, "loader_worker", None)
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            worker.wait(3000)
        super().closeEvent(event)

    def _clear_tasks(self):
        from ..core.layout_utils import clear_layout
        clear_layout(self.tasks_container_layout)

    def _note_widget(self, text):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"background: transparent; border: none; color: {Gate.TEXT_DIM}; font-style: italic;")
        self.tasks_container_layout.addWidget(label)
        return label

    def _on_load_failed(self, reason):
        """The database did not answer: say so, offer Try again, end the intro."""
        logging.warning("Home could not read the database: %s", reason)
        self._is_loaded = True
        self._clear_tasks()
        self._note_widget(f"{OFFLINE_TITLE}. {OFFLINE_BODY}")
        # Home's own button: a plain one took the Light theme's dark text and
        # nearly vanished on the dark panel.
        retry = self._build_glass_button("Try again", Gate.ACCENT)
        retry.clicked.connect(self.refresh)
        self.tasks_container_layout.addWidget(retry, 0, Qt.AlignmentFlag.AlignLeft)
        self._shown_items = None
        for label in self.stat_labels.values():
            self._set_figure(label, "-", Gate.TEXT_DIM, OFFLINE_TITLE)
        if self.has_punch_panel:
            self.lbl_punch_status.setText("Today's punch could not be read")
            self.btn_punch_in.setEnabled(False)
            self.btn_punch_out.setEnabled(False)
        if not self._cinematic_ended:
            self._end_cinematic_mode()

    def _on_data_loaded(self, items, punch_status):
        self._is_loaded = True

        # Land on the work, not on the logo: the intro gets out of the way by
        # itself once the data is ready. Clicking or Enter still skips it.
        if not self._cinematic_ended:
            safe_single_shot(2200, self, self._end_cinematic_mode)

        if self.has_punch_panel:
            self._refresh_punch_buttons(punch_status)
        # The minute refresh rebuilt every row, taking keyboard focus off the
        # one somebody was on. Unchanged rows are left alone.
        if items == getattr(self, "_shown_items", None):
            return
        self._shown_items = [dict(it) for it in items]
        self._clear_tasks()
        if not items:
            self._note_widget("Nothing assigned to you right now." if "shots" in self.figures
                              else "Nobody is on leave now or soon." if self.leave_stage
                              else "You have no leave coming up.")
        else:
            from PySide6.QtGui import QFontMetrics
            for it in items[:5]:
                row_widget = ClickableRow() if it.get("shot") else QFrame()
                if not isinstance(row_widget, ClickableRow):
                    row_widget.setStyleSheet("background: transparent;")
                row = QHBoxLayout(row_widget)
                row.setContentsMargins(6, 4, 6, 4)
                project = str(it.get("project") or "")
                if project:
                    # Two shows can both have SH010: say which.
                    lbl_project = QLabel(f"{project} \u00b7")
                    lbl_project.setStyleSheet(f"background: transparent; border: none; color: {Gate.TEXT_DIM};")
                    row.addWidget(lbl_project)
                title = str(it.get('title') or 'Item')
                lbl_name = QLabel()
                lbl_name.setStyleSheet(f"background: transparent; border: none; color: {Gate.TEXT}; font-weight: bold;")
                # ponytail: a fixed 260 px budget, not the live column width; a
                # resize-aware label if the column ever gets much narrower.
                font = lbl_name.font()
                font.setBold(True)
                lbl_name.setText(QFontMetrics(font).elidedText(title, Qt.TextElideMode.ElideMiddle, 260))
                if lbl_name.text() != title:
                    lbl_name.setToolTip(title)
                status_str = str(it.get('status') or '')
                lbl_status = QLabel(status_str or "-")
                lbl_status.setStyleSheet(f"color: {self.status_colour(status_str)}; font-weight: bold; padding: 2px 8px; border-radius: 4px; background-color: {Gate.overlay(0.08)};")
                row.addWidget(lbl_name)
                row.addStretch()
                row.addWidget(lbl_status)
                if isinstance(row_widget, ClickableRow):
                    where = f" ({project})" if project else ""
                    row_widget.setToolTip(f"Open {title}{where} in the VFX Dashboard")
                    row_widget.clicked.connect(
                        lambda name=title, code=project: self.open_shot(name, code))
                self.tasks_container_layout.addWidget(row_widget)

    @staticmethod
    def status_colour(status: str) -> str:
        """The dashboard's own status colours: Retake is not Review."""
        colour = Gate.STATUS.get(str(status or "").strip().upper())
        if colour is not None:
            return colour
        text = str(status or "").strip().lower()
        if text in ("approved", "done", "resolved"):
            return Gate.OK
        if text.startswith("pending") or text == "cancellation requested":
            return Gate.WARN
        if text in ("rejected", "cancelled"):
            return Gate.BAD
        return Gate.TEXT_2

    def _on_title_changed(self, title):
        if title == "CINEMATIC_ENDED" and not self._cinematic_ended:
            self._end_cinematic_mode()

    def keyPressEvent(self, event):
        if self._is_loaded and not self._cinematic_ended:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._end_cinematic_mode()
        super().keyPressEvent(event)

    def _end_cinematic_mode(self):
        self._cinematic_ended = True
        self._run_js("if (window.triggerEnter) { window.triggerEnter(); }")
        if hasattr(self, 'click_catcher'):
            self.click_catcher.hide()
        # The panels come up once the big SLATE / READY text has faded (0.8 s
        # in the page): shown at once, they sat over it for a moment.
        if HAS_WEBENGINE and isinstance(getattr(self, "web_view", None), QWebEngineView):
            safe_single_shot(800, self, self._show_overlay)
        else:
            self._show_overlay()

    def _show_overlay(self):
        self.overlay_widget.show()
        self.overlay_widget.raise_()

    # ------------------------------------------------------------ punch
    def _refresh_punch_buttons(self, punch_status):
        if not self.has_punch_panel or not hasattr(self, 'lbl_punch_status'):
            return
        pi = punch_status.get('punch_in')
        po = punch_status.get('punch_out')
        text = punch_text(pi, po)
        sessions = int(punch_status.get('sessions') or 0)
        if sessions > 1 and pi:
            text += f" - session {sessions} today" if not po else f" - {sessions} sessions today"
        self.lbl_punch_status.setText(text)
        # After a punch out, punching in again starts a new session the same
        # day (studio decision); only while punched in is Punch In off.
        self.btn_punch_in.setEnabled(not (pi and not po))
        self.btn_punch_out.setEnabled(bool(pi and not po))

    def _read_todays_punch(self) -> dict:
        """
        Today's punch row, read now rather than remembered - by the database's
        date (the one punches are written with), not this workstation's.
        """
        return HomeLoaderWorker(self.username, self.app_context, mode="ops").todays_punch()

    @on_database_error
    def _update_punch_ui(self):
        if not self.has_punch_panel:
            return
        self._refresh_punch_buttons(self._read_todays_punch())

    def _attendance(self):
        if self.attendance is None and self.has_punch_panel:
            context = self.app_context
            self.attendance = context.attendance() if context is not None and hasattr(context, "attendance")                 else CentralAttendance()
        return self.attendance

    def do_punch(self, action: str, on_leave_ok: bool = False):
        """
        Punch in or out on a worker: the write and the re-read used to run in
        the click handler and froze the window on a slow link. Punch out asks
        first - one stray click ended the day, and only HR could undo it.
        A punch-in on a day of approved leave asks too (CentralAttendance.punch),
        as the Attendance tab always did.
        """
        if not self.has_punch_panel or self._attendance() is None or self._punch_worker is not None:
            return None
        if action == "out":
            from ..components.feedback import confirm
            if not confirm(self, "Punch out", f"Punch out at {datetime.now():%H:%M}?",
                           yes_label="Punch out", no_label="Cancel",
                           informative="Punching in again later today starts a new session."):
                return None
        self.btn_punch_in.setEnabled(False)
        self.btn_punch_out.setEnabled(False)
        self.lbl_punch_status.setText("Punching in\u2026" if action == "in" else "Punching out\u2026")
        attendance, username = self.attendance, self.username

        def work():
            try:
                # {"stored"}, {"rule": "Already punched in at 09:42."} or {"on_leave"}
                out = attendance.punch(username, action, on_leave_ok=on_leave_ok)
            except Exception as exc:
                out = {"error": str(exc)}
            try:
                out["status"] = self._read_todays_punch()
            except Exception as exc:
                logging.debug("Punch: could not read the punch back: %s", exc)
            return out

        from slate.core.infra.db_worker import run_db_async
        self._punch_worker = run_db_async(work, on_success=lambda result: self._punched(action, result),
                                          on_error=lambda text: self._punched(action, {"error": text}),
                                          owner=self)
        return self._punch_worker

    def punch_busy(self) -> bool:
        return self._punch_worker is not None

    def _punched(self, action, result):
        self._punch_worker = None
        host = self._host()
        say = getattr(host, "show_feedback", None) if host else None
        status = result.get("status")
        if status is not None:
            self._refresh_punch_buttons(status)
        if result.get("on_leave"):
            if status is None:
                self.btn_punch_in.setEnabled(True)
            from ..components.feedback import confirm
            if confirm(self, "Punch in",
                       "You are on approved leave today (%s). Punch in anyway?"
                       % result["on_leave"].get("type", "Leave"),
                       yes_label="Punch in", no_label="Cancel",
                       informative="Ask HR to cancel the leave if you are working."):
                self.do_punch("in", on_leave_ok=True)
            return
        if result.get("rule"):
            if say:
                say(result["rule"], "warning", 5000)
            return
        if result.get("error"):
            if status is None:
                self.lbl_punch_status.setText("Today's punch could not be read")
                self.btn_punch_in.setEnabled(True)
            if say:
                say("The punch was not saved.", "error", 4000, details=result["error"])
            return
        if status is None:
            # The punch is in the database. Failing to read it back is a
            # different problem, and reporting it as a failed punch would send
            # somebody to press the button again for a punch that already landed.
            if say:
                say(f"Punched {action}. The screen could not be refreshed - open "
                    "Attendance to check.", "warning", 5000)
            return
        if say:
            stored = result.get("stored")
            # The time the database stored, which is what the record says.
            stored_time = _time_text((stored or {}).get("time")) if isinstance(stored, dict) else ""
            now = stored_time or datetime.now().strftime("%H:%M")
            session = (stored or {}).get("session") if isinstance(stored, dict) else None
            extra = f" (session {session} today)" if action == "in" and session and session > 1 else ""
            say(f"Punched {'in' if action == 'in' else 'out'} at {now}{extra}.", "success", 4000)


class VfxHomeTab(HomeTab):
    """Cinematic VFX Home Hub without attendance."""
    def __init__(self, user_data=None, app_context=None, main_window=None, parent=None):
        super().__init__(user_data=user_data, app_context=app_context, main_window=main_window, parent=parent, mode="vfx")


class OpsHomeTab(HomeTab):
    """Cinematic Studio Operations Hub with attendance and HRMS/IT quick actions."""
    def __init__(self, user_data=None, app_context=None, main_window=None, parent=None):
        super().__init__(user_data=user_data, app_context=app_context, main_window=main_window, parent=parent, mode="ops")
