"""
One notification centre, in the header, for everybody.

Notifications used to live in two places, both inside tabs: an "N" button in
the Timeline Viewer (only for people with that tab, only while it was open,
polling the database on the UI thread every ten seconds even when hidden, and
falling back to the shared name "Artist" when it could not tell who you were)
and an amber "Alerts (3)" button on the dashboard that looked people up by a
lower-cased name the notifications were never stored under.

Now there is one bell beside Help. It counts what is unread for the signed-in
person - found by their username and every name they have been addressed by -
reading off the UI thread, straight away when the change feed says the
notifications table changed, and never while the window is minimised.

Sending one (any screen, any thread-safe place):

    from slate.gui.components.notification_center import notify
    notify(["priya", "Rahul Mehta"], "KLC_R01_2110 was assigned to you.", "assignment")

A count on a sidebar entry ("3 tickets waiting"):

    from slate.gui.components.notification_center import set_tab_badge
    set_tab_badge(self, "IT Support", waiting, "3 tickets waiting for IT")

Opening the list from a screen of your own:

    from slate.gui.components.notification_center import open_notifications
    open_notifications(self)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from PySide6.QtCore import QByteArray, QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

logger = logging.getLogger(__name__)

POLL_MS = 30000          # without the change feed; with it, only when something changed
FEED_TOPIC = "notifications"

# Drawn like the rest of the line icons (slate/gui/core/icons.py): a bell on a
# 24x24 grid. Kept here until the shared icon set has one.
_BELL_PATH = ("M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15z "
              "M10 20.5a2 2 0 0 0 4 0")


def _gate():
    from slate.core.infra.gate import Gate
    return Gate


def bell_icon(colour: str, size: int = 20) -> QIcon:
    try:
        from slate.gui.core.icons import has_icon, icon
        if has_icon("bell"):
            return icon("bell", colour, size)
    except Exception:
        pass
    from PySide6.QtSvg import QSvgRenderer
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="{size}" '
           f'height="{size}" fill="none" stroke="{colour}" stroke-width="1.6" '
           f'stroke-linecap="round" stroke-linejoin="round"><path d="{_BELL_PATH}"/></svg>')
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter)
    finally:
        painter.end()
    return QIcon(pixmap)


def badge_text(count: int) -> str:
    """The number on the bell: the count up to 9, "9+" above that, nothing at 0."""
    count = int(count or 0)
    if count <= 0:
        return ""
    return str(count) if count <= 9 else "9+"


def friendly_time(timestamp, now: datetime = None) -> str:
    """
    When, in words: "Just now", "5 min ago", "Today 14:32", "Yesterday 09:10",
    "Mon 14:32" within the week, then "12 Sep 2026". Timestamps are stored as
    epoch seconds; they used to be printed as 1790776600.0.
    """
    try:
        moment = datetime.fromtimestamp(float(timestamp))
    except (TypeError, ValueError, OSError, OverflowError):
        return ""
    now = now or datetime.now()
    delta = now - moment
    if delta < timedelta(minutes=-5):
        return moment.strftime("%d %b %Y %H:%M")
    if delta < timedelta(0):
        delta = timedelta(0)        # another workstation's clock runs a little ahead
    if delta < timedelta(minutes=1):
        return "Just now"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)} min ago"
    if moment.date() == now.date():
        return "Today " + moment.strftime("%H:%M")
    if moment.date() == (now - timedelta(days=1)).date():
        return "Yesterday " + moment.strftime("%H:%M")
    if delta < timedelta(days=7):
        return moment.strftime("%a %H:%M")
    return moment.strftime("%d %b %Y").lstrip("0")


# ------------------------------------------------------------------- sending
def notify(recipients, message: str, kind: str = "info") -> int:
    """
    Send a notification to one or more people (usernames or display names).
    Returns how many were written; never raises into the caller's action.
    """
    try:
        from slate.core.domain.notification_manager import NotificationManager
        return NotificationManager().notify(recipients, message, kind)
    except Exception as exc:
        logger.warning("Notification not sent (%s): %s", exc, message)
        return 0


def _main_window(widget):
    window = widget.window() if widget is not None else None
    return window


def set_tab_badge(widget, label: str, count: int, tooltip: str = None) -> bool:
    """Show a count on a sidebar entry of the window this widget is in."""
    window = _main_window(widget)
    coordinator = getattr(window, "tab_coordinator", None)
    if coordinator is None or not hasattr(coordinator, "set_badge"):
        return False
    return coordinator.set_badge(label, count, tooltip)


def open_notifications(widget) -> bool:
    """Open the header's notification list from anywhere in the window."""
    window = _main_window(widget)
    centre = getattr(window, "notification_center", None)
    if centre is None:
        return False
    centre.open_dialog()
    return True


