"""
Opening a project, reloading it, and saving.

One save model (FIX_PLAN): every edit waits as a pending change, and one
"Save N changes" writes them. Saving writes only the shots that differ from
the database - it used to rewrite and version-bump all 262 shots for one edit
(or none), which every other open dashboard then reloaded, and which this
screen reported back as "262 changed by others".

When somebody else saved one of the same shots first, the save stops and says
which shots, with their reels and both sides' changes (conflict_resolver_dialog).
Overwriting writes only those shots - it used to write the whole project over
everyone's newer work.
"""

import logging
import os

from PySide6.QtWidgets import QDialog

from slate.core.domain import shot_status
from slate.gui.tabs.vfx_dashboard_pro.core.excel_handler import ExcelHandler
from slate.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import (
    ProjectClosedError, SQLiteHandler, StaleDataError,
)
from slate.gui.tabs.vfx_dashboard_pro.core.file_lock import FileLock
from slate.gui.tabs.vfx_dashboard_pro.ui.shot_table_model import (
    changed_fields, display_value, field_label, get_field, restore, set_field, snapshot,
)
from slate.core.infra.database_manager import database_manager


def diff_paths(before: dict, after: dict) -> dict:
    """{field path: new value} between two snapshots."""
    out = {}
    for key in set(before) | set(after):
        if key == "departments":
            old_depts = before.get(key) or {}
            new_depts = after.get(key) or {}
            for dept in set(old_depts) | set(new_depts):
                old = old_depts.get(dept) or {}
                new = new_depts.get(dept) or {}
                for leaf in set(old) | set(new):
                    if old.get(leaf) != new.get(leaf):
                        out[f"departments.{dept}.{leaf}"] = new.get(leaf)
        elif before.get(key) != after.get(key):
            out[key] = after.get(key)
    return out


