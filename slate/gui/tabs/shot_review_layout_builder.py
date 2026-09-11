from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.infra.design_tokens import (
    ColorTokens as C,
    RadiusTokens as R,
    SpacingTokens as S,
    TypographyTokens as T,
)
from .shot_review.comparison_viewer import ComparisonViewer
from .shot_review.live_tech_check import LiveTechCheckWidget


def setup_shot_review_ui(tab):
    """Create main 3-panel Shot Review layout."""
    main_layout = QHBoxLayout(tab)
    main_layout.setContentsMargins(0, 0, 0, 0)

    splitter = QSplitter(Qt.Orientation.Horizontal)
    tab.shot_list_panel = create_shot_list_panel(tab)
    splitter.addWidget(tab.shot_list_panel)

    tab.viewer_panel = create_viewer_panel(tab)
    splitter.addWidget(tab.viewer_panel)

    tab.control_panel = create_control_panel(tab)
    splitter.addWidget(tab.control_panel)

    splitter.setSizes([300, 800, 350])
    splitter.setStretchFactor(1, 1)
    main_layout.addWidget(splitter)


def create_shot_list_panel(tab):
    panel = QWidget()
    layout = QVBoxLayout(panel)

    title = QLabel("SHOT LIST")
    title.setStyleSheet(
        f"font-size: {T.SIZE_MD}px; font-weight: {T.WEIGHT_STYLE_BOLD}; padding: {S.XS}px;"
    )
    layout.addWidget(title)

    tab.project_label = QLabel("No project loaded")
    tab.project_label.setWordWrap(True)
    tab.project_label.setStyleSheet(f"padding: {S.XS}px; color: {C.TEXT_GRAY_LIGHT};")
    layout.addWidget(tab.project_label)

    tab.shot_list = QListWidget()
    tab.shot_list.itemClicked.connect(tab.on_shot_selected)
    tab.shot_list.itemDoubleClicked.connect(tab.open_shot_folder)
    tab.shot_list.setContextMenuPolicy(Qt.CustomContextMenu)
    tab.shot_list.customContextMenuRequested.connect(tab.show_context_menu)
    layout.addWidget(tab.shot_list)

    tab.stats_label = QLabel("0 shots")
    tab.stats_label.setStyleSheet(f"padding: {S.XS}px; color: {C.TEXT_GRAY_LIGHT};")
    layout.addWidget(tab.stats_label)

    pagination_widget = QWidget()
    pagination_layout = QHBoxLayout(pagination_widget)
    pagination_layout.setContentsMargins(0, 0, 0, 0)

    tab.btn_prev_page = QPushButton("Prev")
    tab.btn_prev_page.clicked.connect(tab.prev_page)
    tab.btn_prev_page.setEnabled(False)
    pagination_layout.addWidget(tab.btn_prev_page)

    tab.pagination_label = QLabel("Page 1 / 1 (0 shots)")
    tab.pagination_label.setStyleSheet(f"color: {C.TEXT_GRAY_LIGHT}; font-size: {T.SIZE_XS}px;")
    tab.pagination_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    pagination_layout.addWidget(tab.pagination_label)

    tab.btn_next_page = QPushButton("Next")
    tab.btn_next_page.clicked.connect(tab.next_page)
    tab.btn_next_page.setEnabled(False)
    pagination_layout.addWidget(tab.btn_next_page)
    layout.addWidget(pagination_widget)

    btn_load_dashboard = QPushButton("Load from Dashboard")
    btn_load_dashboard.clicked.connect(tab.load_from_dashboard)
    btn_load_dashboard.setToolTip("Load shots from VFX Dashboard database")
    layout.addWidget(btn_load_dashboard)

    btn_auto_pull = QPushButton("Auto-Pull Folder")
    btn_auto_pull.clicked.connect(tab.auto_pull_project)
    btn_auto_pull.setToolTip("Scan folder for shots (manual fallback)")
    layout.addWidget(btn_auto_pull)

    btn_refresh = QPushButton("Refresh")
    btn_refresh.clicked.connect(tab.refresh_project)
    layout.addWidget(btn_refresh)

    btn_sync = QPushButton("Sync to Dashboard")
    btn_sync.clicked.connect(tab.sync_to_dashboard)
    btn_sync.setToolTip("Save review statuses back to dashboard")
    layout.addWidget(btn_sync)

    return panel


def create_viewer_panel(tab):
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)

    tab.comparison_viewer = ComparisonViewer()
    tab.comparison_viewer.frame_changed.connect(tab.on_frame_changed)
    layout.addWidget(tab.comparison_viewer)
    return panel


