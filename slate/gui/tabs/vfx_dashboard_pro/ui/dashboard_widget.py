"""
The VFX Dashboard: every shot of a project, as a grid or a board, with the
shot's details beside it.

How saving works (FIX_PLAN, "Dashboard save model"):
    - every edit - a grid cell, a board drop, a batch edit, the detail panel's
      Apply - is a pending change, marked in the grid and undone by Ctrl+Z;
    - one "Save N changes" button writes them, and the count is always on it;
    - closing Slate, signing out, switching project or syncing asks first;
    - somebody without full edit rights (an artist) changes only their own
      department's status, which is saved at once, with an Undo.
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QAction, QCursor, QPixmap
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QWidget

from slate.core.dcc_launcher import DCCLauncher
from slate.core.domain import shot_status
from slate.core.infra.database_manager import database_manager
from slate.core.infra.gate import Gate
from slate.core.infra.global_config import GlobalConfig
from slate.core.infra.qt_compat import Qt, QTimer, QItemSelectionModel
from slate.core.system.adaptation_engine import system_engine

from .add_project_dialog import AddProjectDialog
from .dashboard_layout_builder import _build_project_menu, build_dashboard_ui
from .history_dialog import HistoryDialog
from .shot_detail import ShotDetailWidget
from .shot_table_model import SHOT_ROLE, set_field
from ..core.sqlite_handler import ProjectClosedError, StaleDataError  # noqa: F401

from ..controllers.thumbnail_mixin import DashboardThumbnailMixin
from ..controllers.kanban_mixin import DashboardKanbanMixin
from ..controllers.filter_mixin import DashboardFilterMixin
from .components.dashboard_builder_mixin import DashboardBuilderMixin
from .components.dashboard_actions_mixin import DashboardActionsMixin
from .components.dashboard_project_mixin import DashboardProjectMixin
from ..controllers.live_update_mixin import DashboardLiveUpdateMixin

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else keeps the fallback it already had.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""


class AutoPublishWorker(QThread):
    """Copies an approved shot's comp renders into its output folder, off the UI thread."""

    finished_signal = Signal(bool, str)

    def __init__(self, shot, project_manager, project_code):
        super().__init__()
        self.shot = shot
        self.project_manager = project_manager
        self.project_code = project_code

    def run(self):
        name = getattr(self.shot, "shot_name", "")
        try:
            comp_path = self.project_manager.get_folder_path(
                self.project_code, "comp", self.shot.reel_episode, self.shot.shot_name)
            if not comp_path:
                self.finished_signal.emit(False, f"{name}: the project has no Comp folder set up.")
                return
            comp_output = Path(comp_path) / "Output"
            if not comp_output.exists():
                self.finished_signal.emit(False, f"{name}: there is no Comp/Output folder yet.")
                return
            final_path = self.project_manager.get_folder_path(
                self.project_code, "output", self.shot.reel_episode, self.shot.shot_name)
            if not final_path:
                self.finished_signal.emit(False, f"{name}: the project has no Output folder set up.")
                return
            source = self._version_folder(comp_output)
            dest_exr = Path(final_path) / "EXR"
            dest_mov = Path(final_path) / "MOV"
            copied, failed = 0, []
            # One version only (the shot's current one, else the newest), its
            # images and movies only, sub-folders kept: v001 and v002 frames of
            # the same name used to overwrite each other in one flat folder,
            # with Nuke backups and Thumbs.db copied along.
            for file_path in sorted(source.rglob("*")):
                if not file_path.is_file() or file_path.suffix.lower() not in self.MEDIA:
                    continue
                movie = file_path.suffix.lower() in (".mov", ".mp4")
                target = (dest_mov if movie else dest_exr) / file_path.relative_to(source)
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(file_path), str(target))
                    copied += 1
                except OSError as exc:
                    failed.append(f"{file_path.name} ({exc.strerror or exc})")
            if not copied and not failed:
                self.finished_signal.emit(False, f"{name}: no images or movies in {source}.")
                return
            if failed:
                self.finished_signal.emit(
                    False, f"{name}: copied {copied} file(s); {len(failed)} could not be copied: "
                           + ", ".join(failed[:3]) + ("…" if len(failed) > 3 else ""))
            else:
                self.finished_signal.emit(
                    True, f"{name}: copied {copied} file(s) of {source.name} to "
                          f"{Path(final_path).name or 'the output folder'}.")
        except Exception as e:
            self.finished_signal.emit(False, f"{name}: {e}")

    MEDIA = {".exr", ".dpx", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".mov", ".mp4"}

    def _version_folder(self, comp_output: Path) -> Path:
        """Comp/Output/<the shot's version>, else the highest-numbered version folder, else Output itself."""
        import re
        current = str(getattr(self.shot, "curr_version", "") or "").strip()
        if current and (comp_output / current).is_dir():
            return comp_output / current
        versions = [d for d in comp_output.iterdir() if d.is_dir() and re.search(r"v\d+", d.name, re.I)]
        if versions:
            return max(versions, key=lambda d: int(re.search(r"v(\d+)", d.name, re.I).group(1)))
        return comp_output


class ExcelMirrorWorker(QThread):
    """Writes the Excel backup off the UI thread: a passbook open in Excel used to freeze every Save."""

    done = Signal(bool, object)       # ok, the file's new mtime (or None)

    def __init__(self, service, shots, project, handler, force):
        super().__init__()
        self.args = (service, shots, project, handler, force)

    def run(self):
        service, shots, project, handler, force = self.args
        try:
            ok, mtime = service.mirror_shots_to_excel(shots=shots, current_project=project,
                                                      data_handler=handler, force=force)
        except Exception as exc:                       # never lose the answer
            service.last_backup_error = str(exc)
            ok, mtime = False, None
        self.done.emit(bool(ok), mtime)


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


# Spellings of a role that mean the same role. Nothing here turns one role
# into another (the old table made coordinators and producers "supervisor").
_ROLE_SPELLINGS = {"dev": "developer", "coord": "coordinator", "production": "producer", "pro": "producer"}

# Job-title words that name a department family without using its key, label
# or name ('Head of Paint', 'Senior Compositor', 'Mograph Lead').
_DEPARTMENT_WORDS = {
    "paint": "prep", "painter": "prep", "matte": "dmp", "compositor": "comp",
    "compositing": "comp", "mograph": "mgfx", "motion": "mgfx", "matchmover": "matchmove",
    "tracking": "matchmove", "rotoscoping": "roto", "deage": "deage",
}


