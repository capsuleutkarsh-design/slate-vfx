"""
Keep an open dashboard current without reloading the project.

The dashboard used to ask the database every three seconds whether anything in
the project had changed, and on any change reload every shot - which threw
away edits somebody had typed but not saved yet, and lost their place.

Now the change feed (gui/components/change_feed.py) names the shots that
changed. Only those are read, and they are swapped into the grid in place:

- a shot with unsaved edits here is never replaced. It is marked instead, so
  the person sees that somebody else saved it too before they save over it;
- the selection and the scroll position stay where they were;
- nothing happens while a cell is being edited or a dialog is open - it is
  done straight afterwards, not dropped.

Without the feed (an old database, or it cannot be read) the old three-second
check stays on as a fallback, and a change there reads the project once and
merges it the same careful way.
"""

import logging
import weakref
from dataclasses import dataclass, field
from typing import Callable, List, Set

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QAbstractItemView, QApplication

logger = logging.getLogger(__name__)

TOPICS = ("tracking_shots", "tracking_tasks")


@dataclass
class MergeResult:
    shots: list
    replaced: list = field(default_factory=list)     # the new objects
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)      # the old objects
    kept: list = field(default_factory=list)         # unsaved here, changed elsewhere

    @property
    def changed(self) -> bool:
        return bool(self.replaced or self.added or self.removed or self.kept)


def _shot_id(shot):
    try:
        value = int(getattr(shot, "id", None))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def merge_shots(current: list, fresh: list, checked_ids: Set[int],
                visible: Callable = lambda s: True,
                protected: Callable = lambda s: bool(getattr(s, "_modified", False))
                ) -> MergeResult:
    """
    The shot list after taking in `fresh`, which is what the database holds now
    for `checked_ids`.

    A checked shot that did not come back was deleted or moved to another
    project. `visible` says whether this person should see a shot at all (an
    artist sees only their own). `protected` shots - unsaved edits - stay as
    they are and are reported in `kept`.
    """
    fresh_by_id = {}
    for shot in fresh:
        sid = _shot_id(shot)
        if sid is not None:
            fresh_by_id[sid] = shot

    result = MergeResult(shots=[])
    for shot in current:
        sid = _shot_id(shot)
        if sid is None or sid not in checked_ids:
            result.shots.append(shot)
            continue
        new = fresh_by_id.pop(sid, None)
        if protected(shot):
            result.shots.append(shot)
            result.kept.append(shot)
            continue
        if new is None or not visible(new):
            result.removed.append(shot)
            continue
        result.shots.append(new)
        result.replaced.append(new)

    for sid in sorted(fresh_by_id):
        new = fresh_by_id[sid]
        if visible(new):
            result.shots.append(new)
            result.added.append(new)
    return result


