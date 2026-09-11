import logging
import os
import json
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
import copy

from ut_vfx.gui.tabs.vfx_dashboard_pro.core.excel_handler import ExcelHandler
from ut_vfx.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler, StaleDataError
from ut_vfx.gui.tabs.vfx_dashboard_pro.core.file_lock import FileLock
from ut_vfx.gui.tabs.vfx_dashboard_pro.core.poll_worker import PollWorker
from ut_vfx.core.infra.database_manager import database_manager

class DashboardProjectMixin:

    def switch_project(self, project_code):
            if self._is_closing:
                return
            self.log(f"switch_project called with {project_code}")
            if hasattr(self, "save_btn"):
                self.save_btn.setEnabled(bool(self._user_can_edit()))
            self._cleanup_poll_worker(timeout_ms=1500)
            if self.file_lock:
                self.file_lock.release()
                self.file_lock = None
            self._cancel_thumbnail_prefetch()
            self._thumb_requests_inflight.clear()
            if hasattr(self, 'image_cache') and self.image_cache:
                self.image_cache.clear()

            project = self.project_manager.get_project(project_code)
            self.log(f"Project found: {project}")
            if not project:
                self.log("Project is None, returning.")
                return

            self.status_bar.showMessage(f"Loading {project.name}...")
            self.current_project = project

            excel_path = self.project_manager.get_excel_path(project_code)
            self.current_excel_path = excel_path if excel_path and os.path.exists(excel_path) else ""
            self.last_excel_mtime = os.path.getmtime(self.current_excel_path) if self.current_excel_path else None

            # HYBRID LOAD LOGIC
            # Check if project exists in DB (Tracking Data)
            db_project = database_manager.get_tracking_project(project_code)

            if db_project:
                self.log("Loading from DATABASE (SQLiteHandler)...")
                # Resolve ID
                user_id = database_manager.get_user_id(self.user_display_name) or 1
                self.data_handler = SQLiteHandler(
                    project_code, user_id=user_id, user_role=self.access_roles,
                    department_family=self._department_family() or "",
                )

                # PHASE 2: Check/Run Migration to Relational Logic
                # migrated_count = database_manager.migrate_json_to_relational(project_code)
                # if migrated_count > 0:
                #     self.log(f"Migrated {migrated_count} task records to relational table.")

                self.status_bar.showMessage(f"Loading {project.name} (Database Mode)...")

                self.all_shots = self.data_handler.read_shots()
                self.all_shots = self._filter_shots_for_current_user(self.all_shots)
                # The database is the record. Excel is a written-only backup
                # copy, so opening a project never reads from it - importing is
                # a deliberate, confirmed action under Manage Project.

                if not self.local_mode:
                    self._cleanup_poll_worker(timeout_ms=1500)
                    self.poll_worker = PollWorker(project_code, database_manager)
                    self.poll_worker.updates_available.connect(self.on_project_data_updated)
                    self.poll_worker.start()
                else:
                    self.log("LOCAL MODE: real-time DB polling disabled.")
            else:
                self.log("Loading from EXCEL (ExcelHandler)...")
                self.data_handler = ExcelHandler(self.current_excel_path or excel_path, project)
                self.all_shots = self.data_handler.read_shots() # Read now
                self.all_shots = self._filter_shots_for_current_user(self.all_shots)

                # File Locking only for Excel (skip for pure artists)
                # Multi-role check: if user has ANY non-artist role, enable locking
                has_elevated_role = any(r.lower() != 'artist' for r in self.user_roles)
                if has_elevated_role:
                    try:
                        self.file_lock = FileLock(self.current_excel_path or project.excel_path)
                        if not self.file_lock.acquire():
                            self.log("File is locked. Disabling save.")
                            self.status_bar.showMessage("File is locked by another user - Read Only Mode", 10000)
                            self.save_btn.setEnabled(False)
                    except Exception as e:
                        self.log(f"FileLock error: {e}")

            # Load Shots (Common logic already done inside if/else to handle ingest flow)
            try:
                # self.all_shots is already populated above
                self.displayed_shots = list(self.all_shots)

                self.log("Calling populate_filters...")
                self.populate_filters()

                self.log("Calling update_table...")
                self.update_table()

                if hasattr(self, "populate_scope_selector"):
                    self.populate_scope_selector()
                if hasattr(self, "layout_manager") and self.layout_manager:
                    self.layout_manager.set_context(project_code=project_code, model=self.table_model)
                    self.layout_manager.restore_layout()

                self.log("Calling start_thumbnail_loading...")
                self.start_thumbnail_loading()

                self.log("Finished loading.")
                mode_str = "Database" if db_project else "Excel"
                self.status_bar.showMessage(f"Loaded {len(self.all_shots)} shots for {project.name} ({mode_str} Mode)")
                self._warn_if_exr_policy_limits_project()
            except Exception as e:
                self.log(f"Exception in switch_project: {e}")
                import traceback
                traceback.print_exc()
                self._notify("Failed to load shots.", "error", details=str(e))

    def save_changes(self):
            if not self.data_handler:
                return
            if not self._user_can_edit():
                self._notify("Read-only: only Supervisor/Developer/Admin can save changes.", "warning")
                return

            try:
                if hasattr(self.data_handler, 'create_backup'):
                    self.data_handler.create_backup()

                success = self.data_handler.write_shots(self.all_shots)
                if success:
                    # Clear the pending flag explicitly rather than relying on
                    # the reload below to replace the objects.
                    for shot in self.all_shots:
                        shot._modified = False
                    self.update_unsaved_indicator()

                    backed_up = self._mirror_shots_to_excel(self.all_shots)
                    self.status_bar.showMessage("Changes saved to database.")
                    if backed_up:
                        self._notify("All changes have been saved.", "success")
                    else:
                        # Saved, but the failsafe did not update. Say so.
                        self._notify(
                            "Saved to the database, but the Excel backup did not update.",
                            "warning",
                            details=getattr(self.sync_service, "last_backup_error", "") or "",
                        )
                    self.update_backup_indicator()
                    self.refresh_data()
                else:
                    self._notify("One or more rows could not be saved.", "warning")
            except StaleDataError as e:
                from ..conflict_resolver_dialog import ConflictResolverDialog
                from ut_vfx.core.domain.access import can_force_save
                can_force = can_force_save(getattr(self, "access_roles", []))
                dlg = ConflictResolverDialog(str(e), can_force=can_force, parent=self)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    if dlg.action_selected == "reload":
                        self.refresh_data()
                        self._notify("Reloaded latest project data from database.", "info")
                    elif dlg.action_selected == "force":
                        self.data_handler.write_shots(self.all_shots, force=True)
                        self.status_bar.showMessage("Changes force-saved to database!")
                        self._notify("Changes force-saved over conflict.", "warning")
                        self.refresh_data()
            except PermissionError as e:
                self._notify(str(e), "warning")
            except Exception as e:
                self._notify("Failed to save changes.", "error", details=str(e))
