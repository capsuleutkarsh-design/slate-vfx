from typing import Optional, List, Dict, Set
from PySide6.QtWidgets import (QWidget, QLabel, QLineEdit, QMessageBox, QMenu, QFileDialog, QInputDialog)
from PySide6.QtGui import QAction, QPixmap, QCursor
from slate.core.infra.qt_compat import Qt, QTimer, Signal, QItemSelectionModel
from openpyxl import Workbook, load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
from openpyxl.styles import Protection
from openpyxl.worksheet.datavalidation import DataValidation
from ..core.project_manager import ProjectManager
from ..core.excel_handler import ExcelHandler
from ..core.sqlite_handler import SQLiteHandler
from ..core.file_lock import FileLock
from slate.core.infra.app_context import AppContext
from slate.core.infra.database_manager import database_manager
from slate.core.infra.global_config import GlobalConfig
from slate.core.infra.theme_manager import ThemeManager
from slate.core.system.adaptation_engine import system_engine


# ... imports ...
from .shot_detail import ShotDetailWidget
from ..utils.thumbnail import ThumbnailGenerator
from slate.utils.async_image_loader import AsyncImageLoader
import os
import logging
from .add_project_dialog import AddProjectDialog
from .edit_project_dialog import EditProjectDialog
from datetime import datetime
from ..core.sqlite_handler import StaleDataError # Import Exception

from .history_dialog import HistoryDialog # Import UI
from ..core.poll_worker import PollWorker # Real-time updates
from .dashboard_layout_builder import build_dashboard_ui
from .dashboard_sync_service import DashboardSyncService
from .dashboard_avatar_service import DashboardAvatarService
from collections import deque
from slate.core.dcc_launcher import DCCLauncher
import shutil
import glob
from pathlib import Path
from PySide6.QtCore import QThread, Signal

class AutoPublishWorker(QThread):
    finished_signal = Signal(bool, str)
    
    def __init__(self, shot, project_manager, project_code):
        super().__init__()
        self.shot = shot
        self.project_manager = project_manager
        self.project_code = project_code
        
    def run(self):
        try:
            # 1. Find the comp output folder
            comp_path = self.project_manager.get_folder_path(
                self.project_code, "comp", self.shot.reel_episode, self.shot.shot_name
            )
            if not comp_path:
                self.finished_signal.emit(False, "Could not resolve Comp path.")
                return
                
            # Usually render is in 07_Comp/Output
            comp_output = Path(comp_path) / "Output"
            if not comp_output.exists():
                self.finished_signal.emit(False, "Comp Output folder not found.")
                return
                
            # 2. Find the output destination
            final_exr_path = self.project_manager.get_folder_path(
                self.project_code, "output", self.shot.reel_episode, self.shot.shot_name
            )
            if not final_exr_path:
                self.finished_signal.emit(False, "Could not resolve Final Output path.")
                return
                
            dest_exr = Path(final_exr_path) / "EXR"
            dest_mov = Path(final_exr_path) / "MOV"
            dest_exr.mkdir(parents=True, exist_ok=True)
            dest_mov.mkdir(parents=True, exist_ok=True)
            
            moved = 0
            # 3. Copy files over
            for file_path in comp_output.rglob("*.*"):
                if file_path.is_file():
                    ext = file_path.suffix.lower()
                    target_dir = dest_mov if ext in ['.mov', '.mp4'] else dest_exr
                    shutil.copy2(str(file_path), str(target_dir / file_path.name))
                    moved += 1
                    
            self.finished_signal.emit(True, f"Auto-published {moved} files to 08_Output.")
        except Exception as e:
            self.finished_signal.emit(False, str(e))


class ClickableLabel(QLabel):
    clicked = Signal()
    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


from ..controllers.thumbnail_mixin import DashboardThumbnailMixin
from ..controllers.kanban_mixin import DashboardKanbanMixin
from ..controllers.filter_mixin import DashboardFilterMixin
from .components.dashboard_builder_mixin import DashboardBuilderMixin
from .components.dashboard_actions_mixin import DashboardActionsMixin
from .components.dashboard_project_mixin import DashboardProjectMixin

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else keeps the fallback it already had.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""

