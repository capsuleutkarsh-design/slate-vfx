import logging
import os
import copy
from datetime import datetime
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *

from slate.gui.tabs.vfx_dashboard_pro.ui.edit_project_dialog import EditProjectDialog

class DashboardActionsMixin:

    # ------------------------------------------------------------------
    # Excel backup: the project sheet is the studio's backup copy, kept in
    # step with the database in both directions.
    # ------------------------------------------------------------------

    def _excel_allowed(self) -> bool:
        from slate.core.domain.access import can_use_excel
        return can_use_excel(getattr(self, "user_roles", []))

    def review_queue_click(self):
        """Everything submitted and still waiting for a verdict."""
        from slate.gui.tabs.vfx_dashboard_pro.ui.review_queue_dialog import (
            ReviewQueueDialog,
        )

        if not self.current_project:
            self._notify("Open a project first.", "warning")
            return

        # Verdicts need the right to give them (the store refuses otherwise);
        # an artist sees only the shots they see in the grid.
        visible = None
        if self._is_artist_scope():
            visible = {(str(s.reel_episode or "").lower(), s.shot_name.lower()) for s in self.all_shots or []}
        scope = self._department_scope()
        dialog = ReviewQueueDialog(
            self.current_project.code, parent=self, roles=getattr(self, "access_roles", None),
            current_user=self._signed_in_name(), visible=visible,
            departments=scope, on_verdict=self.on_version_verdict)
        dialog.exec()

    def _signed_in_name(self) -> str:
        data = getattr(self, "user_data", {}) or {}
        return str(data.get("display_name") or data.get("username") or "").strip()

    def on_version_verdict(self, version, status):
        """
        A verdict on a version moves its shot too - as a pending edit, so the
        same Save writes it with its history and tells the artist. Approved and
        Retake only; a department version moves that department, one without a
        department the shot itself.
        """
        from slate.core.domain import shot_status
        from slate.core.domain.versions import STATUS_APPROVED, STATUS_RETAKE
        from slate.gui.tabs.vfx_dashboard_pro.ui.shot_table_model import field_label, get_field, set_field
        target = {STATUS_APPROVED: shot_status.APPROVED, STATUS_RETAKE: shot_status.RETAKE}.get(status)
        model = getattr(self, "table_model", None)
        if not target or model is None or not model.can_edit_freely():
            return
        name = version.shot_name.lower()
        matches = [s for s in self.all_shots or [] if s.shot_name.lower() == name
                   and (not version.reel or str(s.reel_episode or "").lower() == version.reel.lower())]
        if len(matches) != 1:
            return              # not on screen, or the reel is not known and the name is ambiguous
        shot = matches[0]
        dept = str(version.department or "").lower()
        scope = self._department_scope()
        if dept in model._department_keys:
            if scope is not None and dept not in scope:
                return
            path = f"departments.{dept}.status"
        else:
            if scope is not None:
                return
            path = "status"
        if shot_status.canonical(get_field(shot, path)) == target:
            return
        model.apply_edit([shot], lambda s: set_field(s, path, target),
                         f"{field_label(path)} change on {shot.shot_name}")
        self._notify(f"{shot.shot_name} {field_label(path)} set to {target} from the review. "
                     "Not saved yet - Save writes it and tells the artist.", "info", 8000,
                     action=self._undo_action())

    def production_summary_click(self):
        """Where the show is, who is loaded, what is late."""
        from slate.gui.tabs.vfx_dashboard_pro.ui.production_summary_dialog import (
            ProductionSummaryDialog,
        )

        if not self.all_shots:
            self._notify("No shots loaded.", "warning")
            return

        # What is on screen, and the whole project - the dialog says which it
        # shows ("Filtered: 33 of 262 shots") and switches between them. An
        # artist only ever has their own shots, and the caption says so.
        name = getattr(self.current_project, "name", "") if self.current_project else ""
        dialog = ProductionSummaryDialog(list(self.displayed_shots or []), project_name=name,
                                         parent=self, all_shots=list(self.all_shots or []),
                                         whole_label="your shots" if self._is_artist_scope() else "whole project")
        dialog.exec()

    def add_shots_click(self):
        """Create shots by hand, for anything that did not arrive via ingest."""
        if not self._can_manage_shots():
            self._notify("You don't have permission to add shots.", "warning")
            return

        if not self.current_project:
            self._notify("Open a project first.", "warning")
            return

        from slate.gui.tabs.vfx_dashboard_pro.ui.add_shots_dialog import AddShotsDialog
        from slate.core.domain.shot_registry import IngestedShot, register_ingested_shots

        existing = self.all_shots or []
        dialog = AddShotsDialog(
            parent=self,
            existing_reels=[s.reel_episode for s in existing if s.reel_episode],
            existing_shots=[(s.reel_episode, s.shot_name) for s in existing],
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.get_values()
        if not values["shots"]:
            return

        entries = [IngestedShot(reel=values["reel"], shot=name)
                   for name in values["shots"]]

        result = register_ingested_shots(
            project_code=self.current_project.code,
            shots=entries,
            project_name=getattr(self.current_project, "name", ""),
            folder_base=getattr(self.current_project, "folder_base", ""),
        )

        if not result.ok:
            self._notify("The shots could not be added.", "error", details=result.error)
            return

        # The status and priority the coordinator chose, written with the
        # shots (not left as a pending edit nobody asked to review).
        failed_status = False
        if result.created and self.data_handler:
            try:
                fresh = self.data_handler.read_shots()
                created = {n.lower() for n in result.created}
                reel = str(values["reel"] or "").lower()
                touched = [s for s in fresh
                           if s.shot_name.lower() in created and str(s.reel_episode or "").lower() == reel]
                for shot in touched:
                    shot.status = values["status"]
                    shot.priority = values["priority"]
                if touched and not self.data_handler.write_shots(touched):
                    failed_status = True
            except Exception as exc:
                logging.warning("Could not apply status to new shots: %s", exc)
                failed_status = True

        count = len(result.created)
        skipped = list(values.get("skipped") or []) + [n for n in values["shots"] if n not in result.created]
        skipped_text = (f" Skipped {len(skipped)} already there: {', '.join(skipped[:8])}"
                        + ("…" if len(skipped) > 8 else "") + ".") if skipped else ""
        if failed_status:
            self._notify(f"Added {count} shot(s), but their status and priority could not be set."
                         + skipped_text, "warning", 10000)
        else:
            self._notify(f"Added {count} shot{'s' if count != 1 else ''}." + skipped_text, "success",
                         10000 if skipped else 4000)
        self.reload_shots()

    def export_to_excel_click(self):
        """Write every shot currently loaded out to the project Excel backup."""
        if not self._excel_allowed():
            self._notify("You don't have permission to export to Excel.", "warning")
            return

        if not self.current_project:
            self._notify("Open a project first.", "warning")
            return

        if not self.all_shots:
            self._notify("Nothing to export - no shots loaded.", "warning")
            return

        # An Excel-only project is its Excel file: every save already goes
        # there. This said "Exported N" having written nothing.
        from slate.gui.tabs.vfx_dashboard_pro.core.excel_handler import ExcelHandler
        if isinstance(self.data_handler, ExcelHandler):
            self._notify("This project is kept in its Excel file, so every save already "
                         "goes there. There is no separate backup to write.", "info")
            return

        # force=True so this runs even when automatic mirroring is off. The
        # mirror creates the passbook when the project has none yet, as a
        # save does. It is written in the background.
        count = len(self.all_shots)

        def done(ok):
            if ok:
                self._notify(f"Exported {count} shot(s) to the Excel backup.", "success")
            else:
                self._notify("The Excel backup could not be written.", "error",
                             details=getattr(self.sync_service, "last_backup_error", "") or "")
        self._notify("Writing the Excel backup…", "info", 2000)
        self._mirror_shots_to_excel(self.all_shots, force=True, on_done=done)

    def edit_project_click(self):
            if not self.current_project:
                self._notify("Pick a project to edit.", "warning")
                return
            if not self._can_manage_shots():
                self._notify("You don't have permission to edit the project.", "warning")
                return

            dialog = EditProjectDialog(self.current_project, self)
            if dialog.exec():
                data = dialog.get_data()
                try:
                    success = self.project_manager.update_project(
                        data['code'],
                        data['name'],
                        data['excel_path'],
                        data['folder_base'],
                        sheet_name=data.get('sheet_name'),
                        header_row=data.get('header_row'),
                        data_start_row=data.get('data_start_row')
                    )
                    if success:
                        self._notify("Project updated.", "success")
                        self.current_project = self.project_manager.get_project(data['code'])
                        self.project_combo.blockSignals(True)
                        self.load_projects()
                        idx = self.project_combo.findData(data['code'])
                        if idx >= 0:
                            self.project_combo.setCurrentIndex(idx)
                        self.project_combo.blockSignals(False)
                    else:
                        self._notify("The project could not be updated.", "error",
                                     details=getattr(self.project_manager, "last_error", "") or "")
                except Exception as e:
                    self._notify("The project could not be updated.", "error", details=str(e))