class DashboardLiveUpdateMixin:
    """Mixed into DashboardWidget."""

    LIVE_DEBOUNCE_MS = 300
    LIVE_RETRY_MS = 1500

    # ------------------------------------------------------------ wiring
    def _init_live_updates(self):
        if getattr(self, "_live_ready", False):
            return
        self._live_ready = True
        self._live_pending_ids = set()
        self._live_pending_full = False
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.timeout.connect(self._apply_live_changes)

        from slate.gui.components.change_feed import ChangeFeed
        self._live_feed = ChangeFeed.instance()
        if self._live_feed is not None:
            ref = weakref.ref(self)

            def _heard(changes, ref=ref):
                widget = ref()
                if widget is not None:
                    widget._on_live_changes(changes)

            self._live_feed.watch(self, TOPICS, _heard)

    def _live_feed_is_up(self) -> bool:
        feed = getattr(self, "_live_feed", None)
        return bool(feed is not None and feed.available)

    def _start_live_updates(self, project_code):
        """Called when a project is opened from the database."""
        self._init_live_updates()
        self._live_pending_ids.clear()
        self._live_pending_full = False
        if getattr(self, "local_mode", False):
            self.log("LOCAL MODE: live updates disabled.")
            return

        # The fallback check. It sleeps without touching the database while
        # the change feed is working, and wakes up if the feed goes away.
        from slate.gui.tabs.vfx_dashboard_pro.core.poll_worker import PollWorker
        from slate.core.infra.database_manager import database_manager

        self._cleanup_poll_worker(timeout_ms=1500)
        self.poll_worker = PollWorker(project_code, database_manager,
                                      skip_when=self._live_feed_is_up)
        self.poll_worker.updates_available.connect(self.on_project_data_updated)
        self.poll_worker.start()

    # ------------------------------------------------------------ hearing
    def _on_live_changes(self, changes):
        """From the change feed: {table: {shot ids}}. None means 'unknown'."""
        if getattr(self, "_is_closing", False) or not getattr(self, "_live_ready", False):
            return
        for keys in changes.values():
            for key in keys:
                if key is None:
                    self._live_pending_full = True
                    continue
                try:
                    self._live_pending_ids.add(int(key))
                except (TypeError, ValueError):
                    continue
        self._schedule_live_apply(self.LIVE_DEBOUNCE_MS)

    def _queue_live_full(self):
        """Something changed, but not which shots: read the project and merge."""
        self._init_live_updates()
        self._live_pending_full = True
        self._schedule_live_apply(self.LIVE_DEBOUNCE_MS)

    def _schedule_live_apply(self, ms):
        timer = getattr(self, "_live_timer", None)
        if timer is not None and not timer.isActive():
            timer.start(ms)

    def _apply_pending_live_changes_on_show(self):
        if getattr(self, "_live_ready", False) and (self._live_pending_ids or self._live_pending_full):
            self._live_timer.start(0)

    # ------------------------------------------------------------ applying
    def _live_busy(self) -> bool:
        """True while taking a change now would pull something from under the user."""
        table = getattr(self, "table", None)
        if table is not None and table.state() == QAbstractItemView.State.EditingState:
            return True
        return QApplication.activeModalWidget() is not None

    def _apply_live_changes(self):
        if getattr(self, "_is_closing", False):
            return
        if not (self._live_pending_ids or self._live_pending_full):
            return

        from slate.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler
        handler = getattr(self, "data_handler", None)
        if not isinstance(handler, SQLiteHandler) or not getattr(self, "current_project", None):
            # An Excel project has no rows in the database to follow.
            self._live_pending_ids.clear()
            self._live_pending_full = False
            return
        if not self.isVisible():
            return                                   # done when next shown
        if self._live_busy():
            self._schedule_live_apply(self.LIVE_RETRY_MS)
            return

        full = self._live_pending_full
        ids = set(self._live_pending_ids)
        self._live_pending_ids.clear()
        self._live_pending_full = False

        current = list(getattr(self, "all_shots", None) or [])
        try:
            if full:
                fresh = handler.read_shots()
                if current and not fresh:
                    # read_shots() answers a failure with an empty list. A
                    # whole project vanishing is far likelier to be that.
                    raise RuntimeError("the project came back empty")
                checked = {_shot_id(s) for s in current} | {_shot_id(s) for s in fresh}
                checked.discard(None)
            else:
                fresh = handler.read_shots_by_id(ids)
                checked = ids
        except Exception as exc:
            # Try again shortly rather than lose the change.
            logger.debug("Live update read failed: %s", exc)
            self._live_pending_ids |= ids
            self._live_pending_full = self._live_pending_full or full
            self._schedule_live_apply(self.LIVE_RETRY_MS * 4)
            return

        result = merge_shots(
            current, fresh, checked,
            visible=lambda s: bool(self._filter_shots_for_current_user([s])),
        )
        if not result.changed:
            return
        self._show_merged_shots(result)

    def _show_merged_shots(self, result: MergeResult):
        table = getattr(self, "table", None)
        model = getattr(self, "table_model", None)

        # Where the person is, so the grid looks the same afterwards.
        selected_ids = []
        if table is not None and model is not None and table.selectionModel():
            for index in table.selectionModel().selectedRows():
                sid = _shot_id(model.get_shot_at(index.row()))
                if sid is not None:
                    selected_ids.append(sid)
        scroll = None
        if table is not None:
            scroll = (table.verticalScrollBar().value(), table.horizontalScrollBar().value())

        newly_marked = []
        for shot in result.kept:
            if not getattr(shot, "_remote_changed", False):
                shot._remote_changed = True
                newly_marked.append(shot)

        self.all_shots = result.shots
        self._keep_undo = True
        try:
            search = getattr(self, "search_input", None)
            if search is not None and (search.text() or "").startswith("?"):
                # A semantic search result: swap objects, never re-run the search.
                by_id = {_shot_id(s): s for s in result.replaced}
                gone = {id(s) for s in result.removed}
                self.displayed_shots = [by_id.get(_shot_id(s), s) for s in self.displayed_shots
                                        if id(s) not in gone]
                self.update_table()
            else:
                self.populate_filters()
                self.apply_filters()
        finally:
            self._keep_undo = False

        if table is not None and model is not None:
            if selected_ids and table.selectionModel():
                from PySide6.QtCore import QItemSelection, QItemSelectionModel
                wanted = set(selected_ids)
                selection = QItemSelection()
                for row in range(model.rowCount()):
                    if _shot_id(model.get_shot_at(row)) in wanted:
                        selection.select(model.index(row, 0),
                                         model.index(row, model.columnCount() - 1))
                table.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            if scroll is not None:
                table.verticalScrollBar().setValue(scroll[0])
                table.horizontalScrollBar().setValue(scroll[1])

        if result.added:
            try:
                self.start_thumbnail_loading()
            except Exception as exc:
                logger.debug("Thumbnails for new shots skipped: %s", exc)

        self._tell_about_live_changes(result, newly_marked)

    def _tell_about_live_changes(self, result: MergeResult, newly_marked):
        parts = []
        if result.replaced:
            parts.append(f"{len(result.replaced)} updated")
        if result.added:
            parts.append(f"{len(result.added)} added")
        if result.removed:
            parts.append(f"{len(result.removed)} removed")
        if parts and hasattr(self, "status_bar"):
            self.status_bar.showMessage(
                "Shots changed by others: " + ", ".join(parts), 6000)

        if newly_marked:
            names = ", ".join(s.shot_name for s in newly_marked[:5])
            more = f" and {len(newly_marked) - 5} more" if len(newly_marked) > 5 else ""
            self._notify(
                f"Someone else changed {names}{more} while you have unsaved edits "
                f"on it. Your edits are kept - check the shot before you save.",
                "warning", 8000)

        detail = getattr(self, "detail_widget", None)
        if detail is not None:
            open_id = _shot_id(getattr(detail, "shot", None))
            touched = {_shot_id(s) for s in result.replaced} | {_shot_id(s) for s in result.removed}
            if open_id is not None and open_id in touched:
                self._notify(
                    f"{detail.shot.shot_name} was changed by someone else. "
                    f"Open it again to see the latest.", "info", 6000)
