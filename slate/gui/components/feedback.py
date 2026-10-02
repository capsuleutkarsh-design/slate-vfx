"""
Telling people what happened.

Across the product an action reported itself in one of four ways: a coloured
status-bar line that vanished after three seconds, a modal box titled "Success"
or "Error", a label inside the tab, or nothing at all. Several reported success
when the work had failed - "Timeline rebuilt from the dashboard." was shown
whatever the rebuild did - and the error boxes showed a raw exception followed
by "Please check logs.", which an artist does not have.

This module is the one way to say it:

    from slate.gui.components.feedback import toast, show_error, Result

    toast(self, "Shot saved.", "success")
    toast(self, "3 shots archived.", "success", action=("Undo", self.undo_archive))
    toast(self, "Proxies written.", "success", action=("Open folder", open_it))

    ok = show_error(self, "Could not open the VFX Dashboard.", exc=e,
                    retry=lambda: self.open_dashboard())

    result = self.save()                 # a Result, not None
    report(self, result)                 # success toast, or the real error

Message boxes take the action as their title ("Collect machine", "Live Ops
sync"), never "Error", "Warning" or "Success" - the title is the first thing
anybody reads, and a word that could head any box says nothing.

Toasts sit above the footer, never over it: the credit line there must stay
visible and in place.
"""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

logger = logging.getLogger(__name__)

# Titles that describe no action at all. A box with one of these was written in
# a hurry; say what was being done instead.
GENERIC_TITLES = frozenset({
    "error", "warning", "success", "info", "information", "notice", "message",
    "failed", "failure", "done", "ok", "alert",
})

Action = Tuple[str, Callable[[], object]]


# --------------------------------------------------------------------- results
@dataclass
class Result:
    """
    What an action did, said honestly.

    A method that used to return None - so its caller announced success
    whatever happened - returns one of these instead. It is truthy when the
    action worked, so ``if not result:`` still reads naturally.

        return Result.success("Timeline rebuilt: 42 shots.")
        return Result.failure("The dashboard has no project open.")
    """
    ok: bool
    message: str = ""
    detail: str = ""
    data: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.ok)

    @classmethod
    def success(cls, message: str = "", **data) -> "Result":
        return cls(True, message, "", dict(data))

    @classmethod
    def failure(cls, message: str, detail: str = "", **data) -> "Result":
        return cls(False, message, detail, dict(data))

    @classmethod
    def from_value(cls, value, success_message: str = "", failure_message: str = "") -> "Result":
        """
        Read the several shapes older methods return: a Result, (ok, message),
        a bool, or None (which is not success - it is "nobody said").
        """
        if isinstance(value, Result):
            return value
        if isinstance(value, tuple) and value and isinstance(value[0], bool):
            message = str(value[1]) if len(value) > 1 and value[1] else ""
            if value[0]:
                return cls.success(message or success_message)
            return cls.failure(message or failure_message or "It did not work.")
        if value is True:
            return cls.success(success_message)
        if value is False:
            return cls.failure(failure_message or "It did not work.")
        return cls.failure(failure_message or "It did not say whether it worked.")


# ---------------------------------------------------------------------- toasts
_LEVEL_COLOURS = {
    # Read through Gate when shown, so the theme in force at the time is used.
    "info": "INFO",
    "success": "OK",
    "warning": "WARN",
    "error": "BAD",
}

_DEFAULT_DURATION = {"info": 3500, "success": 4000, "warning": 6000, "error": 8000}


def _gate():
    from slate.core.infra.gate import Gate
    return Gate


