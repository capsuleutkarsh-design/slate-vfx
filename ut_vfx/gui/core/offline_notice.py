"""
What a screen shows when the database will not answer.

The repositories used to swallow a database failure and hand back an empty
list, which meant a leave balance of "16 days available, nothing used" during
an outage. They now let DatabaseUnavailableError through, which is correct and
which makes this the other half of the fix: somebody has to catch it and say
so, or the screen simply breaks instead of lying.

    @on_database_error
    def refresh(self):
        ...

The wrapped method is skipped when the database is down, and the widget's
EmptyState says what happened in words the person can act on.
"""

from functools import wraps
import logging

logger = logging.getLogger(__name__)

TITLE = "The database is not responding"
BODY = ("Nothing was read, so nothing here is out of date - there is simply "
        "nothing to show yet. Your work has not been lost. This usually clears "
        "on its own; if it does not, tell IT that the studio database is "
        "unreachable from this machine.")


def _unavailable():
    """Imported lazily so this module does not drag the database in."""
    try:
        from ut_vfx.core.infra.postgres_manager import DatabaseUnavailableError
        return DatabaseUnavailableError
    except Exception:                       # pragma: no cover - import guard
        return ()


def show_offline(widget):
    """Put the widget's empty state into the 'database is down' message."""
    empty = getattr(widget, "empty", None) or getattr(widget, "people_empty", None)
    if empty is None:
        return
    try:
        if hasattr(empty, "set_message"):
            empty.set_message(TITLE, BODY)
        else:
            for attr, value in (("title", TITLE), ("message", BODY),
                                ("_title", TITLE), ("_message", BODY)):
                if hasattr(empty, attr):
                    setattr(empty, attr, value)
        if hasattr(empty, "refresh"):
            empty.refresh()
        empty.setVisible(True)
    except RuntimeError:
        # The C++ object is gone - the tab was closed while this ran.
        pass


def on_database_error(method):
    """
    Let a refresh fail politely instead of raising into Qt.

    Only DatabaseUnavailableError is caught. Every other exception still
    propagates, because a bug in the screen is not the same as a database
    being unreachable and should not be dressed up as one.
    """
    @wraps(method)
    def guarded(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except _unavailable():
            logger.warning("%s: the database did not answer", type(self).__name__)
            show_offline(self)
            return None
    return guarded
