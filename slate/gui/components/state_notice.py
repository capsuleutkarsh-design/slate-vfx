"""
What a screen shows instead of its table when it could not read anything.

Two different things used to look exactly like "there is nothing here":

* the database did not answer - some screens said so in one long,
  self-contradicting sentence squeezed into the first cell of the table;
* the read failed for another reason - the Hardware and Deployment tabs
  swallowed the error and said "No machines registered yet".

Both now get the same panel in place of the table - icon, a short title, one
line, and "Try again" - and it goes away by itself the next time the screen
loads successfully.

    from slate.gui.components.state_notice import show_load_error, clear_state

    def load_data(self):
        try:
            rows = self.repo.all()
        except Exception as exc:
            logger.exception("Hardware could not be read")
            show_load_error(self, exc, retry=self.load_data, what="the machine list")
            return
        clear_state(self)
        ...

The database-down case is handled for you by
slate.gui.core.offline_notice.on_database_error.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QBoxLayout, QHBoxLayout, QLabel, QSplitter, QVBoxLayout, QWidget,
)

logger = logging.getLogger(__name__)

_ATTR = "_slate_state_notice"


class StateNotice(QWidget):
    """The panel: glyph, title, one line, and optional buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from slate.core.infra.gate import Gate
        self.setObjectName("stateNotice")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#stateNotice {{ background: {Gate.PANEL}; "
                           f"border: 1px solid {Gate.LINE}; border-radius: {Gate.RADIUS_LG}px; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 32, 24, 32)
        outer.setSpacing(0)
        outer.addStretch(1)

        self.mark = QLabel()
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mark.setStyleSheet("background: transparent; border: none;")
        outer.addWidget(self.mark)
        outer.addSpacing(Gate.SPACE_3)

        self.title = QLabel()
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setWordWrap(True)
        self.title.setStyleSheet(
            f"color: {Gate.TEXT}; font-family: {Gate.FONT_UI}; font-size: {Gate.SIZE_LG}px; "
            "font-weight: 600; background: transparent; border: none;")
        outer.addWidget(self.title)

        self.body = QLabel()
        self.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.setWordWrap(True)
        self.body.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-family: {Gate.FONT_UI}; font-size: {Gate.SIZE_MD}px; "
            "background: transparent; border: none;")
        outer.addSpacing(Gate.SPACE_1)
        outer.addWidget(self.body)

        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(Gate.SPACE_2)
        outer.addSpacing(Gate.SPACE_4)
        outer.addLayout(self.buttons)
        outer.addStretch(1)
        self._button_widgets = []

    def set_content(self, title: str, body: str = "", glyph: str = "alert",
                    actions=()):
        from slate.core.infra.gate import Gate
        from slate.gui.core.controls import make_button
        try:
            from slate.gui.core.icons import icon
            self.mark.setPixmap(icon(glyph, Gate.WARN if glyph == "alert" else Gate.LINE, 34)
                                .pixmap(34, 34))
        except Exception:
            self.mark.clear()
        self.title.setText(title)
        self.body.setText(body)
        self.body.setVisible(bool(body))
        for widget in self._button_widgets:
            widget.deleteLater()
        self._button_widgets = []
        while self.buttons.count():
            self.buttons.takeAt(0)
        self.buttons.addStretch(1)
        for index, (label, callback, kind) in enumerate(actions or ()):
            button = make_button(label, kind or ("primary" if index == 0 else "secondary"),
                                 on_click=callback)
            self.buttons.addWidget(button)
            self._button_widgets.append(button)
        self.buttons.addStretch(1)


def _target_view(widget: QWidget):
    """The table (or list) a screen shows its records in."""
    for name in ("table", "grid", "hw_table", "tbl", "list_view", "view"):
        candidate = getattr(widget, name, None)
        if isinstance(candidate, QAbstractItemView):
            return candidate
    try:
        return widget.findChild(QAbstractItemView)
    except RuntimeError:
        return None


def _empty_state(widget: QWidget):
    for name in ("empty", "people_empty", "empty_state"):
        candidate = getattr(widget, name, None)
        if isinstance(candidate, QWidget):
            return candidate
    return None


class _Overlay(QObject):
    """Keeps a notice laid over a widget it could not be put beside."""

    def __init__(self, notice, target):
        super().__init__(notice)
        self.notice, self.target = notice, target
        target.installEventFilter(self)
        self.fit()

    def fit(self):
        try:
            self.notice.setGeometry(self.target.geometry())
            self.notice.raise_()
        except RuntimeError:
            pass

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show):
            self.fit()
        return False


def _notice_for(widget: QWidget) -> Optional[StateNotice]:
    notice = getattr(widget, _ATTR, None)
    if notice is not None:
        try:
            notice.isVisible()
            return notice
        except RuntimeError:
            setattr(widget, _ATTR, None)
    view = _target_view(widget)
    anchor = view
    if anchor is None:
        return None
    parent = anchor.parentWidget()
    notice = StateNotice()
    placed = False
    if isinstance(parent, QSplitter):
        parent.insertWidget(parent.indexOf(anchor), notice)
        placed = True
    elif parent is not None and isinstance(parent.layout(), QBoxLayout):
        layout = parent.layout()
        index = layout.indexOf(anchor)
        if index >= 0:
            layout.insertWidget(index, notice, layout.stretch(index) or 1)
            placed = True
    if not placed and parent is not None:
        notice.setParent(parent)
        _Overlay(notice, anchor)
    notice.hide()
    setattr(widget, _ATTR, notice)
    return notice


def show_state(widget: QWidget, title: str, body: str = "", *,
               retry: Callable = None, glyph: str = "alert", details: str = "") -> bool:
    """
    Put the notice where the screen's table is. Returns False when the screen
    has no table to stand in for (the caller should then use a toast).
    """
    try:
        notice = _notice_for(widget)
    except RuntimeError:
        return False
    if notice is None:
        return False
    actions = []
    if retry is not None:
        actions.append(("Try again", retry, "primary"))
    if details:
        from .feedback import show_details
        actions.append(("Details", lambda: show_details(widget, title, details), "secondary"))
    notice.set_content(title, body, glyph, actions)
    view = _target_view(widget)
    empty = _empty_state(widget)
    try:
        if view is not None:
            view.setVisible(False)
        if empty is not None:
            empty.setVisible(False)
        notice.setVisible(True)
        notice.raise_()
    except RuntimeError:
        return False
    return True


def clear_state(widget: QWidget) -> None:
    """Take the notice away and give the table (or its empty state) its place back."""
    notice = getattr(widget, _ATTR, None)
    if notice is None:
        return
    try:
        if not notice.isVisible():
            return
        notice.setVisible(False)
        empty = _empty_state(widget)
        view = _target_view(widget)
        if empty is not None and hasattr(empty, "refresh"):
            empty.refresh()           # decides between the table and "nothing yet"
        elif view is not None:
            view.setVisible(True)
    except RuntimeError:
        pass


def show_load_error(widget: QWidget, error, retry: Callable = None,
                    what: str = "this list") -> None:
    """
    A read failed for a reason other than the database being down: say so,
    distinctly from "nothing here yet", with the reason behind Details.
    """
    reason = str(error) if error is not None else ""
    title = f"Could not load {what}"
    body = "Something went wrong while reading it. Try again - if it keeps happening, tell IT."
    if not show_state(widget, title, body, retry=retry, details=reason):
        from .feedback import toast
        toast(widget, f"{title}.", "error", details=reason)
