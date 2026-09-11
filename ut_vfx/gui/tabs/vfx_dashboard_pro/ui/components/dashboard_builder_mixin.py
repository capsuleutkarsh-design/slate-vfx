import logging
import os
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
import copy
from datetime import datetime
from ut_vfx.gui.tabs.vfx_dashboard_pro.core.project_manager import ProjectManager
from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.dashboard_sync_service import DashboardSyncService
from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.dashboard_avatar_service import DashboardAvatarService
from ut_vfx.gui.tabs.vfx_dashboard_pro.utils.thumbnail import ThumbnailGenerator
from ut_vfx.utils.async_image_loader import AsyncImageLoader
from ut_vfx.core.infra.app_context import AppContext
from ut_vfx.core.infra.database_manager import database_manager
from openpyxl.utils import column_index_from_string

class DashboardBuilderMixin:

    def __init__(self, parent=None, user_data: dict = None, user_manager=None, app_context=None):
            super().__init__(parent)
            self.app_context = app_context or AppContext()
            log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "UTVFX", "logs")
            os.makedirs(log_dir, exist_ok=True)
            self.log_file = os.path.join(log_dir, "dashboard_widget.log")
            # Logging configured via main application
            try:
                with open(self.log_file, "w") as f:
                    f.write(f"DashboardWidget Initialized at {datetime.now()}\n")
            except Exception as e:
                logging.exception(f"Failed to init log: {e}")

            # --- USER CONTEXT INTEGRATION ---
            self.user_data = user_data or {}
            self.user_display_name = self.user_data.get('display_name', self.user_data.get('username', 'User'))
            self.inherit_app_theme = bool(self.user_data.get("inherit_app_theme", False))

            # Handle both multi-role arrays and legacy single role, normalized to lowercase.
            roles_data = self.user_data.get('roles', self.user_data.get('role', ['Artist']))
            self.user_roles = self._normalize_roles(roles_data)
            self.user_role = self.user_roles[0] if self.user_roles else "artist"
            # The roles exactly as the user record has them. Normalising folds
            # "coordinator" into "supervisor" for the interface, but access.json
            # tells them apart: a coordinator may not force-save over someone.
            raw = roles_data if isinstance(roles_data, list) else [roles_data]
            self.access_roles = [str(r).strip().lower() for r in raw if str(r).strip()] or ["artist"]
            self.local_mode = self._is_local_fallback_mode()

            self.project_manager = ProjectManager()
            self.sync_service = DashboardSyncService(self.project_manager)
            self.avatar_service = DashboardAvatarService()
            self._is_closing = False
            self._is_cleaned = False
            self.current_project = None
            self.data_handler = None
            self.file_lock = None
            self.poll_worker = None
            self.current_excel_path = ""
            self.last_excel_mtime = None
            self.all_shots = []
            self.displayed_shots = []
            from ut_vfx.gui.tabs.vfx_dashboard_pro.viewmodels.dashboard_viewmodel import DashboardViewModel
            self.viewmodel = DashboardViewModel(self)
            from collections import OrderedDict
            self.image_cache = OrderedDict()
            self._init_thumbnail_system()

            # Fetch Users
            self.user_manager = user_manager or self.app_context.user_manager()
            self.all_users = self._get_user_list()
            if not self.all_users:
                self.all_users = ["Admin", "Artist", "Supervisor"]

            # PHASE 2: Sync Users to DB
            try:
                if not self.local_mode:
                    database_manager.sync_users(self.user_manager.get_all_users())
                else:
                    logging.info("DashboardWidget: user sync skipped in LOCAL MODE.")
            except Exception as e:
                logging.exception(f"ERROR: Failed to sync users to DB: {e}")

            # Paths
            current_dir = os.path.dirname(os.path.abspath(__file__))
            root_dir = os.path.dirname(current_dir)
            cache_dir = os.path.join(root_dir, 'cache', 'thumbnails')

            self.thumb_gen = ThumbnailGenerator(cache_dir)
            self.image_loader = AsyncImageLoader(self.thumb_gen)
            self.image_loader.image_loaded.connect(self.on_image_loaded)
            self.image_loader.image_started.connect(self.on_image_started)
            self.avatar_upload_worker = None

            # In main app, inherit host stylesheet so dashboard looks consistent.
            # Standalone runs can still use dashboard-local QSS.
            if not self.inherit_app_theme:
                from ..qss_generator import generate_dashboard_qss
                self.setStyleSheet(generate_dashboard_qss())

            # UI Elements dynamically populated by layout_builder
            self.detail_container = None
            self.detail_layout = None
            self.detail_widget = None
            self.empty_state_body = None
            self.empty_state_frame = None
            self.empty_state_title = None
            self.header_view = None
            self.kanban_board = None
            self.manage_proj_btn = None
            self.more_actions_btn = None
            self.project_combo = None
            self.search_input = None
            self.status_filter = None
            self.advanced_query_btn = None

            self.advanced_query_rules = []
            self.advanced_query_match_type = "AND"
            self.splitter = None

            self._search_debounce_timer = QTimer(self)
            self._search_debounce_timer.setSingleShot(True)
            self._search_debounce_timer.setInterval(250)
            self._search_debounce_timer.timeout.connect(self.apply_filters)

            self.stats_widget = None
            self.status_bar = None
            self.table = None
            self.table_model = None
            self.theme_btn = None
            self.users_list = None
            self.view_stack = None
            self.view_toggle_btn = None

            self.init_ui()
            self.load_projects()

            self.refresh_timer = QTimer(self)
            self.refresh_timer.timeout.connect(self.check_for_updates)
            self.refresh_timer.start(300000)
            # A banner that stays, not a message that scrolls past.
            self.refresh_connection_state()
            self._update_empty_state()

    def _build_main_sheet_headers(self, project, source_ws=None):
            mapping = dict(getattr(project, "column_mapping", {}) or {})
            if not mapping:
                return []

            max_col = 0
            for letter in mapping.values():
                try:
                    max_col = max(max_col, int(column_index_from_string(str(letter))))
                except Exception:
                    continue
            if max_col <= 0:
                return []

            headers = [""] * max_col
            header_row = int(getattr(project, "header_row", 2) or 2)

            if source_ws is not None:
                for col in range(1, max_col + 1):
                    val = source_ws.cell(row=header_row, column=col).value
                    if val is not None:
                        headers[col - 1] = str(val)

            for field_name, letter in mapping.items():
                try:
                    col = int(column_index_from_string(str(letter)))
                except Exception:
                    continue
                idx = col - 1
                if 0 <= idx < len(headers) and not str(headers[idx] or "").strip():
                    headers[idx] = self._friendly_header_name(field_name)
            return headers
