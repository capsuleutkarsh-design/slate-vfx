from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableView,
    QMenu,
    QVBoxLayout,
)

from ut_vfx.core.infra.design_tokens import (
    ColorTokens as C,
    RadiusTokens as R,
    TypographyTokens as T,
)
from ut_vfx.core.system.adaptation_engine import system_engine
from ut_vfx.gui.widgets.kanban_board import KanbanBoard
from ut_vfx.gui.widgets.users_list_widget import UsersListWidget
from ut_vfx.gui.widgets.styled_buttons import (
    PrimaryButton, SecondaryButton, DropdownButton, GhostButton
)

from .header_filter_view import FilterHeaderView
from .shot_table_model import ShotTableModel
from .stats_widget import StatsWidget
from .status_delegate import StatusDelegate
from .artist_delegate import ArtistDelegate
from .group_header_delegate import GroupHeaderDelegate
from .query_builder_dialog import QueryBuilderDialog


def build_dashboard_ui(widget):
    """Build DashboardWidget UI layout and wire signal connections."""
    sp = system_engine.scale_px

    main_layout = QVBoxLayout(widget)
    main_layout.setSpacing(0)
    main_layout.setContentsMargins(0, 0, 0, 0)

    app_bar = QFrame()
    app_bar.setObjectName("appBar")
    app_bar.setFixedHeight(sp(90, minimum=74))
    app_layout = QVBoxLayout(app_bar)
    app_layout.setContentsMargins(sp(10), sp(5), sp(10), sp(5))
    app_layout.setSpacing(sp(5))

    row1 = QHBoxLayout()
    row1.setSpacing(sp(10))

    title_label = QLabel("UTVFX")
    title_label.setObjectName("dashboardTitle")
    if not widget.inherit_app_theme:
        title_label.setStyleSheet(
            f"font-size: {T.SIZE_XL}px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: {C.ACCENT_BLUE}; background: transparent;"
        )
    row1.addWidget(title_label)

    row1.addStretch()
    widget.stats_widget = StatsWidget()
    row1.addWidget(widget.stats_widget)
    row1.addStretch()
    row1.addStretch()

    widget.theme_btn = QPushButton("Theme")
    widget.theme_btn.setFixedSize(sp(30, minimum=24), sp(30, minimum=24))
    widget.theme_btn.setToolTip("Toggle Theme")
    if not widget.inherit_app_theme:
        widget.theme_btn.setStyleSheet(
            f"background: transparent; border: 1px solid {C.BORDER_LIGHT}; border-radius: {R.LG}px; font-size: 14px;"
        )
    widget.theme_btn.clicked.connect(widget.cycle_theme)
    row1.addWidget(widget.theme_btn)

    app_layout.addLayout(row1)

    row2 = QHBoxLayout()
    row2.setSpacing(sp(15))

    left_layout = QHBoxLayout()
    left_layout.setSpacing(sp(8))
    widget.project_combo = QComboBox()
    widget.project_combo.setObjectName("projectCombo")
    widget.project_combo.setMinimumWidth(sp(140, minimum=120))
    widget.project_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    widget.project_combo.currentIndexChanged.connect(widget.on_project_changed)
    left_layout.addWidget(widget.project_combo)
    row2.addLayout(left_layout)
    row2.addStretch()

    mid_layout = QHBoxLayout()
    mid_layout.setSpacing(sp(8))

    widget.scope_combo = QComboBox()
    widget.scope_combo.setObjectName("headerCombo")
    widget.scope_combo.addItem("Scope: All", "all")
    widget.scope_combo.setMinimumWidth(sp(150, minimum=135))
    widget.scope_combo.setToolTip("Filter by Department or Assigned Shots")
    widget.scope_combo.currentIndexChanged.connect(widget.on_scope_changed)
    mid_layout.addWidget(widget.scope_combo)

    widget.search_input = QLineEdit()
    widget.search_input.setObjectName("headerInput")
    widget.search_input.setPlaceholderText("Search...")
    widget.search_input.setMinimumWidth(sp(180, minimum=150))
    widget.search_input.setClearButtonEnabled(True)
    widget.search_input.textChanged.connect(widget._schedule_filter_update)
    mid_layout.addWidget(widget.search_input)

    widget.status_filter = QComboBox()
    widget.status_filter.setObjectName("headerCombo")
    widget.status_filter.addItem("All Status")
    widget.status_filter.setMinimumWidth(sp(135, minimum=120))
    widget.status_filter.currentTextChanged.connect(widget.apply_filters)
    mid_layout.addWidget(widget.status_filter)

    widget.groupby_combo = QComboBox()
    widget.groupby_combo.setObjectName("headerCombo")
    widget.groupby_combo.addItem("Group: None", "None")
    widget.groupby_combo.addItem("Group: Reel / Seq", "Reel / Sequence")
    widget.groupby_combo.addItem("Group: Status", "Status")
    widget.groupby_combo.addItem("Group: Artist", "Artist")
    widget.groupby_combo.addItem("Group: Priority", "Priority")
    widget.groupby_combo.setMinimumWidth(sp(145, minimum=130))
    widget.groupby_combo.currentIndexChanged.connect(widget.on_groupby_changed)
    mid_layout.addWidget(widget.groupby_combo)

    widget.advanced_query_btn = SecondaryButton("Advanced...")
    widget.advanced_query_btn.setObjectName("headerBtn")
    widget.advanced_query_btn.setToolTip("Advanced Query Builder")
    widget.advanced_query_btn.clicked.connect(widget.open_query_builder)
    mid_layout.addWidget(widget.advanced_query_btn)

    widget.view_toggle_btn = SecondaryButton("Board View")
    widget.view_toggle_btn.setObjectName("headerBtn")
    widget.view_toggle_btn.setCheckable(True)
    widget.view_toggle_btn.toggled.connect(widget.toggle_view_mode)
    mid_layout.addWidget(widget.view_toggle_btn)

    # Create 'More Actions' Menu
    widget.more_actions_btn = GhostButton("⋮")
    widget.more_actions_btn.setFixedWidth(sp(32, minimum=28))
    if not widget.inherit_app_theme:
        widget.more_actions_btn.setStyleSheet(
            "border: none; background: transparent; font-size: 18px;"
        )
    
    more_menu = QMenu(widget.more_actions_btn)
    
    # Columns action
    columns_action = more_menu.addAction("Show/Hide Columns")
    columns_action.triggered.connect(widget.show_column_menu)
    
    more_menu.addSeparator()
    
    # Review queue
    review_action = more_menu.addAction("Review Queue")
    review_action.setToolTip("Versions submitted and still awaiting a verdict")
    review_action.triggered.connect(widget.review_queue_click)

    # Delivery batches
    delivery_action = more_menu.addAction("Delivery Batches")
    delivery_action.setToolTip("Create and inspect outgoing delivery packages")
    delivery_action.triggered.connect(widget.open_delivery_batches_dialog)

    # Production summary
    summary_action = more_menu.addAction("Production Summary")
    summary_action.setToolTip("Load, progress and overdue shots for what is on screen")
    summary_action.triggered.connect(widget.production_summary_click)

    more_menu.addSeparator()

    # Refresh action
    refresh_action = more_menu.addAction("Refresh Data")
    refresh_action.triggered.connect(widget.refresh_data)
    
    more_menu.addSeparator()
    
    # Debug action
    debug_action = more_menu.addAction("Run Debug")
    debug_action.triggered.connect(widget.run_debug)
    
    widget.more_actions_btn.setMenu(more_menu)
    mid_layout.addWidget(widget.more_actions_btn)

    row2.addLayout(mid_layout)
    row2.addStretch()

    right_layout = QHBoxLayout()
    right_layout.setSpacing(sp(8))

    # Project Management Menu
    widget.manage_proj_btn = DropdownButton("Manage Project")
    widget.manage_proj_btn.setObjectName("headerBtn")
    
    proj_menu = QMenu(widget.manage_proj_btn)
    
    set_root_action = proj_menu.addAction("Set Project Root")
    set_root_action.triggered.connect(widget.set_project_root_click)
    
    blank_template_action = proj_menu.addAction("Create Blank Template")
    blank_template_action.triggered.connect(widget.create_blank_template_click)

    # Excel is the studio's backup copy. Only Coordinator, Lead, Supervisor
    # (and admin/dev) get to move data between it and the database.
    from ut_vfx.core.domain.access import can_use_excel
    if can_use_excel(getattr(widget, "user_roles", [])):
        proj_menu.addSeparator()

        # Excel is a passbook: the software writes to it, never reads from
        # it. There is deliberately no "Import from Excel" here - a sheet
        # must never be able to overwrite the database.
        export_excel_action = proj_menu.addAction("Export to Excel (backup now)")
        export_excel_action.setToolTip("Write all loaded shots out to the project Excel file")
        export_excel_action.triggered.connect(widget.export_to_excel_click)

    can_manage = widget._can_manage_shots()
    if can_manage:
        proj_menu.addSeparator()
        
        add_proj_action = proj_menu.addAction("+ Add New Project")
        add_proj_action.triggered.connect(widget.add_project_click)
        
        edit_proj_action = proj_menu.addAction("Edit Current Project")
        edit_proj_action.triggered.connect(widget.edit_project_click)
        
        del_proj_action = proj_menu.addAction("Delete Current Project")
        del_proj_action.triggered.connect(widget.delete_project_click)
    
    widget.manage_proj_btn.setMenu(proj_menu)
    right_layout.addWidget(widget.manage_proj_btn)

    if can_manage:
        widget.add_shots_btn = SecondaryButton("+ Add Shots")
        widget.add_shots_btn.setObjectName("headerBtn")
        widget.add_shots_btn.setToolTip(
            "Create shots by hand. Most shots arrive automatically via Build & Ingest."
        )
        widget.add_shots_btn.clicked.connect(widget.add_shots_click)
        right_layout.addWidget(widget.add_shots_btn)

    widget.notifications_btn = SecondaryButton("Alerts")
    widget.notifications_btn.setObjectName("headerBtn")
    widget.notifications_btn.setToolTip("Unread notifications")
    widget.notifications_btn.clicked.connect(widget.show_notifications)
    right_layout.addWidget(widget.notifications_btn)

    widget.unsaved_label = QLabel("")
    widget.unsaved_label.setToolTip("Edits not yet written to the database.")
    right_layout.addWidget(widget.unsaved_label)

    widget.backup_label = QLabel("Excel backup: --")
    widget.backup_label.setStyleSheet("color: #87857F; font-size: 11px;")
    widget.backup_label.setToolTip("State of the project's Excel backup.")
    right_layout.addWidget(widget.backup_label)

    widget.save_btn = PrimaryButton("Save Changes")
    widget.save_btn.setObjectName("headerBtnPrimary")
    widget.save_btn.clicked.connect(widget.save_changes)
    if not can_manage:
        widget.save_btn.setEnabled(False)
        widget.save_btn.setToolTip("You do not have permission to save changes")
    right_layout.addWidget(widget.save_btn)

    row2.addLayout(right_layout)
    app_layout.addLayout(row2)
    main_layout.addWidget(app_bar)

    # Offline banner. Hidden while the central database is reachable; when it
    # is not, this stays put rather than passing by as a status message.
    widget.offline_banner = QFrame()
    widget.offline_banner.setObjectName("offlineBanner")
    widget.offline_banner.setStyleSheet(
        "#offlineBanner { background-color: #D9635F; border: none; }"
        "#offlineBanner QLabel { color: #D9635F; font-weight: 600; }"
    )
    banner_layout = QHBoxLayout(widget.offline_banner)
    banner_layout.setContentsMargins(sp(14), sp(8), sp(14), sp(8))
    banner_layout.setSpacing(sp(12))

    widget.offline_label = QLabel(
        "Central database unreachable - the dashboard is read-only. "
        "Changes made now would not reach anyone else."
    )
    widget.offline_label.setWordWrap(True)
    banner_layout.addWidget(widget.offline_label, 1)

    widget.offline_retry_btn = QPushButton("Retry connection")
    widget.offline_retry_btn.clicked.connect(widget.retry_connection)
    banner_layout.addWidget(widget.offline_retry_btn)

    widget.offline_banner.setVisible(False)
    main_layout.addWidget(widget.offline_banner)

    # Content Splitter (Table/Kanban vs Sidebar)
    widget.splitter = QSplitter(Qt.Orientation.Horizontal)
    
    # Hide the ugly checkerboard handle style
    if not widget.inherit_app_theme:
        widget.splitter.setStyleSheet("""
            QSplitter::handle {
                background: transparent;
                width: 6px;
            }
            QSplitter::handle:hover {
                background: rgba(255, 255, 255, 0.05);
            }
            QSplitter::handle:pressed {
                background: rgba(255, 255, 255, 0.1);
            }
        """)
    widget.splitter.setHandleWidth(1)

    widget.table = QTableView()
    widget.table_model = ShotTableModel(
        user_role=widget.user_roles,
        user_identities=widget._artist_identity_candidates(),
    )
    # A lead's model knows which columns are theirs.
    widget.table_model.department_scope = widget._department_scope()
    widget.table.setModel(widget.table_model)
    widget.table_model.own_status_edited.connect(widget.on_own_status_edited)

    # Ctrl+Z takes back the last cell edit.
    undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, widget)
    undo_shortcut.activated.connect(widget.undo_last_edit)
    widget.undo_shortcut = undo_shortcut

    # Click a heading to sort; right-click still opens that column's filter.
    widget.table.setSortingEnabled(True)
    header = widget.table.horizontalHeader()
    header.setSortIndicatorShown(True)
    header.setSectionsClickable(True)

    # Multi-Row Selection for ShotGrid-style batch editing
    widget.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
    widget.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
    widget.table.setAlternatingRowColors(True)
    widget.table.verticalHeader().setDefaultSectionSize(sp(36, minimum=34))
    widget.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    widget.table.customContextMenuRequested.connect(widget.show_context_menu)

    # Attach Group Header Delegate to column 0
    widget.group_delegate = GroupHeaderDelegate(widget.table)
    widget.table.setItemDelegateForColumn(0, widget.group_delegate)

    # Attach Status Delegate to Status and Department columns dynamically
    widget.status_delegate = StatusDelegate(widget.table,
                                            allowed=widget._allowed_statuses)
    for col_idx, (col_key, _, _) in enumerate(widget.table_model.COLUMNS):
        if col_key == "status" or col_key in getattr(widget.table_model, "_department_keys", set()):
            widget.table.setItemDelegateForColumn(col_idx, widget.status_delegate)

    # Attach Artist Delegate to Artist column
    widget.artist_delegate = ArtistDelegate(widget._get_user_list, widget.table)
    for col_idx, (col_key, _, _) in enumerate(widget.table_model.COLUMNS):
        if col_key == "artist":
            widget.table.setItemDelegateForColumn(col_idx, widget.artist_delegate)

    from .components.column_layout_manager import ColumnLayoutManager
    widget.layout_manager = ColumnLayoutManager(
        widget.table,
        widget.table_model,
        user_id=getattr(widget, "user_data", {}).get("username", "default"),
        project_code="",
        db_manager=getattr(widget, "database_manager", None),
    )

    widget.header_view = FilterHeaderView(widget.table)
    widget.header_view.filter_changed.connect(widget.on_header_filter_changed)
    widget.table.setHorizontalHeader(widget.header_view)
    widget.header_view.setSectionResizeMode(QHeaderView.Interactive)
    widget.header_view.setStretchLastSection(True)
    widget.table.verticalScrollBar().valueChanged.connect(widget._schedule_visible_thumbnail_refresh)

    widget.table.setColumnWidth(0, sp(80, minimum=68))
    widget.table.setColumnWidth(1, sp(150, minimum=125))
    widget.table.setColumnWidth(2, sp(100, minimum=88))
    widget.table.setColumnWidth(3, sp(110, minimum=92))
    widget.table.setColumnWidth(4, sp(200, minimum=170))

    widget.table.clicked.connect(widget.on_item_clicked)
    widget.table.doubleClicked.connect(widget.on_item_double_clicked)

    widget.view_stack = QStackedWidget()
    widget.view_stack.addWidget(widget.table)

    widget.kanban_board = KanbanBoard(inherit_app_theme=widget.inherit_app_theme)
    widget.kanban_board.status_changed.connect(widget.on_kanban_status_changed)
    widget.kanban_board.task_double_clicked.connect(widget.on_kanban_double_clicked)
    widget.kanban_board.task_assigned.connect(widget.on_task_assigned)
    widget.view_stack.addWidget(widget.kanban_board)

    widget.empty_state_frame = QFrame()
    widget.empty_state_frame.setObjectName("dashboardEmptyState")
    empty_layout = QVBoxLayout(widget.empty_state_frame)
    empty_layout.setContentsMargins(sp(24), sp(24), sp(24), sp(24))
    empty_layout.setSpacing(sp(8))
    empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

    widget.empty_state_title = QLabel("No project selected")
    widget.empty_state_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    widget.empty_state_title.setStyleSheet("font-size: 18px; font-weight: 700; color: #E8E6E1;")
    empty_layout.addWidget(widget.empty_state_title)

    widget.empty_state_body = QLabel("Select a project from the dropdown to load shots and start planning.")
    widget.empty_state_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
    widget.empty_state_body.setWordWrap(True)
    widget.empty_state_body.setStyleSheet("font-size: 13px; color: #6BA4C9;")
    empty_layout.addWidget(widget.empty_state_body)

    widget.view_stack.addWidget(widget.empty_state_frame)

    widget.splitter.addWidget(widget.view_stack)

    widget.users_list = UsersListWidget()
    widget.users_list.setFixedWidth(sp(200, minimum=170))
    widget.users_list.hide()
    widget.splitter.addWidget(widget.users_list)

    widget.detail_container = QFrame()
    widget.detail_container.setMinimumWidth(sp(460, minimum=380))
    if not widget.inherit_app_theme:
        widget.detail_container.setStyleSheet(
            f"background-color: {C.BG_ELEVATED}; border-left: 1px solid {C.BORDER_DEFAULT};"
        )
    widget.detail_layout = QVBoxLayout(widget.detail_container)
    widget.detail_layout.setContentsMargins(0, 0, 0, 0)
    widget.detail_widget = None

    widget.splitter.addWidget(widget.detail_container)
    widget.detail_container.hide()

    main_layout.addWidget(widget.splitter, 1)

    widget.status_bar = QStatusBar()
    if not widget.inherit_app_theme:
        widget.status_bar.setStyleSheet(f"background-color: {C.ACCENT_BLUE}; color: white;")
    widget.status_bar.setSizeGripEnabled(False)
    main_layout.addWidget(widget.status_bar, 0)