def create_control_panel(tab):
    panel = QWidget()
    layout = QVBoxLayout(panel)

    title = QLabel("SHOT DETAILS")
    title.setStyleSheet(
        f"font-size: {T.SIZE_MD}px; font-weight: {T.WEIGHT_STYLE_BOLD}; padding: {S.XS}px;"
    )
    layout.addWidget(title)

    tab.info_label = QTextEdit("No shot selected")
    tab.info_label.setReadOnly(True)
    tab.info_label.setStyleSheet(
        f"padding: {S.MD}px; "
        f"background: {C.BG_HOVER}; "
        f"border-radius: {R.SM}px; "
        f"font-family: '{T.FONT_MONO}';"
    )
    tab.info_label.setMinimumHeight(200)
    tab.live_tech_check = LiveTechCheckWidget()
    layout.addWidget(tab.live_tech_check)

    memory_group = QWidget()
    memory_layout = QVBoxLayout(memory_group)
    memory_layout.setContentsMargins(0, 10, 0, 10)

    memory_title = QLabel("CACHE MONITOR")
    memory_title.setStyleSheet(
        f"font-size: {T.SIZE_SM}px; font-weight: {T.WEIGHT_STYLE_BOLD}; padding: {S.XS}px;"
    )
    memory_layout.addWidget(memory_title)

    tab.cache_stats_label = QLabel("Cache: 0 MB / 4096 MB (0%)")
    tab.cache_stats_label.setStyleSheet(
        f"color: {C.ACCENT_INFO}; font-size: {T.SIZE_XS}px; padding: 2px {S.XS}px;"
    )
    memory_layout.addWidget(tab.cache_stats_label)

    tab.cache_hit_rate_label = QLabel("Hit rate: 0% | Items: 0")
    tab.cache_hit_rate_label.setStyleSheet(
        f"color: {C.TEXT_GRAY_LIGHT}; font-size: {T.SIZE_XS}px; padding: 2px {S.XS}px;"
    )
    memory_layout.addWidget(tab.cache_hit_rate_label)

    btn_clear_cache = QPushButton("Clear All Cache")
    btn_clear_cache.clicked.connect(tab.clear_all_cache)
    btn_clear_cache.setStyleSheet(f"padding: {S.XS}px; font-size: {T.SIZE_XS}px;")
    memory_layout.addWidget(btn_clear_cache)
    layout.addWidget(memory_group)

    layout.addStretch()

    tools_label = QLabel("TOOLS")
    tools_label.setStyleSheet(
        f"font-size: {T.SIZE_SM}px; font-weight: {T.WEIGHT_STYLE_BOLD}; padding: {S.XS}px;"
    )
    layout.addWidget(tools_label)

    tab.btn_cache_shot = QPushButton("Cache Shot")
    tab.btn_cache_shot.clicked.connect(tab.cache_all_frames)
    tab.btn_cache_shot.setEnabled(False)
    tab.btn_cache_shot.setToolTip("Pre-load all frames into memory for instant scrubbing")
    layout.addWidget(tab.btn_cache_shot)

    tab.btn_manual_render = QPushButton("Choose Render")
    tab.btn_manual_render.clicked.connect(tab.choose_manual_render)
    tab.btn_manual_render.setEnabled(False)
    tab.btn_manual_render.setToolTip("Manually select render folder/sequence")
    layout.addWidget(tab.btn_manual_render)

    tab.btn_check_updates = QPushButton("Smart Version Scan")
    tab.btn_check_updates.clicked.connect(tab.check_smart_updates)
    tab.btn_check_updates.setToolTip("Scan for newer versions of approved shots")
    layout.addWidget(tab.btn_check_updates)

    layout.addSpacing(10)

    actions_label = QLabel("REVIEW ACTIONS")
    actions_label.setStyleSheet(
        f"font-size: {T.SIZE_SM}px; font-weight: {T.WEIGHT_STYLE_BOLD}; padding: {S.XS}px;"
    )
    layout.addWidget(actions_label)

    tab.btn_approve = QPushButton("Approve")
    tab.btn_approve.clicked.connect(tab.approve_shot)
    tab.btn_approve.setEnabled(False)
    tab.btn_approve.setStyleSheet(f"background: {C.SUCCESS}; padding: {S.SM}px;")
    layout.addWidget(tab.btn_approve)

    tab.btn_reject = QPushButton("Reject")
    tab.btn_reject.clicked.connect(tab.reject_shot)
    tab.btn_reject.setEnabled(False)
    tab.btn_reject.setStyleSheet(f"background: {C.ERROR_DIM}; padding: {S.SM}px;")
    layout.addWidget(tab.btn_reject)

    tab.btn_add_note = QPushButton("Add Note")
    tab.btn_add_note.clicked.connect(tab.add_note)
    tab.btn_add_note.setEnabled(False)
    layout.addWidget(tab.btn_add_note)

    proxy_note = QLabel("Approved shots auto-render MP4 proxies to Central Library.")
    proxy_note.setWordWrap(True)
    proxy_note.setStyleSheet(f"padding: {S.XS}px; color: {C.TEXT_GRAY_LIGHT};")
    layout.addWidget(proxy_note)
    return panel
