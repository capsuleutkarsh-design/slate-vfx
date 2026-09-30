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

The wrapped method is skipped when the database is down, and a short notice
with "Try again" stands in for the screen's table until a refresh succeeds.
"""

from functools import wraps
import logging

logger = logging.getLogger(__name__)

TITLE = "Can't reach the studio database"
BODY = ("Your work is safe - this screen fills in when the connection is back. "
        "If it does not come back, tell IT.")


def _unavailable():
    """Imported lazily so this module does not drag the database in."""
    try:
        from slate.core.infra.postgres_manager import DatabaseUnavailableError
        return DatabaseUnavailableError
    except Exception:                       # pragma: no cover - import guard
        return ()


def show_offline(widget, retry=None):
    """
    Say the database is down, in place of the screen's table (with Try again).

    It used to be one long sentence that contradicted itself ("nothing here is
    out of date - there is simply nothing to show"), and on screens without an
    EmptyState it was squeezed into the first cell of the table. It is now the
    shared notice panel (slate/gui/components/state_notice.py), which stands
    in for the table and goes away on the next successful refresh.
    """
    try:
        from slate.gui.components.state_notice import show_state
        if show_state(widget, TITLE, BODY, retry=retry):
            return True
    except RuntimeError:
        return False
    # No table on this screen: an EmptyState, if there is one, carries it.
    empty = getattr(widget, "empty", None) or getattr(widget, "people_empty", None)
    if empty is not None and hasattr(empty, "set_message"):
        try:
            empty.set_message(TITLE, BODY)
            empty.setVisible(True)
            return True
        except RuntimeError:
            return False
    from slate.gui.components.feedback import toast
    toast(widget, f"{TITLE}. {BODY}", "warning")
    return False


def on_database_error(method):
    """
    Let a refresh fail politely instead of raising into Qt.

    Only DatabaseUnavailableError is caught. Every other exception still
    propagates, because a bug in the screen is not the same as a database
    being unreachable and should not be dressed up as one.

    When the method succeeds, any "can't reach" notice from an earlier try is
    taken away; the notice's Try again runs the method again.
    """
    @wraps(method)
    def guarded(self, *args, **kwargs):
        # A notice from an earlier try goes first; one the method raises
        # itself this time (show_load_error) stays.
        try:
            from slate.gui.components.state_notice import clear_state
            clear_state(self)
        except RuntimeError:
            pass
        try:
            return method(self, *args, **kwargs)
        except _unavailable():
            logger.warning("%s: the database did not answer", type(self).__name__)
            show_offline(self, retry=lambda: guarded(self, *args, **kwargs))
            return None
    return guarded
