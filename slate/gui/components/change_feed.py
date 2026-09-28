"""
Hear about other people's changes without re-reading everything.

One ChangeFeed per Slate window. A background thread asks the database for
feed rows newer than the last one it saw (see core/infra/migrations/
change_feed.py for where they come from) every few seconds - one indexed read
that is empty almost all the time - and hands each open screen the rows for
the tables it watches, on the GUI thread.

If the feed is not there (an old database, a user without the right to create
triggers), `available` stays False and screens keep re-reading on a timer.
"""

import logging
import weakref

from PySide6.QtCore import QObject, QThread, Signal

logger = logging.getLogger(__name__)

POLL_MS = 5000               # how often to ask; the header's speed dot asks as often
RETRY_MS = 60000             # after the feed was missing or the database was down
KEEP_ROWS = 100000           # older feed rows are deleted now and then
BATCH = 2000


def latest_id(db):
    row = db.execute_query("SELECT MAX(id) AS last_id FROM slate_change_feed", fetch="one")
    return int((row or {}).get("last_id") or 0)


def read_since(db, last_id):
    """({topic: {row keys}}, newest id) for everything after last_id."""
    rows = db.execute_query(
        "SELECT id, topic, row_key FROM slate_change_feed WHERE id > %s ORDER BY id LIMIT %s",
        (int(last_id), BATCH), fetch="all") or []
    changes = {}
    newest = last_id
    for row in rows:
        changes.setdefault(str(row["topic"]), set()).add(
            None if row["row_key"] is None else str(row["row_key"]))
        newest = max(newest, int(row["id"]))
    return changes, newest


def prune(db, newest):
    if newest > KEEP_ROWS:
        db.execute_update("DELETE FROM slate_change_feed WHERE id < %s", (newest - KEEP_ROWS,))


class _FeedWorker(QThread):
    found = Signal(object)          # {topic: {row keys}}
    status = Signal(bool)           # the feed can be read, or not

    def __init__(self, db):
        super().__init__()
        self.db = db
        self._stop = False

    def stop(self):
        self._stop = True
        self.requestInterruption()
        self.wait(3000)

    def _nap(self, ms):
        waited = 0
        while waited < ms and not (self._stop or self.isInterruptionRequested()):
            self.msleep(100)
            waited += 100
        return not (self._stop or self.isInterruptionRequested())

    def run(self):
        last = None
        checks = 0
        while not (self._stop or self.isInterruptionRequested()):
            try:
                if last is None:
                    last = latest_id(self.db)          # start from now, not from history
                    self.status.emit(True)
                else:
                    changes, newest = read_since(self.db, last)
                    if changes:
                        last = newest
                        self.found.emit(changes)
                    checks += 1
                    if checks % 720 == 0:              # about once an hour
                        prune(self.db, last)
                if not self._nap(POLL_MS):
                    break
            except Exception as exc:
                logger.debug("Change feed not readable: %s", exc)
                last = None
                self.status.emit(False)
                if not self._nap(RETRY_MS):
                    break


class ChangeFeed(QObject):
    """The one feed for this window; screens call watch()."""

    _instance = None

    def __init__(self, db=None):
        super().__init__()
        if db is None:
            from slate.core.infra.database_manager import database_manager
            db = database_manager
        self.available = False
        self._subs = []                            # (weakref to owner, topics, callback)
        self._worker = _FeedWorker(db)
        self._worker.found.connect(self._dispatch)
        self._worker.status.connect(self._set_available)
        self._worker.start()

    @classmethod
    def instance(cls):
        if cls._instance is None:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is None:
                return None
            cls._instance = cls()
            cls._instance.setParent(app)
            app.aboutToQuit.connect(cls._instance.shutdown)
        return cls._instance

    def shutdown(self):
        self._worker.stop()

    def watch(self, owner, topics, callback):
        """callback({topic: {row keys}}) on the GUI thread, for these tables only."""
        self._subs.append((weakref.ref(owner), frozenset(topics), callback))

    def _set_available(self, ok):
        ok = bool(ok)
        self.available = ok
        if not ok:
            self._down = True
        elif getattr(self, "_down", False):
            # Back after an outage, starting from now. What changed while it
            # was down is unknown, so every screen re-reads once. A row key of
            # None means exactly that.
            self._down = False
            self._dispatch({t: {None} for _ref, topics, _cb in self._subs for t in topics})

    def _dispatch(self, changes):
        alive = []
        for ref, topics, callback in self._subs:
            owner = ref()
            if owner is None:
                continue
            alive.append((ref, topics, callback))
            mine = {t: keys for t, keys in changes.items() if t in topics}
            if mine:
                try:
                    callback(mine)
                except RuntimeError:               # the screen was closed
                    pass
                except Exception:
                    logger.exception("A screen failed to take a change")
        self._subs = alive
