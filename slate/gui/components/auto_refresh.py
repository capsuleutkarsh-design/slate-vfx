"""
Keep a screen current while it is open.

Tabs are built once and kept, so a screen that reads the database only when it
is built - or after its own buttons - never sees what other people do. The IT
queue showed a ticket raised on another workstation only after Slate was
restarted.

With the change feed (components/change_feed.py) a screen re-reads only when
one of its tables actually changed: at once if it is on screen, or when it is
next shown. Without the feed it falls back to re-reading on a timer.
"""

import time

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication


class AutoRefresh(QObject):
    """
    Calls refresh() when one of `topics` (table names) changes, and when the
    widget is shown after a change it missed. Without the change feed it calls
    refresh() every `seconds` while the widget is visible instead.

    Never while a dialog is open over it (a ticket thread, a status picker) -
    rows must not move under somebody working on one - and at most once every
    couple of seconds; a change that arrives in between is done afterwards,
    not dropped.
    """

    MIN_GAP_SECONDS = 2.0
    RETRY_MS = 1500

    def __init__(self, widget, refresh, seconds=30, topics=()):
        self._widget = None                  # set before Qt can call eventFilter
        super().__init__(widget)
        self._widget = widget
        self._refresh = refresh
        self._last = time.monotonic()        # the widget has just read everything
        self._dirty = False
        self._retry_pending = False

        self._feed = None
        if topics:
            from slate.gui.components.change_feed import ChangeFeed
            self._feed = ChangeFeed.instance()
            if self._feed is not None:
                self._feed.watch(self, topics, self._on_change)

        self._timer = QTimer(self)
        self._timer.setInterval(int(seconds * 1000))
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        widget.installEventFilter(self)

    @property
    def _feed_live(self):
        return self._feed is not None and self._feed.available

    # ------------------------------------------------------------- triggers
    def _on_change(self, _changes):
        self._dirty = True
        if self._widget is not None and self._widget.isVisible():
            self._run()

    def _tick(self):
        if self._widget is None or not self._widget.isVisible():
            return
        if self._feed_live and not self._dirty:
            return                           # nothing changed: no read at all
        self._dirty = True
        self._run()

    def eventFilter(self, obj, event):
        # Qt can still deliver events while the Python side is being torn
        # down, so nothing here may assume our attributes are all there.
        widget = getattr(self, "_widget", None)
        if widget is not None and obj is widget and event.type() == QEvent.Type.Show:
            if getattr(self, "_dirty", False) or not self._feed_live:
                self._dirty = True
                QTimer.singleShot(0, self, self._run)
        return False

    # --------------------------------------------------------------- the read
    def _run(self):
        if not self._dirty:
            return
        wait = self.MIN_GAP_SECONDS - (time.monotonic() - self._last)
        if wait > 0 or QApplication.activeModalWidget() is not None:
            self._retry_later(max(int(wait * 1000), self.RETRY_MS))
            return
        self._dirty = False
        self._last = time.monotonic()
        try:
            self._refresh()
        except RuntimeError:                 # the widget is being torn down
            self._timer.stop()

    def _retry_later(self, ms):
        if self._retry_pending:
            return
        self._retry_pending = True

        def again():
            self._retry_pending = False
            if self._widget is not None and self._widget.isVisible():
                self._run()
        QTimer.singleShot(ms, self, again)
