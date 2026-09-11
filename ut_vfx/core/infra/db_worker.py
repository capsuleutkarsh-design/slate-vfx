"""
DatabaseWorker — Reusable QRunnable for running DB operations off the GUI thread.

Usage:
    from ut_vfx.core.infra.db_worker import run_db_async

    def on_result(data):
        self.populate_table(data)

    def on_error(msg):
        QMessageBox.warning(self, "DB Error", msg)

    run_db_async(
        lambda: db.execute_query("SELECT * FROM ut_users", fetch="all"),
        on_success=on_result,
        on_error=on_error
    )
"""

import logging
import weakref
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from ut_vfx.core.infra.database_manager import database_manager

try:
    import shiboken6
except Exception:
    shiboken6 = None


class _WorkerSignals(QObject):
    """Signal bridge for DatabaseWorker (QRunnable can't emit signals directly)."""
    finished = Signal(object)  # result data (can be None, list, dict, etc.)
    error = Signal(str)        # error message string


class DatabaseWorker(QRunnable):
    """
    Fire-and-forget database operation on QThreadPool.
    
    Wraps any callable and emits finished/error signals
    when the operation completes.
    """

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)
        self._is_cancelled = False

    def cancel(self):
        """Cancel emitting signals, safely ignoring stale results."""
        self._is_cancelled = True

    def run(self):
        if getattr(self, "_is_cancelled", False):
            return
        
        try:
            result = self.fn(*self.args, **self.kwargs)
            if not getattr(self, "_is_cancelled", False):
                if not _is_owner_valid(self.signals):
                    return
                try:
                    self.signals.finished.emit(result)
                except RuntimeError as emit_exc:
                    msg = str(emit_exc).lower()
                    if "has been deleted" in msg or "signal source has been deleted" in msg:
                        logging.debug("Skipping emit on deleted worker signal source: %s", emit_exc)
                        return
                    raise
        except Exception as e:
            if not getattr(self, "_is_cancelled", False):
                logging.exception(f"DatabaseWorker error: {e}")
                if not _is_owner_valid(self.signals):
                    return
                try:
                    context = database_manager.runtime_context_summary()
                    self.signals.error.emit(f"{e} [{context}]")
                except RuntimeError as emit_exc:
                    msg = str(emit_exc).lower()
                    if "has been deleted" in msg or "signal source has been deleted" in msg:
                        logging.debug("Skipping error emit on deleted worker signal source: %s", emit_exc)
                        return
                    raise


def _get_callback_owner(callback):
    """Return bound QObject owner for a callback, if present."""
    owner = getattr(callback, "__self__", None)
    if isinstance(owner, QObject):
        return owner
    return None


def _is_owner_valid(owner):
    """Best-effort QObject validity check (protects against deleted Qt wrappers)."""
    if owner is None:
        return True
    if shiboken6 is None:
        return True
    try:
        return shiboken6.isValid(owner)
    except Exception:
        return True


def _wrap_safe_callback(callback):
    """
    Wrap callback so stale/deleted QObject receivers are skipped cleanly.
    This prevents common "wrapped C++ object has been deleted" crashes during teardown.
    """
    if callback is None:
        return None, None

    owner = _get_callback_owner(callback)
    owner_ref = weakref.ref(owner) if owner is not None else None

    def _safe_invoke(*args):
        target_owner = owner_ref() if owner_ref else None
        if target_owner is not None and not _is_owner_valid(target_owner):
            return
        try:
            callback(*args)
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "has been deleted" in msg or "wrapped c++ object" in msg:
                logging.debug("Skipping stale async callback: %s", exc)
                return
            logging.exception("Runtime error in async callback: %s", exc)
        except Exception as exc:
            logging.exception("Unhandled error in async callback: %s", exc)

    return _safe_invoke, owner


def run_db_async(fn, on_success=None, on_error=None, owner=None):
    """
    Convenience function to run a DB operation asynchronously.
    
    Args:
        fn: Callable that performs the DB operation (will run on thread pool)
        on_success: Optional callback(result) called on main thread when done
        on_error: Optional callback(error_msg) called on main thread on failure
        owner: Optional QObject owner. If destroyed, worker callbacks are cancelled.
    
    Returns:
        The DatabaseWorker instance (keep a reference if you need to track it)
    """
    worker = DatabaseWorker(fn)

    safe_success, success_owner = _wrap_safe_callback(on_success)
    safe_error, error_owner = _wrap_safe_callback(on_error)

    if safe_success:
        worker.signals.finished.connect(safe_success)
    if safe_error:
        worker.signals.error.connect(safe_error)

    owners = []
    if owner is not None and isinstance(owner, QObject):
        owners.append(owner)
    if success_owner is not None:
        owners.append(success_owner)
    if error_owner is not None:
        owners.append(error_owner)

    seen = set()
    for qobj in owners:
        obj_id = id(qobj)
        if obj_id in seen:
            continue
        seen.add(obj_id)
        try:
            qobj.destroyed.connect(worker.cancel)
        except Exception:
            # Best-effort wiring only; worker cancellation still works manually.
            pass

    QThreadPool.globalInstance().start(worker)
    return worker
