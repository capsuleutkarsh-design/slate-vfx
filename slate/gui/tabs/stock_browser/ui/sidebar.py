"""
The Stock Viewer's left panel: categories, the ingest, and library actions.

The file held this class twice - a truncated first copy, shadowed by the
second (MED-077). One copy now, with:

  * "Categories" with counts, plus Favorites (yours) and Studio picks
    (shared); the highlight stays on the category being shown after the list
    reloads (MED-012, MED-013, MED-067, MED-068);
  * the ingest: Fast mode with what it skips, a progress area that keeps its
    last message and shows "Scanning…" while the bar has nothing to measure,
    one label format for Pause/Resume, and every library action locked while
    an ingest runs - Pause and Stop excepted (MED-022, MED-036, MED-037, MED-038);
  * Ingest, Rescan, Reload, Delete (n), Export, Import, and Clear library…
    on its own below a line, always written out in full, in the danger style
    (MED-014, MED-033, MED-040).
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QLabel, QFrame, QSizePolicy, QProgressBar,
)
from PySide6.QtCore import Qt, Signal

from ..widgets import PyToggle
from ....core.controls import make_button, style_button
from ....core.icons import icon as draw_icon
from slate.core.infra.gate import Gate

from slate.core.infra.stock_repository import ALL, FAVORITES, REMOVED, STUDIO_PICKS

CATEGORY_ROLE = Qt.ItemDataRole.UserRole
# The keys stay as stored; the list says them the studio's way (MED2-025).
SHOWN_AS = {FAVORITES: "Favourites"}
ROW_LOOK = {
    ALL: ("grid", "Everything in the library"),
    FAVORITES: ("star", "Assets you have starred - yours alone"),
    STUDIO_PICKS: ("sparkle", "Assets the leads have picked for everybody"),
    REMOVED: ("trash", "Deleted from the library. Select them and choose Restore to bring "
                       "them back; a Rescan leaves them out."),
}


class StockSidebar(QWidget):
    """
    Sidebar Panel for Stock Browser.
    """
    category_selected = Signal(str)
    ingest_requested = Signal(bool)   # fast_mode
    rescan_requested = Signal(bool)   # fast_mode
    pause_requested = Signal()
    stop_requested = Signal()
    refresh_requested = Signal()
    delete_selected_requested = Signal()
    clear_library_requested = Signal()
    import_library_requested = Signal()
    export_library_requested = Signal()
    sidebar_toggle_requested = Signal()

    def __init__(self, parent=None, can_ingest=False):
        super().__init__(parent)
        self.can_ingest = can_ingest
        self.toggle_fast = None
        self.current_category = ALL
        self._categories_shown = None
        self._ingest_running = False
        self._selection = 0
        self.library_total = 0
        self.root_count = None
        self.setup_ui()

    def _heading(self, text):
        label = QLabel(text)
        label.setStyleSheet(f"font-weight: 600; color: {Gate.TEXT_2};")
        return label

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        self.setMinimumWidth(200)
        self.setMaximumWidth(440)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        header_row = QHBoxLayout()
        header_row.addWidget(self._heading("Categories"))
        header_row.addStretch()
        self.btn_collapse = make_button("", "ghost", tooltip="Hide the sidebar", icon="chevron-left")
        self.btn_collapse.setFixedWidth(32)
        self.btn_collapse.clicked.connect(self.sidebar_toggle_requested.emit)
        header_row.addWidget(self.btn_collapse)
        layout.addLayout(header_row)

        self.category_list = QListWidget()
        self.category_list.setObjectName("StockCategories")
        self.category_list.setStyleSheet(Gate.sheet("""
            QListWidget#StockCategories { background: transparent; border: none; outline: none; }
            QListWidget#StockCategories::item { padding: 6px 10px; border-radius: 5px; color: @TEXT; }
            QListWidget#StockCategories::item:selected { background: @ACCENT_SURFACE; color: @TEXT;
                font-weight: 600; }
            QListWidget#StockCategories::item:hover:!selected { background: @HOVER; }
        """))
        self.category_list.itemClicked.connect(self._on_category_clicked)
        self.category_list.currentItemChanged.connect(
            lambda current, _previous: self._on_category_clicked(current))
        self.category_list.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.category_list.setMinimumHeight(120)
        layout.addWidget(self.category_list, 1)
        self.update_categories({})

        if self.can_ingest:
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setStyleSheet(f"color: {Gate.LINE_SOFT};")
            layout.addWidget(line)
            layout.addWidget(self._heading("Add to the library"))

            self.ingest_controls = QFrame()
            self.ingest_controls.setObjectName("StockIngestBox")
            self.ingest_controls.setStyleSheet(Gate.sheet(
                "QFrame#StockIngestBox { background: @RAISED; border: 1px solid @LINE_SOFT; "
                "border-radius: 6px; }"))
            ic_layout = QVBoxLayout(self.ingest_controls)
            ic_layout.setContentsMargins(10, 8, 10, 8)
            ic_layout.setSpacing(8)

            toggle_layout = QHBoxLayout()
            text_col = QVBoxLayout()
            text_col.setSpacing(0)
            self.lbl_fast = QLabel("Fast mode")
            self.lbl_fast_hint = QLabel("Skips review proxies - quicker")
            self.lbl_fast_hint.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 11px;")
            self.lbl_fast_hint.setWordWrap(True)
            text_col.addWidget(self.lbl_fast)
            text_col.addWidget(self.lbl_fast_hint)
            self.toggle_fast = PyToggle()
            self.toggle_fast.setToolTip(
                "Fast mode skips making review proxies, so a big folder ingests much quicker.\n"
                "Thumbnails, sizes, tags and visual tags are still made. The preview then plays\n"
                "the original file.")
            toggle_layout.addLayout(text_col, 1)
            toggle_layout.addWidget(self.toggle_fast)
            ic_layout.addLayout(toggle_layout)

            self.btn_ingest = make_button("Ingest a folder…", "primary", icon="plus",
                                          tooltip="Add a folder of stock. Several folders can be "
                                                  "dropped on the gallery at once.")
            self.btn_ingest.clicked.connect(lambda: self.ingest_requested.emit(self.toggle_fast.isChecked()))
            ic_layout.addWidget(self.btn_ingest)
            self.btn_rescan = make_button("Rescan folders", "secondary", icon="refresh",
                                          tooltip="Look again in every folder the library was "
                                                  "ingested from, and add what is new.")
            self.btn_rescan.clicked.connect(lambda: self.rescan_requested.emit(self.toggle_fast.isChecked()))
            ic_layout.addWidget(self.btn_rescan)

            self.ingest_progress_area = QWidget()
            self.ingest_progress_area.setVisible(False)
            ipa_layout = QVBoxLayout(self.ingest_progress_area)
            ipa_layout.setContentsMargins(0, 4, 0, 0)
            ipa_layout.setSpacing(6)
            self.lbl_ingest_status = QLabel("")
            self.lbl_ingest_status.setWordWrap(True)
            # Its whole sentence, also on a short laptop screen: the category
            # list gives way instead.
            self.lbl_ingest_status.setSizePolicy(QSizePolicy.Policy.Preferred,
                                                 QSizePolicy.Policy.Minimum)
            self.lbl_ingest_status.setStyleSheet(f"color: {Gate.TEXT_2};")
            ipa_layout.addWidget(self.lbl_ingest_status)
            self.progress_bar_ingest = QProgressBar()
            self.progress_bar_ingest.setRange(0, 100)
            self.progress_bar_ingest.setFixedHeight(8)
            self.progress_bar_ingest.setTextVisible(False)
            ipa_layout.addWidget(self.progress_bar_ingest)
            btn_row = QHBoxLayout()
            self.btn_pause = make_button("Pause", "secondary", icon="pause")
            self.btn_pause.clicked.connect(self.pause_requested.emit)
            self.btn_stop = make_button("Stop", "danger", icon="stop")
            self.btn_stop.clicked.connect(self.stop_requested.emit)
            btn_row.addWidget(self.btn_pause)
            btn_row.addWidget(self.btn_stop)
            ipa_layout.addLayout(btn_row)
            ic_layout.addWidget(self.ingest_progress_area)
            layout.addWidget(self.ingest_controls)

        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)
        line2.setStyleSheet(f"color: {Gate.LINE_SOFT};")
        layout.addWidget(line2)

        actions = QVBoxLayout()
        actions.setSpacing(8)
        self.btn_refresh = make_button("Reload", "ghost", icon="refresh",
                                       tooltip="Re-read the library from the database (F5). "
                                               "New files on disk need Rescan folders.")
        self.btn_refresh.clicked.connect(self.refresh_requested.emit)

        if self.can_ingest:
            row1 = QHBoxLayout()
            row1.setSpacing(8)
            row1.addWidget(self.btn_refresh)
            self.btn_delete_selected = make_button("Delete", "danger", icon="trash",
                                                   tooltip="Remove the selected assets from the "
                                                           "library (Delete). The files stay where "
                                                           "they are, and it can be undone.")
            self.btn_delete_selected.clicked.connect(self.delete_selected_requested.emit)
            row1.addWidget(self.btn_delete_selected)
            actions.addLayout(row1)

            row2 = QHBoxLayout()
            row2.setSpacing(8)
            self.btn_export = make_button("Export…", "secondary", icon="upload",
                                          tooltip="Save the library list as a JSON file")
            self.btn_export.clicked.connect(self.export_library_requested.emit)
            self.btn_import = make_button("Import…", "secondary", icon="download",
                                          tooltip="Add assets from a library export")
            self.btn_import.clicked.connect(self.import_library_requested.emit)
            row2.addWidget(self.btn_export)
            row2.addWidget(self.btn_import)
            actions.addLayout(row2)
            layout.addLayout(actions)

            danger_line = QFrame()
            danger_line.setFrameShape(QFrame.Shape.HLine)
            danger_line.setStyleSheet(f"color: {Gate.LINE_SOFT};")
            layout.addWidget(danger_line)
            self.btn_clear = make_button("Clear library…", "danger",
                                         tooltip="Remove every asset from the library, for "
                                                 "everybody. Asks you to type CLEAR first.")
            self.btn_clear.clicked.connect(self.clear_library_requested.emit)
            layout.addWidget(self.btn_clear)
            for button in (self.btn_clear, self.btn_export, self.btn_rescan):
                button.setProperty("full_tip", button.toolTip())
            self.set_selection_count(0)
        else:
            actions.addWidget(self.btn_refresh)
            layout.addLayout(actions)
        self.set_compact_mode(False)

    # ---------------------------------------------------------- categories
    def _on_category_clicked(self, item):
        if item is None:
            return
        name = item.data(CATEGORY_ROLE) or ALL
        if name != self.current_category:
            self.current_category = name
            self._apply_library_state()
            self.category_selected.emit(name)

    def update_categories(self, categories, favorites: int = None, picks: int = None,
                          current: str = None, removed: int = None):
        """
        Rebuild the list - only when it really changed - and keep the
        highlight on the category being shown (MED-013).

        categories is {name: count} (a plain list is accepted, without counts).
        removed is how many deleted assets "Removed" holds; it is listed for
        the people who can restore them (MED2-028).
        """
        if current is not None:
            self.current_category = current or ALL
        if isinstance(categories, dict):
            counts = dict(categories)
        else:
            counts = {str(c): None for c in (categories or [])}
        total = sum(v for v in counts.values() if v) if counts else 0
        self.library_total = total
        entries = [(ALL, total if counts else None), (FAVORITES, favorites),
                   (STUDIO_PICKS, picks)]
        entries += [(name, counts[name]) for name in sorted(counts, key=str.lower)]
        if self.can_ingest and removed is not None:
            entries.append((REMOVED, removed))
        if self.current_category not in [name for name, _ in entries]:
            self.current_category = ALL

        if entries != self._categories_shown:
            self._categories_shown = entries
            self.category_list.blockSignals(True)
            self.category_list.clear()
            for name, count in entries:
                label = SHOWN_AS.get(name, name)
                text = label if count is None else f"{label}  ({count:,})"
                item = QListWidgetItem(text)
                item.setData(CATEGORY_ROLE, name)
                # Every row has its icon, so the names line up (MED2-029).
                glyph, tip = ROW_LOOK.get(name, ("folder", ""))
                item.setIcon(draw_icon(glyph, Gate.TEXT_2, 14))
                if tip:
                    item.setToolTip(tip)
                self.category_list.addItem(item)
            self.category_list.blockSignals(False)
        self._select_current()
        self._apply_library_state()

    def _select_current(self):
        self.category_list.blockSignals(True)
        for row in range(self.category_list.count()):
            item = self.category_list.item(row)
            if item.data(CATEGORY_ROLE) == self.current_category:
                self.category_list.setCurrentRow(row)
                break
        self.category_list.blockSignals(False)

    def category_texts(self):
        return [self.category_list.item(r).data(CATEGORY_ROLE)
                for r in range(self.category_list.count())]

    # ---------------------------------------------------------- the ingest
    def set_ingest_state(self, status_text="", visible=True):
        """Show the progress area with a message; it is never hidden mid-sentence (MED-021)."""
        if not hasattr(self, 'ingest_progress_area'):
            return
        self.ingest_progress_area.setVisible(bool(visible))
        if status_text:
            self.lbl_ingest_status.setText(status_text)

    def set_ingest_progress(self, percent, status_text):
        if not hasattr(self, 'ingest_progress_area'):
            return
        if percent is None or int(percent) < 0:
            # Still scanning: nothing to measure yet, so the bar moves on its
            # own instead of sitting at 0% (MED-037).
            self.progress_bar_ingest.setRange(0, 0)
        else:
            self.progress_bar_ingest.setRange(0, 100)
            self.progress_bar_ingest.setValue(int(percent))
        if status_text:
            self.lbl_ingest_status.setText(status_text)

    def set_ingest_running(self, running: bool):
        """
        Lock what must not happen during an ingest; keep Pause and Stop usable.

        A drop used to skip this, and a second ingest stopped the first
        (MED-022); and the old switch-off disabled the frame holding Pause and
        Stop while Ingest itself stayed live.
        """
        self._ingest_running = bool(running)
        if not self.can_ingest:
            return
        for widget in (self.btn_ingest, self.btn_refresh, self.btn_import, self.toggle_fast):
            widget.setEnabled(not running)
        self._apply_library_state()
        self.btn_pause.setEnabled(running)
        self.btn_stop.setEnabled(running)
        # When it ends only the last sentence stays: no full bar, no greyed
        # Pause/Stop (NEW-media-6).
        for widget in (self.progress_bar_ingest, self.btn_pause, self.btn_stop):
            widget.setVisible(bool(running))
        if running:
            self.set_pause_btn_text("Pause")
            self.ingest_progress_area.setVisible(True)
            self.progress_bar_ingest.setRange(0, 0)

    def ingest_running(self) -> bool:
        return self._ingest_running

    def set_pause_btn_text(self, text):
        """'Pause' or 'Resume', each with its drawn icon - one format (MED-036)."""
        if not hasattr(self, "btn_pause"):
            return
        text = str(text or "").replace("▶", "").replace("⏸", "").strip()
        resume = text.lower().startswith("resume")
        self.btn_pause.setText("Resume" if resume else "Pause")
        self.btn_pause.setIcon(draw_icon("play" if resume else "pause", Gate.TEXT, 16))

    # ---------------------------------------------------------- actions
    def set_selection_count(self, count: int):
        """'Delete (3)' - or 'Restore (3)' in Removed - enabled only with something selected (MED-033)."""
        self._selection = int(count or 0)
        self._apply_library_state()

    def set_root_count(self, count):
        """How many folders Rescan would look in (None: not known yet)."""
        self.root_count = count
        self._apply_library_state()

    def _apply_library_state(self):
        """
        Only what can do something is enabled: Clear and Export need assets,
        Rescan needs a folder to look in (MED2-020), and nothing of it while
        an ingest runs.
        """
        if not self.can_ingest or not hasattr(self, "btn_clear"):
            return
        running = self._ingest_running
        empty = not self.library_total
        for button, why_not in ((self.btn_clear, "The library is empty."),
                                (self.btn_export, "The library is empty.")):
            button.setEnabled(not running and not empty)
            button.setToolTip(why_not if empty else button.property("full_tip"))
        no_roots = self.root_count == 0
        self.btn_rescan.setEnabled(not running and not no_roots)
        self.btn_rescan.setToolTip("No folders are recorded yet: ingest a folder first."
                                   if no_roots else self.btn_rescan.property("full_tip"))
        restore = self.current_category == REMOVED
        noun = "Restore" if restore else "Delete"
        self.btn_delete_selected.setText(f"{noun} ({self._selection})" if self._selection > 1 else noun)
        if self.btn_delete_selected.property("kind") != ("secondary" if restore else "danger"):
            # Bringing back is not destructive: not in the danger red.
            style_button(self.btn_delete_selected, "secondary" if restore else "danger")
        self.btn_delete_selected.setIcon(draw_icon("undo" if restore else "trash", Gate.TEXT, 16))
        self.btn_delete_selected.setEnabled(self._selection > 0 and not running)

    def set_controls_enabled(self, enabled):
        """While the list loads: Reload waits. The ingest lock is separate."""
        self.btn_refresh.setEnabled(bool(enabled) and not self._ingest_running)

    def set_collapsed_visual(self, collapsed: bool):
        """One chevron, pointing the way it will move the panel (MED-065)."""
        if hasattr(self, "btn_collapse"):
            self.btn_collapse.setText("")
            self.btn_collapse.setIcon(draw_icon("chevron-right" if collapsed else "chevron-left",
                                                Gate.TEXT_2, 16))
            self.btn_collapse.setToolTip("Show the sidebar" if collapsed else "Hide the sidebar")

    def set_compact_mode(self, compact: bool):
        """Labels stay whole at every width; 'Clear library…' is never shortened (MED-014)."""
        if not self.can_ingest:
            return
        self.btn_ingest.setText("Ingest…" if compact else "Ingest a folder…")
        self.btn_rescan.setText("Rescan" if compact else "Rescan folders")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.set_compact_mode(self.width() < 230)
