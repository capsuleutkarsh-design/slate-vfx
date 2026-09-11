import os
import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QSplitter, QStackedWidget, QListWidget, QSystemTrayIcon, QMenu, QStatusBar
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QFont, QColor


class MainWindowBuilderMixin:
    """
    Mixin for VFXFolderCreatorApp that handles the initial UI construction.
    """

    # ---------------------------------------------------------- role-split tabs
    #
    # These two modules serve two different jobs. Rather than show everyone the
    # manager's toolbar and refuse most of it, each person gets the screen built
    # for what they actually do.

    def _build_leave_tab(self):
        from ..tabs.leave_approvals_view import LeaveApprovalsView
        from ..tabs.my_leave_view import MyLeaveView
        from ...core.domain.workplace_access import manages_leave

        roles = {str(r).strip().lower() for r in (getattr(self, "user_roles", None) or [])}

        # HR own the final stage; a supervisor owns the first one. Everybody
        # else gets their own balance - including HR and supervisors, who take
        # leave like anyone and reach that through their own Home.
        if manages_leave(getattr(self, "user_roles", None), self.allowed_tabs):
            return LeaveApprovalsView(self._current_username(), stage="HR")
        if roles & {"supervisor", "lead"}:
            return LeaveApprovalsView(self._current_username(), stage="Supervisor")
        return MyLeaveView(self._current_username())

    def _build_joining_tab(self):
        """
        Joining and leaving, shown to whichever half of it this person owns.

        HR start people and hold the paperwork; IT provision them and issue the
        machine. Same record, two halves - and nobody is shown a checklist they
        cannot action.
        """
        from ..tabs.joining_leaving_view import JoiningLeavingView
        from ...core.domain.workplace_access import manages_it

        team = "IT" if manages_it(getattr(self, "user_roles", None), self.allowed_tabs) else "HR"
        return JoiningLeavingView(self._current_username(), team=team)

    def _build_ticketing_tab(self):
        from ..tabs.service_desk_view import ServiceDeskView
        from ..tabs.my_tickets_view import MyTicketsView
        from ...core.domain.workplace_access import manages_it

        if manages_it(getattr(self, "user_roles", None), self.allowed_tabs):
            return ServiceDeskView(self._current_username())
        return MyTicketsView(self._current_username())

    def _current_username(self) -> str:
        data = self.user_data or {}
        return str(data.get("user_id") or data.get("username") or "unknown")

    def init_ui(self):
            """Initialize the user interface components."""
            from ... import __version__ as APP_VERSION
            from .tab_coordinator import TabCoordinator
            from ..tabs.home_tab import HomeTab
            from ..tabs.folder_creator_tab import FolderCreatorTab
            from ..cap_rename_tab import CapRenameTab 
            from ..tabs.stock_browser_tab import StockBrowserTab
            from ..tabs.vfx_review_dual_mode_tab import VFXReviewDualModeTab
            from ..tabs.vfx_dashboard_pro.ui.dashboard_widget import DashboardWidget
            from ..tabs.settings_tab import SettingsTab
            from ..admin_panel import AdminPanelTab
            from ..tester_panel import TesterPanel
            from ..attendance_tab import AttendanceTab

            from ..tabs.prod_scheduling_tab import ProdSchedulingTab
            from ..tabs.prod_bidding_tab import ProdBiddingTab
            from ..tabs.it_inventory_tab import ItInventoryTab
            from ..tabs.licence_view import LicenceView
            from ..tabs.it_deployment_tab import ItDeploymentTab
            from ..tabs.admin_users_tab import AdminUsersTab
            central_widget = QWidget()
            self.setCentralWidget(central_widget)

            main_layout = QVBoxLayout(central_widget)
            main_layout.setContentsMargins(15, 15, 15, 2)  # Minimal bottom margin for max editor space
            main_layout.setSpacing(15)

            # A. Toolbar Removed (Moved to Header)
            # self.create_toolbar()

            # B. Header
            self.header_widget = self.create_header()
            main_layout.addWidget(self.header_widget)

            # C. Main Content Area (Sidebar + Stack)
            content_container = QWidget()
            content_layout = QHBoxLayout(content_container)
            content_layout.setContentsMargins(0, 0, 0, 0)
            content_layout.setSpacing(0)

            # 1. Sidebar (Left Rail) with Collapse Container
            self.sidebar_container = QWidget()
            self.sidebar_container.setObjectName("SidebarContainer")
            # The rail needs its own ground and an edge.
            #
            # It was fully transparent with no border, so it sat on the same
            # colour as the content beside it. Expanded you could infer the
            # boundary from the labels; collapsed to bare icons there was
            # nothing at all to say where the navigation ended and the work
            # began - the icons appeared to float in the page.
            self.sidebar_container.setStyleSheet("""
                QWidget#SidebarContainer {
                    background-color: #16161A;
                    border-right: 1px solid #2C2C34;
                }
            """)

            sidebar_layout = QVBoxLayout(self.sidebar_container)
            sidebar_layout.setContentsMargins(0, 0, 0, 0)
            sidebar_layout.setSpacing(0)

            self.sidebar_nav = QListWidget()
            self.sidebar_nav.setObjectName("MainSidebar")
            self.sidebar_nav.setFocusPolicy(Qt.NoFocus)
            self.sidebar_nav.setTextElideMode(Qt.TextElideMode.ElideNone)

            self.sidebar_toggle_btn = QPushButton("⮜")
            self.sidebar_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.sidebar_toggle_btn.setFixedHeight(40)
            self.sidebar_toggle_btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    color: #2C2C34;
                    border: none;
                    border-top: 1px solid #1D1D22;
                    font-size: 16px;
                    text-align: right;
                    padding-right: 20px;
                }
                QPushButton:hover {
                    color: #3EA8BF;
                    background-color: rgba(62, 168, 191, 0.05);
                }
            """)
            self.sidebar_toggle_btn.clicked.connect(self.toggle_sidebar)

            sidebar_layout.addWidget(self.sidebar_nav)
            sidebar_layout.addWidget(self.sidebar_toggle_btn)

            # 2. Content Stack (Right Panel)
            self.content_stack = QStackedWidget()

            content_layout.addWidget(self.sidebar_container)
            content_layout.addWidget(self.content_stack)
            self.sidebar_collapsed = False

            main_layout.addWidget(content_container, 1)

            # Initialize Tab Coordinator (EXTRACTED COMPONENT)
            self.tab_coordinator = TabCoordinator(self, self.sidebar_nav, self.content_stack)
            self.tab_coordinator.tab_switched.connect(self._on_tab_switched)
            self.tab_coordinator.sidebar_collapsed = True

            # Make sidebar collapsed by default on startup without triggering animations yet
            self.sidebar_collapsed = True
            self.sidebar_container.setFixedWidth(64)

            self.sidebar_toggle_btn.setText("⮞")
            self.sidebar_toggle_btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    color: #2C2C34;
                    border: none;
                    border-top: 1px solid #1D1D22;
                    font-size: 16px;
                    text-align: center;
                    padding: 0;
                }
                QPushButton:hover { color: #3EA8BF; background-color: rgba(62, 168, 191, 0.05); }
            """)

            self.sidebar_nav.setStyleSheet("""
                QListWidget { background: transparent; border: none; outline: none; padding: 2px; }
                QListWidget::item { 
                    color: #E8E6E1;
                    padding: 12px 0px; 
                    border-radius: 6px;
                    margin: 2px 4px;
                    font-size: 32px;
                }
                QListWidget::item:hover {
                    background-color: rgba(255, 255, 255, 0.05);
                }
                QListWidget::item:selected {
                    background-color: rgba(62, 168, 191, 0.15);
                    color: #3EA8BF;
                    border-left: 3px solid #3EA8BF;
                    border-radius: 4px;
                }
            """)

            # === LAZY TAB LOADING (Improvement #4 & Suite Decoupling) ===
            mode = getattr(self, "app_mode", "all") or "all"
            show_vfx = mode in ("vfx", "all")
            show_ops = mode in ("ops", "all")

            logging.info(f"[LAZY] Registering tab factories for mode='{mode}' (show_vfx={show_vfx}, show_ops={show_ops})...")

            if show_vfx:
                self.tab_coordinator.add_category_header("PRODUCTION")

                # Home Tab (Cinematic Hub - VFX Mode without attendance)
                self.tab_coordinator.register_tab_factory(
                    "Home",
                    lambda: HomeTab(
                        user_data=self.user_data,
                        app_context=self.app_context,
                        main_window=self,
                        mode="vfx"
                    ),
                    icon="🏠",
                    permission_key=None,  # Always allowed
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="VFX Production Hub & Quick Actions"
                )

                def create_folder_creator():
                    tab = FolderCreatorTab(self.config_manager)
                    if hasattr(tab, "template_changed"):
                        tab.template_changed.connect(lambda *_: self.on_templates_refreshed())
                    return tab

                # Build & Ingest (structure + scan move)
                self.tab_coordinator.register_tab_factory(
                    "Build & Ingest",
                    create_folder_creator,
                    icon="📁",
                    permission_key="Folder Creator",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Scan the client drive, build the project structure, and move the scans into it"
                )

                # CAP Rename
                self.tab_coordinator.register_tab_factory(
                    "CAP Rename",
                    lambda: CapRenameTab(self.config_manager),
                    icon="🏷️",
                    permission_key="Rename Tool",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Batch-rename VFX deliverables using smart pattern matching"
                )

                # Stock Viewer
                self.tab_coordinator.register_tab_factory(
                    "Stock Viewer",
                    lambda: StockBrowserTab(
                        self.library_manager,
                        user_roles=self.user_roles,
                        user_role=self.user_role,
                    ),
                    icon="🎞️",
                    permission_key="Stock Browser",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Browse, preview and manage your stock asset library"
                )

                # Timeline Viewer (the Olive lineup).
                # The permission key stays "Shot Review": it is what every
                # existing role config grants, and renaming it would quietly
                # lock people out of a tab they already have.
                self.tab_coordinator.register_tab_factory(
                    "Timeline Viewer",
                    lambda: VFXReviewDualModeTab(self.config_manager, self.user_data),
                    icon="🎬",
                    permission_key="Shot Review",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Build the reel lineup and open it in Olive"
                )

                # VFX Dashboard Pro
                self.tab_coordinator.register_tab_factory(
                    "VFX Dashboard",
                    lambda: DashboardWidget(
                        user_data={**(self.user_data or {}), "inherit_app_theme": True},
                        user_manager=self.app_context.user_manager(),
                        app_context=self.app_context,
                    ),
                    icon="📊",
                    permission_key="Dashboard",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Live project dashboard with shot tracking and production metrics"
                )

                # Production Scheduling
                self.tab_coordinator.register_tab_factory(
                    "Scheduling",
                    lambda: ProdSchedulingTab(),
                    icon="📅",
                    permission_key="Scheduling",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Production Scheduling & Gantt Charts"
                )

                # Production Bidding
                self.tab_coordinator.register_tab_factory(
                    "Bidding",
                    lambda: ProdBiddingTab(),
                    icon="💰",
                    permission_key="Bidding",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Project Bidding & Cost Tracking"
                )

            if show_ops:
                self.tab_coordinator.add_category_header("OPERATIONS" if mode == "ops" else "HRMS")

                # In Ops mode, register the dedicated Operations Home tab with Attendance!
                if mode == "ops":
                    self.tab_coordinator.register_tab_factory(
                        "Home",
                        lambda: HomeTab(
                            user_data=self.user_data,
                            app_context=self.app_context,
                            main_window=self,
                            mode="ops"
                        ),
                        icon="🏠",
                        permission_key=None,  # Always allowed
                        user_role=self.user_role,
                        allowed_tabs=self.allowed_tabs,
                        tooltip="Operations Hub & Biometric Attendance"
                    )

                # Attendance
                self.tab_coordinator.register_tab_factory(
                    "Attendance",
                    lambda: AttendanceTab(
                        self.user_data,
                        attendance=self.app_context.attendance(),
                        user_manager=self.app_context.user_manager(),
                        app_context=self.app_context,
                        sync_enabled=not self._is_sqlite_fallback_mode(),
                    ),
                    icon="⏱️",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip=(
                        "Track team attendance, hours and export timesheets"
                        if not self._is_sqlite_fallback_mode()
                        else "LOCAL MODE. Team sync/export actions are limited."
                    )
                )

                # Leave. One entry, two entirely different screens behind it:
                # HR get the approval queue, everyone else gets their own
                # balance and their own requests. No permission key, because
                # every employee needs the asking side of this.
                self.tab_coordinator.register_tab_factory(
                    "Leave",
                    lambda: self._build_leave_tab(),
                    icon="🌴",
                    permission_key=None,
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Request leave, or work through the approval queue"
                )

                # Joining and leaving. HR and IT reach the same spine from
                # opposite ends, and the coordinator only understands a single
                # permission key - so the two-team test happens here, and the
                # entry is simply absent for everybody else rather than shown
                # locked.
                from ...core.domain.workplace_access import manages_it, manages_leave
                if (manages_leave(getattr(self, "user_roles", None), self.allowed_tabs)
                        or manages_it(getattr(self, "user_roles", None), self.allowed_tabs)):
                    self.tab_coordinator.register_tab_factory(
                        "Joining & Leaving",
                        lambda: self._build_joining_tab(),
                        icon="🤝",
                        permission_key=None,
                        user_role=self.user_role,
                        allowed_tabs=self.allowed_tabs,
                        tooltip="Start somebody joining or leaving, and work the checklist"
                    )

                self.tab_coordinator.add_category_header("IT & INFRA")

                # Hardware Inventory
                self.tab_coordinator.register_tab_factory(
                    "Hardware",
                    lambda: ItInventoryTab(),
                    icon="🖥️",
                    permission_key="IT",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Studio Hardware Inventory"
                )

                # Licences. Not an inventory - a compliance and renewal read,
                # which is the only version of this question anybody asks.
                self.tab_coordinator.register_tab_factory(
                    "Licences",
                    lambda: LicenceView(self._current_username()),
                    icon="🔑",
                    permission_key="IT",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Seats bought against seats used, and what each renewal needs"
                )

                # IT support. Same shape as Leave: IT get the service desk,
                # everyone else gets their own tickets and the thread on them.
                self.tab_coordinator.register_tab_factory(
                    "IT Support",
                    lambda: self._build_ticketing_tab(),
                    icon="🎫",
                    permission_key=None,
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Report a problem to IT, or work the queue"
                )

                # Auto Deployment
                self.tab_coordinator.register_tab_factory(
                    "Deployment",
                    lambda: ItDeploymentTab(user_data=self.user_data),
                    icon="📦",
                    permission_key="IT",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Manage automated script and software deployments"
                )

                self.tab_coordinator.add_category_header("ADMINISTRATION")

                # User Management
                self.tab_coordinator.register_tab_factory(
                    "Users & Roles",
                    lambda: AdminUsersTab(user_role=self.user_role, user_data=self.user_data),
                    icon="👥",
                    permission_key="HRMS",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Manage users, roles, departments, and access"
                )

                # Admin Panel
                self.tab_coordinator.register_tab_factory(
                    "Admin Panel",
                    lambda: (
                        self._build_sync_disabled_tab(
                            "Admin Panel Unavailable",
                            "Admin fleet monitoring and remote controls are unavailable in LOCAL MODE.",
                        )
                        if self._is_sqlite_fallback_mode()
                        else AdminPanelTab(
                            current_username=(self.user_data or {}).get(
                                "user_id",
                                (self.user_data or {}).get("username", "Unknown"),
                            ),
                            app_context=self.app_context,
                        )
                    ),
                    icon="🛡️",
                    permission_key="Admin Panel",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip=(
                        "User management, live workstation monitoring and fleet reports"
                        if not self._is_sqlite_fallback_mode()
                        else "Unavailable in LOCAL MODE (requires central PostgreSQL)."
                    )
                )

            # SYSTEM Category (Always available)
            self.tab_coordinator.add_category_header("SYSTEM")

            # Tester Panel
            self.tab_coordinator.register_tab_factory(
                "Tester Panel",
                lambda: TesterPanel(
                    user_manager=self.app_context.user_manager(),
                    app_context=self.app_context,
                ),
                icon="🧪",
                permission_key="Tester Panel",
                user_role=self.user_role,
                allowed_tabs=self.allowed_tabs,
                tooltip="Generate test data and validate workflows - developer/QA tool"
            )

            # Settings
            def create_settings():
                settings = SettingsTab(self.config_manager)
                settings.templates_refresh_requested.connect(self.on_templates_refreshed)
                settings.global_settings_updated.connect(self.on_global_settings_updated)
                return settings

            self.tab_coordinator.register_tab_factory(
                "Settings",
                create_settings,
                icon="⚙️",
                permission_key="Settings",
                user_role=self.user_role,
                allowed_tabs=self.allowed_tabs,
                tooltip="Configure application paths, templates and global preferences"
            )

            # Store nav_items reference for backward compatibility
            self.nav_items = self.tab_coordinator.nav_items

            # --- DYNAMIC PLUGIN LOADER ---
            self.load_plugins()

            # Guarantee that all dynamically loaded tabs have their text stripped on boot
            if hasattr(self, 'tab_coordinator'):
                self.tab_coordinator.set_sidebar_collapsed(True)

            # main_layout.addWidget(self.tab_widget, 1) # Removed

            # D. Footer / Status Bar
            footer_widget = self.create_footer()
            main_layout.addWidget(footer_widget)

            self.status_bar = QStatusBar()
            self.setStatusBar(self.status_bar)
            suite_title = getattr(self, "suite_title", "Slate")
            self.status_bar.showMessage(f"Ready - {suite_title} v{APP_VERSION}", 5000)

            # E. Global Task Progress (Status Bar)
            try:
                from PySide6.QtWidgets import QProgressBar
                self.global_progress = QProgressBar()
                self.global_progress.setMaximumWidth(200)
                self.global_progress.setFixedHeight(14)
                self.global_progress.setTextVisible(False)
                self.global_progress.setVisible(False)
                self.status_bar.addPermanentWidget(self.global_progress)
                
                from .task_manager_dock import task_registry
                
                def update_global_progress(task_id):
                    # Find highest progress among running tasks, or just show active
                    active_tasks = [t for t in task_registry.get_all_tasks() if t.status.lower() in ("running", "pending", "paused")]
                    if not active_tasks:
                        self.global_progress.setVisible(False)
                        return
                    
                    self.global_progress.setVisible(True)
                    # Use the progress of the most recently updated active task
                    task = active_tasks[-1]
                    self.global_progress.setValue(task.progress)
                    self.global_progress.setToolTip(f"{task.name}: {task.progress}%")
                
                def on_task_added(task): update_global_progress(task.task_id)
                def on_task_removed(task_id): update_global_progress(task_id)
                def on_task_updated(task_id): update_global_progress(task_id)
                
                task_registry.task_added.connect(on_task_added)
                task_registry.task_removed.connect(on_task_removed)
                task_registry.task_updated.connect(on_task_updated)
                
            except Exception as e:
                logging.error(f"Failed to load Global Progress Bar: {e}")

    def create_toolbar(self):
            """Create application toolbar with workflow switching."""
            # --- ARTIST CHECK: HIDE TOOLBAR ---
            if self.user_role.lower() == "artist":  # Case-insensitive
                return

    def create_header(self):
            """
            Create the application header.

            DELEGATED to HeaderBuilder component for better maintainability.
            Kept as wrapper method for backward compatibility.
            """
            # Delegate to header builder component
            header_widget = self.header_builder.create_header()


            # Connect help button (stored in header_builder during creation)
            if hasattr(self.header_builder, 'help_button'):
                self.header_builder.help_button.clicked.connect(self.show_help_dialog)
            if hasattr(self.header_builder, 'logout_button') and self.header_builder.logout_button:
                self.header_builder.logout_button.clicked.connect(self.logout_user)
            if hasattr(self.header_builder, "health_label") and self.header_builder.health_label:
                try:
                    self.header_builder.health_label.clicked.connect(self.show_runtime_diagnostics)
                except Exception as exc:
                    logging.debug("Health label click bind skipped: %s", exc)

            return header_widget

    def create_footer(self):
            footer = QWidget()
            footer.setObjectName("footer")
            footer_layout = QVBoxLayout(footer)
            footer_layout.setContentsMargins(0, 0, 0, 0)
            team_layout = QHBoxLayout()
            team_layout.setContentsMargins(10, 2, 10, 5)

            team_label = QLabel("TEAM SLATE")
            team_label.setFont(QFont("Segoe UI", 8))
            team_label.setStyleSheet("color: #87857F;")

            license_label = QLabel("Copyright (c) 2026 Utkarsh Tripathi | Licensed under GPLv3")
            license_label.setFont(QFont("Segoe UI", 8))
            license_label.setStyleSheet("color: #87857F;")

            team_layout.addWidget(team_label)
            team_layout.addStretch()
            team_layout.addWidget(license_label)
            footer_layout.addLayout(team_layout)
            return footer

    def init_variables(self):
            # Initialize database connection asynchronously to avoid blocking UI
            try:
                from slate.core.infra.db_worker import run_db_async
                db = self.app_context.db_manager()
                if self._db_init_worker and hasattr(self._db_init_worker, "cancel"):
                    self._db_init_worker.cancel()
                    self._db_init_worker = None
                self._db_init_worker = run_db_async(
                    lambda: db.execute_query("SELECT 1"),
                    on_success=lambda r: logging.info("Database initialized successfully (async)"),
                    on_error=lambda e: logging.warning(f"Initial DB check failed (User might work offline): {e}"),
                    owner=self,
                )
            except Exception as e:
                logging.warning(f"DB init setup failed: {e}")

            self.is_processing = False
            self._omnibar_recent_entries = []
            self._omnibar_recent_shots = []
            self._load_omnibar_state()