class DashboardWidget(
    DashboardBuilderMixin,
    DashboardActionsMixin,
    DashboardProjectMixin,
    DashboardFilterMixin, 
    DashboardThumbnailMixin, 
    DashboardKanbanMixin, 
    QWidget
):
    def _notify(self, message: str, level: str = "info", duration: int = 4000, details: str = ""):
        """Use main window feedback style when available, else fallback locally."""
        host = self.window()
        if host and hasattr(host, "show_feedback"):
            try:
                host.show_feedback(message=message, level=level, duration=duration, details=details)
                return
            except Exception:
                pass
        self.status_bar.showMessage(message, duration)
        if level == "error" and details:
            QMessageBox.critical(self, "Error", details)

    def refresh_connection_state(self):
        """Reflect whether the central database is reachable."""
        from slate.core.domain.access import is_offline_fallback

        offline = is_offline_fallback()
        banner = getattr(self, "offline_banner", None)
        if banner is not None:
            banner.setVisible(offline)

        save_btn = getattr(self, "save_btn", None)
        if save_btn is not None:
            can_save = self._user_can_edit()
            save_btn.setEnabled(can_save)
            save_btn.setToolTip(
                "The central database is unreachable. Saving is disabled so "
                "work cannot be stranded on this machine."
                if offline else
                ("" if can_save else "You do not have permission to save changes")
            )
        return offline

    def on_own_status_edited(self, shot, dept_key, status):
        """
        Save an artist's own status change immediately.

        People without full rights have no Save button, so the edit is written
        as it is made rather than sitting in memory waiting for one.
        """
        if not self.data_handler or not hasattr(
                self.data_handler, "update_department_status"):
            return

        try:
            ok = self.data_handler.update_department_status(
                shot_name=shot.shot_name,
                reel=shot.reel_episode,
                dept_key=dept_key,
                status=status,
                current_version=getattr(shot, "version", 0),
                actor_identities=self._artist_identity_candidates(),
            )
        except PermissionError as exc:
            self._notify(str(exc), "warning")
            self.refresh_data()
            return
        except Exception as exc:
            self._notify("Could not save that status.", "error", details=str(exc))
            self.refresh_data()
            return

        if ok:
            self._notify(
                f"{shot.shot_name} {dept_key} set to {status}.", "success", 2500
            )
            self.refresh_data()

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _notifier(self):
        notifier = getattr(self, "_notification_manager", None)
        if notifier is None:
            try:
                from slate.core.domain.notification_manager import NotificationManager
                notifier = NotificationManager()
            except Exception as exc:
                logging.debug("Notifications unavailable: %s", exc)
                notifier = False        # remembered so we do not retry per refresh
            self._notification_manager = notifier
        return notifier or None

    def unread_notifications(self):
        from .notifications_panel import unread_for

        identities = list(self._artist_identity_candidates())
        return unread_for(self._notifier(), identities)

    def refresh_notification_indicator(self):
        """Keep the unread count in the header current."""
        button = getattr(self, "notifications_btn", None)
        if button is None:
            return 0

        notes = self.unread_notifications()
        count = len(notes)
        button.setText(f"Alerts ({count})" if count else "Alerts")
        button.setToolTip(
            f"{count} unread notification(s)" if count
            else "No unread notifications"
        )
        button.setStyleSheet(
            "color: #D9A441; font-weight: 600;" if count else ""
        )
        return count

    def show_notifications(self):
        from .notifications_panel import NotificationsDialog

        dialog = NotificationsDialog(
            self.unread_notifications(), notifier=self._notifier(), parent=self,
        )
        dialog.exec()
        self.refresh_notification_indicator()

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def undo_last_edit(self):
        """Take back the last cell edit, and say what was taken back."""
        model = getattr(self, "table_model", None)
        if model is None or not model.can_undo():
            self._notify("Nothing to undo.", "info", 2000)
            return

        undone = model.undo()
        if not undone:
            return

        self.update_unsaved_indicator()
        shown = undone["to"] if undone["to"] not in (None, "") else "(empty)"
        self._notify(
            f"Undone: {undone['shot']} {undone['column']} back to {shown}.",
            "info", 3000,
        )

    # ------------------------------------------------------------------
    # Unsaved work
    # ------------------------------------------------------------------

    def unsaved_shots(self):
        """Shots edited in the grid and not yet written."""
        return [s for s in (self.all_shots or [])
                if getattr(s, "_modified", False)]

    def has_unsaved_changes(self) -> bool:
        return bool(self.unsaved_shots())

    def update_unsaved_indicator(self):
        """Keep the count of pending edits in front of the person making them."""
        label = getattr(self, "unsaved_label", None)
        if label is None:
            return

        pending = len(self.unsaved_shots())
        if pending:
            label.setText(f"{pending} unsaved change(s)")
            label.setStyleSheet("color: #D9A441; font-size: 11px; font-weight: 600;")
            label.setToolTip("These edits are not in the database yet.")
        else:
            label.setText("")
            label.setToolTip("")

    def confirm_discarding_changes(self, action: str) -> bool:
        """
        Ask before throwing away pending edits.

        Returns True when it is safe to continue. Offers to save rather than
        making the choice a straight save-or-lose.
        """
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
        return True

    def update_backup_indicator(self):
        """
        Show the state of the Excel backup.

        Excel is the studio's failsafe. A failsafe whose state nobody can see
        is not a failsafe, so the header carries it: green while it is current,
        amber after a failure, red once a day has passed without a good write.
        """
        label = getattr(self, "backup_label", None)
        if label is None:
            return

        service = getattr(self, "sync_service", None)
        error = getattr(service, "last_backup_error", None)
        last = getattr(service, "last_backup_at", None)

        if error:
            stale = False
            if last is not None:
                from datetime import datetime, timedelta
                stale = (datetime.now() - last) > timedelta(hours=24)
            colour = "#D9635F" if (stale or last is None) else "#D9A441"
            label.setText("Excel backup: FAILING")
            label.setToolTip(error)
        elif last is not None:
            colour = "#5FBF8F"
            label.setText(f"Excel backup: {last.strftime('%H:%M')}")
            label.setToolTip("Last successful backup of the project sheet.")
        else:
            colour = "#87857F"
            label.setText("Excel backup: --")
            label.setToolTip("No backup written yet in this session.")

        label.setStyleSheet(f"color: {colour}; font-size: 11px;")

    def retry_connection(self):
        """Try the central database again after an outage."""
        from slate.core.infra.database_manager import database_manager

        try:
            if hasattr(database_manager, "reconnect"):
                database_manager.reconnect()
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logging.warning("Reconnect attempt failed: %s", exc)

        if self.refresh_connection_state():
            self._notify("Still unable to reach the central database.", "warning")
        else:
            self._notify("Reconnected. The dashboard is editable again.", "success")
            self.refresh_data()

    def _update_empty_state(self):
        if not hasattr(self, "view_stack") or not hasattr(self, "empty_state_frame"):
            return

        if not self.current_project:
            self.empty_state_title.setText("No project selected")
            self.empty_state_body.setText("Select a project from the dropdown to load shots and start planning.")
            self.view_stack.setCurrentWidget(self.empty_state_frame)
            return

        if self._is_artist_scope() and not self.all_shots:
            self.empty_state_title.setText("No shots assigned to you")
            self.empty_state_body.setText("Assigned shots will appear here automatically when production updates assignments.")
            self.view_stack.setCurrentWidget(self.empty_state_frame)
            return

        if self.local_mode and not self.all_shots:
            self.empty_state_title.setText("LOCAL MODE: no synced shots yet")
            self.empty_state_body.setText(
                "Import from Excel or connect to central database. Team sync actions are limited in LOCAL MODE."
            )
            self.view_stack.setCurrentWidget(self.empty_state_frame)
            return

        if self.all_shots and not self.displayed_shots:
            self.empty_state_title.setText("No matching shots")
            self.empty_state_body.setText("Try clearing search/filter selections to reveal available shots.")
            self.view_stack.setCurrentWidget(self.empty_state_frame)
            return

        board_mode = bool(getattr(self, "view_toggle_btn", None) and self.view_toggle_btn.isChecked())
        active_widget = self.kanban_board if board_mode else self.table
        self.view_stack.setCurrentWidget(active_widget)

    @staticmethod
    def _normalize_roles(roles_data):
        """Normalize role payload to lowercase canonical role names."""
        if isinstance(roles_data, str):
            raw_roles = [roles_data]
        elif isinstance(roles_data, list):
            raw_roles = roles_data
        else:
            raw_roles = ["Artist"]

        aliases = {
            "producer": "supervisor",
            "production": "supervisor",
            "pro": "supervisor",
            "coordinator": "supervisor",
            "coord": "supervisor",
            "dev": "developer",
        }

        normalized = []
        for role in raw_roles:
            role_text = str(role or "Artist").strip().lower()
            normalized.append(aliases.get(role_text, role_text))

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
        from slate.core.domain.access import (
            can_edit_dashboard, is_offline_fallback,
        )
        if is_offline_fallback():
            return False
        return can_edit_dashboard(self.user_roles)

    def _is_artist_scope(self) -> bool:
        """Artists should only see their assigned shots."""
        return "artist" in self.user_roles and not self._user_can_edit()

    def _artist_identity_candidates(self):
        candidates = {
            str(self.user_data.get("user_id", "")).strip(),
            str(self.user_data.get("username", "")).strip(),
            str(self.user_data.get("display_name", "")).strip(),
            str(self.user_display_name or "").strip(),
        }
        return {c.lower() for c in candidates if c}

    def _filter_shots_for_current_user(self, shots):
        if not self._is_artist_scope():
            return list(shots or [])

        identities = self._artist_identity_candidates()
        filtered = []
        for shot in shots or []:
            # Match on every department the artist could be assigned to, not
            # just the shot-level lead - otherwise roto/prep/paint/matchmove
            # artists see an empty dashboard.
            try:
                names = shot.get_all_artists()
            except Exception:
                names = [getattr(shot, "assigned_artist", "")]
            if any(str(n or "").strip().lower() in identities for n in names):
                filtered.append(shot)
        return filtered



    def _warn_if_exr_policy_limits_project(self):
        """Warn once per project when EXR assets exist but EXR loading is disabled."""
        try:
            if GlobalConfig.exr_loading_enabled():
                return
            has_exr = False
            for shot in self.all_shots or []:
                scan_path = str(getattr(shot, "scan_path", "") or "").lower()
                render_path = str(getattr(shot, "render_path", "") or "").lower()
                if ".exr" in scan_path or ".exr" in render_path:
                    has_exr = True
                    break
            if has_exr:
                self.status_bar.showMessage(
                    "EXR assets detected. EXR loading is OFF (safe default). "
                    "Set SLATE_ENABLE_EXR_LOADING=1 or enable_exr_loading=true.",
                    12000,
                )
        except Exception as exc:
            logging.debug("EXR policy warning check skipped: %s", exc)

    def showEvent(self, event):
        super().showEvent(event)
        if self.current_project and self.displayed_shots:
            self._queue_visible_thumbnails()
            if self._thumb_prefetch_queue and not self._thumb_prefetch_timer.isActive():
                self._thumb_prefetch_timer.start()

    def hideEvent(self, event):
        if self._thumb_prefetch_timer.isActive():
            self._thumb_prefetch_timer.stop()
        if self._visible_thumb_timer.isActive():
            self._visible_thumb_timer.stop()
        super().hideEvent(event)

    def log(self, message):
        """Log message using proper logging module."""
        try:
            logging.info(message)
        except Exception as e:
            logging.exception(f"Failed to log message: {e}")

    def _get_user_list(self):
        """Flatten user dict to list of display names. Filters by Role (Artist/Supervisor)."""
        valid_users = set()
        try:
            db_users = self.user_manager.get_all_users()
            for username, u in db_users.items():
                role = u.get('role')
                if not role:
                    roles = u.get('roles', [])
                    role = roles[0] if isinstance(roles, list) and roles else "Artist"
                name = u.get('display_name', '').strip()
                
                # Check normalized role (case-insensitive)
                # Allow any role that contains 'artist' or is in the specific list
                r_norm = str(role).lower()
                if (r_norm in ['artist', 'supervisor', 'lead', 'generalist', 'admin', 'producer'] or 
                    'artist' in r_norm):
                    if name:
                        valid_users.add(name)
                    else:
                        valid_users.add(username)
        except Exception as e:
            logging.exception(f"ERROR: Failed to fetch DB users: {e}")
        result = sorted(list(valid_users))
        if not result:
            result = ["Artist"]
        return result

    def init_ui(self):
        build_dashboard_ui(self)

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
        if self._is_closing:
            return
        if not self.current_project:
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
                    self.log(
                        f"Excel backup was modified outside the software: "
                        f"{self.current_excel_path}. Not imported - the database "
                        f"remains the source of truth."
                    )
            except Exception as e:
                logging.debug(f"Excel update check failed: {e}")

    def refresh_data(self):
        if self._is_closing:
            return
        # An outage can start or end between refreshes.
        self.refresh_connection_state()
        self.refresh_notification_indicator()
        if self.current_project:
            logging.debug("Refreshing data...")
            self.switch_project(self.current_project.code)
            
    def open_query_builder(self):
        """Opens the Advanced Query Builder dialog."""
        from .query_builder_dialog import QueryBuilderDialog
        dialog = QueryBuilderDialog(self)
        
        # Populate with existing rules if any
        if self.advanced_query_rules:
            # Clear default
            for w in list(dialog.rules):
                dialog.remove_rule(w)
            for rule in self.advanced_query_rules:
                dialog.add_rule()
                w = dialog.rules[-1]
                w.field_combo.setCurrentText(rule['field'])
                w.op_combo.setCurrentText(rule['operator'])
                w.value_input.setText(rule['value'])
            idx = 0 if self.advanced_query_match_type == "AND" else 1
            dialog.match_combo.setCurrentIndex(idx)
            
        dialog.query_applied.connect(self._on_query_applied)
        dialog.exec()
        
    def _on_query_applied(self, rules, match_type):
        self.advanced_query_rules = rules
        self.advanced_query_match_type = match_type
        
        # Highlight button if active
        if rules:
            self.advanced_query_btn.setStyleSheet("background-color: #3EA8BF; color: black; font-weight: bold;")
        else:
            self.advanced_query_btn.setStyleSheet("")
            
        self.apply_filters()
            
    def load_projects(self):
        projects = self.project_manager.get_all_projects()
        self.project_combo.clear()
        self.project_combo.addItem("Select Project...", None)
        for p in projects:
            self.project_combo.addItem(f"{p.code} - {p.name}", p.code)
            
        if self.project_manager.default_project:
            index = self.project_combo.findData(self.project_manager.default_project)
            if index >= 0:
                self.project_combo.setCurrentIndex(index)
                
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
            self.all_shots = []
            self.displayed_shots = []
            self.update_table()
            return
        self.switch_project(project_code)

    def _apply_table_spans(self):
        if not hasattr(self, "table") or not hasattr(self, "table_model"):
            return
        self.table.clearSpans()
        if hasattr(self.table_model, "get_header_rows"):
            col_count = self.table_model.columnCount()
            for r in self.table_model.get_header_rows():
                self.table.setSpan(r, 0, 1, col_count)

    def on_groupby_changed(self, index=0):
        if not hasattr(self, "groupby_combo") or not hasattr(self, "table_model"):
            return
        mode = self.groupby_combo.currentData() or self.groupby_combo.currentText()
        self.table_model.set_group_by(mode)
        self._apply_table_spans()

    def update_table(self):
        self.update_unsaved_indicator()
        self.table_model.update_data(self.displayed_shots)
        self._apply_table_spans()
        self.header_view.update_filters(self.displayed_shots)
        self.stats_widget.update_stats(self.displayed_shots)
        self.update_kanban()
        self._update_empty_state()
        
        # Update Users List
        self.users_list.populate(self.all_users)





    def start_thumbnail_loading(self):
        if not self.current_project:
            return
        self._cancel_thumbnail_prefetch()
        self._queue_visible_thumbnails()

        visible_names = {s.shot_name for s in self._visible_shots() if getattr(s, "shot_name", None)}
        for shot in self.displayed_shots:
            shot_name = getattr(shot, "shot_name", "")
            if not shot_name or shot_name in visible_names:
                continue
            self._enqueue_thumbnail_prefetch(shot)

        if self._thumb_prefetch_queue and self.isVisible():
            self._thumb_prefetch_timer.start()
                
    def cycle_theme(self):
        """Cycle through available themes."""
        try:
            if hasattr(ThemeManager, "get_available_themes"):
                themes = ThemeManager.get_available_themes()
                if not themes:
                    return
                current = getattr(self, "current_theme_idx", 0)
                current = (current + 1) % len(themes)
                self.current_theme_idx = current
                new_theme = themes[current]
                ThemeManager.apply_theme(new_theme)
            else:
                ThemeManager.toggle_mode()
                new_theme = "Dark" if ThemeManager.is_dark_mode() else "Light"

            if str(new_theme).lower() in {"default", "dark", "oceanic", "neon"}:
                self.theme_btn.setText("🌙")
            else:
                self.theme_btn.setText("☀")
        except Exception as e:
            logging.exception(f"Theme toggle failed: {e}")

    def on_image_loaded(self, identifier, path, image):
        self._thumb_requests_inflight.discard(identifier)
        identifier_project = self._project_code_from_identifier(identifier)
        if self.current_project and identifier_project and identifier_project != self.current_project.code:
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return

        # LRU eviction cap at 150 items to prevent RAM bloat
        while len(self.image_cache) >= 150:
            try:
                self.image_cache.popitem(last=False)
            except Exception:
                break

        self.image_cache[identifier] = pixmap
        if path:
            self.image_cache[path] = pixmap

        shot_name = self._shot_name_from_identifier(identifier)
        # Update Shot object
        # Find shot by name (identifier)
        for shot in self.all_shots:
            if shot.shot_name == shot_name:
                shot.thumbnail_path = path
                break
        
        # Force table update to replace Yellow with Result
        self.table.viewport().update()

        # If Detail Widget is open for this shot, update it
        if self.detail_widget and self.detail_widget.shot.shot_name == shot_name:
            self.detail_widget.thumb_label.setPixmap(pixmap.scaled(100, 56, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def on_image_started(self, identifier):
        """Called when loader starts processing a thumbnail (Show Yellow)."""
        identifier_project = self._project_code_from_identifier(identifier)
        if self.current_project and identifier_project and identifier_project != self.current_project.code:
            return
        yellow_path = self.thumb_gen.get_yellow_placeholder()
        shot_name = self._shot_name_from_identifier(identifier)

        # Update Shot object to point to yellow placeholder temporarily
        for shot in self.all_shots:
            if shot.shot_name == shot_name:
                shot.thumbnail_path = yellow_path
                break
        
        # Trigger table repaint
        self.table.viewport().update()

        # Update Detail Widget if open
        if self.detail_widget and self.detail_widget.shot.shot_name == shot_name:
            pix = QPixmap(yellow_path)
            self.detail_widget.thumb_label.setPixmap(pix.scaled(100, 56, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def on_item_clicked(self, index):
        """Handle single click on table: toggle groups, allow inline edits, or open dock."""
        if not index.isValid():
            return

        # Check if group header row clicked
        if hasattr(self.table_model, "is_group_header") and self.table_model.is_group_header(index.row()):
            info = self.table_model.get_group_info(index.row())
            if info:
                self.table_model.toggle_group_collapse(info.get("group_key", ""))
                self._apply_table_spans()
            return

        # Don't steal focus into detail dock if clicking inline editable columns
        editable_cols = {2, 3, 9, 11, 12, 13, 14, 15}
        if index.column() in editable_cols:
            return

        shot = self.table_model.get_shot_at(index.row()) if hasattr(self.table_model, "get_shot_at") else None
        if not shot and 0 <= index.row() < len(self.displayed_shots):
            shot = self.displayed_shots[index.row()]

        if not shot:
            return

        if self.detail_widget and getattr(self.detail_widget.shot, "shot_name", "") == shot.shot_name:
            if not self.detail_container.isVisible():
                self.detail_container.show()
            return

        self.open_detail_dock(shot)

    def on_item_double_clicked(self, index):
        if not index.isValid():
            return

        # Toggle collapse on group headers
        if hasattr(self.table_model, "is_group_header") and self.table_model.is_group_header(index.row()):
            info = self.table_model.get_group_info(index.row())
            if info:
                self.table_model.toggle_group_collapse(info.get("group_key", ""))
                self._apply_table_spans()
            return

        editable_cols = {2, 3, 9, 11, 12, 13, 14, 15}
        if index.column() in editable_cols:
            return

        shot = self.table_model.get_shot_at(index.row()) if hasattr(self.table_model, "get_shot_at") else None
        if not shot and 0 <= index.row() < len(self.displayed_shots):
            shot = self.displayed_shots[index.row()]

        if shot:
            self.open_detail_dock(shot)

    def keyPressEvent(self, event):
        """Spacebar triggers instant Quick Look preview for selected shot."""
        if event.key() == Qt.Key.Key_Space:
            selected_rows = self.table.selectionModel().selectedRows()
            if selected_rows:
                r = selected_rows[0].row()
                shot = self.table_model.get_shot_at(r) if hasattr(self.table_model, "get_shot_at") else None
                if shot:
                    self.open_quick_look(shot)
                    event.accept()
                    return
        super().keyPressEvent(event)

    def _find_shot_media_path(self, shot) -> str:
        """Find the best media file (MOV, MP4, EXR, thumbnail) for Quick Look playback."""
        if not self.current_project:
            return getattr(shot, "thumbnail_path", "") or ""

        try:
            # 1. Output MOV/MP4 in 08_Output
            out_dir = self.project_manager.get_folder_path(
                self.current_project.code, "output", shot.reel_episode, shot.shot_name
            )
            if out_dir and os.path.exists(out_dir):
                movs = list(glob.glob(os.path.join(out_dir, "**", "*.mov"), recursive=True)) + \
                       list(glob.glob(os.path.join(out_dir, "**", "*.mp4"), recursive=True))
                if movs:
                    movs.sort(key=os.path.getmtime, reverse=True)
                    return movs[0]

            # 2. Comp output MOV in 07_Comp
            comp_dir = self.project_manager.get_folder_path(
                self.current_project.code, "comp", shot.reel_episode, shot.shot_name
            )
            if comp_dir and os.path.exists(comp_dir):
                comp_movs = list(glob.glob(os.path.join(comp_dir, "**", "*.mov"), recursive=True)) + \
                            list(glob.glob(os.path.join(comp_dir, "**", "*.mp4"), recursive=True))
                if comp_movs:
                    comp_movs.sort(key=os.path.getmtime, reverse=True)
                    return comp_movs[0]

            # 3. Fallback to thumbnail
            if getattr(shot, "thumbnail_path", None) and os.path.exists(shot.thumbnail_path):
                return shot.thumbnail_path
        except Exception as e:
            logging.debug(f"Quick look path search error: {e}")

        return getattr(shot, "thumbnail_path", "") or ""

    def open_quick_look(self, shot):
        """Open MacOS/ShotGrid style Spacebar Quick Look window for the shot."""
        if not shot:
            return
        media_path = self._find_shot_media_path(shot)
        try:
            from slate.gui.widgets.quick_look import QuickLookDialog
            asset_title = f"{shot.shot_name} (Version: {shot.curr_version or 'v001'})"
            dialog = QuickLookDialog(self, asset_name=asset_title, asset_path=media_path)
            dialog.exec()
        except Exception as e:
            self._notify(f"Could not open Quick Look: {e}", "warning")

    def open_batch_edit_dialog(self, shots):
        """Open dialog to edit multiple selected shots at once."""
        if not shots:
            return
        if not self._user_can_edit():
            self._notify("Read-only: only Supervisor/Developer/Admin can batch edit.", "warning")
            return
        from .batch_edit_dialog import BatchEditDialog
        dialog = BatchEditDialog(len(shots), all_users=self._get_user_list(), parent=self)
        if dialog.exec():
            updates = dialog.get_updates()
            if updates:
                self.on_batch_update(shots, updates)

    def on_batch_update(self, shots, updates: dict):
        """Apply batch updates to shots, write to DB, and mirror to Excel."""
        if not shots or not updates:
            return
        if not self._user_can_edit():
            self._notify("Read-only: only Supervisor/Developer/Admin can batch edit.", "warning")
            return

        for s in shots:
            for field, val in updates.items():
                if hasattr(s, field):
                    setattr(s, field, val)
                    s._modified = True

        try:
            if hasattr(self.data_handler, "write_shots"):
                self.data_handler.write_shots(shots)
                self._mirror_shots_to_excel(shots)
            self.update_table()
            self._notify(f"Batch updated {len(shots)} shots successfully!", "success", 4000)
        except Exception as e:
            self._notify("Batch update failed.", "error", details=str(e))

    def open_detail_dock(self, shot):
        logging.debug(f"Opening detail dock for {shot.shot_name}")
        if self.detail_widget:
            self.detail_layout.removeWidget(self.detail_widget)
            self.detail_widget.deleteLater()

        # Ensure thumb refresh also covers stale/missing placeholder paths.
        if self._needs_thumbnail_refresh(shot):
            self._queue_thumbnail_load(shot)

        # Pass all_users to ShotDetailWidget
        self.detail_widget = ShotDetailWidget(
            shot,
            self.user_roles,
            self.project_manager,
            self.all_shots,
            self.all_users,
            user_data=self.user_data,
            current_project_code=(self.current_project.code if self.current_project else ""),
            inherit_app_theme=self.inherit_app_theme,
        )
            
        self.detail_widget.close_requested.connect(self.close_detail_dock)
        self.detail_widget.search_requested.connect(self.on_detail_search)
        self.detail_widget.save_requested.connect(self.on_shot_save) # Connect save signal
        if hasattr(self.detail_widget, "quick_look_requested"):
            self.detail_widget.quick_look_requested.connect(self.open_quick_look)
        if hasattr(self.detail_widget, "rv_review_requested"):
            self.detail_widget.rv_review_requested.connect(self.review_in_rv)
        self.detail_layout.addWidget(self.detail_widget)
        self.detail_container.show()
        
        # Ensure splitter gives it space
        if self.detail_container.width() < 50:
             sizes = self.splitter.sizes()
             # Allocate a larger default width for readability in SOW/feedback fields.
             if len(sizes) >= 2:
                 new_sizes = list(sizes)
                 total = max(sum(sizes), 1)
                 target_detail = max(system_engine.scale_px(460, minimum=380), int(total * 0.36))
                 available_for_others = max(100, total - target_detail)
                 new_sizes[-1] = target_detail
                 if len(new_sizes) == 2:
                     new_sizes[0] = available_for_others
                 else:
                     middle_width = new_sizes[1] if self.users_list.isVisible() else 0
                     new_sizes[1] = middle_width
                     new_sizes[0] = max(100, available_for_others - middle_width)
                 self.splitter.setSizes(new_sizes)

    def show_context_menu(self, pos):
        try:
            index = self.table.indexAt(pos)
            if not index.isValid():
                return

            # Check if group header clicked
            if hasattr(self.table_model, "is_group_header") and self.table_model.is_group_header(index.row()):
                info = self.table_model.get_group_info(index.row())
                if info:
                    menu = QMenu(self)
                    is_col = bool(info.get("is_collapsed", False))
                    act_txt = "Expand Group" if is_col else "Collapse Group"
                    act = menu.addAction(act_txt)
                    act.triggered.connect(lambda: (
                        self.table_model.toggle_group_collapse(info.get("group_key", "")),
                        self._apply_table_spans()
                    ))
                    menu.exec(self.table.viewport().mapToGlobal(pos))
                return

            # Collect selected shots
            selected_shots = []
            if self.table.selectionModel():
                for s_idx in self.table.selectionModel().selectedRows():
                    s = self.table_model.get_shot_at(s_idx.row()) if hasattr(self.table_model, "get_shot_at") else None
                    if not s and 0 <= s_idx.row() < len(self.displayed_shots):
                        s = self.displayed_shots[s_idx.row()]
                    if s and s not in selected_shots:
                        selected_shots.append(s)

            # Fallback to clicked shot if selection was empty
            if not selected_shots:
                s = self.table_model.get_shot_at(index.row()) if hasattr(self.table_model, "get_shot_at") else None
                if not s and 0 <= index.row() < len(self.displayed_shots):
                    s = self.displayed_shots[index.row()]
                if s:
                    selected_shots = [s]

            if not selected_shots:
                return

            menu = QMenu(self)

            # === MULTI-SHOT BATCH EDITING ===
            # Shot-level edits. Offering them to someone who cannot make them
            # just produces a "read-only" message after the click.
            if len(selected_shots) > 1 and self._can_manage_shots():
                hdr = menu.addAction(f"── {len(selected_shots)} Shots Selected ──")
                hdr.setEnabled(False)

                batch_act = QAction(f"Batch Edit ({len(selected_shots)} Shots)...", menu)
                batch_act.triggered.connect(lambda: self.open_batch_edit_dialog(selected_shots))
                menu.addAction(batch_act)
                menu.addSeparator()

                # Quick Status Submenu
                st_menu = menu.addMenu("Batch Set Status")
                for st in ["APPROVED", "WIP", "RETAKE", "SENT FOR REVIEW", "READY", "YTS", "OMIT"]:
                    act = QAction(st, st_menu)
                    act.triggered.connect(lambda ch=False, s=st: self.on_batch_update(selected_shots, {"status": s}))
                    st_menu.addAction(act)

                # Quick Assign Artist Submenu
                art_menu = menu.addMenu("Batch Assign Artist")
                unassign_act = QAction("Unassigned", art_menu)
                unassign_act.triggered.connect(lambda ch=False: self.on_batch_update(selected_shots, {"assigned_artist": ""}))
                art_menu.addAction(unassign_act)
                art_menu.addSeparator()
                for user in self._get_user_list():
                    act = QAction(user, art_menu)
                    act.triggered.connect(lambda ch=False, u=user: self.on_batch_update(selected_shots, {"assigned_artist": u}))
                    art_menu.addAction(act)

                # Quick Priority Submenu
                prio_menu = menu.addMenu("Batch Set Priority")
                for p_num, p_name in [(0, "0 (Urgent)"), (1, "1 (High)"), (2, "2 (Normal)"), (3, "3 (Low)")]:
                    act = QAction(p_name, prio_menu)
                    act.triggered.connect(lambda ch=False, p=p_num: self.on_batch_update(selected_shots, {"priority": p}))
                    prio_menu.addAction(act)

                menu.addSeparator()

            # Single Shot Actions (using primary clicked shot)
            primary_shot = selected_shots[0]

            # Quick Look action
            ql_act = QAction("Quick Look (Space)", menu)
            ql_act.triggered.connect(lambda: self.open_quick_look(primary_shot))
            menu.addAction(ql_act)

            rv_act = QAction("Review in RV...", menu)
            rv_act.triggered.connect(lambda: self.review_in_rv(primary_shot))
            menu.addAction(rv_act)
            menu.addSeparator()

            # Quick Folders
            folders = {
                "Open Scan": "scan",
                "Open Roto": "roto",
                "Open Prep": "prep",
                "Open DMP": "dmp",
                "Open Comp": "comp",
                "Open Output": "output"
            }
            
            for label, key in folders.items():
                action = QAction(label, menu)
                action.triggered.connect(lambda checked=False, k=key, s=primary_shot: self.open_shot_folder(k, s))
                menu.addAction(action)
                
            menu.addSeparator()
            
            # DCC Launchers
            dcc_menu = menu.addMenu("Launch DCC")
            dcc_launcher = DCCLauncher(self)
            
            nuke_action = QAction("Foundry Nuke", dcc_menu)
            nuke_action.triggered.connect(lambda: dcc_launcher.launch("nuke", primary_shot.id))
            dcc_menu.addAction(nuke_action)
            
            natron_action = QAction("Natron", dcc_menu)
            natron_action.triggered.connect(lambda: dcc_launcher.launch("natron", primary_shot.id))
            dcc_menu.addAction(natron_action)
            
            blender_action = QAction("Blender", dcc_menu)
            blender_action.triggered.connect(lambda: dcc_launcher.launch("blender", primary_shot.id))
            dcc_menu.addAction(blender_action)
            
            silhouette_action = QAction("BorisFX Silhouette", dcc_menu)
            silhouette_action.triggered.connect(lambda: dcc_launcher.launch("silhouette", primary_shot.id))
            dcc_menu.addAction(silhouette_action)
            
            menu.addSeparator()
            
            # View History
            history_action = QAction("View History", menu)
            history_action.triggered.connect(lambda: self.show_history_dialog(primary_shot))
            menu.addAction(history_action)
                
            menu.exec(self.table.viewport().mapToGlobal(pos))
        except Exception as e:
            logging.exception(f"Context Menu Error: {e}")
            
    def _shot_folder_resolver(self, shot, key):
        """
        Where one of a shot's folders actually is.

        Goes through the project manager rather than the shot's stored paths:
        it copes with the older projects whose folder names do not match the
        template exactly, which the stored paths do not.
        """
        if not self.current_project:
            return None
        try:
            return self.project_manager.get_folder_path(
                self.current_project.code, key,
                getattr(shot, "reel_episode", ""),
                getattr(shot, "shot_name", ""),
            ) or None
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
            opened = review_shot_in_rv(
                shot, parent=self, project_root=project_root,
                folder_resolver=self._shot_folder_resolver,
            )
        except Exception as exc:
            logging.exception("Review in RV failed: %s", exc)
            self._notify("Could not open the review picker.", "error",
                         details=str(exc))
            return

        if not opened:
            self._notify(
                f"Nothing to review for {getattr(shot, 'shot_name', 'this shot')} "
                "yet - no scan or render was found on disk.",
                "warning",
            )

    def open_shot_folder(self, folder_key, shot):
        if not self.current_project:
            return
            
        try:
            path = self.project_manager.get_folder_path(
                self.current_project.code, 
                folder_key, 
                shot.reel_episode, # Use reel_episode as per integrated app model
                shot.shot_name
            )
            
            if path:
                if not self.project_manager.open_folder(path):
                    self._notify(
                        "Could not open folder. Check if it exists.",
                        "warning",
                        details=path,
                    )
            else:
                self._notify(f"No path configured for '{folder_key}'.", "warning")
        except Exception as e:
            self._notify("Failed to open folder.", "error", details=str(e))

    def show_history_dialog(self, shot):
        if not self.current_project:
            return
            
        dialog = HistoryDialog(self.current_project.code, shot.shot_name, self)
        dialog.exec()
        
    def on_shot_save(self, shot):
        """Handle save request from Detail Widget with Optimistic Locking"""
        if not self.data_handler:
            return
        if not self._user_can_edit():
            self._notify("Read-only: only Supervisor/Developer/Admin can save changes.", "warning")
            return
            
        try:
            success = self.data_handler.write_shots([shot])
            if success:
                mirrored = self._mirror_shots_to_excel([shot])
                self.refresh_data()
                if mirrored:
                    self._notify(f"Shot {shot.shot_name} saved successfully.", "success")
                else:
                    self._notify(
                        f"Shot {shot.shot_name} saved to database, but Excel mirror failed.",
                        "warning",
                    )
                    
                # TRIGGER AUTO-PUBLISH IF APPROVED
                status = getattr(shot, 'status', '').upper()
                if status == 'APPROVED':
                    self._notify("Shot Approved! Auto-publishing renders to 08_Output...", "info")
                    self.publish_worker = AutoPublishWorker(shot, self.project_manager, self.current_project.code)
                    def on_publish_finished(ok, msg):
                        if ok:
                            self._notify(msg, "success")
                        else:
                            self._notify(f"Auto-publish failed: {msg}", "warning")
                    self.publish_worker.finished_signal.connect(on_publish_finished)
                    self.publish_worker.start()
                    
            else:
                self._notify("Could not save shot. Check logs.", "warning")
        except StaleDataError as e:
            from .conflict_resolver_dialog import ConflictResolverDialog
            can_force = any(str(r).lower() in ("admin", "developer", "supervisor") for r in getattr(self, "user_roles", []))
            dlg = ConflictResolverDialog(str(e), can_force=can_force, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                if dlg.action_selected == "reload":
                    self.refresh_data()
                    self._notify("Reloaded latest project data from database.", "info")
                elif dlg.action_selected == "force":
                    shot.version = 0
                    self.data_handler.write_shots([shot], force=True)
                    self._notify("Shot changes force-saved over conflict.", "warning")
                    self.refresh_data()
        except PermissionError as e:
            self._notify(str(e), "warning")
        except Exception as e:
            self._notify("An unexpected error occurred.", "error", details=str(e))

    def on_detail_search(self, text):
        # If text is empty, maybe use the shot name? Or just focus search bar
        if not text:
            self.search_input.setFocus()
            self.search_input.selectAll()
        else:
            self.search_input.setText(text)
            
    def change_avatar(self):
        """Open file picker and save new avatar."""
        if self._is_closing:
            return
        if not hasattr(self, "avatar_label"):
            self._notify("Profile avatar is managed in the main header.", "info")
            return

        path = self.avatar_service.choose_avatar_file(self)
        if not path:
            return

        try:
            self._cleanup_avatar_upload_worker(timeout_ms=1000)
            username = self.user_data.get("username", "unknown")
            self.avatar_upload_worker = self.avatar_service.start_avatar_upload(
                path,
                username,
                self.on_avatar_upload_finished,
            )
            if self.avatar_upload_worker:
                self._notify("Uploading profile picture in background...", "info")
            else:
                self._notify("No avatar file selected.", "warning")
        except Exception as e:
            self._notify("Failed to initialize avatar upload.", "error", details=str(e))

    def on_avatar_upload_finished(self, success, result_path, username):
        sender = self.sender()
        if sender is not None and sender is not self.avatar_upload_worker:
            return
        if self._is_closing:
            self._cleanup_avatar_upload_worker(timeout_ms=500)
            return
        if not success:
            self._notify("Could not copy avatar image.", "error", details=str(result_path))
            self.avatar_upload_worker = None
            return

        ok, payload = self.avatar_service.finalize_avatar_upload(success, result_path, username)
        if ok:
            self.load_user_avatar_from_path(payload)
        else:
            self._notify("Avatar update could not be saved to database.", "warning", details=str(payload))

        self.avatar_upload_worker = None

    def load_user_avatar(self):
        """Load avatar from DB on startup."""
        try:
            username = self.user_data.get('username', 'unknown')
            path = self.avatar_service.get_user_avatar_path(username)
            if path and os.path.exists(path):
                self.load_user_avatar_from_path(path)
        except Exception as e:
            logging.exception(f"Avatar load error: {e}")

    def load_user_avatar_from_path(self, path):
        """Helper to render the avatar."""
        if not hasattr(self, "avatar_label"):
            return

        self.avatar_service.apply_avatar_to_label(self.avatar_label, path, size=40)
        
    def close_detail_dock(self):
        self.detail_container.hide()
        if self.detail_widget:
            self.detail_widget.deleteLater()
            self.detail_widget = None

    def set_project_root_click(self):
        code = self.project_combo.currentData()
        if not code:
            self._notify("Please select a project first.", "warning")
            return
        current_root = ""
        if self.current_project:
            current_root = self.current_project.folder_base
            
        path = QFileDialog.getExistingDirectory(self, f"Select Root for {code}", current_root)
        if path:
            self.project_manager.set_project_folder_base(code, path)
            self._notify(f"Project root set to: {path}", "success")
            # Refresh to Apply
            self.switch_project(code)

    @staticmethod
    def _friendly_header_name(field_name: str) -> str:
        text = str(field_name or "").replace("_", " ").strip()
        return text.title() if text else ""

    def add_project_click(self):
        dialog = AddProjectDialog(self)
        if dialog.exec():
            data = dialog.get_data()
            try:
                self.project_manager.add_project(
                    code=data['code'],
                    name=data['name'],
                    excel_path=data['excel_path'],
                    folder_base=data['folder_base'],
                    sheet_name=data['sheet_name'],
                    header_row=data['header_row'],
                    data_start_row=data['data_start_row']
                )
                self.load_projects() # Refresh list
                # Select the new project
                idx = self.project_combo.findData(data['code'])
                if idx >= 0:
                    self.project_combo.setCurrentIndex(idx)
            except Exception as e:
                self._notify("Failed to create project.", "error", details=str(e))

    def delete_project_click(self):
        if not self.current_project:
            return
            
        code = self.current_project.code
        text, ok = QInputDialog.getText(self, "Delete Project", 
                                        f"WARNING: This will delete ALL data for '{code}'.\n\nType 'DELETE' to confirm:",
                                        QLineEdit.EchoMode.Normal, "")
        if ok and text == "DELETE":
            if self.project_manager.delete_project(code):
                self._notify(f"Project {code} deleted.", "success")
                self.load_projects()
                # Clear selection or select another
                if self.project_combo.count() > 0:
                    self.project_combo.setCurrentIndex(0)
            else:
                self._notify(f"Failed to delete project {code}.", "error")

    def show_column_menu(self):
        menu = QMenu(self)
        header = self.table.horizontalHeader()
        
        select_all = QAction("Select All", menu)
        select_all.triggered.connect(lambda: self._set_all_columns(True))
        menu.addAction(select_all)
        
        deselect_all = QAction("Deselect All", menu)
        deselect_all.triggered.connect(lambda: self._set_all_columns(False))
        menu.addAction(deselect_all)
        
        menu.addSeparator()

        reset_layout = QAction("Reset to Default Layout", menu)
        reset_layout.triggered.connect(self.reset_column_layout)
        menu.addAction(reset_layout)

        if self._user_can_edit():
            save_default = QAction("Save as Project Default", menu)
            save_default.triggered.connect(self.save_project_default_layout)
            menu.addAction(save_default)

        menu.addSeparator()
        
        for i in range(self.table_model.columnCount()):
            col_name = self.table_model.headerData(i, Qt.Orientation.Horizontal)
            action = QAction(col_name, menu)
            action.setCheckable(True)
            action.setChecked(not header.isSectionHidden(i))
            action.setData(i)
            action.triggered.connect(self.toggle_column)
            menu.addAction(action)
            
        anchor = getattr(self, "columns_btn", None) or getattr(self, "more_actions_btn", None) or self
        if hasattr(anchor, "mapToGlobal") and hasattr(anchor, "rect"):
            pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        else:
            pos = QCursor.pos()
        menu.exec(pos)
        
    def toggle_column(self):
        action = self.sender()
        if not action:
            return
        col_idx = action.data()
        if action.isChecked():
            self.table.showColumn(col_idx)
        else:
            self.table.hideColumn(col_idx)
        if hasattr(self, "layout_manager") and self.layout_manager:
            self.layout_manager.save_user_layout()
            
    def _set_all_columns(self, visible: bool):
        for i in range(self.table_model.columnCount()):
            if visible:
                self.table.showColumn(i)
            else:
                self.table.hideColumn(i)
        if hasattr(self, "layout_manager") and self.layout_manager:
            self.layout_manager.save_user_layout()

    def reset_column_layout(self):
        if hasattr(self, "layout_manager") and self.layout_manager:
            self.layout_manager.reset_to_defaults()
            self._notify("Column layout reset to defaults.", "info")

    def save_project_default_layout(self):
        if hasattr(self, "layout_manager") and self.layout_manager:
            success = self.layout_manager.save_project_default()
            if success:
                self._notify("Saved current layout as project default.", "success")
            else:
                self._notify("Failed to save project default layout.", "error")

    def open_delivery_batches_dialog(self):
        """Open Delivery Batches Dialog for packaging and delivery note generation."""
        if not getattr(self, "current_project", None):
            self._notify("Please select a project first.", "warning")
            return
        from slate.gui.tabs.vfx_dashboard_pro.ui.delivery_batches_dialog import (
            DeliveryBatchesDialog,
        )
        dialog = DeliveryBatchesDialog(
            project_code=self.current_project.code,
            current_user=getattr(self, "user_display_name", "") or "unknown",
            parent=self,
        )
        dialog.exec()

    def _department_family(self) -> Optional[str]:
        """The department family on this person's job title, if any."""
        return self._detect_user_department_family()

    def _is_department_scoped(self) -> bool:
        """A lead: full edit rights, but only inside their own department."""
        from slate.core.domain.access import is_department_scoped
        return is_department_scoped(getattr(self, "access_roles", self.user_roles))

    def _department_scope(self):
        """
        The department keys this person may edit, or None if unrestricted.

        An empty set is a scoped role whose job title names no department:
        they can edit nothing until an admin sets one, and are told so.
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
        from .status_delegate import StatusDelegate
        if can_edit_dashboard(self.user_roles):
            return list(StatusDelegate.STATUS_CHOICES)
        allowed = artist_statuses()
        return [s for s in StatusDelegate.STATUS_CHOICES if s.upper() in allowed]

    def _detect_user_department_family(self) -> Optional[str]:
        job_title = str(getattr(self, "user_data", {}).get("job_title", "")).strip().lower()
        dept = str(getattr(self, "user_data", {}).get("department", "")).strip().lower()
        search = f"{job_title} {dept}".strip()
        if not search:
            return None
        from slate.core.domain.departments import load_departments, families
        all_depts = load_departments()
        for d in all_depts:
            if d.key.lower() in search or d.name.lower() in search or d.label.lower() in search:
                return d.family
        for fam in families().keys():
            if fam.lower() in search:
                return fam
        return None

    def populate_scope_selector(self):
        if not hasattr(self, "scope_combo") or not self.scope_combo:
            return
        current_data = self.scope_combo.currentData()
        self.scope_combo.blockSignals(True)
        self.scope_combo.clear()
        self.scope_combo.addItem("Scope: All", "all")
        self.scope_combo.addItem("Scope: My Shots", "my_shots")

        user_fam = self._detect_user_department_family()
        if user_fam:
            self.scope_combo.addItem(f"Scope: My Dept ({user_fam.title()})", f"dept:{user_fam}")

        if any(r.lower() in ("supervisor", "admin", "lead", "coordinator") for r in getattr(self, "user_roles", [])):
            from slate.core.domain.departments import families
            for fam in families().keys():
                if fam != user_fam:
                    self.scope_combo.addItem(f"Scope: {fam.title()} Dept", f"dept:{fam}")

        # The combo is built with "All" already in it, so "current" is not
        # proof that anybody chose anything. Only a pick the person actually
        # made (on_scope_changed) is kept; until then a lead opens on their
        # own department.
        picked = bool(getattr(self, "_scope_user_picked", False))
        idx = self.scope_combo.findData(current_data) if picked else -1
        if idx >= 0:
            self.scope_combo.setCurrentIndex(idx)
        elif user_fam and self._is_department_scoped():
            self.scope_combo.setCurrentIndex(
                max(0, self.scope_combo.findData(f"dept:{user_fam}")))
        elif not picked:
            self.scope_combo.setCurrentIndex(0)
        else:
            self.scope_combo.setCurrentIndex(max(0, idx))
        self.scope_combo.blockSignals(False)
        # The columns have to follow the scope that was just set.
        self.on_scope_changed()

    def on_scope_changed(self, index=None):
        if not hasattr(self, "scope_combo") or not self.scope_combo:
            return
        # Signals are blocked while the combo is being rebuilt, so reaching
        # here from a signal means a person picked this.
        if not self.scope_combo.signalsBlocked() and index is not None:
            self._scope_user_picked = True
        scope_data = self.scope_combo.currentData() or "all"
        
        # Column narrowing
        if str(scope_data).startswith("dept:"):
            fam = str(scope_data).split(":", 1)[1]
            from slate.core.domain.departments import load_departments
            all_depts = load_departments()
            fam_dept_keys = {d.key for d in all_depts if d.family == fam}
            other_dept_keys = {d.key for d in all_depts if d.family != fam}

            for col_idx, (col_key, _, _) in enumerate(self.table_model.COLUMNS):
                if col_key in other_dept_keys:
                    self.table.setColumnHidden(col_idx, True)
                elif col_key in fam_dept_keys:
                    self.table.setColumnHidden(col_idx, False)
        else:
            # Restore saved column layout
            if hasattr(self, "layout_manager") and self.layout_manager:
                self.layout_manager.restore_layout()

        self.apply_filters()

    def _mirror_shots_to_excel(self, shots, force: bool = False):
        success, last_mtime = self.sync_service.mirror_shots_to_excel(
            shots=shots,
            current_project=self.current_project,
            data_handler=self.data_handler,
            force=force,
        )
        if last_mtime is not None:
            self.last_excel_mtime = last_mtime
        return success
             
    def run_debug(self):
        if self.data_handler:
            logging.info("Running dashboard debug mode...")
            self.data_handler.debug_column_mapping()
            self._notify("Column mapping printed to console.", "info")
        else:
            self._notify("No project loaded.", "warning")

    def cleanup_resources(self):
        """Called when app closes."""
        if self._is_cleaned:
            return
        self._is_closing = True
        self._is_cleaned = True

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