class Toast(QFrame):
    """
    One message: a coloured edge, the text, an optional action and a close.

    It stays while the pointer is over it, so the Undo you were reaching for
    does not vanish under the mouse.
    """

    def __init__(self, host: "ToastHost", message: str, level: str = "info",
                 action: Optional[Action] = None, duration: Optional[int] = None):
        super().__init__(host.window)
        self.host = host
        self.level = level if level in _LEVEL_COLOURS else "info"
        self.setObjectName("slateToast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("level", self.level)
        self._action = action

        Gate = _gate()
        edge = getattr(Gate, _LEVEL_COLOURS[self.level])
        self.setStyleSheet(f"""
            QFrame#slateToast {{
                background: {Gate.RAISED};
                border: 1px solid {Gate.LINE};
                border-left: 4px solid {edge};
                border-radius: {Gate.RADIUS_LG}px;
            }}
            QFrame#slateToast QLabel {{
                color: {Gate.TEXT}; background: transparent; border: none;
                font-family: {Gate.FONT_UI}; font-size: {Gate.SIZE_MD}px;
            }}
            QFrame#slateToast QPushButton#toastAction {{
                color: {Gate.ACCENT}; background: transparent; border: none;
                font-family: {Gate.FONT_UI}; font-size: {Gate.SIZE_MD}px; font-weight: 600;
                padding: 2px 6px;
            }}
            QFrame#slateToast QPushButton#toastAction:hover {{ color: {Gate.ACCENT_HI}; }}
            QFrame#slateToast QPushButton#toastClose {{
                color: {Gate.TEXT_DIM}; background: transparent; border: none;
                font-size: 15px; padding: 0 4px;
            }}
            QFrame#slateToast QPushButton#toastClose:hover {{ color: {Gate.TEXT}; }}
        """)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 8, 10)
        row.setSpacing(10)

        self.label = QLabel(message)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.label, 1)

        self.action_button = None
        if action:
            text, _callback = action
            self.action_button = QPushButton(str(text).replace("&", "&&"))
            self.action_button.setObjectName("toastAction")
            self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_button.clicked.connect(self._run_action)
            row.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignVCenter)

        close = QPushButton("×")
        close.setObjectName("toastClose")
        close.setToolTip("Dismiss")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.dismiss)
        row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)

        self.setFixedWidth(380)

        if duration is None:
            duration = _DEFAULT_DURATION[self.level]
            if action:
                # Time to read it and decide.
                duration = max(duration, 8000)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._duration = int(duration)
        if self._duration > 0:
            self._timer.start(self._duration)

    def _run_action(self):
        _text, callback = self._action
        self.dismiss()
        try:
            callback()
        except Exception as exc:          # the action is the caller's code
            logger.exception("Toast action failed: %s", exc)
            show_error(self.host.window, "That did not work.", exc=exc)

    def enterEvent(self, event):
        self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._duration > 0:
            self._timer.start(2500)
        super().leaveEvent(event)

    def dismiss(self):
        try:
            self._timer.stop()
            self.hide()
            self.host.remove(self)
            self.deleteLater()
        except RuntimeError:
            pass


