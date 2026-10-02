"""
The middle of the Stock Viewer: the toolbar, the cards or the table, and what
it says when there is nothing to show.

Grid and List are one pair of buttons that say what they switch to (MED-052);
List is a real table with sortable columns (MED-049). The count is always
visible, in a short form when the panel is narrow (MED-032, MED-069). The
empty state knows who is looking and why it is empty (MED-042, MED-009,
MED-063).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QStackedWidget,
    QLineEdit, QButtonGroup, QSlider, QPushButton,
    QProgressBar, QListView, QAbstractItemView, QFrame, QGridLayout, QSizePolicy,
    QTableView, QHeaderView, QMenu, QApplication,
)
from PySide6.QtCore import Qt, Signal, QTimer, QSize, QEvent

from ..widgets import DraggableListView, AssetKeys, copy_paths
from ....stock_model import (
    StockDelegate, COLUMN_SORTS, asset_path, can_preview,
)
from ....components.qt_safety import safe_single_shot
from .....core.infra.design_tokens import TypographyTokens as T
from ....widgets.styled_buttons import StyledComboBox
from ....core.controls import make_button, style_button
from ....core.empty_state import EmptyState
from slate.core.infra.gate import Gate
from ....core.icons import icon as draw_icon

# What the Sort box offers, and the database order each one means.
SORT_CHOICES = (
    ("Newest first", "newest"),
    ("Oldest first", "oldest"),
    ("Name A–Z", "name"),
    ("Name Z–A", "name_desc"),
    ("Largest first", "size"),
    ("Type", "type"),
    ("Category", "category"),
)
VISUAL_CHOICES = ("Any look", "Dark", "Bright", "Warm", "Cold", "Green Screen", "Blue Screen")

# Card sizes. The thumbnails are made 320 px wide, so the cards stop there:
# beyond it they were only blown-up, blurred copies (MED-070).
ZOOM_MIN, ZOOM_DEFAULT, ZOOM_MAX = 110, 170, 320


def count_text(visible: int, total: int, compact: bool = False) -> str:
    """'434 assets', 'Showing 120 of 434 assets', '1 asset' - or '120 / 434' when narrow."""
    visible, total = int(visible or 0), int(total or 0)
    total = max(total, visible)
    noun = "asset" if total == 1 else "assets"
    if compact:
        return f"{visible:,}" if visible == total else f"{visible:,} / {total:,}"
    if visible == total:
        return f"{total:,} {noun}"
    return f"Showing {visible:,} of {total:,} {noun}"


class SkeletonStateWidget(QWidget):
    """Displayed while assets are loading to avoid blank/frozen UI."""
    def __init__(self):
        super().__init__()
        self._pulse_state = False
        self._tiles = []
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(150)
        self._pulse_timer.timeout.connect(self._pulse)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        lbl_text = QLabel("Loading assets…")
        lbl_text.setStyleSheet(f"font-size: 15px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: {Gate.TEXT_2};")
        lbl_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_text)

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        for idx in range(8):
            tile = QFrame()
            tile.setFixedSize(160, 100)
            tile.setStyleSheet(f"border-radius: 6px; background-color: {Gate.RAISED_HI};")
            self._tiles.append(tile)
            grid.addWidget(tile, idx // 4, idx % 4)
        layout.addWidget(grid_host, 0, Qt.AlignmentFlag.AlignCenter)

    def start(self):
        self._pulse_timer.start()
        self._pulse()

    def stop(self):
        self._pulse_timer.stop()
        self._apply_pulse(False)

    def _pulse(self):
        self._pulse_state = not self._pulse_state
        self._apply_pulse(self._pulse_state)

    def _apply_pulse(self, bright: bool):
        color = Gate.LINE if bright else Gate.RAISED_HI
        for tile in self._tiles:
            tile.setStyleSheet(f"border-radius: 6px; background-color: {color};")


class AssetTableView(QTableView):
    """The List view: the same assets as rows, with the gallery's keys."""

    folders_dropped = Signal(list)
    files_dropped = Signal(list)
    preview_requested = Signal()
    play_requested = Signal()
    delete_requested = Signal()
    favorite_requested = Signal()
    copy_requested = Signal()
    player_key = Signal(object)

    def keyPressEvent(self, event):
        if AssetKeys.handle(self, event):
            event.accept()
            return
        super().keyPressEvent(event)