# ------------------------------------------------------------------ the bell
class NotificationBell(QToolButton):
    """
    The header's bell. The count is a badge in the corner; the look changes
    through a dynamic property, so hover styling survives every update (the
    old bell replaced its whole stylesheet on each poll and lost its hover).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("notificationBell")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Notifications")
        self.setAccessibleName("Notifications")
        self.setAutoRaise(True)
        self.setIconSize(QSize(20, 20))
        self.setFixedSize(34, 32)
        self.setProperty("unread", False)
        self._count = 0

        Gate = _gate()
        self.setStyleSheet(f"""
            QToolButton#notificationBell {{
                background: transparent; border: 1px solid transparent;
                border-radius: {Gate.RADIUS_MD}px;
            }}
            QToolButton#notificationBell:hover {{
                background: {Gate.RAISED}; border-color: {Gate.LINE};
            }}
        """)
        self.setIcon(bell_icon(Gate.TEXT_DIM))

        self.badge = QLabel(self)
        self.badge.setObjectName("notificationBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.badge.setStyleSheet(f"""
            QLabel#notificationBadge {{
                background: {Gate.ACCENT}; color: {Gate.TEXT_ON_ACCENT};
                border-radius: 8px; font-size: 10px; font-weight: 700;
                padding: 0 3px;
            }}
        """)
        self.badge.hide()

    def count(self) -> int:
        return self._count

    def set_count(self, count: int):
        count = max(0, int(count or 0))
        self._count = count
        Gate = _gate()
        text = badge_text(count)
        self.setProperty("unread", bool(count))
        self.setIcon(bell_icon(Gate.TEXT if count else Gate.TEXT_DIM))
        self.setToolTip(f"Notifications - {count} unread" if count else "Notifications - nothing unread")
        if text:
            self.badge.setText(text)
            self.badge.adjustSize()
            w = max(16, self.badge.sizeHint().width())
            self.badge.setGeometry(self.width() - w - 1, 1, w, 16)
            self.badge.show()
            self.badge.raise_()
        else:
            self.badge.hide()


# ------------------------------------------------------------------ the list
class NotificationsDialog(QDialog):
    """
    The person's recent notifications, unread first in weight. Close is always
    there; "Mark all read" only does something when something is unread; an
    empty list says so in words instead of showing an empty table.
    """

    def __init__(self, notes, on_mark_all=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Notifications")
        self.setMinimumSize(420, 320)
        self.resize(520, 460)
        self.notes = list(notes or [])
        self._on_mark_all = on_mark_all

        Gate = _gate()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        layout.setSpacing(Gate.SPACE_3)

        unread = sum(1 for n in self.notes if not n.get("read"))
        self.heading = QLabel(f"{unread} unread" if unread else "Nothing unread")
        self.heading.setStyleSheet(
            f"color: {Gate.TEXT}; font-size: {Gate.SIZE_LG}px; font-weight: 600;")
        layout.addWidget(self.heading)

        self.stack = QStackedWidget()
        self.list = QListWidget()
        self.list.setObjectName("notificationList")
        self.list.setWordWrap(True)
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setStyleSheet(f"""
            QListWidget#notificationList {{
                background: {Gate.PANEL}; border: 1px solid {Gate.LINE};
                border-radius: {Gate.RADIUS_LG}px;
            }}
            QListWidget#notificationList::item {{
                color: {Gate.TEXT}; padding: 8px 10px;
                border-bottom: 1px solid {Gate.LINE_SOFT};
            }}
        """)
        for note in self.notes:
            when = friendly_time(note.get("timestamp"))
            kind = str(note.get("type") or "").strip().capitalize()
            meta = " · ".join(part for part in (when, kind) if part)
            item = QListWidgetItem(f"{note.get('message') or ''}\n{meta}")
            font = item.font()
            font.setBold(not note.get("read"))
            item.setFont(font)
            item.setToolTip(str(note.get("message") or ""))
            self.list.addItem(item)

        self.empty = QLabel("No notifications yet.\nAssignments, new scans and replies to "
                            "your requests will appear here.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_MD}px;")
        self.stack.addWidget(self.list)
        self.stack.addWidget(self.empty)
        self.stack.setCurrentWidget(self.list if self.notes else self.empty)
        layout.addWidget(self.stack, 1)

        from slate.gui.core.controls import make_button
        buttons = QHBoxLayout()
        self.mark_button = make_button("Mark all read", "secondary", on_click=self._mark_all)
        self.mark_button.setEnabled(unread > 0)
        self.mark_button.setVisible(bool(self.notes))
        close = make_button("Close", "primary", on_click=self.accept)
        close.setDefault(True)
        buttons.addWidget(self.mark_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _mark_all(self):
        if self._on_mark_all is not None:
            try:
                self._on_mark_all()
            except Exception as exc:
                logger.warning("Could not mark notifications read: %s", exc)
                return
        for row in range(self.list.count()):
            item = self.list.item(row)
            font = item.font()
            font.setBold(False)
            item.setFont(font)
        for note in self.notes:
            note["read"] = True
        self.heading.setText("Nothing unread")
        self.mark_button.setEnabled(False)


# ---------------------------------------------------------------- the centre
class NotificationCenter(QObject):
    """
    Owns the bell and keeps its count current for one signed-in person.
    """

    count_changed = Signal(int)

    def __init__(self, window, username: str, aliases=(), poll_ms: int = POLL_MS):
        super().__init__(window)
        self.window = window
        self.username = str(username or "").strip()
        self.aliases = tuple(a for a in (aliases or ()) if a)
        self.bell = NotificationBell()
        self.bell.clicked.connect(self.open_dialog)
        self._manager = None
        self._pending = None
        self._stale = True

        self._timer = QTimer(self)
        self._timer.setInterval(int(poll_ms))
        self._timer.timeout.connect(self.refresh)
        if self.username:
            self._timer.start()
            QTimer.singleShot(1500, self.refresh)
        window.installEventFilter(self)

        # Straight away when somebody writes to the notifications table.
        self._feed = None
        try:
            from slate.gui.components.change_feed import ChangeFeed
            self._feed = ChangeFeed.instance()
            if self._feed is not None:
                self._feed.watch(self, (FEED_TOPIC,), lambda _changes: self.refresh())
        except Exception as exc:
            logger.debug("Notification centre without change feed: %s", exc)

    # ------------------------------------------------------------ plumbing
    def manager(self):
        if self._manager is None:
            from slate.core.domain.notification_manager import NotificationManager
            self._manager = NotificationManager()
        return self._manager

    def _window_active(self) -> bool:
        try:
            return self.window.isVisible() and not self.window.isMinimized()
        except RuntimeError:
            return False

    def eventFilter(self, obj, event):
        if obj is getattr(self, "window", None) and event.type() == QEvent.Type.WindowStateChange:
            if self._window_active() and self._stale:
                QTimer.singleShot(0, self.refresh)
        return False

    # ------------------------------------------------------------- reading
    def refresh(self):
        """Re-count unread notifications, off the UI thread."""
        if not self.username:
            return
        if not self._window_active():
            self._stale = True             # counted when the window comes back
            return
        if self._pending is not None:
            return
        self._stale = False
        username, aliases = self.username, self.aliases

        def work():
            return self.manager().unread_count(username, *aliases)

        try:
            from slate.core.infra.db_worker import run_db_async
            self._pending = run_db_async(work, on_success=self._counted,
                                         on_error=self._count_failed, owner=self)
        except Exception as exc:
            logger.debug("Notification count not started: %s", exc)
            self._pending = None

    def _counted(self, count):
        self._pending = None
        try:
            count = int(count or 0)
        except (TypeError, ValueError):
            count = 0
        try:
            self.bell.set_count(count)
        except RuntimeError:
            return
        self.count_changed.emit(count)

    def _count_failed(self, message):
        self._pending = None
        logger.debug("Notification count failed: %s", message)

    def open_dialog(self):
        notes = []
        try:
            notes = self.manager().get_recent(self.username, *self.aliases, limit=50)
        except Exception as exc:
            logger.warning("Notifications could not be read: %s", exc)

        def mark_all():
            self.manager().mark_all_read(self.username, *self.aliases)

        dialog = NotificationsDialog(notes, on_mark_all=mark_all, parent=self.window)
        dialog.exec()
        self.refresh()
        # A dialog shown by a test harness returns at once; count again later too.
        QTimer.singleShot(500, self.refresh)

    def stop(self):
        try:
            self._timer.stop()
            if self._pending is not None and hasattr(self._pending, "cancel"):
                self._pending.cancel()
        except RuntimeError:
            pass
