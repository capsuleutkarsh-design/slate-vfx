import os
import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QSplitter, QStackedWidget, QListWidget, QSystemTrayIcon, QMenu, QStatusBar
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QFont, QColor
from slate.core.infra.gate import Gate


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
        from PySide6.QtWidgets import QTabWidget
        from ..tabs.leave_approvals_view import LeaveApprovalsView
        from ..tabs.my_leave_view import MyLeaveView
        from ...core.domain.workplace_access import leave_stage

        username = self._current_username()

        # HR own the final stage; a supervisor owns the first one. Who counts
        # as a supervisor here is the "approve_leave" ability, so a Comp
        # Supervisor or Team Lead gets the queue without a code change.
        stage = leave_stage(getattr(self, "user_roles", None), self.allowed_tabs)

        if not stage:
            return MyLeaveView(username)

        # An approver takes leave like anybody else. They used to get the queue
        # and nothing else, with a comment claiming they reached their own
        # balance through Home - which has no such entry, so HR and supervisors
        # simply could not ask for leave at all.
        queue = LeaveApprovalsView(username, stage=stage)
        mine = MyLeaveView(username)
        queue.changed.connect(mine.refresh)
        mine.changed.connect(queue.refresh)

        both = QTabWidget()
        both.addTab(queue, "Queue")
        both.addTab(mine, "My leave")
        return both

    def _build_joining_tab(self):
        """
        Joining and leaving, shown to whichever half of it this person owns.

        HR start people and hold the paperwork; IT provision them and issue the
        machine. Same record, two halves - and nobody is shown a checklist they
        cannot action.
        """
        from ..tabs.joining_leaving_view import JoiningLeavingView, joining_teams

        teams = joining_teams(getattr(self, "user_roles", None), self.allowed_tabs)
        if len(teams) == 1:
            return JoiningLeavingView(self._current_username(), team=teams[0])
        # Both halves (admin, or somebody who is HR and IT): one tab each.
        # They used to get IT's half only, so could not start anybody.
        from PySide6.QtWidgets import QTabWidget
        both = QTabWidget()
        views = [JoiningLeavingView(self._current_username(), team=t) for t in teams]
        for view, team in zip(views, teams):
            both.addTab(view, "HR checklist" if team == "HR" else "IT checklist")
        for view in views:
            for other in views:
                if other is not view:
                    view.changed.connect(other.refresh)
        return both

    def _build_ticketing_tab(self):
        from ..tabs.service_desk_view import ServiceDeskView
        from ..tabs.my_tickets_view import MyTicketsView
        from ...core.domain.workplace_access import manages_it

        username = self._current_username()
        if not manages_it(getattr(self, "user_roles", None), self.allowed_tabs):
            return MyTicketsView(username)
        # IT get both, like Leave: the queue, and their own tickets. They used
        # to get the queue only, so IT staff could not report a problem of
        # their own (the queue's New ticket logs one for somebody else).
        from ..tabs.service_desk_view import desk_pages
        queue = ServiceDeskView(username)
        mine = MyTicketsView(username)
        mine.show_badge = False          # the sidebar count is the queue's
        queue.changed.connect(mine.refresh)
        mine.changed.connect(queue.refresh)
        return desk_pages(queue, mine)

    def _attendance_tooltip(self) -> str:
        """
        What Attendance is for this person. Everybody punches in and out and
        sees their month; only people who look after others see a team. It
        used to promise every artist "team attendance and timesheets".
        """
        from ...core.domain.access import can
        roles = list(getattr(self, "user_roles", None) or [])
        text = "Punch in and out and see your month"
        if can(roles, "view_team_attendance"):
            text = "Punch in and out, see your month - and the studio's attendance and timesheets"
        elif can(roles, "approve_leave"):
            text += ", and your team's attendance"
        if self._is_sqlite_fallback_mode():
            text += " (LOCAL MODE: team views and exports are limited)"
        return text

    def _current_username(self) -> str:
        data = self.user_data or {}
        return str(data.get("user_id") or data.get("username") or "unknown")

    def _screen_roles(self):
        """
        The signed-in person's roles for screens that gate their own parts
        (Settings, Admin Panel): the window's role list, else the single role.
        None lets the screen look them up itself.
        """
        roles = getattr(self, "user_roles", None)
        if not roles:
            single = getattr(self, "user_role", None)
            roles = [single] if single else None
        if isinstance(roles, str):
            roles = [roles]
        return list(roles) if roles else None

    def init_ui(self):
            """Initialize the user interface components."""
            from ... import __version__ as APP_VERSION
            from .tab_coordinator import TabCoordinator
            from ..tabs.home_tab import HomeTab
            from ..tabs.licence_view import LicenceView

            # Each screen's module is imported when the screen is first
            # opened, not while the window is built: importing all of them
            # (the dashboard alone is large) held the start-up for over a
            # second with the loading window frozen.
            from importlib import import_module

            def screen(module, name):
                return lambda *a, **k: getattr(import_module(module), name)(*a, **k)

            central_widget = QWidget()
            self.setCentralWidget(central_widget)

            # Header, sidebar and footer run edge to edge. They used to sit
            # 15 px inside a charcoal frame of another colour, with the
            # header's rule stopping short of both edges.
            main_layout = QVBoxLayout(central_widget)
            main_layout.setContentsMargins(0, 0, 0, 0)
            main_layout.setSpacing(0)

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
            self.sidebar_container.setStyleSheet(f"""
                QWidget#SidebarContainer {{
                    background-color: {Gate.PANEL};
                    border-right: 1px solid {Gate.LINE};
                }}
            """)

            sidebar_layout = QVBoxLayout(self.sidebar_container)
            sidebar_layout.setContentsMargins(0, 0, 0, 0)
            sidebar_layout.setSpacing(0)

            self.sidebar_nav = QListWidget()
            self.sidebar_nav.setObjectName("MainSidebar")
            self.sidebar_nav.setFocusPolicy(Qt.NoFocus)
            self.sidebar_nav.setTextElideMode(Qt.TextElideMode.ElideNone)
            # Qt ignores font-size on a list's ::item rule, so the nav's size is
            # set on the widget (it followed the window-wide font rule instead).
            nav_font = QFont(self.sidebar_nav.font())
            nav_font.setPixelSize(14)
            self.sidebar_nav.setFont(nav_font)

            self.sidebar_toggle_btn = QPushButton()
            self.sidebar_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.sidebar_toggle_btn.setFixedHeight(40)
            self.sidebar_toggle_btn.clicked.connect(self.toggle_sidebar)
            # Long lists scroll smoothly, and the last entry is never left
            # half-cut against the bottom edge.
            self.sidebar_nav.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)

            sidebar_layout.addWidget(self.sidebar_nav)
            sidebar_layout.addWidget(self.sidebar_toggle_btn)

            # 2. Content Stack (Right Panel) - the pages keep their breathing
            # room inside it.
            self.content_stack = QStackedWidget()
            self.content_stack.setContentsMargins(16, 12, 16, 8)

            content_layout.addWidget(self.sidebar_container)
            content_layout.addWidget(self.content_stack)
            self.sidebar_collapsed = False

            main_layout.addWidget(content_container, 1)

            # Initialize Tab Coordinator (EXTRACTED COMPONENT)
            self.tab_coordinator = TabCoordinator(self, self.sidebar_nav, self.content_stack)
            self.tab_coordinator.tab_switched.connect(self._on_tab_switched)
            self.tab_coordinator.sidebar_collapsed = True

            # Folded to icons the very first time; after that, as it was left
            # (it was forced to icons on every start).
            collapsed = bool((getattr(self, "global_settings", None) or {}).get("sidebar_collapsed", True))
            self.sidebar_collapsed = collapsed
            self.tab_coordinator.sidebar_collapsed = collapsed
            if collapsed:
                self.sidebar_container.setFixedWidth(64)
            self.apply_sidebar_look(collapsed)

            # The sidebar is quiet while the screens are registered: the
            # first one used to be built the moment it was added, with only
            # itself in the sidebar (Home had no tiles). open_current() below
            # builds it once every screen is in.
            self.sidebar_nav.blockSignals(True)

            # === LAZY TAB LOADING (Improvement #4 & Suite Decoupling) ===
            mode = getattr(self, "app_mode", "all") or "all"
            show_vfx = mode in ("vfx", "all")
            show_ops = mode in ("ops", "all")

            logging.info(f"[LAZY] Registering tab factories for mode='{mode}' (show_vfx={show_vfx}, show_ops={show_ops})...")

            # Home, above every group heading: folding PRODUCTION used to hide
            # it, and HR reached it under a heading they had nothing else in.
            # In the full suite it is built from what this person has
            # (production and operations); Operations has the punch panel.
            self.tab_coordinator.register_tab_factory(
                "Home",
                lambda: HomeTab(
                    user_data=self.user_data,
                    app_context=self.app_context,
                    main_window=self,
                    mode=mode if mode in ("ops", "all") else "vfx"
                ),
                icon="🏠",
                permission_key=None,  # Always allowed
                user_role=self.user_role,
                allowed_tabs=self.allowed_tabs,
                tooltip=("Punch in and out, and quick links to your screens" if mode == "ops"
                         else "Your shots, the studio's figures and quick links to your screens")
            )

            if show_vfx:
                self.tab_coordinator.add_category_header("PRODUCTION")

                def create_folder_creator():
                    # Not wired to on_templates_refreshed: that reloads this
                    # same tab, and the reload re-emitted - an endless loop
                    # that crashed Slate (ING-001).
                    return screen("slate.gui.tabs.folder_creator_tab", "FolderCreatorTab")(
                        self.config_manager, user_data=self.user_data)

                # Build & Ingest (structure + scan move)
                self.tab_coordinator.register_tab_factory(
                    "Build & Ingest",
                    create_folder_creator,
                    icon="📁",
                    permission_key="Folder Creator",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Scan the client drive, build the project structure, and bring the scans into it"
                )

                # CAP Rename
                self.tab_coordinator.register_tab_factory(
                    "CAP Rename",
                    lambda: screen("slate.gui.cap_rename_tab", "CapRenameTab")(
                        self.config_manager, user_data=self.user_data),
                    icon="🏷️",
                    permission_key="Rename Tool",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Rename delivered files to the studio's naming, in batches"
                )

                # Stock Viewer
                self.tab_coordinator.register_tab_factory(
                    "Stock Viewer",
                    lambda: screen("slate.gui.tabs.stock_browser_tab", "StockBrowserTab")(
                        self.library_manager,
                        user_roles=self.user_roles,
                        user_role=self.user_role,
                        user_data=self.user_data,
                    ),
                    icon="🎞️",
                    permission_key="Stock Browser",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Browse and preview the stock library"
                )

                # Timeline Viewer (the reel lineup).
                # The permission key stays "Shot Review": it is what every
                # existing role config grants, and renaming it would quietly
                # lock people out of a tab they already have.
                self.tab_coordinator.register_tab_factory(
                    "Timeline Viewer",
                    lambda: screen("slate.gui.tabs.vfx_review_dual_mode_tab", "VFXReviewDualModeTab")(self.config_manager, self.user_data),
                    icon="🎬",
                    permission_key="Shot Review",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Build the reel lineup, play it in RV, export an EDL"
                )

                # VFX Dashboard Pro
                self.tab_coordinator.register_tab_factory(
                    "VFX Dashboard",
                    lambda: screen("slate.gui.tabs.vfx_dashboard_pro.ui.dashboard_widget", "DashboardWidget")(
                        user_data={**(self.user_data or {}), "inherit_app_theme": True},
                        user_manager=self.app_context.user_manager(),
                        app_context=self.app_context,
                    ),
                    icon="📊",
                    permission_key="Dashboard",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Every shot: status, artist, versions and figures"
                )

                # Production Scheduling
                self.tab_coordinator.register_tab_factory(
                    "Scheduling",
                    lambda: screen("slate.gui.tabs.prod_scheduling_tab", "ProdSchedulingTab")(user_data=self.user_data),
                    icon="📅",
                    permission_key="Scheduling",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Milestones, dependencies and a Gantt timeline"
                )

                # Production Bidding
                self.tab_coordinator.register_tab_factory(
                    "Bidding",
                    lambda: screen("slate.gui.tabs.prod_bidding_tab", "ProdBiddingTab")(user_data=self.user_data),
                    icon="💰",
                    permission_key="Bidding",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Bids, their revisions and cost tracking"
                )

            if show_ops:
                # One plain name in both apps and in Help ("HRMS" and
                # "OPERATIONS" before - jargon, and two names for one group).
                self.tab_coordinator.add_category_header("PEOPLE")

                # Attendance
                self.tab_coordinator.register_tab_factory(
                    "Attendance",
                    lambda: screen("slate.gui.attendance_tab", "AttendanceTab")(
                        self.user_data,
                        attendance=self.app_context.attendance(),
                        user_manager=self.app_context.user_manager(),
                        app_context=self.app_context,
                        sync_enabled=not self._is_sqlite_fallback_mode(),
                    ),
                    icon="⏱️",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip=self._attendance_tooltip()
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

                # The "IT" tab key opens the IT screens; so does working the
                # desk (manage_it). They used to be one flag that also swapped
                # the person's own tickets for the queue.
                from ...core.domain.workplace_access import can_view_licences, sees_it_screens
                roles_now = getattr(self, "user_roles", None)
                it_screens = sees_it_screens(roles_now, self.allowed_tabs)
                it_key = None if it_screens else "IT"
                # Changing anything on Hardware, Licences and Deployment needs
                # manage_it; the tab key alone reads them (it used to give full
                # write on two of them and read-only Licences).
                it_read_only = not manages_it(roles_now, self.allowed_tabs)

                # Hardware Inventory
                self.tab_coordinator.register_tab_factory(
                    "Hardware",
                    lambda: screen("slate.gui.tabs.it_inventory_tab", "ItInventoryTab")(
                        user_data=self.user_data, read_only=it_read_only),
                    icon="🖥️",
                    permission_key=it_key,
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Machines the studio owns, who has them, and their state"
                )

                # Licences. Not an inventory - a compliance and renewal read,
                # which is the only version of this question anybody asks.
                #
                # Open to IT and to people with view_licences (a Production
                # Head who approves renewals). Changing anything needs
                # manage_it; everybody else reads it.
                licence_visible = it_screens or can_view_licences(roles_now, self.allowed_tabs)
                self.tab_coordinator.register_tab_factory(
                    "Licences",
                    lambda: LicenceView(self._current_username(), read_only=it_read_only),
                    icon="🔑",
                    permission_key=None if licence_visible else "IT",
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
                    lambda: screen("slate.gui.tabs.it_deployment_tab", "ItDeploymentTab")(
                        user_data=self.user_data, read_only=it_read_only),
                    icon="📦",
                    permission_key=it_key,
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip="Keep a record of what was installed where"
                )

                self.tab_coordinator.add_category_header("ADMINISTRATION")

                # Users & Roles: the only place people and roles are managed.
                # Shown to HR (the HRMS tab) and to anybody who may manage
                # users or edit permissions - IT edits permissions without
                # holding the HR tab. Inside, each sees only their own half.
                from ...core.domain.access import can
                from ...core.domain.workplace_access import has_permission
                roles_for_access = list(getattr(self, "user_roles", None) or []) + [self.user_role]
                if (has_permission(self.allowed_tabs, "HRMS")
                        or can(roles_for_access, "manage_users")
                        or can(roles_for_access, "manage_permissions")):
                    self.tab_coordinator.register_tab_factory(
                        "Users & Roles",
                        lambda: screen("slate.gui.tabs.admin_users_tab", "AdminUsersTab")(user_role=self.user_role, user_data=self.user_data),
                        icon="👥",
                        permission_key=None,
                        user_role=self.user_role,
                        allowed_tabs=self.allowed_tabs,
                        tooltip="Add people, import a list, and set what each role can open and do"
                    )

                # Admin Panel
                self.tab_coordinator.register_tab_factory(
                    "Admin Panel",
                    lambda: (
                        self._build_sync_disabled_tab(
                            "Admin Panel needs the studio server",
                            "Slate is working offline right now. Live Ops, the logs and the "
                            "database come back when the studio server can be reached.",
                        )
                        if self._is_sqlite_fallback_mode()
                        else screen("slate.gui.admin_panel", "AdminPanelTab")(
                            current_username=(self.user_data or {}).get(
                                "user_id",
                                (self.user_data or {}).get("username", "Unknown"),
                            ),
                            app_context=self.app_context,
                            # Who may do what inside (manage_system)
                            # from the signed-in person's roles, not a guess.
                            roles=self._screen_roles(),
                        )
                    ),
                    icon="🛡️",
                    permission_key="Admin Panel",
                    user_role=self.user_role,
                    allowed_tabs=self.allowed_tabs,
                    tooltip=(
                        "Workstations, logs and the database"
                        if not self._is_sqlite_fallback_mode()
                        else "Admin Panel needs the studio server. Slate is working offline right now."
                    )
                )

            # SYSTEM Category (Always available)
            self.tab_coordinator.add_category_header("SYSTEM")

            # Tester Panel
            self.tab_coordinator.register_tab_factory(
                "Tester Panel",
                lambda: screen("slate.gui.tester_panel", "TesterPanel")(
                    user_manager=self.app_context.user_manager(),
                    app_context=self.app_context,
                    roles=self._screen_roles(),
                ),
                icon="🧪",
                permission_key="Tester Panel",
                user_role=self.user_role,
                allowed_tabs=self.allowed_tabs,
                tooltip="Tools for testing Slate (developers and QA)"
            )

            # Settings
            def create_settings():
                settings = screen("slate.gui.tabs.settings_tab", "SettingsTab")(
                    self.config_manager, roles=self._screen_roles())
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
                tooltip="Your preferences, and the studio's settings if you may change them"
            )

            # Store nav_items reference for backward compatibility
            self.nav_items = self.tab_coordinator.nav_items

            # --- DYNAMIC PLUGIN LOADER ---
            self.load_plugins()

            # Every entry, plugins included, in the remembered state.
            self.tab_coordinator.set_sidebar_collapsed(collapsed)

            # Groups fold only when the person folds them, and stay folded
            # the next time (they used to fold by themselves on every click).
            self.tab_coordinator.restore_folds(
                (getattr(self, "global_settings", None) or {}).get("sidebar_folded_groups", []))
            self.tab_coordinator.folds_changed.connect(
                lambda labels: self._remember_sidebar("sidebar_folded_groups", list(labels)))

            # Now that every screen is registered, open the first one.
            self.sidebar_nav.blockSignals(False)
            self.tab_coordinator.open_current()

            # main_layout.addWidget(self.tab_widget, 1) # Removed

            # D. Footer: status messages and running tasks on the left, the
            # credit line on the right. There used to be a second bar under
            # it - an empty QStatusBar with a size grip - taking another 20 px.
            self.status_bar = QStatusBar()
            self.status_bar.setSizeGripEnabled(False)
            self.status_bar.setObjectName("footerStatus")
            self.status_bar.setStyleSheet("QStatusBar { background: transparent; border: none; }")
            footer_widget = self.create_footer()
            main_layout.addWidget(footer_widget)
            suite_title = getattr(self, "suite_title", "Slate")
            from ..login_dialog import version_text
            self.status_bar.showMessage(f"Ready - {suite_title} \u00b7 {version_text()}", 5000)

            # E. Running tasks: a name and percent, click for the list.
            try:
                from .task_manager_dock import task_registry
                self._build_task_progress()

                def safe_update(_task_id=None):
                    # The registry outlives this window. Once the window is
                    # gone, stop listening instead of touching a dead widget.
                    try:
                        self.update_task_progress()
                    except RuntimeError:
                        for signal, slot in ((task_registry.task_added, on_task_added),
                                             (task_registry.task_removed, on_task_removed),
                                             (task_registry.task_updated, on_task_updated)):
                            try:
                                signal.disconnect(slot)
                            except (RuntimeError, TypeError):
                                pass

                def on_task_added(task): safe_update(task.task_id)
                def on_task_removed(task_id): safe_update(task_id)
                def on_task_updated(task_id): safe_update(task_id)

                task_registry.task_added.connect(on_task_added)
                task_registry.task_removed.connect(on_task_removed)
                task_registry.task_updated.connect(on_task_updated)
            except Exception as e:
                logging.error(f"Failed to load Global Progress Bar: {e}")

    def _build_task_progress(self):
        """The footer's running-task display: 'Copying plates 37%' and a bar."""
        from PySide6.QtWidgets import QProgressBar
        from .header_builder import ClickableLabel
        self.task_progress_label = ClickableLabel("")
        self.task_progress_label.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 11px;")
        self.task_progress_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.task_progress_label.setToolTip("Running tasks - click for the list")
        self.task_progress_label.clicked.connect(self.show_task_list)
        self.task_progress_label.setVisible(False)
        self.global_progress = QProgressBar()
        self.global_progress.setMaximumWidth(160)
        self.global_progress.setFixedHeight(10)
        self.global_progress.setTextVisible(False)
        self.global_progress.setVisible(False)
        self.global_progress.setCursor(Qt.CursorShape.PointingHandCursor)
        self.global_progress.mousePressEvent = lambda _e: self.show_task_list()
        self._footer_row.insertWidget(1, self.task_progress_label)
        self._footer_row.insertWidget(2, self.global_progress)

    @staticmethod
    def task_progress_text(active_tasks) -> str:
        """'Copying plates 37%' for one task, '2 tasks' for more."""
        if not active_tasks:
            return ""
        if len(active_tasks) > 1:
            return f"{len(active_tasks)} tasks"
        task = active_tasks[0]
        return f"{task.name} {int(task.progress or 0)}%"

    def update_task_progress(self):
        from .task_manager_dock import task_registry
        active = [t for t in task_registry.get_all_tasks()
                  if str(t.status).lower() in ("running", "pending", "paused")]
        label = getattr(self, "task_progress_label", None)
        bar = getattr(self, "global_progress", None)
        if label is None or bar is None:
            return
        if not active:
            label.setVisible(False)
            bar.setVisible(False)
            return
        label.setText(self.task_progress_text(active))
        label.setVisible(True)
        bar.setVisible(True)
        bar.setValue(int(sum(int(t.progress or 0) for t in active) / len(active)))
        bar.setToolTip("\n".join(f"{t.name}: {int(t.progress or 0)}%" for t in active))

    def show_task_list(self):
        """The list of running tasks, with Pause and Cancel (the task dock)."""
        dock = getattr(self, "task_dock", None)
        if dock is None:
            from .task_manager_dock import TaskManagerDock
            dock = TaskManagerDock(self)
            self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
            self.task_dock = dock
        dock.show()
        dock.raise_()
        return dock

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
            # Sync pushes offline changes; it was created and connected to nothing.
            if getattr(self.header_builder, "sync_button", None) is not None:
                self.header_builder.sync_button.clicked.connect(self.trigger_sync_database)

            # The one notification centre, beside Help, for everybody. It used
            # to exist only inside the Timeline Viewer and the dashboard.
            self.notification_center = None
            if self.user_data:
                try:
                    from .notification_center import NotificationCenter
                    data = self.user_data or {}
                    self.notification_center = NotificationCenter(
                        self, self._current_username(),
                        aliases=(data.get("display_name"),))
                    self.header_builder.insert_before_help(self.notification_center.bell)
                except Exception as exc:
                    logging.warning("Notification centre not available: %s", exc)

            return header_widget

    def create_footer(self):
            footer = QWidget()
            footer.setObjectName("footer")
            footer_layout = QVBoxLayout(footer)
            footer_layout.setContentsMargins(0, 0, 0, 0)
            team_layout = QHBoxLayout()
            # The credit line keeps its place: 25 px from the right edge, as
            # it was inside the old 15 px window frame plus this row's 10.
            team_layout.setContentsMargins(25, 2, 25, 5)

            # Status messages where "TEAM SLATE" used to be (it meant nothing
            # to anybody); the separate status bar under the footer is gone.
            status = getattr(self, "status_bar", None) if self is not None else None
            if status is not None:
                team_layout.addWidget(status, 1)
            else:
                team_layout.addStretch()

            from slate.licence import credit_label
            license_label = credit_label()    # licence section 5: must stay

            team_layout.addWidget(license_label)
            footer_layout.addLayout(team_layout)
            if self is not None:
                self._footer_row = team_layout
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