class DashboardWidget(
    DashboardBuilderMixin,
    DashboardActionsMixin,
    DashboardProjectMixin,
    DashboardLiveUpdateMixin,
    DashboardFilterMixin,
    DashboardThumbnailMixin,
    DashboardKanbanMixin,
    QWidget
):
    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------
    def _notify(self, message: str, level: str = "info", duration: int = 4000, details: str = "",
                action=None):
        """
        A toast (with an optional action such as Undo). Inside Slate the main
        window keeps the status-bar copy; only a standalone dashboard writes
        its own, so a message is never shown three times at once.
        """
        bar = getattr(self, "status_bar", None)
        window = self.window()
        if bar is not None and (window is self or not callable(getattr(window, "show_feedback", None))):
            bar.showMessage(message, max(duration, 3000))
        try:
            from slate.gui.components.feedback import toast
            toast(self, message, level, action=action, duration=duration, details=details)
        except Exception as exc:
            logging.debug("Toast not shown: %s", exc)

    def refresh_connection_state(self):
        """Reflect whether the central database is reachable."""
        from slate.core.domain.access import is_offline_fallback

        offline = is_offline_fallback()
        banner = getattr(self, "offline_banner", None)
        if banner is not None:
            banner.setVisible(offline)
        scope_banner = getattr(self, "scope_banner", None)
        if scope_banner is not None:
            scope_banner.setVisible(self._department_scope() == set() and not offline)
        self.update_unsaved_indicator()
        return offline

    # ------------------------------------------------------------------
    # Artists' own status: saved at once, with Undo
    # ------------------------------------------------------------------
    def on_own_status_edited(self, shot, dept_key, status, previous="", undoing=False, undo_of=None):
        """
        Save an artist's own status change immediately.

        People without full rights have no Save button, so the change is
        written as it is made - with an Undo on the confirmation, which writes
        the previous status back the same way (``undo_of`` is the status being
        taken back, so the store lets an undo restore a status the artist
        could not pick).
        """
        dept_name = next((d.name for d in self._departments() if d.key == dept_key), dept_key)

        def put_back():
            shot.dept(dept_key).status = previous
            self.table_model.mark_clean([shot])

        if not self.data_handler or not hasattr(self.data_handler, "update_department_status"):
            # A project kept only in its Excel sheet: there is nowhere to save
            # one status to, and it must not look saved.
            put_back()
            self._notify("This project is kept only in its Excel sheet, so statuses cannot be "
                         "changed here. Ask production to move it into Slate.", "warning", 8000)
            return False

        try:
            ok = self.data_handler.update_department_status(
                shot_name=shot.shot_name,
                reel=shot.reel_episode,
                dept_key=dept_key,
                status=status,
                current_version=getattr(shot, "version", 0),
                actor_identities=self._artist_identity_candidates(),
                shot_id=getattr(shot, "id", None),
                undo_of=undo_of,
            )
        except PermissionError as exc:
            put_back()
            self._notify(str(exc), "warning", 8000)
            return False
        except ProjectClosedError as exc:
            put_back()
            self._notify(str(exc), "warning", 10000)
            return False
        except StaleDataError:
            put_back()
            self._notify(f"{shot.shot_name} was changed by someone else a moment ago. "
                         "It has been reloaded - set the status again.", "warning", 8000)
            self.reload_shots()
            return False
        except Exception as exc:
            put_back()
            self._notify("Could not save that status.", "error", details=str(exc))
            return False

        if not ok:
            put_back()
            reason = getattr(self.data_handler, "last_error", "") or ""
            self._notify(f"{shot.shot_name}: the status could not be saved.", "error", details=reason)
            return False

        shot.dept(dept_key).status = status
        self._refresh_saved_shot(shot)
        if undoing:
            self._notify(f"{shot.shot_name} {dept_name} is back to {shot_status.label(status)}.",
                         "info", 4000)
        else:
            entry = {"project": self.current_project.code if self.current_project else "",
                     "shot": shot, "dept": dept_key, "previous": previous, "status": status}
            self._own_status_undo.append(entry)
            self._own_status_undo = self._own_status_undo[-50:]
            self._notify(f"{shot.shot_name} {dept_name} set to {shot_status.label(status)}. Saved.",
                         "success", 8000, action=("Undo", lambda e=entry: self._undo_own_status(e)))
        return True

    def _undo_own_status(self, entry):
        """Take back one of this person's own saved statuses - the one the toast is about."""
        if entry not in self._own_status_undo:
            self._notify("That change was already taken back.", "info", 3000)
            return
        self._own_status_undo.remove(entry)
        code = self.current_project.code if self.current_project else ""
        if entry["project"] != code:
            self._notify(f"That change was made on {entry['project']}. Open it again to undo it.",
                         "info", 5000)
            return
        shot = entry["shot"]
        # A live update may have swapped the object: find the shot on screen by id.
        sid = int(getattr(shot, "id", -1) or -1)
        shot = next((s for s in self.all_shots or [] if s is shot or (sid >= 0 and s.id == sid)), shot)
        dept = entry["dept"]
        self.on_own_status_edited(shot, dept, entry["previous"], shot.dept(dept).status,
                                  undoing=True, undo_of=entry["status"])

    def _refresh_saved_shot(self, shot):
        """After a save of one shot outside the batch: take its new version and baseline."""
        try:
            fresh = self.data_handler.read_shots_by_id([shot.id]) if int(shot.id) >= 0 else []
        except Exception:
            fresh = []
        if fresh:
            shot.version = fresh[0].version
            self._own_writes[int(shot.id)] = int(shot.version or 0)
        self.table_model.mark_clean([shot])
        self.stats_widget.update_stats(self.displayed_shots)
        self._board_dirty = True
        if self._board_visible():
            self.update_kanban()

    # ------------------------------------------------------------------
    # Notifications (the header bell; this tab's own copy only standalone)
    # ------------------------------------------------------------------
    def _notifier(self):
        notifier = getattr(self, "_notification_manager", None)
        if notifier is None:
            try:
                from slate.core.domain.notification_manager import NotificationManager
                notifier = NotificationManager()
            except Exception as exc:
                logging.debug("Notifications unavailable: %s", exc)
                notifier = False
            self._notification_manager = notifier
        return notifier or None

    def unread_notifications(self):
        from .notifications_panel import unread_for
        return unread_for(self._notifier(), list(self._artist_identity_candidates()))

    def refresh_notification_indicator(self):
        return len(self.unread_notifications()) if getattr(self, "notifications_btn", None) else 0

    def show_notifications(self):
        from slate.gui.components.notification_center import open_notifications
        if open_notifications(self):
            return
        from .notifications_panel import NotificationsDialog
        NotificationsDialog(self.unread_notifications(), notifier=self._notifier(), parent=self).exec()

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------
    @staticmethod
    def _step_text(done) -> str:
        what = done["description"]
        if done["count"] > 1 and " on " not in what:
            what = f"{what} on {done['count']} shots"
        return what

    def undo_last_edit(self, step=None):
        """
        Take back the last edit (or, for an artist, the last status they saved).
        A toast passes its own `step`: only that edit is taken back, and only
        while it is still the newest one.
        """
        model = getattr(self, "table_model", None)
        if model is not None and model.can_edit_freely():
            if not model.can_undo():
                self._notify("Nothing to undo.", "info", 2000)
                return
            undone = model.undo(step)
            if undone:
                self._notify(f"Undid the {self._step_text(undone)}.", "info", 3000)
                self._reread_if_changed_elsewhere(undone["shots"])
            elif step is not None:
                self._notify("Newer edits were made since. Press Ctrl+Z to step back through them.",
                             "info", 5000)
            return
        if not self._own_status_undo:
            self._notify("Nothing to undo.", "info", 2000)
            return
        self._undo_own_status(self._own_status_undo[-1])

    def redo_last_edit(self):
        """Put back the last edit that was undone (Ctrl+Y / Ctrl+Shift+Z)."""
        model = getattr(self, "table_model", None)
        if model is None or not model.can_edit_freely():
            return
        redone = model.redo()
        if redone:
            self._notify(f"Redid the {self._step_text(redone)}.", "info", 3000)
        else:
            self._notify("Nothing to redo.", "info", 2000)

    def _undo_action(self):
        """('Undo', callback) for a toast about the edit just made - that edit only."""
        step = self.table_model.last_step()
        return ("Undo", lambda st=step: self.undo_last_edit(st))

    def _reread_if_changed_elsewhere(self, shots):
        """
        A shot left clean by an undo or a discard while someone else had saved
        it still shows the old values: read it again from the database.
        """
        ids = set()
        for shot in shots or []:
            if getattr(shot, "_remote_changed", False) and not getattr(shot, "_modified", False):
                shot._remote_changed = False
                if int(getattr(shot, "id", -1) or -1) >= 0:
                    ids.add(int(shot.id))
        if ids and hasattr(self, "_init_live_updates"):
            self._init_live_updates()
            self._live_pending_ids |= ids
            self._schedule_live_apply(0)

    def on_edits_changed(self):
        self.update_unsaved_indicator()
        if getattr(self, "stats_widget", None) is not None and self.current_project:
            self.stats_widget.update_stats(self.displayed_shots)
            self.stats_widget.set_active(self._status_filter_value())
        self._board_dirty = True
        if self._board_visible():
            self.update_kanban()
        if self.detail_widget is not None and hasattr(self.detail_widget, "reload_from_shot"):
            self.detail_widget.reload_from_shot()

    # ------------------------------------------------------------------
    # Unsaved work
    # ------------------------------------------------------------------
    def unsaved_shots(self):
        """Shots with edits that are not in the database yet."""
        return [s for s in (self.all_shots or []) if getattr(s, "_modified", False)]

    def _panel_has_changes(self) -> bool:
        panel = getattr(self, "detail_widget", None)
        return bool(panel is not None and getattr(panel, "has_unapplied_changes", lambda: False)())

    def has_unsaved_changes(self) -> bool:
        return bool(self.unsaved_shots()) or self._panel_has_changes()

    def unsaved_summary(self) -> str:
        count = len(self.unsaved_shots())
        text = f"{count} shot{'s have' if count != 1 else ' has'} unsaved edits on the VFX Dashboard"
        if self._panel_has_changes():
            text += " (and the shot panel has changes that are not applied)"
        return text

    def _settle_panel(self, before: str) -> bool:
        """
        Apply or drop the shot panel's unapplied changes before something
        would throw them away. False when the person cancels (or Apply failed).
        """
        panel = getattr(self, "detail_widget", None)
        if not self._panel_has_changes():
            return True
        answer = QMessageBox.question(
            self, "Apply the panel's changes",
            f"The panel has changes to {panel.shot.shot_name} that are not applied yet. "
            f"Apply them before you {before}?",
            QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Apply)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Apply:
            panel.apply_changes()
            return not panel.has_unapplied_changes()
        panel.load_data()
        return True

    def update_unsaved_indicator(self):
        """The Save button carries the count: 'Save 3 changes'."""
        button = getattr(self, "save_btn", None)
        if button is None:
            return
        from slate.core.domain.access import is_offline_fallback
        can_save = self._can_ever_save()
        button.setVisible(can_save)
        pending = len(self.unsaved_shots())
        offline = is_offline_fallback()
        if pending:
            button.setText(self._save_label())
        else:
            button.setText("Saved" if self.current_project else "Save changes")
        button.setEnabled(bool(can_save and pending and self.current_project and not offline))
        if offline:
            button.setToolTip("The central database is unreachable. Saving is off until it is back, "
                              "so work cannot be stranded on this machine.")
        elif pending:
            button.setToolTip(f"Write {pending} shot{'s' if pending != 1 else ''} with pending edits "
                              "to the database (Ctrl+S)")
        else:
            button.setToolTip("Nothing waiting to be saved")

    def _save_label(self) -> str:
        """'Save 6 changes (1 shot)': the count is of changes, and says how many shots when it differs."""
        from .shot_table_model import changed_fields
        shots = self.unsaved_shots()
        changes = sum(max(1, len(changed_fields(s))) for s in shots)
        text = f"Save {changes} change{'s' if changes != 1 else ''}"
        if changes != len(shots):
            text += f" ({len(shots)} shot{'s' if len(shots) != 1 else ''})"
        return text

    def _can_ever_save(self) -> bool:
        """Has a Save button at all: full edit rights, and not a lead with no department."""
        from slate.core.domain.access import can_edit_dashboard
        if not can_edit_dashboard(self.user_roles):
            return False
        return self._department_scope() != set()

    def confirm_discarding_changes(self, action: str) -> bool:
        """
        Ask before throwing away pending edits. True when it is safe to go on.
        Offers to save rather than making it a straight save-or-lose. The
        shot panel's unapplied changes are settled first.
        """
        if not self._settle_panel(action):
            return False
        pending = self.unsaved_shots()
        if not pending:
            return True

        names = ", ".join(s.shot_name for s in pending[:5])
        if len(pending) > 5:
            names += f" and {len(pending) - 5} more"

        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            f"{len(pending)} shot(s) have edits that are not saved:\n\n{names}\n\n"
            f"Save them before you {action}?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )

        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            self.save_changes()
            # A failed save must not silently become a discard.
            return not self.has_unsaved_changes()
        self.table_model.discard_changes()
        self._reread_if_changed_elsewhere(pending)
        return True

    # ------------------------------------------------------------------
    # Excel backup state
    # ------------------------------------------------------------------
    def update_backup_indicator(self):
        """
        When the Excel backup was last written - shown to the people who keep it
        (excel_sync), in words: 'Backup saved 19:21', 'Backup failing'.
        """
        label = getattr(self, "backup_label", None)
        if label is None:
            return
        if not self._excel_allowed() or not self.current_project:
            label.hide()
            return
        service = getattr(self, "sync_service", None)
        error = getattr(service, "last_backup_error", None)
        last = getattr(service, "last_backup_at", None)
        from slate.gui.core.icons import icon as draw_icon
        if error:
            from datetime import datetime, timedelta
            stale = last is None or (datetime.now() - last) > timedelta(hours=24)
            colour = Gate.BAD if stale else Gate.WARN
            label.setText("Backup failing")
            label.setToolTip(f"The Excel backup could not be written: {error}")
        elif last is not None:
            from datetime import date
            from slate.core.domain.dates import format_datetime
            colour = Gate.OK
            when = last.strftime('%H:%M') if last.date() == date.today() else format_datetime(last)
            label.setText(f"Backup saved {when}")
            label.setToolTip("When the project's Excel backup was last written.")
        else:
            colour = Gate.TEXT_DIM
            label.setText("Backup: not yet today")
            label.setToolTip("The Excel backup is written each time you save.")
        label.setStyleSheet(f"color: {colour}; font-size: {Gate.SIZE_XS}px;")
        label.show()

    def retry_connection(self):
        """Try the central database again after an outage."""
        # The manager has no reconnect(): asking for one found nothing, so this
        # button never tried the database again. Rebuilding from the settings
        # is what the sign-in window's Try again does.
        from slate.core.domain.access import is_offline_fallback
        try:
            if is_offline_fallback():
                database_manager.reload_from_config()
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logging.warning("Reconnect attempt failed: %s", exc)

        if self.refresh_connection_state():
            self._notify("Still unable to reach the central database.", "warning")
        else:
            self._notify("Reconnected. The dashboard is editable again.", "success")
            self.refresh_data()

    # ------------------------------------------------------------------
    # What is on screen when there is nothing to show
    # ------------------------------------------------------------------
    def _update_empty_state(self):
        if not hasattr(self, "view_stack") or getattr(self, "empty_state", None) is None:
            return
        state = self.empty_state
        has_project = bool(self.current_project)
        if getattr(self, "stats_widget", None) is not None:
            self.stats_widget.setVisible(has_project)

        if not has_project:
            state.set_message("No project selected", "Pick a project above to load its shots.")
            self.view_stack.setCurrentWidget(state)
            return

        if not self.all_shots:
            failed = getattr(self, "_read_failed", None)
            if failed:
                state.set_message(f"{failed} could not be read",
                                  "The database did not answer, so nothing is shown. Nothing has "
                                  "changed - try again in a moment (Refresh).")
            elif self._is_artist_scope():
                state.set_message("No shots assigned to you",
                                  "Shots you are named on - as the artist or on any department - "
                                  "appear here as soon as production assigns them.")
            elif self.local_mode:
                state.set_message(
                    "Working offline: no shots on this machine yet",
                    "This project's shots are on the central database. They appear here when the "
                    "connection is back; new shots arrive through Build & Ingest.")
            else:
                state.set_message("No shots in this project yet",
                                  "Shots arrive through Build & Ingest, or with Add shots.")
            self.view_stack.setCurrentWidget(state)
            return

        board_mode = bool(getattr(self, "view_toggle_btn", None) and self.view_toggle_btn.isChecked())
        self.view_stack.setCurrentWidget(self.kanban_board if board_mode else self.table)

        # Filters that match nothing: the grid stays, headings and all, with a
        # message over it and a button that clears them.
        overlay = getattr(self, "no_match_state", None)
        if overlay is not None:
            no_match = not self.displayed_shots
            if no_match and self._scope_mode() == "my_shots" and self.active_filter_count() == 0:
                overlay.set_filtered(True, on_clear=self.show_all_shots_scope, noun="shots",
                                     title="Nothing is assigned to you",
                                     body="You are not named on any shot of this project. "
                                          "Switch to All shots to see the rest.")
            else:
                overlay.set_filtered(True, on_clear=self.clear_all_filters, noun="shots",
                                     title="No shots match",
                                     body=self._filters_description())
            overlay.refresh()

    def _filters_description(self) -> str:
        parts = []
        needle = self._search_needle_now()
        if needle:
            parts.append(f"search \"{needle}\"")
        status = self._status_filter_value()
        if status is not None:
            parts.append(f"status {shot_status.label(status)}")
        header = getattr(self, "header_view", None)
        if header is not None:
            for key in header.active_filters:
                column = self.table_model.column_index(key)
                if column >= 0:
                    parts.append(f"{self.table_model.headers[column]} filter")
        rules = len(self.advanced_query_rules or [])
        if rules:
            parts.append(f"{rules} Filters rule{'s' if rules != 1 else ''}")
        scope = self._scope_mode()
        if scope != "all":
            parts.append(self.scope_combo.currentText())
        if not parts:
            return "Nothing matches."
        return "Nothing matches " + ", ".join(parts) + "."

    def show_all_shots_scope(self):
        index = self.scope_combo.findData("all")
        if index >= 0:
            self.scope_combo.setCurrentIndex(index)

    # ------------------------------------------------------------------
    # Roles
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_roles(roles_data):
        """Lower-case role names, with only spellings folded (never one role into another)."""
        if isinstance(roles_data, str):
            raw_roles = [roles_data]
        elif isinstance(roles_data, (list, tuple)):
            raw_roles = list(roles_data)
        else:
            raw_roles = ["Artist"]
        normalized = []
        for role in raw_roles:
            text = str(role or "").strip().lower()
            if not text:
                continue
            normalized.append(_ROLE_SPELLINGS.get(text, text))
        return normalized or ["artist"]

    @staticmethod
    def _is_local_fallback_mode() -> bool:
        try:
            status = database_manager.get_runtime_status() or {}
            mode = str(status.get("active_mode", "")).lower()
            return mode == "sqlite" and bool(status.get("fallback_used", False))
        except DatabaseUnavailableError:
            raise
        except Exception:
            return False

    def _user_can_edit(self):
        from slate.core.domain.access import can_edit_dashboard, is_offline_fallback
        if is_offline_fallback():
            return False
        return can_edit_dashboard(self.user_roles)

    def _is_artist_scope(self) -> bool:
        """
        Whether this person sees only the shots they are named on: no
        dashboard_view_all and no dashboard_write (access.can_view_all_shots).
        """
        from slate.core.domain.access import can_view_all_shots
        return not can_view_all_shots(self.user_roles)

    def _developer_tools_on(self) -> bool:
        """The developer role with dev tools switched on (as the header decides)."""
        if "developer" not in self.user_roles:
            return False
        try:
            return bool(GlobalConfig.get("dev_tools_enabled", False)
                        or GlobalConfig.get("developer_mode", False))
        except Exception:
            return False

    def _artist_identity_candidates(self):
        candidates = {
            str(self.user_data.get("user_id", "")).strip(),
            str(self.user_data.get("username", "")).strip(),
            str(self.user_data.get("display_name", "")).strip(),
            str(getattr(self, "user_display_name", "") or "").strip(),
        }
        return {c.lower() for c in candidates if c}

    def _filter_shots_for_current_user(self, shots):
        if not self._is_artist_scope():
            return list(shots or [])
        identities = self._artist_identity_candidates()
        filtered = []
        for shot in shots or []:
            # Every department the person could be on, not just the shot's lead.
            try:
                names = shot.get_all_artists()
            except Exception:
                names = [getattr(shot, "assigned_artist", "")]
            if any(str(n or "").strip().lower() in identities for n in names):
                filtered.append(shot)
        return filtered

    def _warn_if_exr_policy_limits_project(self):
        """Say once per project when EXR media exists but EXR loading is off."""
        try:
            if GlobalConfig.exr_loading_enabled():
                return
            for shot in self.all_shots or []:
                scan_path = str(getattr(shot, "scan_path", "") or "").lower()
                render_path = str(getattr(shot, "render_path", "") or "").lower()
                if ".exr" in scan_path or ".exr" in render_path:
                    logging.info("EXR loading is off (SLATE_ENABLE_EXR_LOADING / enable_exr_loading).")
                    self.status_bar.showMessage(
                        "This project has EXR media, and EXR loading is off. Turn on "
                        "\"EXR loading\" in Settings to preview it.", 12000)
                    return
        except Exception as exc:
            logging.debug("EXR policy warning check skipped: %s", exc)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_pending_live_changes_on_show()
        self._watch_scroll_frame()
        QTimer.singleShot(0, self._fit_toolbar)

    def _watch_scroll_frame(self):
        """Refit the toolbar when the frame the tab sits in changes size."""
        if getattr(self, "_watched_viewport", None) is not None:
            return
        from PySide6.QtWidgets import QAbstractScrollArea
        parent = self.parentWidget()
        while parent is not None and not isinstance(parent, QAbstractScrollArea):
            parent = parent.parentWidget()
        if parent is not None:
            self._watched_viewport = parent.viewport()
            self._watched_viewport.installEventFilter(self)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is getattr(self, "_watched_viewport", None) and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._fit_toolbar)
        return super().eventFilter(obj, event)

    def hideEvent(self, event):
        self._cancel_thumbnail_prefetch()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        combo = getattr(self, "project_combo", None)
        if combo is not None:
            combo.updateGeometry()          # its room depends on the bar's width
        self._fit_toolbar()

    def _fit_toolbar(self):
        """Fold View, Reports, Manage project and Add shots into More when they do not fit."""
        row = getattr(self, "toolbar_row", None)
        if row is None or getattr(self, "app_bar", None) is None:
            return
        foldable = [b for b in (self.view_btn, self.reports_btn, self.manage_proj_btn, self.add_shots_btn)
                    if b is not None]
        wanted_visible = {b: (b is not self.manage_proj_btn or not self.project_menu.isEmpty())
                          for b in foldable}
        margins = self.app_bar.layout().contentsMargins()
        # The room actually on screen: inside Slate the tab sits in a scroll
        # frame, which would otherwise grow to fit whatever the row asks for.
        width = self.width()
        from PySide6.QtWidgets import QAbstractScrollArea
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                width = min(width, parent.viewport().width())
                break
            parent = parent.parentWidget()
        available = width - margins.left() - margins.right()
        needed = 0
        for i in range(row.count()):
            item = row.itemAt(i)
            widget = item.widget()
            if widget is None or widget is self.more_btn:
                continue
            if widget in wanted_visible and not wanted_visible[widget]:
                continue
            if not widget.isVisibleTo(self.app_bar) and widget not in wanted_visible:
                continue
            hint = widget.minimumSizeHint().width() if widget is self.search_input else widget.sizeHint().width()
            needed += hint + row.spacing()
        compact = needed > available
        for button in foldable:
            button.setVisible(wanted_visible[button] and not compact)
        self.more_btn.setVisible(compact)

    def _fill_more_menu(self):
        menu = self.more_menu
        menu.clear()
        menu.addMenu(self.view_menu).setText("View")
        menu.addMenu(self.reports_menu).setText("Reports")
        if not self.project_menu.isEmpty():
            menu.addMenu(self.project_menu).setText("Manage project")
        if self.add_shots_btn is not None:
            menu.addAction("Add shots…", self.add_shots_click)

    def log(self, message):
        logging.info(message)

    def _departments(self):
        from slate.core.domain.departments import load_departments
        return load_departments()

    def _get_user_list(self):
        """
        The people work can be assigned to, by display name: everybody active
        with the "assignable" ability (artist-type roles and leads, or any role
        it is ticked on). When nobody can be read the list is empty - the old
        fallback offered a made-up "Artist".
        """
        from slate.core.domain.access import can_be_assigned
        valid_users = set()
        try:
            db_users = self.user_manager.get_all_users() or {}
            for username, u in db_users.items():
                if not u.get('active', True):
                    continue
                roles = u.get('roles') or u.get('role') or []
                if isinstance(roles, str):
                    roles = [roles]
                if not can_be_assigned(roles):
                    continue
                name = str(u.get('display_name') or '').strip()
                valid_users.add(name or username)
        except Exception as e:
            logging.exception(f"Failed to fetch DB users: {e}")
            if getattr(self, "status_bar", None) is not None:
                self.status_bar.showMessage("The list of people could not be read; artist lists are empty.", 8000)
        return sorted(valid_users, key=str.lower)

    def init_ui(self):
        build_dashboard_ui(self)
        self.shown_label = QLabel("")
        self.shown_label.setObjectName("shownCount")
        self.status_bar.addPermanentWidget(self.shown_label)

    def _cleanup_poll_worker(self, timeout_ms: int = 2000):
        worker = self.poll_worker
        if worker is None:
            return
        try:
            if hasattr(worker, "stop"):
                try:
                    worker.stop(timeout_ms=timeout_ms)
                except TypeError:
                    worker.stop()
            elif worker.isRunning():
                worker.requestInterruption()
                worker.wait(timeout_ms)
        except Exception as exc:
            logging.debug("Poll worker shutdown warning: %s", exc)
        try:
            worker.deleteLater()
        except RuntimeError as exc:
            logging.debug("Poll worker deleteLater skipped: %s", exc)
        if self.poll_worker is worker:
            self.poll_worker = None

    def _cleanup_avatar_upload_worker(self, timeout_ms: int = 1500):
        worker = self.avatar_upload_worker
        if worker is None:
            return
        try:
            if worker.isRunning():
                if hasattr(worker, "stop"):
                    worker.stop()
                else:
                    worker.requestInterruption()
                worker.wait(timeout_ms)
        except Exception as exc:
            logging.debug("Avatar upload worker shutdown warning: %s", exc)
        try:
            worker.deleteLater()
        except RuntimeError as exc:
            logging.debug("Avatar worker deleteLater skipped: %s", exc)
        if self.avatar_upload_worker is worker:
            self.avatar_upload_worker = None

    def check_for_updates(self):
        if self._is_closing or not self.current_project:
            return
        if self.current_excel_path and os.path.exists(self.current_excel_path):
            try:
                current_mtime = os.path.getmtime(self.current_excel_path)
                if self.last_excel_mtime is None:
                    self.last_excel_mtime = current_mtime
                elif current_mtime > (self.last_excel_mtime + 0.5):
                    # Someone touched the backup sheet. Note it, but never pull
                    # from it: the database is the record.
                    self.last_excel_mtime = current_mtime
                    self.log(f"Excel backup was modified outside the software: "
                             f"{self.current_excel_path}. Not imported - the database "
                             f"remains the source of truth.")
            except Exception as e:
                logging.debug(f"Excel update check failed: {e}")

    def refresh_data(self):
        """Read the project again. Pending edits are kept (and marked if they changed elsewhere)."""
        if self._is_closing:
            return
        self.refresh_connection_state()
        if self.current_project:
            self.reload_shots()

    # Kept for callers that still say refresh().
    def refresh(self):
        self.refresh_data()

    # ------------------------------------------------------------------
    # Filters dialog
    # ------------------------------------------------------------------
    def open_query_builder(self):
        from .query_builder_dialog import QueryBuilderDialog
        dialog = QueryBuilderDialog(self, rules=self.advanced_query_rules,
                                    match_type=self.advanced_query_match_type)
        dialog.query_applied.connect(self._on_query_applied)
        dialog.exec()

    def _on_query_applied(self, rules, match_type):
        self.advanced_query_rules = list(rules or [])
        self.advanced_query_match_type = match_type
        self.apply_filters()

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------
    def load_projects(self):
        from slate.gui.core.controls import plain
        projects = self.project_manager.get_all_projects()
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem("Select a project…", None)
        for p in projects:
            text = f"{p.code} - {p.name}"
            self.project_combo.addItem(text, p.code)
            self.project_combo.setItemData(self.project_combo.count() - 1, text,
                                           Qt.ItemDataRole.ToolTipRole)
        self.project_combo.blockSignals(False)
        # Open on the project this person had open last time, if it still exists.
        remembered = self._remembered_project()
        index = self.project_combo.findData(remembered) if remembered else -1
        if index > 0:
            self.project_combo.setCurrentIndex(index)
        self._sync_project_tooltip()

    def _sync_project_tooltip(self):
        self.project_combo.setToolTip(self.project_combo.currentText() if self.current_project
                                      else "The project on screen")

    def on_project_changed(self, index):
        if not self.confirm_discarding_changes("switch project"):
            # Put the selector back where it was.
            if self.current_project:
                previous = self.project_combo.findData(self.current_project.code)
                if previous >= 0:
                    self.project_combo.blockSignals(True)
                    self.project_combo.setCurrentIndex(previous)
                    self.project_combo.blockSignals(False)
            return

        project_code = self.project_combo.currentData()
        if not project_code:
            self.current_project = None
            self.data_handler = None
            self.all_shots = []
            self._cleanup_poll_worker(timeout_ms=1000)
            self.table_model.set_shots([])
            self.clear_all_filters(apply=False)
            self.close_detail_dock()
            self.apply_filters()
            self.update_unsaved_indicator()
            self.update_backup_indicator()
            self._sync_project_tooltip()
            return
        self.switch_project(project_code)
        self._sync_project_tooltip()
        self.update_backup_indicator()

    # ------------------------------------------------------------------
    # Grid: grouping, spans, selection
    # ------------------------------------------------------------------
    def _apply_table_spans(self):
        table = getattr(self, "table", None)
        model = getattr(self, "group_model", None)
        if table is None or model is None:
            return
        table.clearSpans()
        col_count = model.columnCount()
        for r in model.get_header_rows():
            table.setSpan(r, 0, 1, col_count)
        if hasattr(table, "copy_spans"):
            table.copy_spans()

    def on_groupby_changed(self, index=0):
        if getattr(self, "group_model", None) is None:
            return
        mode = self.groupby_combo.currentData() or "None"
        self.group_model.set_group_by(mode)
        self._apply_table_spans()

    def set_groups_collapsed(self, collapsed: bool):
        if self.group_model.group_by == "None":
            self._notify("Choose a grouping first (Group: Reel, Status…).", "info", 3000)
            return
        self.group_model.set_all_collapsed(collapsed)
        self._apply_table_spans()

    def _sort_from_frozen_header(self, logical):
        header = self.header_view
        order = Qt.SortOrder.AscendingOrder
        if header.sortIndicatorSection() == logical and header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder:
            order = Qt.SortOrder.DescendingOrder
        self.table.sortByColumn(logical, order)

    def update_table(self):
        """Re-show the shots (kept for older callers)."""
        self.table_model.set_shots(self.all_shots, keep_undo=getattr(self, "_keep_undo", False))
        self.apply_filters()

    def _selected_shots(self):
        shots = []
        selection = self.table.selectionModel()
        if selection is None:
            return shots
        model = self.table.model()
        for index in selection.selectedRows():
            shot = model.get_shot_at(index.row())
            if shot is not None and shot not in shots:
                shots.append(shot)
        return shots

    def _current_shot(self):
        index = self.table.currentIndex()
        shot = self.table.model().get_shot_at(index.row()) if index.isValid() else None
        if shot is None:
            selected = self._selected_shots()
            shot = selected[0] if selected else None
        return shot

    # ------------------------------------------------------------------
    # Clicks and keys
    # ------------------------------------------------------------------
    def on_item_clicked(self, index):
        """A click selects. On a group heading it folds the group."""
        if not index.isValid():
            return
        model = self.table.model()
        if model.is_group_header(index.row()):
            info = model.get_group_info(index.row())
            if info:
                model.toggle_group_collapse(info.get("group_key", ""))
                self._apply_table_spans()
            return
        # With the panel already open, it follows the selection.
        if self.detail_widget is not None and self.detail_container.isVisible():
            shot = model.get_shot_at(index.row())
            if shot is not None and shot is not getattr(self.detail_widget, "shot", None):
                self.open_detail_dock(shot)

    def on_item_double_clicked(self, index):
        """Double-click edits an editable cell (Qt does that); anywhere else it opens the shot."""
        if not index.isValid():
            return
        model = self.table.model()
        if model.is_group_header(index.row()):
            return
        if model.flags(index) & Qt.ItemFlag.ItemIsEditable:
            return
        shot = model.get_shot_at(index.row())
        if shot is not None:
            self.open_detail_dock(shot)

    def focus_search(self):
        self.search_input.setFocus()
        self.search_input.selectAll()

    def on_escape(self):
        # Esc in a search box with text clears it first; otherwise it closes the panel.
        if self.search_input.hasFocus() and self.search_input.text():
            self.search_input.clear()
        elif self.detail_container.isVisible():
            self.close_detail_dock()

    def quick_look_current(self):
        if self.table.state() == self.table.State.EditingState:
            return
        shot = self._current_shot()
        if shot is not None:
            self.open_quick_look(shot)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.table.hasFocus() \
                and self.table.state() != self.table.State.EditingState:
            shot = self._current_shot()
            if shot is not None:
                self.open_detail_dock(shot)
                return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Quick Look
    # ------------------------------------------------------------------
    def _find_shot_media_path(self, shot) -> str:
        """The newest movie for Quick Look, from the shot's output and comp folders (3 levels deep)."""
        # ponytail: bounded walk on the UI thread (3 levels, movies only); a worker with a spinner
        # if studios keep deeper trees.
        fallback = getattr(shot, "_display_thumb", "") or ""
        if not self.current_project:
            return fallback
        try:
            for key in ("output", "comp"):
                folder = self.project_manager.get_folder_path(
                    self.current_project.code, key, shot.reel_episode, shot.shot_name)
                if not folder or not os.path.isdir(folder):
                    continue
                movies = []
                top = folder.rstrip("/\\").count(os.sep)
                for root, dirs, files in os.walk(folder):
                    if root.count(os.sep) - top >= 3:
                        dirs[:] = []
                    movies += [os.path.join(root, f) for f in files if f.lower().endswith((".mov", ".mp4"))]
                if movies:
                    return max(movies, key=os.path.getmtime)
        except Exception as e:
            logging.debug(f"Quick look path search error: {e}")
        return fallback

    def open_quick_look(self, shot):
        if not shot:
            return
        media_path = self._find_shot_media_path(shot)
        try:
            from slate.gui.widgets.quick_look import QuickLookDialog
            asset_title = f"{shot.shot_name} ({shot.curr_version or 'no version yet'})"
            QuickLookDialog(self, asset_name=asset_title, asset_path=media_path).exec()
        except Exception as e:
            self._notify("Could not open Quick Look.", "warning", details=str(e))

    # ------------------------------------------------------------------
    # Batch edit (pending, undoable, like every other edit)
    # ------------------------------------------------------------------
    def open_batch_edit_dialog(self, shots):
        if not shots:
            return
        if not self._can_manage_shots():
            self._notify("You don't have permission to batch edit shots.", "warning")
            return
        from .batch_edit_dialog import BatchEditDialog
        dialog = BatchEditDialog(len(shots), all_users=self._get_user_list(), parent=self)
        if dialog.exec():
            updates = dialog.get_updates()
            if updates:
                self.on_batch_update(shots, updates)

    def on_batch_update(self, shots, updates: dict):
        """Apply the same values to several shots, as one pending, undoable step."""
        if not shots or not updates:
            return
        if not self._can_manage_shots():
            self._notify("You don't have permission to batch edit shots.", "warning")
            return

        def change(shot):
            for field, value in updates.items():
                if hasattr(shot, field):
                    setattr(shot, field, value)

        names = ", ".join(sorted({self._field_name(f) for f in updates}))
        changed = self.table_model.apply_edit(shots, change, f"{names} change")
        if not changed:
            self._notify("Those shots already had those values.", "info", 3000)
            return
        self._notify(f"{names[:1].upper() + names[1:]} changed on {len(changed)} "
                     f"shot{'s' if len(changed) != 1 else ''}. Not saved yet.", "info", 6000,
                     action=self._undo_action())

    @staticmethod
    def _field_name(field):
        from .shot_table_model import field_label
        return field_label(field)

    # ------------------------------------------------------------------
    # Detail panel
    # ------------------------------------------------------------------
    def open_detail_dock(self, shot):
        if self.detail_widget:
            if self.detail_widget.shot is not shot and not self._settle_panel("open another shot"):
                return
            self.detail_layout.removeWidget(self.detail_widget)
            self.detail_widget.deleteLater()
            self.detail_widget = None

        self.detail_widget = ShotDetailWidget(
            shot,
            self.user_roles,
            self.project_manager,
            self.all_shots,
            self._get_user_list(),
            user_data=self.user_data,
            current_project_code=(self.current_project.code if self.current_project else ""),
            inherit_app_theme=self.inherit_app_theme,
            department_scope=self._department_scope(),
            user_identities=self._artist_identity_candidates(),
            allowed_statuses=self._allowed_statuses(),
        )
        self.detail_widget.close_requested.connect(self.close_detail_dock)
        self.detail_widget.show_only_requested.connect(lambda s: self.show_only_shots([s]))
        self.detail_widget.apply_requested.connect(self.on_detail_apply)
        self.detail_widget.quick_look_requested.connect(self.open_quick_look)
        self.detail_widget.rv_review_requested.connect(self.review_in_rv)
        self.detail_widget.history_requested.connect(self.show_history_dialog)
        self.detail_layout.addWidget(self.detail_widget)
        self.detail_container.show()
        self._load_detail_thumbnail(shot)

        if self.detail_container.width() < 50:
            sizes = self.splitter.sizes()
            if len(sizes) >= 2:
                new_sizes = list(sizes)
                total = max(sum(sizes), 1)
                target_detail = max(system_engine.scale_px(440, minimum=380), int(total * 0.34))
                middle = new_sizes[1] if (len(new_sizes) > 2 and self.users_list.isVisible()) else 0
                new_sizes[-1] = target_detail
                if len(new_sizes) > 2:
                    new_sizes[1] = middle
                new_sizes[0] = max(200, total - target_detail - middle)
                self.splitter.setSizes(new_sizes)

    def _shot_on_screen(self, shot):
        """The grid's own object for this shot (a live update may have swapped it), or None."""
        shots = self.all_shots or []
        if any(s is shot for s in shots):
            return shot
        sid = int(getattr(shot, "id", -1) if getattr(shot, "id", None) is not None else -1)
        return next((s for s in shots if sid >= 0 and s.id == sid), None)

    def on_detail_apply(self, shot, changes: dict):
        """The panel's Apply: its changes become one pending edit (or, for an artist, a saved status)."""
        if not changes:
            self.detail_widget.show_apply_result(True, "Nothing changed.")
            return
        target = self._shot_on_screen(shot)
        if target is None:
            self.detail_widget.show_apply_result(
                False, f"{shot.shot_name} is no longer in this project - someone removed it - so "
                       "these changes cannot be applied.")
            return
        if target is not shot:
            self.detail_widget.shot = target
            shot = target
        if self.table_model.can_edit_freely():
            def change(s):
                for path, value in changes.items():
                    set_field(s, path, value)
            applied = self.table_model.apply_edit([shot], change, f"panel changes on {shot.shot_name}")
            if applied:
                self.detail_widget.show_apply_result(
                    True, f"Applied. {self._save_label()} writes it to the database.")
            else:
                self.detail_widget.show_apply_result(True, "Nothing changed.")
            return
        # An artist: their own department statuses, saved one by one.
        saved_all = True
        for path, value in changes.items():
            if not path.startswith("departments.") or not path.endswith(".status"):
                continue
            dept_key = path.split(".")[1]
            previous = shot.dept(dept_key).status
            shot.dept(dept_key).status = value
            saved_all = self.on_own_status_edited(shot, dept_key, value, previous) and saved_all
        self.detail_widget.show_apply_result(saved_all, "Saved." if saved_all else
                                             "Not everything could be saved - see the message.")

    def close_detail_dock(self, force: bool = False) -> bool:
        """Close the shot panel - after asking about changes it has not applied."""
        if self.detail_container is None:
            return True
        if not force and not self._settle_panel("close the panel"):
            return False
        self.detail_container.hide()
        if self.detail_widget:
            self.detail_widget.deleteLater()
            self.detail_widget = None
        return True

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------
    def show_context_menu(self, pos):
        try:
            index = self.table.indexAt(pos)
            if not index.isValid():
                return
            model = self.table.model()

            if model.is_group_header(index.row()):
                info = model.get_group_info(index.row())
                if info:
                    menu = QMenu(self)
                    key = info.get("group_key", "")
                    collapsed = bool(info.get("is_collapsed", False))
                    menu.addAction("Expand this group" if collapsed else "Collapse this group",
                                   lambda: (model.toggle_group_collapse(key), self._apply_table_spans()))
                    menu.addSeparator()
                    menu.addAction("Collapse all groups", lambda: self.set_groups_collapsed(True))
                    menu.addAction("Expand all groups", lambda: self.set_groups_collapsed(False))
                    menu.exec(self.table.viewport().mapToGlobal(pos))
                return

            selected_shots = self._selected_shots()
            clicked = model.get_shot_at(index.row())
            if clicked is not None and clicked not in selected_shots:
                selected_shots = [clicked]
                self.table.selectRow(index.row())
            if not selected_shots:
                return

            menu = QMenu(self)
            several = len(selected_shots) > 1
            if several and self._can_manage_shots():
                self._menu_heading(menu, f"{len(selected_shots)} shots selected")
                menu.addAction(f"Batch edit {len(selected_shots)} shots…",
                               lambda: self.open_batch_edit_dialog(selected_shots))
                st_menu = menu.addMenu("Set status")
                for st in shot_status.WORKFLOW:
                    st_menu.addAction(st, lambda s=st: self.on_batch_update(selected_shots, {"status": s}))
                art_menu = menu.addMenu("Assign artist")
                art_menu.addAction("Unassigned", lambda: self.on_batch_update(selected_shots, {"assigned_artist": ""}))
                art_menu.addSeparator()
                for user in self._get_user_list():
                    art_menu.addAction(user, lambda u=user: self.on_batch_update(selected_shots, {"assigned_artist": u}))
                prio_menu = menu.addMenu("Set priority")
                for value, label in shot_status.priorities():
                    prio_menu.addAction(label, lambda p=value: self.on_batch_update(selected_shots, {"priority": p}))
                menu.addSeparator()

            primary = selected_shots[0]
            if several:
                menu.addSeparator()
            self._menu_heading(menu, primary.shot_name if not several else f"{primary.shot_name} only")

            details = menu.addAction("Open details", lambda: self.open_detail_dock(primary))
            details.setShortcut("Return")
            ql = menu.addAction("Quick Look", lambda: self.open_quick_look(primary))
            ql.setShortcut("Space")
            menu.addAction("Review in RV…", lambda: self.review_in_rv(primary))

            folders = menu.addMenu("Open folder")
            for label, key in self._folder_shortcuts():
                folders.addAction(label, lambda k=key: self.open_shot_folder(k, primary))

            dcc_menu = menu.addMenu("Open in")
            launcher = DCCLauncher(self)
            for app_key, app_label in self._dcc_apps():
                dcc_menu.addAction(app_label, lambda k=app_key: self._launch_dcc(launcher, k, primary))

            menu.addSeparator()
            menu.addAction("View history", lambda: self.show_history_dialog(primary))
            if several:
                # Single-shot actions say which shot they act on.
                for action in (details, ql):
                    action.setText(f"{action.text()} - {primary.shot_name}")
            menu.exec(self.table.viewport().mapToGlobal(pos))
        except Exception as e:
            logging.exception(f"Context Menu Error: {e}")

    @staticmethod
    def _menu_heading(menu, text):
        """A heading line in a menu (the app style draws addSection as a plain gap)."""
        action = menu.addAction(text)
        action.setEnabled(False)
        font = action.font()
        font.setBold(True)
        action.setFont(font)
        return action

    def _folder_shortcuts(self):
        """Scan, every department in departments.json, and Output - one list for menu and panel."""
        items = [("Scan", "scan")]
        for dept in self._departments():
            items.append((dept.name, dept.key))
        items.append(("Output", "output"))
        return items

    @staticmethod
    def _dcc_apps():
        from slate.core.dcc_launcher import dashboard_apps
        return dashboard_apps()

    def _launch_dcc(self, launcher, app_key, shot):
        if app_key == "rv":
            self.review_in_rv(shot)
            return
        launcher.launch(app_key, shot.id, shot_name=shot.shot_name,
                        scan_path=self._shot_folder_resolver(shot, "scan") or "")

    def _shot_folder_resolver(self, shot, key):
        """Where one of a shot's folders actually is (through the project manager)."""
        if not self.current_project:
            return None
        try:
            return self.project_manager.get_folder_path(
                self.current_project.code, key,
                getattr(shot, "reel_episode", ""), getattr(shot, "shot_name", "")) or None
        except Exception as exc:
            logging.debug("Could not resolve %s folder: %s", key, exc)
            return None

    def review_in_rv(self, shot):
        """Open the plate or a department render for this shot in OpenRV."""
        if not shot:
            return
        from slate.gui.dialogs.rv_review_dialog import review_shot_in_rv
        project_root = getattr(self.current_project, "folder_base", "") or None
        try:
            opened = review_shot_in_rv(shot, parent=self, project_root=project_root,
                                       folder_resolver=self._shot_folder_resolver)
        except Exception as exc:
            logging.exception("Review in RV failed: %s", exc)
            self._notify("Could not open the review picker.", "error", details=str(exc))
            return
        if not opened:
            self._notify(f"Nothing to review for {getattr(shot, 'shot_name', 'this shot')} "
                         "yet - no scan or render was found on disk.", "warning")

    def open_shot_folder(self, folder_key, shot):
        if not self.current_project:
            return
        try:
            path = self.project_manager.get_folder_path(
                self.current_project.code, folder_key, shot.reel_episode, shot.shot_name)
            if path:
                if not self.project_manager.open_folder(path):
                    self._notify("That folder does not exist yet.", "warning", details=path)
            else:
                self._notify(f"The project has no '{folder_key}' folder set up.", "warning")
        except Exception as e:
            self._notify("Could not open the folder.", "error", details=str(e))

    def show_history_dialog(self, shot):
        if not self.current_project:
            return
        HistoryDialog(self.current_project.code, shot.shot_name, self,
                      shot_id=getattr(shot, "id", None),
                      reel=getattr(shot, "reel_episode", None)).exec()

    # ------------------------------------------------------------------
    # Avatar (kept for the standalone dashboard)
    # ------------------------------------------------------------------
    def change_avatar(self):
        if self._is_closing:
            return
        if not hasattr(self, "avatar_label"):
            self._notify("Your profile picture is changed from the main header.", "info")
            return
        path = self.avatar_service.choose_avatar_file(self)
        if not path:
            return
        try:
            self._cleanup_avatar_upload_worker(timeout_ms=1000)
            username = self.user_data.get("username", "unknown")
            self.avatar_upload_worker = self.avatar_service.start_avatar_upload(
                path, username, self.on_avatar_upload_finished)
        except Exception as e:
            self._notify("Could not start the upload.", "error", details=str(e))

    def on_avatar_upload_finished(self, success, result_path, username):
        sender = self.sender()
        if sender is not None and sender is not self.avatar_upload_worker:
            return
        if self._is_closing:
            self._cleanup_avatar_upload_worker(timeout_ms=500)
            return
        if not success:
            self._notify("Could not copy the picture.", "error", details=str(result_path))
            self.avatar_upload_worker = None
            return
        ok, payload = self.avatar_service.finalize_avatar_upload(success, result_path, username)
        if ok:
            self.load_user_avatar_from_path(payload)
        else:
            self._notify("The picture could not be saved.", "warning", details=str(payload))
        self.avatar_upload_worker = None

    def load_user_avatar(self):
        try:
            username = self.user_data.get('username', 'unknown')
            path = self.avatar_service.get_user_avatar_path(username)
            if path and os.path.exists(path):
                self.load_user_avatar_from_path(path)
        except Exception as e:
            logging.exception(f"Avatar load error: {e}")

    def load_user_avatar_from_path(self, path):
        if not hasattr(self, "avatar_label"):
            return
        self.avatar_service.apply_avatar_to_label(self.avatar_label, path, size=40)

    # ------------------------------------------------------------------
    # Manage project
    # ------------------------------------------------------------------
    def set_project_root_click(self):
        code = self.project_combo.currentData()
        if not code:
            self._notify("Pick a project first.", "warning")
            return
        if not self._can_manage_shots():
            self._notify("You don't have permission to change the project's folders.", "warning")
            return
        current_root = self.current_project.folder_base if self.current_project else ""
        path = QFileDialog.getExistingDirectory(self, f"Project root for {code}", current_root)
        if not path:
            return
        result = self.project_manager.set_project_folder_base(code, path)
        if result:
            self._notify(f"Project root set to {path}.", "success")
            self.current_project = self.project_manager.get_project(code)
        else:
            self._notify("The project root could not be saved.", "error",
                         details=getattr(self.project_manager, "last_error", "") or "")

    def add_project_click(self):
        dialog = AddProjectDialog(self)
        if dialog.exec():
            data = dialog.get_data()
            project = self.project_manager.add_project(
                code=data['code'], name=data['name'], excel_path=data['excel_path'],
                folder_base=data['folder_base'], sheet_name=data['sheet_name'],
                header_row=data['header_row'], data_start_row=data['data_start_row'])
            if not project:
                reason = getattr(self.project_manager, "last_error", "") or ""
                self._notify(f"The project could not be created. {reason}".strip(), "error", 10000)
                return
            self.load_projects()
            idx = self.project_combo.findData(project.code)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
            self._notify(f"Project {project.code} created.", "success")

    def archive_project_click(self):
        """Hide the project from every list; its shots and history stay, and it can come back."""
        from slate.core.domain.access import can_delete_project
        from slate.gui.components.feedback import confirm
        if not self.current_project:
            self._notify("Pick a project first.", "warning")
            return
        if not can_delete_project(self.user_roles):
            self._notify("You don't have permission to archive a project.", "warning")
            return
        if not self.confirm_discarding_changes("archive the project"):
            return
        code = self.current_project.code
        if not confirm(self, "Archive project",
                       f"Archive {code}? It disappears from the project list for everyone. Its "
                       "shots and history are kept, and it can be brought back from "
                       "Manage project > Show archived projects.", yes_label=f"Archive {code}"):
            return
        if self.project_manager.archive_project(code, roles=self.user_roles,
                                                by=self.user_data.get("username", "")):
            self.project_combo.setCurrentIndex(0)
            self.load_projects()
            self._notify(f"{code} archived.", "success", 8000,
                         action=("Undo", lambda c=code: self._restore_archived(c)))
        else:
            self._notify(f"{code} could not be archived.", "error",
                         details=getattr(self.project_manager, "last_error", "") or "")

    def _restore_archived(self, code):
        if self.project_manager.restore_project(code, roles=self.user_roles,
                                                by=self.user_data.get("username", "")):
            self.load_projects()
            idx = self.project_combo.findData(code)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
            self._notify(f"{code} is back.", "success")
        else:
            self._notify(f"{code} could not be restored.", "error",
                         details=getattr(self.project_manager, "last_error", "") or "")

    def show_archived_projects(self):
        from slate.core.domain.access import can_delete_project
        if not can_delete_project(self.user_roles):
            return
        archived = self.project_manager.archived_projects()
        if not archived:
            self._notify("No archived projects.", "info")
            return
        labels = [f"{p['code']} - {p.get('name') or p['code']}" for p in archived]
        choice, ok = QInputDialog.getItem(self, "Archived projects",
                                          "Bring back which project?", labels, 0, False)
        if ok and choice:
            self._restore_archived(archived[labels.index(choice)]["code"])

    def delete_project_click(self):
        """Admin/Developer only: remove the project and its shots for good (history is kept)."""
        from slate.core.domain.access import can_delete_project
        if not self.current_project:
            return
        if not can_delete_project(self.user_roles):
            self._notify("You don't have permission to delete a project.", "warning")
            return
        code = self.current_project.code
        if not self.confirm_discarding_changes("delete the project"):
            return
        text, ok = QInputDialog.getText(
            self, "Delete project permanently",
            f"This removes {code}, all of its shots, their department rows, versions, review "
            f"notes and delivery packages for good. It cannot be undone - Archive keeps "
            f"everything and is reversible.\n\n"
            f"The change history is kept for the audit log.\n\n"
            f"Type the project code ({code}) to delete it:",
            QLineEdit.EchoMode.Normal, "")
        if not ok:
            return
        if text.strip() != code:
            self._notify("The code did not match, so nothing was deleted.", "info")
            return
        if self.project_manager.delete_project(code, roles=self.user_roles,
                                               by=self.user_data.get("username", "")):
            # Only now are edits still on screen meaningless.
            self.table_model.discard_changes()
            self.project_combo.setCurrentIndex(0)
            self.load_projects()
            self._notify(f"{code} deleted.", "success")
        else:
            self._notify(f"{code} could not be deleted.", "error",
                         details=getattr(self.project_manager, "last_error", "") or "")

    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------
    def show_column_menu(self):
        from .components.column_layout_manager import PINNED_KEYS
        menu = QMenu(self)
        menu.addAction("Show all columns", lambda: self._set_all_columns(True))
        menu.addAction("Hide all but Reel and Shot Name", lambda: self._set_all_columns(False))
        menu.addSeparator()
        menu.addAction("Reset column layout", self.reset_column_layout)
        if self._can_manage_shots():
            menu.addAction("Save as project default layout", self.save_project_default_layout)
        menu.addSeparator()
        for i, (key, _label, _) in enumerate(self.table_model.COLUMNS):
            if key in PINNED_KEYS:
                continue          # always shown, so the grid keeps its heading
            name = self.table_model.headerData(i, Qt.Orientation.Horizontal)
            action = QAction(str(name), menu)
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(i))
            action.setData(i)
            action.triggered.connect(self.toggle_column)
            menu.addAction(action)
        anchor = self.view_btn if self.view_btn.isVisible() else self.more_btn
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()) if anchor.isVisible() else QCursor.pos())

    def toggle_column(self):
        action = self.sender()
        if not action:
            return
        self.table.setColumnHidden(action.data(), not action.isChecked())
        self.table.sync_columns()

    def _set_all_columns(self, visible: bool):
        from .components.column_layout_manager import PINNED_KEYS
        for i, (key, _, _) in enumerate(self.table_model.COLUMNS):
            self.table.setColumnHidden(i, (not visible) and key not in PINNED_KEYS)
        self.table.sync_columns()

    def reset_column_layout(self):
        if getattr(self, "layout_manager", None):
            self.layout_manager.reset_to_defaults()
            self._notify("Column layout reset.", "info")

    def save_project_default_layout(self):
        if not self._can_manage_shots():
            self._notify("You don't have permission to set the project's default layout.", "warning")
            return
        if getattr(self, "layout_manager", None) and self.layout_manager.save_project_default():
            self._notify("Saved as the layout everyone starts with on this project.", "success")
        else:
            self._notify("The project default layout could not be saved.", "error")

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------
    def open_delivery_batches_dialog(self):
        if not getattr(self, "current_project", None):
            self._notify("Pick a project first.", "warning")
            return
        if self._is_artist_scope():
            self._notify("Deliveries are kept by production.", "info")
            return
        from slate.gui.tabs.vfx_dashboard_pro.ui.delivery_batches_dialog import DeliveryBatchesDialog
        DeliveryBatchesDialog(
            project_code=self.current_project.code,
            current_user=getattr(self, "user_display_name", "") or "unknown",
            parent=self,
            roles=self.access_roles,
            can_manage=self._can_manage_shots(),
        ).exec()

    # ------------------------------------------------------------------
    # Departments and scope
    # ------------------------------------------------------------------
    def _department_family(self) -> Optional[str]:
        return self._detect_user_department_family()

    def _is_department_scoped(self) -> bool:
        """A lead: full edit rights, but only inside their own department."""
        from slate.core.domain.access import is_department_scoped
        return is_department_scoped(getattr(self, "access_roles", self.user_roles))

    def _department_scope(self):
        """
        The department keys this person may edit, or None if unrestricted.
        An empty set is a scoped role whose job title names no department.
        """
        if not self._is_department_scoped():
            return None
        from slate.core.domain.departments import families
        family = self._department_family()
        return {d.key for d in families().get(family or "", [])}

    def _can_manage_shots(self) -> bool:
        """Add, remove and batch-edit shots: a coordinator's job, not a lead's."""
        return self._user_can_edit() and not self._is_department_scoped()

    def _allowed_statuses(self):
        """The statuses this person may pick from a status dropdown."""
        from slate.core.domain.access import artist_statuses, can_edit_dashboard
        if can_edit_dashboard(self.user_roles):
            return list(shot_status.WORKFLOW)
        allowed = artist_statuses()
        return [s for s in shot_status.WORKFLOW if s in allowed]

    def _detect_user_department_family(self) -> Optional[str]:
        job_title = str(getattr(self, "user_data", {}).get("job_title", "")).strip().lower()
        dept = str(getattr(self, "user_data", {}).get("department", "")).strip().lower()
        import re
        from slate.core.domain.departments import load_departments, families

        def words(text):
            return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))

        # Whole words only: 'ai' is inside 'paint' and 'trainee', which put a
        # Paint Lead in the AI department.
        title = f" {words(job_title + ' ' + dept)} "
        if not title.strip():
            return None
        for d in load_departments():
            for phrase in (d.key, d.label, d.name):
                if words(phrase) and f" {words(phrase)} " in title:
                    return d.family
        known = families()
        for word in title.split():
            family = _DEPARTMENT_WORDS.get(word)
            if family in known:
                return family
        return None

    @staticmethod
    def _family_name(family: str) -> str:
        """'Matte Painting' for 'dmp' - the department's own name, not the key title-cased."""
        from slate.core.domain.departments import families
        members = families().get(family, [])
        if not members:
            return family.title()
        lead = next((d for d in members if d.key == family), members[0])
        return lead.name

    def populate_scope_selector(self, apply: bool = True):
        """
        All shots / My shots / one department's shots. Scopes are only view
        filters, so every department is offered to everyone. 'All shots' is not
        offered to somebody who only ever sees their own shots, and 'My shots'
        only to people who can be given work.
        """
        if not getattr(self, "scope_combo", None):
            return
        from slate.core.domain.access import can_be_assigned
        from slate.core.domain.departments import families
        current_data = self.scope_combo.currentData()
        self.scope_combo.blockSignals(True)
        self.scope_combo.clear()
        if self._is_artist_scope():
            self.scope_combo.addItem("My shots (all departments)", "all")
        else:
            self.scope_combo.addItem("All shots", "all")
            if can_be_assigned(self.user_roles):
                self.scope_combo.addItem("My shots", "my_shots")

        user_fam = self._detect_user_department_family()
        if user_fam:
            self.scope_combo.addItem(f"My department ({self._family_name(user_fam)})", f"dept:{user_fam}")
        for fam in families().keys():
            if fam != user_fam:
                self.scope_combo.addItem(f"{self._family_name(fam)} department", f"dept:{fam}")

        picked = bool(getattr(self, "_scope_user_picked", False))
        idx = self.scope_combo.findData(current_data) if picked else -1
        if idx >= 0:
            self.scope_combo.setCurrentIndex(idx)
        elif user_fam and self._is_department_scoped():
            self.scope_combo.setCurrentIndex(max(0, self.scope_combo.findData(f"dept:{user_fam}")))
        else:
            self.scope_combo.setCurrentIndex(0)
        self.scope_combo.blockSignals(False)
        self._narrow_columns_to_scope()
        if apply:
            self.apply_filters()

    def on_scope_changed(self, index=None):
        if not getattr(self, "scope_combo", None):
            return
        if not self.scope_combo.signalsBlocked() and index is not None:
            self._scope_user_picked = True
        self._narrow_columns_to_scope()
        self.apply_filters()

    def show_my_shots(self) -> bool:
        """
        The 'my shots' scope, for Home's "See all my shots": every shot this
        person is named on (shot artist or any department's), whatever name
        they were entered under. Home used to type the display name into the
        search box, which also matched other text and missed shots entered
        under the login. The search is cleared so it does not narrow it.
        """
        combo = getattr(self, "scope_combo", None)
        if combo is None:
            return False
        if combo.count() == 0:
            self.populate_scope_selector(apply=False)
        # Somebody who only sees their own shots has them under "all".
        wanted = "all" if self._is_artist_scope() else "my_shots"
        index = combo.findData(wanted)
        if index < 0:
            return False
        search = getattr(self, "search_input", None)
        if search is not None and search.text():
            search.clear()
        self._scope_user_picked = True
        if combo.currentIndex() == index:
            self.on_scope_changed(index)
        else:
            combo.setCurrentIndex(index)
        return True

    def _narrow_columns_to_scope(self):
        scope_data = self.scope_combo.currentData() or "all"
        if str(scope_data).startswith("dept:"):
            fam = str(scope_data).split(":", 1)[1]
            from slate.core.domain.departments import load_departments
            fam_keys = {d.key for d in load_departments() if d.family == fam}
            other_keys = {d.key for d in load_departments() if d.family != fam}
            for col_idx, (col_key, _, _) in enumerate(self.table_model.COLUMNS):
                if col_key in other_keys:
                    self.table.setColumnHidden(col_idx, True)
                elif col_key in fam_keys:
                    self.table.setColumnHidden(col_idx, False)
            self._scope_narrowed = True
        elif getattr(self, "_scope_narrowed", False) and getattr(self, "layout_manager", None):
            self._scope_narrowed = False
            self.layout_manager.restore_layout()
        self.table.sync_columns()

    def _mirror_shots_to_excel(self, shots, force: bool = False, on_done=None):
        """
        Write these shots to the Excel backup in the background, one write at a
        time (a second save waits for the first). on_done(ok) is called on the
        UI thread when it is written - or not.
        """
        import copy
        queue = getattr(self, "_mirror_queue", None)
        if queue is None:
            queue = self._mirror_queue = []
        # Copies: the grid can go on changing the shots while the file is written.
        queue.append(([copy.deepcopy(s) for s in shots], self.current_project, self.data_handler,
                      force, on_done))
        if getattr(self, "_mirror_worker", None) is None:
            self._next_mirror()
        return True

    def _next_mirror(self):
        if not self._mirror_queue:
            self._mirror_worker = None
            return
        shots, project, handler, force, on_done = self._mirror_queue.pop(0)
        worker = ExcelMirrorWorker(self.sync_service, shots, project, handler, force)

        def finished(ok, mtime, cb=on_done):
            if mtime is not None:
                self.last_excel_mtime = mtime
            self.update_backup_indicator()
            worker.wait(5000)            # run() returns right after emitting
            worker.deleteLater()
            if cb is not None:
                cb(ok)
            self._next_mirror()

        worker.done.connect(finished)
        self._mirror_worker = worker
        worker.start()

    def run_debug(self):
        """Developer check: how the project's columns map to the backup sheet."""
        if not self.data_handler or not self.current_project:
            self._notify("Open a project first.", "warning")
            return
        mapping = dict(getattr(self.current_project, "column_mapping", {}) or {})
        lines = [f"Project {self.current_project.code}: {len(self.all_shots)} shots, "
                 f"{len(self.unsaved_shots())} with pending edits.",
                 f"Handler: {type(self.data_handler).__name__}",
                 f"Excel mapping: {len(mapping)} columns"]
        lines += [f"  {field} -> {letter}" for field, letter in sorted(mapping.items())]
        from slate.gui.components.feedback import show_details
        show_details(self, "Dashboard debug check", "\n".join(lines), level="info")

    # ------------------------------------------------------------------
    # Auto-publish
    # ------------------------------------------------------------------
    def _start_auto_publish(self, shots):
        for shot in shots:
            worker = AutoPublishWorker(shot, self.project_manager, self.current_project.code)
            worker.finished_signal.connect(
                lambda ok, msg: self._notify(msg, "success" if ok else "warning", 8000))
            worker.finished.connect(lambda w=worker: self.publish_workers.remove(w)
                                    if w in self.publish_workers else None)
            self.publish_workers.append(worker)
            worker.start()
        self._notify(f"Copying renders for {len(shots)} shot(s) to {self.output_folder_name(shots[0])}…", "info")

    def busy_reason(self):
        running = [w for w in getattr(self, "publish_workers", []) if w.isRunning()]
        if running:
            return f"The VFX Dashboard is still copying renders for {len(running)} shot(s) to their output folder."
        if getattr(self, "_mirror_worker", None) is not None:
            return "The VFX Dashboard is still writing the Excel backup."
        return None

    def cleanup_resources(self):
        """Called when app closes."""
        if self._is_cleaned:
            return
        self._is_closing = True
        self._is_cleaned = True
        if getattr(self, "layout_manager", None) is not None:
            self.layout_manager.flush()
        if self.refresh_timer and self.refresh_timer.isActive():
            self.refresh_timer.stop()
        self._cancel_thumbnail_prefetch()
        self._cleanup_poll_worker(timeout_ms=1500)
        if self.image_loader and hasattr(self.image_loader, "shutdown"):
            try:
                self.image_loader.shutdown(2000)
            except Exception as e:
                logging.debug(f"Image loader shutdown warning: {e}")
        self._cleanup_avatar_upload_worker(timeout_ms=1500)
        for worker in list(getattr(self, "publish_workers", [])):
            worker.wait(3000)
        mirror = getattr(self, "_mirror_worker", None)
        if mirror is not None:
            mirror.wait(10000)
        if self.file_lock:
            self.file_lock.release()
            self.file_lock = None
        if hasattr(self, 'image_cache') and self.image_cache:
            self.image_cache.clear()

    def closeEvent(self, event):
        if not self.confirm_discarding_changes("close"):
            event.ignore()
            return
        self._is_closing = True
        self.cleanup_resources()
        event.accept()
