"""
Asking the open screens before Slate closes, signs out or syncs.

The main window used to close without asking any tab anything. A tab never
receives closeEvent when the window closes, so the Dashboard's own "you have
unsaved edits" question was never reached on quit or sign-out, and an ingest
half way through a sequence was stopped after two seconds and deleted.

A tab (or any widget inside one) takes part by implementing any of these. All
are optional; a screen with nothing to lose implements none.

    has_unsaved_changes() -> bool
        Edits that are not in the database yet.
    unsaved_summary() -> str
        Optional: "3 shots have unsaved edits". Used in the combined question.
    confirm_discarding_changes(action: str) -> bool
        Optional: ask in the screen's own words (Save / Discard / Cancel).
        `action` completes "Save them before you ...?" - "close Slate",
        "sign out", "sync". Return True when it is safe to go on.

    busy_reason() -> str | None
        Work in progress that stopping would cut short, in words:
        "An ingest is copying files to DEMO_PRJ." None when idle.
    shutdown(timeout_ms: int) -> bool
        Stop that work cleanly (at a file boundary, not mid-copy) within
        roughly timeout_ms. Return True once it has stopped.

    from slate.gui.components.work_guard import confirm_leave
    if not confirm_leave(self, self.tab_coordinator.tab_instances, "close Slate"):
        event.ignore()
        return
"""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Tuple

from PySide6.QtWidgets import QMessageBox, QWidget

logger = logging.getLogger(__name__)

_UNSAVED = ("has_unsaved_changes", "confirm_discarding_changes")
_BUSY = ("busy_reason",)


def _implements(obj, names) -> bool:
    return any(callable(getattr(obj, name, None)) for name in names)


def _participants(page: QWidget) -> List[QWidget]:
    """
    The widgets on one tab that take part: the tab itself when it does,
    otherwise anything inside it that does (the Leave tab, for instance, is a
    QTabWidget holding two views).
    """
    if page is None:
        return []
    try:
        if _implements(page, _UNSAVED + _BUSY):
            return [page]
        found = []
        for child in page.findChildren(QWidget):
            if _implements(child, _UNSAVED + _BUSY):
                found.append(child)
        return found
    except RuntimeError:            # the tab is being torn down
        return []


def _safe(call, default):
    try:
        return call()
    except RuntimeError:
        return default
    except Exception as exc:
        # A screen that cannot answer is treated as having nothing to lose,
        # rather than making Slate impossible to close.
        logger.warning("Work guard: %s", exc)
        return default


def pending_work(tabs: Dict[str, QWidget]) -> Tuple[list, list]:
    """
    What the open tabs would lose.

    Returns (unsaved, busy): unsaved is [(label, widget)], busy is
    [(label, widget, reason)].
    """
    unsaved, busy = [], []
    for label, page in list((tabs or {}).items()):
        for widget in _participants(page):
            if callable(getattr(widget, "has_unsaved_changes", None)):
                if _safe(widget.has_unsaved_changes, False):
                    unsaved.append((label, widget))
            if callable(getattr(widget, "busy_reason", None)):
                reason = _safe(widget.busy_reason, None)
                if reason:
                    busy.append((label, widget, str(reason)))
    return unsaved, busy


def _summary(label, widget) -> str:
    text = ""
    if callable(getattr(widget, "unsaved_summary", None)):
        text = _safe(widget.unsaved_summary, "") or ""
    return f"{label}: {text}" if text else f"{label} has unsaved changes"


def confirm_leave(parent: QWidget, tabs: Dict[str, QWidget], action: str,
                  stop_busy: bool = True, timeout_ms: int = 15000) -> bool:
    """
    Ask every open tab before `action` ("close Slate", "sign out", "sync").

    Unsaved work first: a tab with its own question asks it (it can offer to
    save); the rest are listed together in one question whose safe answer is
    the default. Then running work: "An ingest is copying files - stop it and
    close anyway?" defaulting to No; on Yes each busy tab is asked to stop
    cleanly and we wait for it.

    Returns True when it is safe to go on.
    """
    unsaved, busy = pending_work(tabs)

    plain = []
    for label, widget in unsaved:
        if callable(getattr(widget, "confirm_discarding_changes", None)):
            if not _safe(lambda: widget.confirm_discarding_changes(action), True):
                return False
        else:
            plain.append(_summary(label, widget))

    if plain:
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unsaved changes")
        box.setText("Some screens have changes that are not saved:")
        box.setInformativeText("\n".join("• " + line for line in plain)
                               + f"\n\nIf you {action} now, these changes are lost.")
        go = box.addButton(f"Discard and {action}", QMessageBox.ButtonRole.DestructiveRole)
        stay = box.addButton("Go back", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(stay)
        box.setEscapeButton(stay)
        box.exec()
        if box.clickedButton() is not go:
            return False

    if busy:
        reasons = "\n".join("• " + reason for _label, _widget, reason in busy)
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Work in progress")
        box.setText("Slate is still working:")
        box.setInformativeText(
            reasons + f"\n\nStop it and {action}? It stops at the next safe point "
            "- nothing is left half-copied.")
        go = box.addButton(f"Stop and {action}", QMessageBox.ButtonRole.DestructiveRole)
        stay = box.addButton("Keep working", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(stay)
        box.setEscapeButton(stay)
        box.exec()
        if box.clickedButton() is not go:
            return False
        if stop_busy:
            stop_all(busy, timeout_ms)
    return True


def stop_all(busy: Iterable, timeout_ms: int = 15000) -> bool:
    """Ask each busy screen to stop cleanly; True when all of them did."""
    all_stopped = True
    for label, widget, _reason in busy:
        stopper = getattr(widget, "shutdown", None)
        if not callable(stopper):
            continue
        stopped = _safe(lambda: stopper(timeout_ms), False)
        if not stopped:
            all_stopped = False
            logger.warning("Work guard: %s did not stop within %d ms", label, timeout_ms)
    return all_stopped