class ToastHost(QObject):
    """
    Keeps a window's toasts stacked in its bottom-right corner.

    They are placed above the footer, so nothing ever sits on the credit line,
    and they follow the window when it is resized.
    """

    MAX_VISIBLE = 3
    MARGIN = 16

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.window = window
        self.toasts = []
        window.installEventFilter(self)

    @classmethod
    def for_window(cls, widget: QWidget) -> "ToastHost":
        window = widget.window() if widget is not None else None
        if window is None:
            raise ValueError("A toast needs a window to sit in.")
        host = getattr(window, "_slate_toast_host", None)
        if host is None:
            host = cls(window)
            window._slate_toast_host = host
        return host

    def show(self, message, level="info", action=None, duration=None) -> Toast:
        # The same words twice in a row are one message, not two.
        for existing in list(self.toasts):
            if existing.label.text() == message and existing.level == level:
                existing.dismiss()
        toast_widget = Toast(self, message, level, action, duration)
        self.toasts.append(toast_widget)
        while len(self.toasts) > self.MAX_VISIBLE:
            self.toasts[0].dismiss()
        toast_widget.adjustSize()
        toast_widget.show()
        toast_widget.raise_()
        self.layout()
        return toast_widget

    def remove(self, toast_widget):
        if toast_widget in self.toasts:
            self.toasts.remove(toast_widget)
            self.layout()

    def _bottom(self) -> int:
        """The lowest y a toast may reach: above the footer and the status bar."""
        window = self.window
        bottom = window.height()
        for name in ("footer",):
            footer = window.findChild(QWidget, name)
            if footer is not None and footer.isVisible():
                top = footer.mapTo(window, QPoint(0, 0)).y()
                bottom = min(bottom, top)
        status = getattr(window, "status_bar", None)
        try:
            if status is not None and status.isVisible():
                bottom = min(bottom, status.mapTo(window, QPoint(0, 0)).y())
        except RuntimeError:
            pass
        return bottom - self.MARGIN

    def layout(self):
        try:
            y = self._bottom()
            right = self.window.width() - self.MARGIN
            for toast_widget in reversed(self.toasts):
                toast_widget.adjustSize()
                h = toast_widget.sizeHint().height()
                y -= h
                toast_widget.setGeometry(right - toast_widget.width(), y, toast_widget.width(), h)
                toast_widget.raise_()
                y -= 8
        except RuntimeError:
            pass

    def eventFilter(self, obj, event):
        if obj is self.window and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            QTimer.singleShot(0, self.layout)
        return False


def toast(parent: QWidget, message: str, level: str = "info",
          action: Optional[Action] = None, duration: Optional[int] = None,
          details: str = ""):
    """
    Say what just happened, in the corner of the window, without stopping work.

    ``action`` is an optional (label, callback) pair shown as a button on the
    toast - "Undo", "Open folder", "View log". ``details`` adds a "Details"
    button that opens the full text (for an error, the reason).

    Routed through the main window's show_feedback when there is one, so the
    status bar keeps a copy of the line as well.
    """
    window = parent.window() if parent is not None else None
    handler = getattr(window, "show_feedback", None)
    if callable(handler) and window is not parent:
        try:
            return handler(message, level=level, duration=duration, details=details, action=action)
        except TypeError:
            # An older window without action support: fall through.
            pass
    return raw_toast(parent, message, level, action=action, duration=duration, details=details)


def raw_toast(parent, message, level="info", action=None, duration=None, details=""):
    """A toast straight onto the parent's window (what show_feedback itself uses)."""
    if parent is None:
        logger.info("[%s] %s", level.upper(), message)
        return None
    if details and not action:
        action = ("Details", lambda: show_details(parent, message, details, level))
    try:
        return ToastHost.for_window(parent).show(message, level, action, duration)
    except (RuntimeError, ValueError) as exc:
        logger.info("[%s] %s (%s)", level.upper(), message, exc)
        return None


def report(parent, result, success_level: str = "success", action: Optional[Action] = None,
           title: str = "") -> bool:
    """
    Show a Result: a toast when it worked, the real reason when it did not.

    Returns result.ok so a caller can carry on only after success.
    """
    result = Result.from_value(result)
    if result.ok:
        if result.message:
            toast(parent, result.message, success_level, action=action)
    else:
        toast(parent, result.message or "That did not work.", "error",
              details=result.detail)
    return result.ok


# ----------------------------------------------------------------- dialogs
def _check_title(title: str) -> str:
    if str(title or "").strip().lower() in GENERIC_TITLES:
        logger.debug("Message box titled %r: say what was being done instead.", title)
    return title or "Slate"


def _box(parent, icon, title, text, buttons, default=None, informative=""):
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(_check_title(title))
    box.setText(text)
    if informative:
        box.setInformativeText(informative)
    box.setStandardButtons(buttons)
    if default is not None:
        box.setDefaultButton(default)
    return box