class StockGallery(QWidget):
    """
    Gallery View for Stock Browser.
    """
    filter_changed = Signal()
    asset_double_clicked = Signal(object)
    folders_dropped = Signal(list)
    files_dropped = Signal(list)
    selection_changed = Signal(object, object)
    zoom_changed = Signal(int)
    scroll_bottom_reached = Signal()
    delete_requested = Signal()
    sidebar_expand_requested = Signal()
    preview_requested = Signal()
    play_requested = Signal()
    player_key = Signal(object)
    favorite_requested = Signal()            # star / unstar the current selection
    favorite_clicked = Signal(object)        # the star on one card
    pick_requested = Signal()
    tags_requested = Signal()
    ingest_requested = Signal()
    clear_filters_requested = Signal()

    def __init__(self, model, proxy_model, can_manage_assets=False, parent=None):
        super().__init__(parent)
        self.model = model
        self.proxy_model = proxy_model
        self.can_manage_assets = bool(can_manage_assets)
        self.is_loading_state = False
        self._last_total_count = 0
        self._last_visible_count = 0
        self._zoom = ZOOM_DEFAULT
        self.category = "All"
        self._sort_column = None

        self.setup_ui()
        self.setup_connections()

    # ------------------------------------------------------------- layout
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top_bar_host = QWidget(self)
        top_bar_host.setObjectName("StockTopBar")
        top_bar_root = QVBoxLayout(top_bar_host)
        top_bar_root.setContentsMargins(10, 10, 10, 8)
        top_bar_root.setSpacing(8)

        # Row 1: filters, view switch, search, count
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        self.btn_show_filters = make_button("Filters", "ghost", tooltip="Show the filter sidebar",
                                            icon="chevron-right")
        self.btn_show_filters.setVisible(False)
        self.btn_show_filters.clicked.connect(self.sidebar_expand_requested.emit)
        top_bar.addWidget(self.btn_show_filters)

        # Two buttons that say what they show, not one that names the mode
        # it is in and does the opposite (MED-052).
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        self.btn_grid = make_button("", "secondary", tooltip="Grid: thumbnails", icon="grid")
        self.btn_list = make_button("", "secondary", tooltip="List: details in columns", icon="list")
        for i, button in enumerate((self.btn_grid, self.btn_list)):
            button.setCheckable(True)
            button.setFixedWidth(36)
            self.view_group.addButton(button, i)
            top_bar.addWidget(button)
        self.btn_grid.setChecked(True)
        # Kept for callers that toggled the single old button.
        self.btn_view_toggle = self.btn_grid

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search name, tag, 4K, 24fps, folder…")
        self.search_bar.setToolTip("Every word must match. Searches names, tags, category, "
                                   "folders, resolution (4K, 1920), frame rate and codec.\n"
                                   "Esc clears it.")
        self.search_bar.addAction(draw_icon("search", Gate.TEXT_DIM, 16),
                                  QLineEdit.ActionPosition.LeadingPosition)
        self.search_bar.setClearButtonEnabled(True)
        self.search_bar.setMinimumWidth(160)
        self.search_bar.installEventFilter(self)
        top_bar.addWidget(self.search_bar, 1)

        self.lbl_count = QLabel(count_text(0, 0))
        self.lbl_count.setStyleSheet(f"color: {Gate.TEXT_2}; font-weight: 600;")
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_count.setMinimumWidth(60)
        top_bar.addWidget(self.lbl_count)
        top_bar_root.addLayout(top_bar)

        # Row 2: Visual, Sort, media type
        self.combo_visual = StyledComboBox()
        self.combo_visual.addItems(VISUAL_CHOICES)
        self.combo_visual.setToolTip("Show only pictures that look a certain way")
        self.combo_visual.setMinimumWidth(120)

        self.sort_combo = StyledComboBox()
        for label, key in SORT_CHOICES:
            self.sort_combo.addItem(label, key)
        self.sort_combo.setToolTip("Order of the library")
        self.sort_combo.setMinimumWidth(130)

        self.btn_group = QButtonGroup(self)
        self.btn_all = make_button("All", "secondary", tooltip="Every kind of asset")
        self.btn_img = make_button("Images", "secondary", tooltip="Stills and image sequences")
        self.btn_vid = make_button("Videos", "secondary", tooltip="Movie files")
        for btn in (self.btn_all, self.btn_img, self.btn_vid):
            btn.setCheckable(True)
            self.btn_group.addButton(btn)
        self.btn_all.setChecked(True)

        media_row = QHBoxLayout()
        media_row.setSpacing(8)
        media_row.addWidget(self.combo_visual)
        media_row.addWidget(self.sort_combo)
        media_row.addSpacing(4)
        media_row.addWidget(self.btn_all)
        media_row.addWidget(self.btn_img)
        media_row.addWidget(self.btn_vid)
        media_row.addStretch(1)
        top_bar_root.addLayout(media_row)

        top_bar_host.setStyleSheet(
            f"QWidget#StockTopBar {{ border-bottom: 1px solid {Gate.LINE_SOFT}; background: transparent; }}")
        layout.addWidget(top_bar_host)

        # --- the views
        self.stack = QStackedWidget()

        self.asset_view = DraggableListView()
        self.asset_view.setViewMode(QListView.ViewMode.IconMode)
        self.asset_view.setResizeMode(QListView.ResizeMode.Adjust)
        self.asset_view.setSpacing(8)
        self.asset_view.setModel(self.proxy_model)
        self._refit = QTimer(self)
        self._refit.setSingleShot(True)
        self._refit.setInterval(0)
        self._refit.timeout.connect(lambda: self._fit_cards())
        self.asset_view.viewport().installEventFilter(self)
        self.delegate = StockDelegate(self.asset_view)
        self.asset_view.setItemDelegate(self.delegate)
        self.asset_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.asset_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.asset_view.setUniformItemSizes(True)
        self.asset_view.setLayoutMode(QListView.LayoutMode.Batched)
        self.asset_view.setBatchSize(50)
        self.asset_view.setAutoScroll(False)
        self.asset_view.setMovement(QListView.Movement.Static)
        self.asset_view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.asset_view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.asset_view.accept_folders = self.can_manage_assets
        self.stack.addWidget(self.asset_view)

        self.table_view = AssetTableView()
        self.table_view.setModel(self.proxy_model)
        # One selection for both views: switching keeps what was picked.
        self.table_view.setSelectionModel(self.asset_view.selectionModel())
        from ....core.table_style import style_table
        style_table(self.table_view, {"Name": "stretch", "Type": ("interactive", 110),
                                      "Resolution": ("interactive", 110),
                                      "Length": ("interactive", 140), "Size": "numeric",
                                      "Added": ("interactive", 150),
                                      "Category": ("interactive", 130)},
                    multi_select=True, sortable=False, row_height=34)
        self.table_view.setIconSize(QSize(48, 27))
        self.table_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_view.setDragEnabled(True)
        header = self.table_view.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.table_view._twin = self.asset_view
        self.asset_view._twin = self.table_view
        self.stack.addWidget(self.table_view)

        self.empty_state = EmptyState("", "", glyph="folder")
        self._empty_ingest = make_button("Ingest a folder…", "primary",
                                         on_click=self.ingest_requested.emit)
        self.empty_state.layout().insertWidget(self.empty_state.layout().count() - 1,
                                               self._empty_ingest, 0, Qt.AlignmentFlag.AlignHCenter)
        self.stack.addWidget(self.empty_state)

        self.skeleton_state = SkeletonStateWidget()
        self.stack.addWidget(self.skeleton_state)

        self.no_results = EmptyState("No assets match", "", glyph="search")
        self.no_results.set_filtered(True, on_clear=self.clear_filters_requested.emit,
                                     noun="assets")
        self.stack.addWidget(self.no_results)

        # A read that failed is said as such, never shown as an empty library.
        self.error_page = QWidget()
        error_layout = QVBoxLayout(self.error_page)
        error_layout.addStretch(1)
        mark = QLabel()
        mark.setPixmap(draw_icon("alert", Gate.WARN, 36).pixmap(36, 36))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        error_layout.addWidget(mark)
        self.error_title = QLabel("")
        self.error_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_title.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {Gate.TEXT};")
        self.error_body = QLabel("")
        self.error_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_body.setWordWrap(True)
        self.error_body.setStyleSheet(f"color: {Gate.TEXT_2};")
        error_layout.addWidget(self.error_title)
        error_layout.addWidget(self.error_body)
        row = QHBoxLayout()
        row.addStretch()
        self.btn_error_retry = make_button("Try again", "primary", on_click=self._error_retry_clicked)
        self.btn_error_details = make_button("Details", "secondary", on_click=self._error_details_clicked)
        row.addWidget(self.btn_error_retry)
        row.addWidget(self.btn_error_details)
        row.addStretch()
        error_layout.addLayout(row)
        error_layout.addStretch(1)
        self.stack.addWidget(self.error_page)
        self._error = None
        layout.addWidget(self.stack, 1)

        # --- bottom bar: progress and zoom
        zoom_layout = QHBoxLayout()
        zoom_layout.setContentsMargins(10, 4, 10, 6)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximumHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMaximumWidth(160)
        zoom_layout.addWidget(self.progress_bar)
        zoom_layout.addStretch()
        self.lbl_zoom = QLabel("Card size")
        self.lbl_zoom.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        zoom_layout.addWidget(self.lbl_zoom)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(ZOOM_MIN, ZOOM_MAX)
        self.zoom_slider.setValue(ZOOM_DEFAULT)
        self.zoom_slider.setMinimumWidth(90)
        self.zoom_slider.setMaximumWidth(200)
        self.zoom_slider.setToolTip("Card size. Ctrl + mouse wheel over the grid works too; "
                                    "double-click or Ctrl+0 to reset.")
        self.zoom_slider.installEventFilter(self)
        zoom_layout.addWidget(self.zoom_slider)
        layout.addLayout(zoom_layout)

        self.set_empty_message()
        self.change_thumbnail_size(ZOOM_DEFAULT)
        self._apply_responsive_toolbar()

    def setup_connections(self):
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(300)
        self.search_timer.timeout.connect(self._emit_filter)

        self.search_bar.textChanged.connect(lambda: self.search_timer.start())
        self.combo_visual.currentIndexChanged.connect(self._emit_filter)
        self.btn_group.buttonClicked.connect(self._emit_filter)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_combo)
        self.view_group.idClicked.connect(lambda i: self.set_view_mode("list" if i else "grid"))

        self.asset_view.selectionModel().selectionChanged.connect(self.selection_changed.emit)
        self.asset_view.selectionModel().currentChanged.connect(
            lambda current, previous: self.selection_changed.emit(None, None))

        for view in (self.asset_view, self.table_view):
            view.doubleClicked.connect(self.asset_double_clicked.emit)
            view.folders_dropped.connect(self.folders_dropped.emit)
            view.files_dropped.connect(self.files_dropped.emit)
            view.preview_requested.connect(self.preview_requested.emit)
            view.play_requested.connect(self.play_requested.emit)
            view.delete_requested.connect(self._delete_from_keyboard)
            view.favorite_requested.connect(self.favorite_requested.emit)
            view.copy_requested.connect(self.copy_selection)
            view.player_key.connect(self.player_key.emit)
            view.customContextMenuRequested.connect(
                lambda pos, v=view: self.show_context_menu(pos, v))
        self.delegate.favorite_clicked.connect(self.favorite_clicked.emit)
        self.asset_view.zoom_step.connect(lambda step: self.zoom_slider.setValue(
            self.zoom_slider.value() + step * 20))
        self.table_view.horizontalHeader().sectionClicked.connect(self._on_header_clicked)

        self.zoom_debounce = QTimer(self)
        self.zoom_debounce.setSingleShot(True)
        self.zoom_debounce.setInterval(60)
        self.zoom_debounce.timeout.connect(lambda: self.change_thumbnail_size(self.zoom_slider.value()))
        self.zoom_slider.valueChanged.connect(lambda: self.zoom_debounce.start())

        for view in (self.asset_view, self.table_view):
            view.verticalScrollBar().valueChanged.connect(self._check_scroll)

    # ------------------------------------------------------------ events
    def eventFilter(self, obj, event):
        if obj is getattr(self, "search_bar", None) and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape and self.search_bar.text():
                self.search_bar.clear()
                return True
        if (obj is getattr(getattr(self, "asset_view", None), "viewport", lambda: None)()
                and event.type() == QEvent.Type.Resize):
            # The viewport narrows when the scroll bar appears, after the
            # first layout - the cards are fitted again then (MED-046).
            if event.size().width() != getattr(self, "_fitted_width", -1):
                self._refit.start()
        if obj is getattr(self, "zoom_slider", None) and event.type() == QEvent.Type.MouseButtonDblClick:
            self.zoom_slider.setValue(ZOOM_DEFAULT)
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_0 and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_slider.setValue(ZOOM_DEFAULT)
            event.accept()
            return
        super().keyPressEvent(event)

    def _delete_from_keyboard(self):
        if self.can_manage_assets:
            self.delete_requested.emit()

    # ------------------------------------------------------------ views
    def active_view(self):
        return self.table_view if self.view_mode() == "list" else self.asset_view

    def view_mode(self) -> str:
        return "list" if self.btn_list.isChecked() else "grid"

    def set_view_mode(self, mode: str):
        """Grid or list; the selection, and the current item's place, are kept (MED-051)."""
        mode = "list" if mode == "list" else "grid"
        (self.btn_list if mode == "list" else self.btn_grid).setChecked(True)
        if self.stack.currentWidget() in (self.asset_view, self.table_view):
            self.stack.setCurrentWidget(self.active_view())
        # The zoom only means something for cards (MED-050).
        self.zoom_slider.setEnabled(mode == "grid")
        self.lbl_zoom.setEnabled(mode == "grid")
        safe_single_shot(0, self, self.scroll_to_current)

    def toggle_view_mode(self):
        self.set_view_mode("grid" if self.view_mode() == "list" else "list")

    def scroll_to_current(self):
        view = self.active_view()
        index = self.current_index()
        if index.isValid():
            view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)

    def current_index(self):
        """The item the person is on, not an arbitrary one of the selected (MED-074)."""
        sm = self.asset_view.selectionModel()
        index = sm.currentIndex() if sm else None
        if index is not None and index.isValid() and sm.isSelected(index.siblingAtColumn(0)):
            return index.siblingAtColumn(0)
        rows = self.selected_rows()
        if rows:
            return self.proxy_model.index(rows[0], 0)
        return self.proxy_model.index(-1, 0)

    def selected_rows(self):
        sm = self.asset_view.selectionModel()
        if not sm:
            return []
        return sorted({i.row() for i in sm.selectedIndexes()})

    def selected_assets(self):
        out = []
        for row in self.selected_rows():
            asset = self.proxy_model.index(row, 0).data(Qt.ItemDataRole.UserRole)
            if isinstance(asset, dict):
                out.append(asset)
        return out

    def select_row(self, row):
        if 0 <= row < self.proxy_model.rowCount():
            index = self.proxy_model.index(row, 0)
            self.active_view().setCurrentIndex(index)
            self.active_view().scrollTo(index)

    # ------------------------------------------------------------ menus
    def show_context_menu(self, pos, view=None):
        view = view or self.asset_view
        index = view.indexAt(pos)
        if not index.isValid():
            return
        index = index.siblingAtColumn(0)
        sel_model = view.selectionModel()
        if sel_model and not sel_model.isSelected(index):
            view.setCurrentIndex(index)
        menu = self.build_context_menu(index)
        menu.exec(view.viewport().mapToGlobal(pos))

    def build_context_menu(self, index):
        """
        One separator between groups, none at the end, and the actions people
        look for (MED-055): Preview, favourite, studio pick, tags, copy, show
        in Explorer, then Delete for those who may.
        """
        asset = index.data(Qt.ItemDataRole.UserRole) or {}
        count = max(1, len(self.selected_rows()))
        menu = QMenu(self)
        if can_preview(asset):
            menu.addAction(draw_icon("eye"), "Preview\tSpace", self.preview_requested.emit)
        if asset.get("is_favorite"):
            menu.addAction(draw_icon("star"), "Remove from favourites\tCtrl+D",
                           self.favorite_requested.emit)
        else:
            menu.addAction(draw_icon("star-filled"), "Add to favourites\tCtrl+D",
                           self.favorite_requested.emit)
        if self.can_manage_assets:
            menu.addAction(draw_icon("sparkle"),
                           "Remove from studio picks" if asset.get("is_pick") else "Add to studio picks",
                           self.pick_requested.emit)
            if count == 1:
                menu.addAction(draw_icon("tag"), "Edit tags…", self.tags_requested.emit)
        menu.addSeparator()
        menu.addAction(draw_icon("copy"), "Copy path" if count == 1 else f"Copy {count} paths",
                       self.copy_selection)
        menu.addAction(draw_icon("copy"), "Copy name", lambda: self._copy_name(index))
        menu.addAction(draw_icon("folder"), "Show in Explorer", lambda: self._reveal_in_explorer(index))
        if self.can_manage_assets:
            menu.addSeparator()
            menu.addAction(draw_icon("trash"),
                           "Delete from library…" if count == 1 else f"Delete {count} from library…",
                           self.delete_requested.emit)
        return menu

    def copy_selection(self):
        return copy_paths([self.proxy_model.index(r, 0) for r in self.selected_rows()])

    def _copy_name(self, index):
        asset = index.data(Qt.ItemDataRole.UserRole) or {}
        QApplication.clipboard().setText(str(asset.get("name") or asset.get("file_name") or ""))

    def _copy_path_to_clipboard(self, index):
        asset = index.data(Qt.ItemDataRole.UserRole) or {}
        path = asset_path(asset)
        if path:
            QApplication.clipboard().setText(path)

    def _reveal_in_explorer(self, index):
        asset = index.data(Qt.ItemDataRole.UserRole) or {}
        reveal_in_explorer(asset_path(asset))

    # ------------------------------------------------------------ filters
    def _check_scroll(self, value):
        bar = self.active_view().verticalScrollBar()
        maximum = bar.maximum()
        if maximum > 0 and value >= (maximum - 200):
            self.scroll_bottom_reached.emit()

    def _emit_filter(self):
        self.filter_changed.emit()

    def _on_sort_combo(self):
        self._sort_column = None
        self.table_view.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self._emit_filter()

    def _on_header_clicked(self, column):
        """A header click orders the whole library in the database, not the loaded page."""
        choices = COLUMN_SORTS.get(column)
        header = self.table_view.horizontalHeader()
        if not choices:
            header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
            if self._sort_column is not None:
                header.setSortIndicator(self._sort_column[0], self._sort_column[1])
            return
        if self._sort_column and self._sort_column[0] == column:
            order = (Qt.SortOrder.DescendingOrder if self._sort_column[1] == Qt.SortOrder.AscendingOrder
                     else Qt.SortOrder.AscendingOrder)
        else:
            order = Qt.SortOrder.AscendingOrder
        self._sort_column = (column, order)
        header.setSortIndicator(column, order)
        key = choices[0] if order == Qt.SortOrder.AscendingOrder else choices[1]
        self.sort_combo.blockSignals(True)
        for i in range(self.sort_combo.count()):
            if self.sort_combo.itemData(i) == key:
                self.sort_combo.setCurrentIndex(i)
                break
        self.sort_combo.blockSignals(False)
        self._header_sort = key
        self._emit_filter()

    def sort_key(self) -> str:
        if self._sort_column is not None and getattr(self, "_header_sort", None):
            return self._header_sort
        return self.sort_combo.currentData() or "newest"

    def get_filter_state(self):
        media_type = "All"
        if self.btn_img.isChecked():
            media_type = "Images"
        elif self.btn_vid.isChecked():
            media_type = "Videos"
        visual = self.combo_visual.currentText()
        return {
            "search": self.search_bar.text().strip(),
            "visual": "" if self.combo_visual.currentIndex() <= 0 else visual,
            "media_type": media_type,
            "sort": self.sort_key(),
            "sort_index": self.sort_combo.currentIndex(),
        }

    def filters_active(self) -> bool:
        state = self.get_filter_state()
        return bool(state["search"] or state["visual"] or state["media_type"] != "All")

    def clear_filters(self):
        """Search, visual, media type back to everything (the category is the sidebar's)."""
        for widget in (self.search_bar, self.combo_visual):
            widget.blockSignals(True)
        self.search_bar.clear()
        self.combo_visual.setCurrentIndex(0)
        self.btn_all.setChecked(True)
        for widget in (self.search_bar, self.combo_visual):
            widget.blockSignals(False)

    # ------------------------------------------------------------ states
    def show_error(self, title, body, retry=None, details=""):
        self._error = {"retry": retry, "details": details, "title": title}
        self.error_title.setText(title)
        self.error_body.setText(body)
        self.btn_error_retry.setVisible(retry is not None)
        self.btn_error_details.setVisible(bool(details))
        self.skeleton_state.stop()
        self.stack.setCurrentWidget(self.error_page)

    def hide_error(self):
        self._error = None

    def _error_retry_clicked(self):
        retry = (self._error or {}).get("retry")
        if callable(retry):
            retry()

    def _error_details_clicked(self):
        from ....components.feedback import show_details
        info = self._error or {}
        show_details(self, info.get("title", ""), info.get("details", ""))

    def set_category(self, category):
        self.category = category or "All"
        self.set_empty_message()

    def set_empty_message(self):
        """What an empty gallery says depends on who is looking and what is selected."""
        if self.category == "Favorites":
            self.empty_state.set_message(
                "No favourites yet",
                "Star assets to keep them here: click the star on a card, or press Ctrl+D. "
                "Your favourites are yours alone.")
            self._empty_ingest.hide()
        elif self.category == "Studio picks":
            self.empty_state.set_message(
                "No studio picks yet",
                "Leads and supervisors mark the studio's picks from a card's right-click menu."
                if not self.can_manage_assets else
                "Right-click an asset and choose Add to studio picks to share it with everyone.")
            self._empty_ingest.hide()
        elif self.can_manage_assets:
            self.empty_state.set_message(
                "The stock library is empty",
                "Drag folders here, or click Ingest a folder. Ingesting reads the files where "
                "they are; nothing is copied.")
            self._empty_ingest.show()
        else:
            # Artists cannot ingest; the message used to tell them to (MED-042).
            self.empty_state.set_message(
                "The stock library is empty",
                "Ask a lead or supervisor to ingest footage.")
            self._empty_ingest.hide()

    def update_count(self, total, visible):
        self._last_total_count = int(total or 0)
        self._last_visible_count = int(visible or 0)
        self._apply_responsive_toolbar()
        if self._error is not None:
            return
        if self.is_loading_state:
            self.stack.setCurrentWidget(self.skeleton_state)
            return
        if visible:
            self.stack.setCurrentWidget(self.active_view())
            return
        special = self.category in ("Favorites", "Studio picks")
        narrowed = self.filters_active() or (self.category not in ("All", "") and not special)
        if narrowed:
            # Something is narrowing the list: say so, and offer to clear it.
            self.stack.setCurrentWidget(self.no_results)
        else:
            self.set_empty_message()
            self.stack.setCurrentWidget(self.empty_state)

    def set_loading_state(self, is_loading: bool):
        self.is_loading_state = bool(is_loading)
        if self.is_loading_state:
            self.stack.setCurrentWidget(self.skeleton_state)
            self.skeleton_state.start()
            return
        self.skeleton_state.stop()
        self.update_count(max(self._last_total_count, self.proxy_model.rowCount()),
                          self.proxy_model.rowCount())

    def set_sidebar_collapsed(self, collapsed: bool):
        self.btn_show_filters.setVisible(bool(collapsed))

    def set_can_manage(self, can_manage: bool):
        self.can_manage_assets = bool(can_manage)
        self.asset_view.accept_folders = self.can_manage_assets
        self.set_empty_message()

    # ------------------------------------------------------------ zoom
    def change_thumbnail_size(self, val):
        """
        Card width from the slider, then widened so whole columns fill the row
        (MED-046): the leftover space is shared out instead of left on the right.
        """
        self._zoom = max(ZOOM_MIN, min(ZOOM_MAX, int(val)))
        self._fit_cards()

    def _fit_cards(self):
        delegate = self.delegate
        spacing = self.asset_view.spacing()
        # A little is kept back so the last column never wraps by a pixel.
        self._fitted_width = self.asset_view.viewport().width()
        viewport = max(1, self._fitted_width - 4)
        minimum_cell = self._zoom + delegate.padding * 2 + 4 + spacing
        columns = max(1, viewport // minimum_cell)
        cell = viewport // columns
        card = cell - spacing
        delegate.thumb_width = max(40, card - delegate.padding * 2 - 4)
        delegate.thumb_height = int(delegate.thumb_width * 0.5625)
        scrollbar = self.asset_view.verticalScrollBar()
        ratio = scrollbar.value() / max(scrollbar.maximum(), 1) if scrollbar.maximum() > 0 else 0
        self.asset_view.setGridSize(QSize(cell, delegate.thumb_height + delegate.text_height
                                          + delegate.padding * 2 + 4 + spacing))
        self.asset_view.doItemsLayout()
        safe_single_shot(30, self.asset_view,
                         lambda: scrollbar.setValue(int(ratio * scrollbar.maximum())),
                         skip_when_closing_attr=None)

    def showEvent(self, event):
        super().showEvent(event)
        self._refit.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_toolbar()
        self._fit_cards()

    def _apply_responsive_toolbar(self):
        """Narrow panels get the short count, never no count (MED-032)."""
        width = max(1, self.width())
        compact = width < 700
        self.btn_show_filters.setText("" if width < 560 else "Filters")
        self.lbl_count.setText(count_text(self._last_visible_count, self._last_total_count,
                                          compact=compact))
        self.lbl_count.setToolTip(count_text(self._last_visible_count, self._last_total_count))
        self.lbl_count.setVisible(True)


def reveal_in_explorer(path: str):
    """Select the file in Explorer (or open its folder elsewhere)."""
    if not path:
        return
    import os
    from pathlib import Path as _P
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtCore import QUrl
    if os.name == 'nt' and _P(path).exists():
        import subprocess
        subprocess.Popen(['explorer', '/select,', str(_P(path))])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(_P(path).parent)))
