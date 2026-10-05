import logging
import os
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
import copy
from datetime import datetime
from slate.gui.tabs.vfx_dashboard_pro.core.project_manager import ProjectManager
from slate.gui.tabs.vfx_dashboard_pro.ui.dashboard_sync_service import DashboardSyncService
from slate.gui.tabs.vfx_dashboard_pro.utils.thumbnail import ThumbnailGenerator
from slate.utils.async_image_loader import AsyncImageLoader
from slate.core.infra.app_context import AppContext
from slate.core.infra.database_manager import database_manager
from openpyxl.utils import column_index_from_string

class DashboardBuilderMixin:

    def __init__(self, parent=None, user_data: dict = None, user_manager=None, app_context=None):
            super().__init__(parent)
            self.app_context = app_context or AppContext()
            log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Slate", "logs")
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

            # The roles as the user record has them, lower-cased. Every right
            # is asked of access.json with these: the old alias table turned a
            # coordinator into a "supervisor" on screen (and so offered Force
            # Overwrite access.json does not give them). Only spellings are
            # folded now ("coord" -> "coordinator"), never one role into another.
            roles_data = self.user_data.get('roles', self.user_data.get('role', ['Artist']))
            self.user_roles = self._normalize_roles(roles_data)
            self.user_role = self.user_roles[0] if self.user_roles else "artist"
            self.access_roles = list(self.user_roles)
            self.local_mode = self._is_local_fallback_mode()

            self.project_manager = ProjectManager()
            self.sync_service = DashboardSyncService(self.project_manager)
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
            from collections import OrderedDict
            self.image_cache = OrderedDict()
            self._init_thumbnail_system()

            # Fetch Users. No made-up names when the list cannot be read: they
            # used to be offered (and saved) as real assignments.
            self.user_manager = user_manager or self.app_context.user_manager()
            self.all_users = self._get_user_list()

            # PHASE 2: Sync Users to DB
            try:
                if not self.local_mode:
                    database_manager.sync_users(self.user_manager.get_all_users())
                else:
                    logging.info("DashboardWidget: user sync skipped in LOCAL MODE.")
            except Exception as e:
                logging.exception(f"ERROR: Failed to sync users to DB: {e}")

            # Thumbnails are cached per person (GlobalConfig.local_cache_dir), not
            # in the install folder - an installed copy cannot write there, and
            # those machine-local paths ended up saved into the database.
            self.thumb_gen = ThumbnailGenerator()
            self.image_loader = AsyncImageLoader(self.thumb_gen)
            self.image_loader.image_loaded.connect(self.on_image_loaded)
            self.image_loader.image_started.connect(self.on_image_started)

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
            self._only_shots = None
            self._search_needle = ""
            self._board_dirty = True
            self._board_shots = []
            self._own_writes = {}
            self._own_status_undo = []
            self.publish_workers = []

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
            # Scope names that fit this person before any project is open.
            self.populate_scope_selector(apply=False)
            self.refresh_timer = QTimer(self)
            self.refresh_timer.timeout.connect(self.check_for_updates)
            self.refresh_timer.start(300000)
            # A banner that stays, not a message that scrolls past.
            self.refresh_connection_state()
            self._update_empty_state()
            self.load_projects()
