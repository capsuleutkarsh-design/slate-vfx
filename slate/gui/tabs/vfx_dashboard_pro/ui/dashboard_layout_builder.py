"""
Builds the VFX Dashboard's screen and wires its controls.

The app bar has two rows:

    project | status counters ................ backup state | Save N changes
    scope | search | status | group | Filters | (filters on - Clear) .. List|Board  View  Reports  Manage project  Add shots

At 1366 px and below the toolbar used to draw its controls over each other
("Group: None" under "Advanced..."). The rows now lay out by size policy, and
when the second row cannot fit, View, Reports, Manage project and Add shots
fold into one "More" menu (DashboardWidget._fit_toolbar).
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from slate.core.domain import shot_status
from slate.core.infra.gate import Gate
from slate.core.system.adaptation_engine import system_engine
from slate.gui.core.controls import make_button, style_button
from slate.gui.core.empty_state import EmptyState
from slate.gui.core.icons import icon as draw_icon
from slate.gui.core.table_style import style_table
from slate.gui.widgets.kanban_board import KanbanBoard
from slate.gui.widgets.users_list_widget import UsersListWidget

from .artist_delegate import ArtistDelegate
from .cell_delegates import (
    FramesDelegate, PriorityDelegate, ShotTypeDelegate, TargetDateDelegate, TextDelegate,
)
from .eliding_combo import ElidingComboBox
from .frozen_table import FrozenColumnTable
from .group_header_delegate import GroupHeaderDelegate
from .header_filter_view import FilterHeaderView
from .shot_proxy_models import ShotFilterProxy, ShotGroupModel
from .shot_table_model import ShotTableModel
from .stats_widget import StatsWidget
from .status_delegate import StatusDelegate


ALL_STATUSES = "All statuses"


def _menu_button(text, menu, tooltip=""):
    button = make_button(text, "secondary", tooltip=tooltip)
    button.setMenu(menu)
    style_button(button, "secondary")
    return button


def build_dashboard_ui(widget):
    """Build DashboardWidget UI layout and wire signal connections."""
    sp = system_engine.scale_px

    main_layout = QVBoxLayout(widget)
    main_layout.setSpacing(0)
    main_layout.setContentsMargins(0, 0, 0, 0)

    app_bar = QFrame()
    app_bar.setObjectName("appBar")
    widget.app_bar = app_bar
    app_layout = QVBoxLayout(app_bar)
    app_layout.setContentsMargins(sp(10), sp(8), sp(10), sp(8))
    app_layout.setSpacing(sp(8))

    # ---------------------------------------------------------------- row 1
    row1 = QHBoxLayout()
    row1.setSpacing(sp(10))

    widget.project_combo = ElidingComboBox()
    widget.project_combo.setObjectName("projectCombo")
    widget.project_combo.setToolTip("The project on screen")
    # Long names are cut with an ellipsis, with the whole name as the tooltip.
    widget.project_combo.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    widget.project_combo.setMinimumContentsLength(18)
    widget.project_combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    widget.project_combo.setMaximumWidth(sp(360, minimum=300))
    widget.project_combo.currentIndexChanged.connect(widget.on_project_changed)
    row1.addWidget(widget.project_combo)

    widget.stats_widget = StatsWidget()
    widget.stats_widget.status_clicked.connect(widget.on_stat_clicked)
    # It folds what does not fit into "+N more", so it must not hold the row open.
    widget.stats_widget.setMinimumWidth(1)
    row1.addWidget(widget.stats_widget, 1)

    widget.backup_label = QLabel("")
    widget.backup_label.setObjectName("backupLabel")
    widget.backup_label.setToolTip("When the project's Excel backup was last written.")
    row1.addWidget(widget.backup_label)

    widget.save_btn = make_button("Save changes", "primary", icon="save",
                                  tooltip="Write your pending edits to the database (Ctrl+S)")
    widget.save_btn.setObjectName("headerBtnPrimary")
    widget.save_btn.clicked.connect(widget.save_changes)
    widget.save_btn.setEnabled(False)
    row1.addWidget(widget.save_btn)
    app_layout.addLayout(row1)

    # ---------------------------------------------------------------- row 2
    row2 = QHBoxLayout()
    row2.setSpacing(sp(8))
    widget.toolbar_row = row2

    widget.scope_combo = QComboBox()
    widget.scope_combo.setObjectName("headerCombo")
    widget.scope_combo.addItem("All shots", "all")
    widget.scope_combo.setToolTip("Which shots to show: everything, yours, or one department's")
    widget.scope_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    widget.scope_combo.currentIndexChanged.connect(widget.on_scope_changed)
    row2.addWidget(widget.scope_combo)

    widget.search_input = QLineEdit()
    widget.search_input.setObjectName("headerInput")
    widget.search_input.setPlaceholderText("Search shot, SOW, status, artist…")
    widget.search_input.setToolTip(
        "Searches every column: shot, reel, status, artists, scope of work, type, "
        "target, version, department statuses and the description (Ctrl+F)")
    widget.search_input.setMinimumWidth(sp(150, minimum=130))
    widget.search_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    widget.search_input.setClearButtonEnabled(True)
    widget.search_input.addAction(draw_icon("search", Gate.TEXT_DIM, 16),
                                  QLineEdit.ActionPosition.LeadingPosition)
    widget.search_input.textChanged.connect(widget._schedule_filter_update)
    row2.addWidget(widget.search_input, 1)

    widget.status_filter = QComboBox()
    widget.status_filter.setObjectName("headerCombo")
    widget.status_filter.addItem(ALL_STATUSES, None)
    widget.status_filter.setToolTip("Show one status only")
    widget.status_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    widget.status_filter.currentIndexChanged.connect(widget.on_status_filter_changed)
    row2.addWidget(widget.status_filter)

    widget.groupby_combo = QComboBox()
    widget.groupby_combo.setObjectName("headerCombo")
    widget.groupby_combo.addItem("Group: None", "None")
    widget.groupby_combo.addItem("Group: Reel", "Reel / Sequence")
    widget.groupby_combo.addItem("Group: Status", "Status")
    widget.groupby_combo.addItem("Group: Artist", "Artist")
    widget.groupby_combo.addItem("Group: Priority", "Priority")
    widget.groupby_combo.setToolTip("Show the shots in groups")
    widget.groupby_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    widget.groupby_combo.currentIndexChanged.connect(widget.on_groupby_changed)
    row2.addWidget(widget.groupby_combo)

    widget.advanced_query_btn = make_button("Filters…", "secondary", icon="filter",
                                            tooltip="Filter by rules: field, condition, value")
    widget.advanced_query_btn.setObjectName("headerBtn")
    widget.advanced_query_btn.clicked.connect(widget.open_query_builder)
    row2.addWidget(widget.advanced_query_btn)

    # "3 filters on - Clear": always visible while anything narrows the grid,
    # so a filter can never hide shots without saying so.
    widget.filter_chip = make_button("", "ghost", icon="close",
                                     tooltip="Clear the search, the status filter, column filters and Filters rules")
    widget.filter_chip.setObjectName("filterChip")
    widget.filter_chip.clicked.connect(widget.clear_all_filters)
    widget.filter_chip.hide()
    row2.addWidget(widget.filter_chip)

    row2.addStretch(0)

    # List | Board: which view is on screen, at a glance.
    toggle = QWidget()
    toggle_row = QHBoxLayout(toggle)
    toggle_row.setContentsMargins(0, 0, 0, 0)
    toggle_row.setSpacing(0)
    widget.list_btn = make_button("List", "secondary", icon="list", tooltip="Show the shots as a table")
    widget.board_btn = make_button("Board", "secondary", icon="grid",
                                   tooltip="Show the shots as cards, one column per status")
    for button in (widget.list_btn, widget.board_btn):
        button.setCheckable(True)
        toggle_row.addWidget(button)
    widget.view_group = QButtonGroup(widget)
    widget.view_group.setExclusive(True)
    widget.view_group.addButton(widget.list_btn)
    widget.view_group.addButton(widget.board_btn)
    widget.list_btn.setChecked(True)
    widget._style_view_toggle = lambda: _style_segments(widget.list_btn, widget.board_btn)
    widget._style_view_toggle()
    # The board button is the old toggle: checked means the board is showing.
    widget.view_toggle_btn = widget.board_btn
    widget.board_btn.toggled.connect(widget.toggle_view_mode)
    widget.board_btn.toggled.connect(lambda *_: widget._style_view_toggle())
    row2.addWidget(toggle)

    # View: columns and layout.
    widget.view_menu = QMenu(widget)
    widget.view_menu.addAction("Show/hide columns…", widget.show_column_menu)
    widget.view_menu.addAction("Reset column layout", widget.reset_column_layout)
    widget.save_default_layout_action = widget.view_menu.addAction(
        "Save as project default layout", widget.save_project_default_layout)
    widget.save_default_layout_action.setVisible(widget._can_manage_shots())
    widget.view_menu.addSeparator()
    widget.view_menu.addAction("Collapse all groups", lambda: widget.set_groups_collapsed(True))
    widget.view_menu.addAction("Expand all groups", lambda: widget.set_groups_collapsed(False))
    widget.view_menu.addSeparator()
    widget.view_menu.addAction("Refresh	F5", widget.refresh_data)
    widget.debug_action = widget.view_menu.addAction("Run debug check", widget.run_debug)
    widget.debug_action.setVisible(widget._developer_tools_on())
    widget.view_btn = _menu_button("View", widget.view_menu, "Columns, layout and refresh")
    widget.columns_btn = widget.view_btn
    widget.more_actions_btn = widget.view_btn
    row2.addWidget(widget.view_btn)

    # Reports: read-only views over the project.
    widget.reports_menu = QMenu(widget)
    review_action = widget.reports_menu.addAction("Review queue", widget.review_queue_click)
    review_action.setToolTip("Versions submitted and still waiting for a verdict")
    delivery_action = widget.reports_menu.addAction("Delivery batches", widget.open_delivery_batches_dialog)
    delivery_action.setToolTip("Outgoing delivery packages")
    summary_action = widget.reports_menu.addAction("Production summary", widget.production_summary_click)
    summary_action.setToolTip("Progress, load and overdue shots")
    widget.reports_btn = _menu_button("Reports", widget.reports_menu, "Review queue, deliveries and the production summary")
    row2.addWidget(widget.reports_btn)

    # Manage project: built from what this person may do.
    widget.project_menu = QMenu(widget)
    widget.manage_proj_btn = _menu_button("Manage project", widget.project_menu)
    widget.manage_proj_btn.setObjectName("headerBtn")
    _build_project_menu(widget)
    row2.addWidget(widget.manage_proj_btn)

    widget.add_shots_btn = None
    if widget._can_manage_shots():
        widget.add_shots_btn = make_button(
            "Add shots", "secondary", icon="plus",
            tooltip="Create shots by hand. Most shots arrive automatically through Build & Ingest.")
        widget.add_shots_btn.setObjectName("headerBtn")
        widget.add_shots_btn.clicked.connect(widget.add_shots_click)
        row2.addWidget(widget.add_shots_btn)

    # Narrow windows: the four above fold into this one.
    widget.more_menu = QMenu(widget)
    widget.more_btn = _menu_button("More", widget.more_menu, "View, Reports, Manage project and Add shots")
    widget.more_menu.aboutToShow.connect(widget._fill_more_menu)
    widget.more_btn.hide()
    row2.addWidget(widget.more_btn)

    app_layout.addLayout(row2)
    main_layout.addWidget(app_bar)

    # Notifications are the bell in the main header now, for every screen.
    widget.notifications_btn = None

    # ---------------------------------------------------------------- banners
    widget.offline_banner = QFrame()
    widget.offline_banner.setObjectName("offlineBanner")
    widget.offline_banner.setStyleSheet(
        f"#offlineBanner {{ background-color: {Gate.BAD}; border: none; }}"
        f"#offlineBanner QLabel {{ color: {Gate.TEXT_ON_BAD}; font-weight: 600; background: transparent; }}"
    )
    banner_layout = QHBoxLayout(widget.offline_banner)
    banner_layout.setContentsMargins(sp(14), sp(8), sp(14), sp(8))
    banner_layout.setSpacing(sp(12))
    widget.offline_icon = QLabel()
    widget.offline_icon.setPixmap(draw_icon("wifi-off", Gate.TEXT_ON_BAD, 18).pixmap(18, 18))
    banner_layout.addWidget(widget.offline_icon)
    widget.offline_label = QLabel(
        "Can't reach the studio database - the dashboard is read-only until it is back. "
        "Nothing you see here is lost.")
    widget.offline_label.setWordWrap(True)
    banner_layout.addWidget(widget.offline_label, 1)
    widget.offline_retry_btn = QPushButton("Try again")
    widget.offline_retry_btn.setStyleSheet(
        f"QPushButton {{ color: {Gate.TEXT_ON_BAD}; background: transparent;"
        f" border: 1px solid {Gate.TEXT_ON_BAD}; border-radius: {Gate.RADIUS_MD}px; padding: 4px 12px;"
        f" font-weight: 600; }}"
        f"QPushButton:hover {{ background: {Gate.overlay(0.15)}; }}")
    widget.offline_retry_btn.clicked.connect(widget.retry_connection)
    banner_layout.addWidget(widget.offline_retry_btn)
    widget.offline_banner.setVisible(False)
    main_layout.addWidget(widget.offline_banner)

    # A lead whose job title names no department edits nothing; say so.
    widget.scope_banner = QFrame()
    widget.scope_banner.setObjectName("scopeBanner")
    widget.scope_banner.setStyleSheet(
        f"#scopeBanner {{ background: {Gate.WARN_SURFACE}; border-bottom: 1px solid {Gate.LINE}; }}"
        f"#scopeBanner QLabel {{ color: {Gate.TEXT}; background: transparent; }}")
    scope_layout = QHBoxLayout(widget.scope_banner)
    scope_layout.setContentsMargins(sp(14), sp(6), sp(14), sp(6))
    scope_icon = QLabel()
    scope_icon.setPixmap(draw_icon("info", Gate.WARN, 16).pixmap(16, 16))
    scope_layout.addWidget(scope_icon)
    widget.scope_banner_label = QLabel(
        "Your profile names no department, so you can view the dashboard but not change it. "
        "Ask an admin to set your job title (for example \"Roto Lead\").")
    widget.scope_banner_label.setWordWrap(True)
    scope_layout.addWidget(widget.scope_banner_label, 1)
    widget.scope_banner.setVisible(False)
    main_layout.addWidget(widget.scope_banner)

    # ---------------------------------------------------------------- grid
    widget.splitter = QSplitter(Qt.Orientation.Horizontal)
    widget.splitter.setHandleWidth(1)

    widget.table = FrozenColumnTable()
    widget.table.setObjectName("shotGrid")
    widget.table_model = ShotTableModel(
        user_role=widget.user_roles,
        user_identities=widget._artist_identity_candidates(),
    )
    # A lead's model knows which columns are theirs.
    widget.table_model.department_scope = widget._department_scope()
    widget.filter_proxy = ShotFilterProxy(widget)
    widget.filter_proxy.setSourceModel(widget.table_model)
    widget.filter_proxy.set_predicate(widget._shot_passes_filters)
    widget.group_model = ShotGroupModel(widget)
    widget.group_model.setSourceModel(widget.filter_proxy)

    widget.header_view = FilterHeaderView(widget.table)
    widget.header_view.set_value_provider(widget.column_filter_values)
    widget.header_view.filter_changed.connect(widget.on_header_filter_changed)
    widget.table.setHorizontalHeader(widget.header_view)
    widget.table.setModel(widget.group_model)

    frozen_header = FilterHeaderView(widget.table.frozen)
    frozen_header.active_filters = widget.header_view.active_filters
    frozen_header.set_value_provider(widget.column_filter_values)
    frozen_header.filter_changed.connect(widget.header_view.filter_changed)
    frozen_header.setSortIndicatorShown(True)
    widget.frozen_header = frozen_header
    widget.table.set_frozen_header(frozen_header)

    widget.table_model.own_status_edited.connect(widget.on_own_status_edited)
    widget.table_model.edits_changed.connect(widget.on_edits_changed)
    widget.group_model.modelReset.connect(widget._apply_table_spans)
    widget.group_model.layoutChanged.connect(widget._apply_table_spans)

    style_table(widget.table, editable=True, multi_select=True, row_height=sp(34, minimum=32))
    style_table(widget.table.frozen, editable=False, multi_select=True, row_height=sp(34, minimum=32))
    widget.table.setEditTriggers(
        QTableView.EditTrigger.DoubleClicked | QTableView.EditTrigger.EditKeyPressed
        | QTableView.EditTrigger.AnyKeyPressed)
    widget.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
    widget.table.frozen.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
    widget.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    widget.table.customContextMenuRequested.connect(widget.show_context_menu)

    # Click a heading to sort; the funnel (or a right-click) filters.
    header = widget.header_view
    header.setSortIndicatorShown(True)
    header.setSectionsClickable(True)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(True)
    # Open sorted by reel then shot (a fresh layout), not by Reel descending.
    header.setSortIndicator(widget.table_model.column_index("reel"), Qt.SortOrder.AscendingOrder)
    widget.table.setSortingEnabled(True)
    header.sortIndicatorChanged.connect(
        lambda section, order: frozen_header.setSortIndicator(section, order))
    frozen_header.sectionClicked.connect(widget._sort_from_frozen_header)
    header.sectionResized.connect(widget.table.on_main_section_resized)

    # Delegates, on both views where the column can show there.
    widget.group_delegate = GroupHeaderDelegate(widget.table, frozen_width=widget.table.frozen_width)
    widget.table.setItemDelegateForColumn(0, widget.group_delegate)
    widget.table.frozen.setItemDelegateForColumn(0, GroupHeaderDelegate(
        widget.table.frozen, frozen_width=widget.table.frozen_width, frozen=True))

    widget.status_delegate = StatusDelegate(widget.table, allowed=widget._allowed_statuses)
    widget.artist_delegate = ArtistDelegate(widget._get_user_list, widget.table)
    widget.text_delegate = TextDelegate(widget.table)
    typed = {
        "artist": widget.artist_delegate,
        "priority": PriorityDelegate(widget.table),
        "frames": FramesDelegate(widget.table),
        "target": TargetDateDelegate(widget.table),
        "type": ShotTypeDelegate(widget.table),
    }
    for col_idx, (col_key, _, _) in enumerate(widget.table_model.COLUMNS):
        if col_idx == 0:
            continue
        if col_key == "status" or col_key in widget.table_model._department_keys:
            widget.table.setItemDelegateForColumn(col_idx, widget.status_delegate)
        elif col_key in typed:
            widget.table.setItemDelegateForColumn(col_idx, typed[col_key])
        else:
            widget.table.setItemDelegateForColumn(col_idx, widget.text_delegate)

    from .components.column_layout_manager import ColumnLayoutManager
    from slate.core.infra.database_manager import database_manager
    widget.layout_manager = ColumnLayoutManager(
        widget.table,
        widget.table_model,
        user_id=getattr(widget, "user_data", {}).get("username", "default"),
        project_code="",
        db_manager=database_manager,
    )
    widget.layout_manager.attach()
    widget.layout_manager.apply_default_widths()

    widget.table.clicked.connect(widget.on_item_clicked)
    widget.table.doubleClicked.connect(widget.on_item_double_clicked)
    widget.table.verticalScrollBar().valueChanged.connect(widget._schedule_visible_thumbnail_refresh)

    # "No shots match": over the grid, with the headings (and their funnels)
    # left in place and a button that clears every filter.
    widget.no_match_state = EmptyState.over(
        widget.table, "No shots match",
        "Nothing matches the current search or filters.", glyph="search")
    widget.no_match_state.set_filtered(True, on_clear=widget.clear_all_filters, noun="shots")

    widget.view_stack = QStackedWidget()
    widget.view_stack.addWidget(widget.table)

    widget.kanban_board = KanbanBoard(inherit_app_theme=widget.inherit_app_theme)
    widget.kanban_board.status_changed.connect(widget.on_kanban_status_changed)
    widget.kanban_board.task_double_clicked.connect(widget.on_kanban_double_clicked)
    widget.kanban_board.task_assigned.connect(widget.on_task_assigned)
    widget.view_stack.addWidget(widget.kanban_board)

    widget.empty_state = EmptyState(
        "No project selected",
        "Pick a project above to load its shots.",
        glyph="film")
    widget.empty_state_frame = widget.empty_state
    widget.view_stack.addWidget(widget.empty_state)

    widget.splitter.addWidget(widget.view_stack)

    widget.users_list = UsersListWidget()
    widget.users_list.setMinimumWidth(sp(200, minimum=180))
    widget.users_list.setMaximumWidth(sp(260, minimum=220))
    widget.users_list.hide()
    widget.splitter.addWidget(widget.users_list)

    widget.detail_container = QFrame()
    widget.detail_container.setObjectName("detailContainer")
    widget.detail_container.setMinimumWidth(sp(420, minimum=360))
    widget.detail_container.setStyleSheet(
        f"#detailContainer {{ background-color: {Gate.PANEL}; border-left: 1px solid {Gate.LINE}; }}")
    widget.detail_layout = QVBoxLayout(widget.detail_container)
    widget.detail_layout.setContentsMargins(0, 0, 0, 0)
    widget.detail_widget = None
    widget.splitter.addWidget(widget.detail_container)
    widget.detail_container.hide()

    main_layout.addWidget(widget.splitter, 1)

    widget.status_bar = QStatusBar()
    widget.status_bar.setSizeGripEnabled(False)
    main_layout.addWidget(widget.status_bar, 0)

    _install_shortcuts(widget)


def _style_segments(left: QPushButton, right: QPushButton):
    """Two buttons that read as one control, the current one filled."""
    for button, side in ((left, "left"), (right, "right")):
        checked = button.isChecked()
        radius = (f"border-top-left-radius: {Gate.RADIUS_MD}px; border-bottom-left-radius: {Gate.RADIUS_MD}px;"
                  f" border-top-right-radius: 0; border-bottom-right-radius: 0;") if side == "left" else (
                  f"border-top-right-radius: {Gate.RADIUS_MD}px; border-bottom-right-radius: {Gate.RADIUS_MD}px;"
                  f" border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: none;")
        background = Gate.ACCENT_SURFACE if checked else Gate.RAISED
        colour = Gate.ACCENT if checked else Gate.TEXT_2
        button.setStyleSheet(
            f"QPushButton {{ background: {background}; color: {colour}; border: 1px solid {Gate.LINE};"
            f" {radius} padding: 6px 12px; font-weight: 600; min-height: 18px; }}"
            f"QPushButton:hover {{ color: {Gate.TEXT}; }}")
        button.setIcon(draw_icon("list" if side == "left" else "grid", colour, 16))


def _build_project_menu(widget):
    """Manage project, from what this person may do. Hidden when that is nothing."""
    from slate.core.domain.access import can_use_excel, can_delete_project
    menu = widget.project_menu
    menu.clear()
    can_manage = widget._can_manage_shots()
    if can_manage:
        menu.addAction("Add new project…", widget.add_project_click)
        menu.addAction("Edit current project…", widget.edit_project_click)
        menu.addAction("Set project root…", widget.set_project_root_click)
        menu.addSeparator()
        menu.addAction("Create blank template…", widget.create_blank_template_click)
    if can_use_excel(getattr(widget, "user_roles", [])):
        # Excel is a passbook: the software writes to it, never reads from it.
        export_action = menu.addAction("Export to Excel (backup now)", widget.export_to_excel_click)
        export_action.setToolTip("Write all loaded shots out to the project's Excel backup")
    if can_delete_project(widget.user_roles):
        menu.addSeparator()
        menu.addAction("Archive project…", widget.archive_project_click)
        menu.addAction("Show archived projects…", widget.show_archived_projects)
        delete_action = menu.addAction("Delete project permanently…", widget.delete_project_click)
        delete_action.setToolTip("Removes the project, its shots and its history for good")
    widget.manage_proj_btn.setVisible(not menu.isEmpty())


def _install_shortcuts(widget):
    def shortcut(keys, slot, parent=widget, context=Qt.ShortcutContext.WidgetWithChildrenShortcut):
        s = QShortcut(QKeySequence(keys), parent)
        s.setContext(context)
        s.activated.connect(slot)
        return s

    # Ctrl+Z takes back the last edit (any editor, not only the grid).
    widget.undo_shortcut = shortcut(QKeySequence.StandardKey.Undo, widget.undo_last_edit)
    widget.save_shortcut = shortcut("Ctrl+S", widget.save_changes)
    widget.find_shortcut = shortcut("Ctrl+F", widget.focus_search)
    widget.escape_shortcut = shortcut("Esc", widget.on_escape)
    # Space on a row is Quick Look; the table would otherwise eat it.
    widget.quick_look_shortcut = shortcut("Space", widget.quick_look_current, parent=widget.table)
    # Inside Slate, F5 is the main window's (it calls refresh_data); a second
    # F5 here would make the key do nothing at all.
    widget.refresh_shortcut = None
    if not widget.inherit_app_theme:
        widget.refresh_shortcut = shortcut("F5", widget.refresh_data)