class DashboardProjectMixin:

    # ------------------------------------------------------------ opening
    def switch_project(self, project_code, refresh: bool = False):
        if self._is_closing:
            return
        self.log(f"switch_project called with {project_code}")
        changing = (not refresh) or (self.current_project is None
                                     or self.current_project.code != project_code)
        if getattr(self, "layout_manager", None) is not None:
            self.layout_manager.flush()
        self._cleanup_poll_worker(timeout_ms=1500)
        if self.file_lock:
            self.file_lock.release()
            self.file_lock = None
        self._cancel_thumbnail_prefetch()
        self._thumb_requests_inflight.clear()
        if hasattr(self, 'image_cache') and self.image_cache:
            self.image_cache.clear()

        project = self.project_manager.get_project(project_code)
        if not project:
            self._notify(f"Project {project_code} is not available any more.", "warning")
            return

        self.status_bar.showMessage(f"Loading {project.name}…")
        self.current_project = project
        self._remember_project(project_code)

        excel_path = self.project_manager.get_excel_path(project_code)
        self.current_excel_path = excel_path if excel_path and os.path.exists(excel_path) else ""
        self.last_excel_mtime = os.path.getmtime(self.current_excel_path) if self.current_excel_path else None

        db_project = database_manager.get_tracking_project(project_code)
        if db_project:
            # History is written under the signed-in username; notifications
            # skip whoever made the change.
            username = str(self.user_data.get("username")
                           or self.user_data.get("user_id") or "").strip()
            self.data_handler = SQLiteHandler(
                project_code, user_role=self.access_roles,
                department_family=self._department_family() or "",
                username=username,
                actor_identities=self._artist_identity_candidates(),
            )
            try:
                shots = self.data_handler.read_shots()
            except Exception as exc:
                # An outage must not look like an empty project somebody
                # could start re-adding shots to.
                logging.exception("Could not read project %s", project_code)
                self._show_read_failure(project, exc)
                return
            self._report_read_problems()
            shots = self._filter_shots_for_current_user(shots)
            # Other people's changes arrive shot by shot from here on.
            self._start_live_updates(project_code)
        else:
            self.data_handler = ExcelHandler(self.current_excel_path or excel_path, project)
            shots = self._filter_shots_for_current_user(self.data_handler.read_shots())
            if self._user_can_edit():
                try:
                    self.file_lock = FileLock(self.current_excel_path or project.excel_path)
                    if not self.file_lock.acquire():
                        self.status_bar.showMessage("The sheet is open somewhere else - read only.", 10000)
                except Exception as e:
                    self.log(f"FileLock error: {e}")

        try:
            if changing:
                # A filter set on one project never carries into the next.
                self.clear_all_filters(apply=False)
                self.close_detail_dock(force=True)
                # An artist's Undo belongs to the project it was made on.
                self._own_status_undo = []
            self._own_writes = {}
            self._read_failed = None
            self.all_shots = shots
            self.table_model.set_shots(shots)
            self.populate_filters()
            if changing and hasattr(self, "populate_scope_selector"):
                self.populate_scope_selector(apply=False)
            if getattr(self, "layout_manager", None) is not None:
                self.layout_manager.set_context(project_code=project_code, model=self.table_model)
                if changing:
                    self.layout_manager.restore_layout()
            self.apply_filters()
            self.update_unsaved_indicator()
            self.refresh_connection_state()
            self.status_bar.showMessage(f"Loaded {project.name}.", 4000)
            self._warn_if_exr_policy_limits_project()
        except Exception as e:
            logging.exception("Could not show project %s", project_code)
            self._notify("The project's shots could not be shown.", "error", details=str(e))

    def _show_read_failure(self, project, exc):
        """The project could not be read: say so, with Retry - never an empty grid."""
        self.all_shots = []
        self._read_failed = project.name
        self.table_model.set_shots([])
        self.apply_filters()
        self.status_bar.showMessage(f"{project.name} could not be read.", 8000)
        self._notify(f"{project.name} could not be read.", "error", 15000, details=str(exc),
                     action=("Retry", lambda code=project.code: self.switch_project(code)))

    def _report_read_problems(self):
        """Stored shots the read could not show: said, not just missing from the grid."""
        problems = list(getattr(self.data_handler, "read_problems", None) or [])
        if problems:
            n = len(problems)
            self._notify(f"{n} shot{'s' if n != 1 else ''} could not be shown: "
                         "the stored data is damaged. Tell a supervisor or IT.",
                         "warning", 15000, details="\n".join(problems[:200]))

    def reload_shots(self) -> bool:
        """
        Read the project again and merge it in: shots with pending edits keep
        them (and are marked if they changed elsewhere), everything else is
        replaced. Refresh used to reload over the top and lose pending edits.
        """
        handler = getattr(self, "data_handler", None)
        if getattr(self, "_read_failed", None) and self.current_project:
            self.switch_project(self.current_project.code)      # open it properly this time
            return True
        if not isinstance(handler, SQLiteHandler) or not self.current_project:
            # A project kept only in its sheet is read again from the sheet,
            # which cannot keep pending edits - so ask first.
            if self.current_project and self.confirm_discarding_changes("reload the sheet"):
                self.switch_project(self.current_project.code, refresh=True)
            return True
        from ...controllers.live_update_mixin import merge_shots, _shot_id
        current = list(self.all_shots or [])
        try:
            fresh = handler.read_shots()
        except Exception as exc:
            logging.exception("Reload failed: %s", exc)
            self._notify("The project could not be read again.", "error", details=str(exc))
            return False
        self._report_read_problems()
        checked = {_shot_id(s) for s in current} | {_shot_id(s) for s in fresh}
        checked.discard(None)
        result = merge_shots(current, fresh, checked,
                             visible=lambda s: bool(self._filter_shots_for_current_user([s])))
        # Replaced shots are new objects: nothing on screen is "theirs" any more.
        self._own_writes = {}
        self._show_merged_shots(result)
        return True

    # ------------------------------------------------------------ saving
    def save_changes(self) -> bool:
        """Write every pending edit. Returns whether everything was saved."""
        if not self.data_handler or not self.current_project:
            return False
        if not self._user_can_edit():
            self._notify("You don't have permission to save changes.", "warning")
            return False
        editor = self.table.focusWidget() if hasattr(self, "table") else None
        if self.table.state() == self.table.State.EditingState and editor is not None:
            # Commit the cell being typed in, so Ctrl+S saves what is on screen.
            self.table.commitData(editor)
            self.table.closeEditor(editor, self.table.EndEditHint.NoHint)
        pending = self.unsaved_shots()
        if not pending:
            self.status_bar.showMessage("Nothing to save.", 3000)
            return True

        approved_now = [s for s in pending
                        if shot_status.canonical(s.status) == shot_status.APPROVED
                        and shot_status.canonical((getattr(s, "_baseline", None) or {}).get("status"))
                        != shot_status.APPROVED]
        try:
            # ponytail: the save runs on the UI thread - one read and a few batched
            # statements; move it to a worker if large saves to a remote server still stall.
            ok = self.data_handler.write_shots(pending)
        except StaleDataError as exc:
            return self._resolve_conflicts(exc, pending)
        except ProjectClosedError as exc:
            self._project_closed(exc)
            return False
        except PermissionError as exc:
            self._notify(str(exc), "warning", 8000)
            return False
        except Exception as exc:
            logging.exception("Save failed: %s", exc)
            self._notify("Your changes could not be saved. They are still here, unsaved.",
                         "error", details=str(exc))
            return False

        if not ok:
            reason = getattr(self.data_handler, "last_error", "") or "The database refused the save."
            self._notify(f"Not everything was saved: {reason} Your edits are still here.",
                         "error", 10000, details=reason)
            return False

        self._after_save(pending, approved_now)
        return True

    def _project_closed(self, exc):
        """Someone deleted or archived the open project: nothing was saved; say what can be done."""
        from slate.core.domain.access import can_delete_project
        action = None
        code = self.current_project.code if self.current_project else ""
        if exc.archived and can_delete_project(self.user_roles) and code:
            action = ("Restore", lambda c=code: self._restore_archived(c))
        self._notify(f"{exc} Your edits are still here.", "warning", 15000, action=action)

    def _after_save(self, saved, approved_now=()):
        self.table_model.mark_clean(saved)
        # Undo steps of the shots just written go; edits to other shots can
        # still be taken back.
        self.table_model.forget_undo_for(saved)
        self._own_writes = getattr(self, "_own_writes", None) or {}
        for shot in saved:
            try:
                if int(shot.id) >= 0:
                    self._own_writes[int(shot.id)] = int(shot.version or 0)
            except (TypeError, ValueError):
                pass
        self.update_unsaved_indicator()
        count = len(saved)
        self._notify(f"Saved {count} shot{'s' if count != 1 else ''}.", "success")

        def backed_up(ok):
            # The backup is written after the save, in the background; only a
            # failure needs saying.
            if not ok:
                self._notify("The Excel backup did not update.", "warning",
                             details=getattr(self.sync_service, "last_backup_error", "") or "")
        self._mirror_shots_to_excel(saved, on_done=backed_up)
        self._board_dirty = True
        if self._board_visible():
            self.update_kanban()
        if approved_now:
            self._offer_auto_publish(approved_now)

    # ------------------------------------------------------------ conflicts
    def _conflict_details(self, conflicts, pending):
        by_key = {(str(s.reel_episode or "").lower(), s.shot_name.lower()): s for s in pending}
        ids = [c["shot_id"] for c in conflicts if c.get("shot_id", -1) and c.get("shot_id", -1) > 0]
        fresh = {}
        try:
            for shot in self.data_handler.read_shots_by_id(ids):
                fresh[int(shot.id)] = shot
        except Exception as exc:
            logging.debug("Could not read the other side of a conflict: %s", exc)
        details = []
        for c in conflicts:
            mine_shot = by_key.get((str(c.get("reel") or "").lower(), c["shot_name"].lower()))
            theirs_shot = fresh.get(int(c.get("shot_id") or -1))
            mine = changed_fields(mine_shot) if mine_shot is not None else []
            baseline = getattr(mine_shot, "_baseline", None) or {}
            theirs = diff_paths(baseline, snapshot(theirs_shot)) if theirs_shot is not None else {}
            saved_by, saved_at = "", ""
            try:
                rows = database_manager.get_history(
                    self.current_project.code, c["shot_name"], limit=1,
                    shot_id=c.get("shot_id"), reel=c.get("reel")) or []
                if rows:
                    from slate.core.domain.dates import format_datetime
                    saved_by = rows[0].get("user_name") or rows[0].get("display_name") or ""
                    saved_at = format_datetime(rows[0].get("timestamp"))
            except Exception:
                pass
            details.append({
                "shot_name": c["shot_name"], "reel": c.get("reel", ""),
                "saved_by": saved_by, "saved_at": saved_at,
                "mine": [(field_label(p), display_value(p, old), display_value(p, new)) for p, old, new in mine],
                "theirs": [(field_label(p), display_value(p, v)) for p, v in sorted(theirs.items())],
                "overlap": [field_label(p) for p, _o, _n in mine if p in theirs],
                "_shot": mine_shot, "_fresh": theirs_shot,
            })
        return details

    def _resolve_conflicts(self, exc: StaleDataError, pending) -> bool:
        from ..conflict_resolver_dialog import ConflictResolverDialog
        from slate.core.domain.access import can_force_save
        conflicts = exc.conflicts or []
        deleted = [c for c in conflicts if c.get("kind") == "deleted"]
        if deleted:
            # Nothing to merge with: the shot is gone. Its edits cannot be
            # saved, and the rest are not saved either until this is settled.
            gone = {(str(c.get("reel") or "").lower(), c["shot_name"].lower()) for c in deleted}
            lost = [s for s in pending if (str(s.reel_episode or "").lower(), s.shot_name.lower()) in gone]
            self.table_model.discard_changes(lost)
            self.reload_shots()
            self._notify(f"{exc} Their edits here could not be saved and were dropped; your other "
                         "edits are still waiting - save again.", "warning", 12000)
            return False
        details = self._conflict_details(conflicts, pending) if conflicts else []
        can_force = can_force_save(getattr(self, "access_roles", []))
        dlg = ConflictResolverDialog(details or str(exc), can_force=can_force, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._notify("Nothing was saved. Your edits are still waiting.", "info")
            return False
        if dlg.action_selected == "reload":
            kept, dropped = self._rebase_on_latest(details, keep_mine_on_overlap=False)
            message = "Their changes are loaded"
            if kept:
                message += (f"; {kept} of your edit{'s are' if kept != 1 else ' is'} back on top, "
                            "not saved yet")
            if dropped:
                message += (f"; {dropped} of your edit{'s were' if dropped != 1 else ' was'} dropped "
                            f"because they changed the same field")
            self._notify(message + ". Check, then save.", "info", 10000)
            return False
        if dlg.action_selected == "force":
            if not can_force:
                return False
            self._rebase_on_latest(details, keep_mine_on_overlap=True)
            return self.save_changes()
        return False

    def _rebase_on_latest(self, details, keep_mine_on_overlap: bool):
        """
        Load their save into each conflicting shot and put this person's edits
        back on top. Returns (edits kept, edits dropped).
        """
        kept = dropped = 0
        touched = []
        for detail in details:
            shot = detail.get("_shot")
            fresh = detail.get("_fresh")
            if shot is None or fresh is None:
                continue
            mine = changed_fields(shot)
            theirs = set(diff_paths(getattr(shot, "_baseline", None) or {}, snapshot(fresh)))
            restore(shot, snapshot(fresh))
            shot.version = fresh.version
            shot._baseline = snapshot(fresh)
            for path, _old, new in mine:
                if path in theirs and not keep_mine_on_overlap:
                    dropped += 1
                    continue
                if get_field(shot, path) != new:
                    set_field(shot, path, new)
                kept += 1
            shot._modified = self.table_model.is_dirty(shot)
            shot._remote_changed = False
            touched.append(shot)
        # Only these shots' undo steps go: they no longer match what is under them.
        self.table_model.forget_undo_for(touched)
        self.table_model.refresh_shots(touched)
        self.update_unsaved_indicator()
        self.apply_filters()
        return kept, dropped

    # ------------------------------------------------------------ auto-publish
    def output_folder_name(self, shot=None) -> str:
        """
        The name of the project's output folder ('08_Deliver' on new projects),
        from the folder template - never by listing the share before a question.
        """
        if self.current_project is None:
            return "the output folder"
        template = (getattr(self.current_project, "folder_template", {}) or {}).get("output", "")
        name = str(template).replace("\\", "/").rstrip("/").split("/")[-1]
        return name if name and "{" not in name else "the output folder"

    def _offer_auto_publish(self, shots):
        """Approved just now: offer to copy the comp renders to the shot's output folder."""
        if not self.current_project:
            return
        from slate.gui.components.feedback import confirm
        folder = self.output_folder_name(shots[0])
        names = ", ".join(s.shot_name for s in shots[:5]) + (f" and {len(shots) - 5} more" if len(shots) > 5 else "")
        if not confirm(self, "Publish approved shots",
                       f"{names} {'was' if len(shots) == 1 else 'were'} just approved. Copy "
                       f"the comp renders to {folder} now? Files with the same name there are replaced.",
                       yes_label=f"Copy to {folder}", no_label="Not now"):
            return
        self._start_auto_publish(shots)

    # ------------------------------------------------------------ last project
    def _last_project_key(self) -> str:
        user = str(self.user_data.get("username") or "default").replace("/", "_")
        return f"dashboard/last_project/{user}"

    def _remember_project(self, code):
        try:
            from .column_layout_manager import settings_factory
            settings = settings_factory()
            settings.setValue(self._last_project_key(), code)
            settings.sync()
        except Exception as exc:
            logging.debug("Could not remember the project: %s", exc)

    def _remembered_project(self):
        try:
            from .column_layout_manager import settings_factory
            value = settings_factory().value(self._last_project_key())
            return str(value) if value else ""
        except Exception:
            return ""