def inform(parent, title: str, text: str, informative: str = "") -> None:
    """A fact the person should read before carrying on. Title = the action."""
    _box(parent, QMessageBox.Icon.Information, title, text,
         QMessageBox.StandardButton.Ok, informative=informative).exec()


def warn(parent, title: str, text: str, informative: str = "") -> None:
    """Something needs attention (nothing selected, a value refused). Title = the action."""
    _box(parent, QMessageBox.Icon.Warning, title, text,
         QMessageBox.StandardButton.Ok, informative=informative).exec()


def confirm(parent, title: str, text: str, yes_label: str = "Continue",
            no_label: str = "Cancel", destructive: bool = False,
            informative: str = "") -> bool:
    """
    Ask before doing something. The buttons say what they do ("Delete 3
    shots", not "Yes"), and a destructive question defaults to the safe answer
    so Enter never throws work away.
    """
    box = _box(parent, QMessageBox.Icon.Warning if destructive else QMessageBox.Icon.Question,
               title, text, QMessageBox.StandardButton.NoButton, informative=informative)
    yes = box.addButton(yes_label, QMessageBox.ButtonRole.AcceptRole)
    no = box.addButton(no_label, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(no if destructive else yes)
    box.setEscapeButton(no)
    box.exec()
    return box.clickedButton() is yes


def _details_text(summary: str, detail: str) -> str:
    try:
        from slate import __version__ as version
    except Exception:
        version = "?"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"{summary}\n\nSlate {version} - {stamp}\n\n{detail}".strip()


def show_details(parent, summary: str, detail: str, level: str = "error") -> None:
    """The full text behind a toast's Details button, with a copy button."""
    box = _box(parent,
               QMessageBox.Icon.Critical if level == "error" else QMessageBox.Icon.Information,
               "Details", summary, QMessageBox.StandardButton.Close)
    box.setDetailedText(str(detail))
    copy = box.addButton("Copy details for IT", QMessageBox.ButtonRole.ActionRole)
    box.exec()
    if box.clickedButton() is copy:
        QApplication.clipboard().setText(_details_text(summary, str(detail)))


def show_error(parent, summary: str, detail: str = "", *, exc: BaseException = None,
               retry: Optional[Callable[[], object]] = None, title: str = "",
               hint: str = "") -> bool:
    """
    Something failed. Say so in plain words, and offer what can be done.

    summary  one sentence the person can act on: "Could not open Scheduling."
    hint     optional next step: "Check the network, then try again."
    detail   the technical part (exception, traceback). It goes behind "Show
             Details..." and "Copy details for IT", never in the main text.
    retry    when given, a "Try again" button runs it.

    Returns True when the person pressed Try again (after running retry).
    """
    if exc is not None and not detail:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if not hint:
        hint = ("Try again. If it keeps happening, copy the details and send them to IT."
                if retry else "If it keeps happening, copy the details and send them to IT.")

    box = _box(parent, QMessageBox.Icon.Warning, title or summary.rstrip("."), summary,
               QMessageBox.StandardButton.NoButton, informative=hint)
    if detail:
        box.setDetailedText(str(detail))
    retry_button = box.addButton("Try again", QMessageBox.ButtonRole.AcceptRole) if retry else None
    copy_button = box.addButton("Copy details for IT", QMessageBox.ButtonRole.ActionRole) if detail else None
    close_button = box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(retry_button or close_button)
    box.setEscapeButton(close_button)

    while True:
        box.exec()
        clicked = box.clickedButton()
        if copy_button is not None and clicked is copy_button:
            QApplication.clipboard().setText(_details_text(summary, str(detail)))
            # Copying is not an answer: keep the box so they can still retry.
            copy_button.setText("Copied")
            continue
        break

    if retry_button is not None and clicked is retry_button:
        try:
            retry()
        except Exception as again:
            logger.exception("Retry failed: %s", again)
            return show_error(parent, summary, exc=again, retry=retry, title=title, hint=hint)
        return True
    return False
