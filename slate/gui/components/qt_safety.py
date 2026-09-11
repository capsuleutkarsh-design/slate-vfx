"""Qt callback safety helpers for teardown-prone async/timer flows."""

from __future__ import annotations

import logging
import weakref
from typing import Callable, Optional

from PySide6.QtCore import QTimer

try:
    import shiboken6
except Exception:  # pragma: no cover - optional runtime dependency
    shiboken6 = None


def is_qobject_alive(obj) -> bool:
    """Best-effort check for wrapped QObject validity."""
    if obj is None:
        return False
    if shiboken6 is None:
        return True
    try:
        return shiboken6.isValid(obj)
    except Exception:
        return False


def safe_single_shot(
    delay_ms: int,
    owner,
    callback: Callable[[], None],
    *,
    skip_when_closing_attr: Optional[str] = "_is_closing",
) -> None:
    """
    Run callback via QTimer.singleShot only if owner is still alive.

    This prevents stale callbacks from touching deleted QWidget wrappers during teardown.
    """
    owner_ref = weakref.ref(owner) if owner is not None else None

    def _invoke():
        obj = owner_ref() if owner_ref else None
        if obj is None:
            return
        if not is_qobject_alive(obj):
            return
        if skip_when_closing_attr and bool(getattr(obj, skip_when_closing_attr, False)):
            return
        try:
            callback()
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "wrapped c++ object" in msg or "has been deleted" in msg:
                logging.debug("Skipping stale singleShot callback: %s", exc)
                return
            logging.exception("Runtime error in singleShot callback: %s", exc)
        except Exception as exc:
            logging.exception("Unhandled singleShot callback error: %s", exc)

    QTimer.singleShot(delay_ms, _invoke)

