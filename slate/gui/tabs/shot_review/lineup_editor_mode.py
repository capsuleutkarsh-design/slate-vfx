"""
The Timeline Viewer's lineup: a table of the shots, a player to watch them
in, and the bridge to Olive.

What changed, by finding:
  * The shots are a table - include box, reel, shot, frames, fps, layers,
    scan version - with a reel filter and a search; shots with no scan are
    listed greyed out with "no scan yet" instead of vanishing; unknown lengths
    and odd frame rates are marked (MED-087, MED-088, MED-089).
  * Reading the shot folders and planning proxies run on a thread with a
    busy line; the window does not freeze on a large show (MED-091).
  * Sync writes only the ticked shots into <project>/editorial/lineups and
    reports in a dialog with "...and N more", Open folder and Copy path
    (MED-086, MED-092, MED-093). Last sync is read from the written file, so
    it survives a restart (MED-105).
  * Proxies already made are not offered again; "Rebuild" is there when all
    are up to date (MED-090).
  * Launch: only once a lineup is written, disabled with a reason when Olive
    is not installed (MED-094, MED-095). It never kills an Olive somebody
    already has open (MED-080), and only a window of the Olive it started is
    ever taken into Slate (MED-081). "Back to lineup" leaves Olive running
    with a "Return to Olive" button; "Close Olive" asks first (MED-082). While
    Olive starts, the panel says so; if it cannot be embedded there are
    "Show Olive window" and "Try again" (MED-083). Re-sync while Olive is open
    offers to reload it (MED-096).
  * Styles are scoped, Sync is the primary action, red only for Close Olive,
    plain words and plurals, a toolbar instead of a column of slabs
    (MED-099 to MED-102). Win32 is only touched on Windows, when needed (MED-107).
"""

import datetime
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QEvent, QThread, QTimer, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from slate.core.infra.gate import Gate
from ....core.infra.global_config import GlobalConfig
from ...core.controls import make_button, set_default_button
from .lineup_preview import LineupPreview

logger = logging.getLogger(__name__)

# Windows API constants for embedding
GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_CHILD = 0x40000000
SWP_NOACTIVATE = 0x0010
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020

OLIVE_EXE = "olive-editor.exe"
# Said to the person: no developer script, no setting that is not there (MED2-048).
OLIVE_MISSING = "Olive is not installed on this machine - ask IT to install it."
EMBED_ATTEMPTS = 20          # x 500 ms


def _user32():
    """user32, only on Windows and only when asked for - importing this never fails."""
    if sys.platform != "win32":
        return None
    import ctypes
    return ctypes.windll.user32


def _olive_pids(root_pid: int) -> set:
    """The Olive process we started and its children."""
    pids = {int(root_pid)} if root_pid else set()
    try:
        import psutil
        for child in psutil.Process(int(root_pid)).children(recursive=True):
            pids.add(child.pid)
    except Exception:
        pass
    return pids


def find_olive_window(target_pid: int = 0, windows=None):
    """
    The visible top-level window of the Olive we launched, or None.

    Only windows owned by that process (or its children) count. The old
    fallbacks - any olive-editor.exe, any title containing "olive" - could
    swallow somebody's own Olive, or a browser tab called "Olive oil order"
    (MED-081). windows: optional [(hwnd, pid)] for tests.
    """
    if not target_pid:
        return None
    wanted = _olive_pids(target_pid)
    if windows is None:
        windows = _visible_windows()
    for hwnd, pid in windows:
        if int(pid) in wanted:
            return hwnd
    return None


def _visible_windows():
    user32 = _user32()
    if user32 is None:
        return []
    import ctypes
    from ctypes import wintypes
    found = []

    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append((hwnd, int(pid.value)))
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(proc(callback), 0)
    return found


def other_olive_running(own_pid: int = 0) -> bool:
    """Whether an Olive we did not start is open on this machine."""
    try:
        import psutil
        for proc in psutil.process_iter(["name", "pid"]):
            if (proc.info.get("name") or "").lower() == OLIVE_EXE and proc.info["pid"] != own_pid:
                return True
    except Exception:
        return False
    return False


def friendly_time(stamp: datetime.datetime, now: datetime.datetime = None) -> str:
    """'today 19:24', 'yesterday 09:05', '3 Oct 2026, 19:24'."""
    now = now or datetime.datetime.now()
    if stamp.date() == now.date():
        return f"today {stamp:%H:%M}"
    if stamp.date() == (now - datetime.timedelta(days=1)).date():
        return f"yesterday {stamp:%H:%M}"
    from slate.core.domain.dates import format_datetime
    return format_datetime(stamp)


class _Job(QThread):
    """Run fn() off the interface thread and hand back what it returned."""
    done = Signal(object, object)    # result, error

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(), None)
        except Exception as exc:
            logger.exception("Timeline background job failed: %s", exc)
            self.done.emit(None, exc)


class ProxyBuildWorker(QThread):
    """
    Makes review proxies in the background.

    Cancelling is checked between shots rather than mid-file, so stopping never
    leaves a half-written MP4 that looks like a real proxy.
    """

    progress_signal = Signal(int, int, str)
    finished_signal = Signal(object)

    def __init__(self, jobs, parent=None, overwrite=False):
        super().__init__(parent)
        self.jobs = list(jobs or [])
        self.overwrite = overwrite
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from slate.core.domain.proxy_builder import build
        result = build(self.jobs, overwrite=self.overwrite,
                       progress=lambda done, total, label: self.progress_signal.emit(done, total, label),
                       should_stop=lambda: self._stop)
        self.finished_signal.emit(result)


class SyncResultDialog(QDialog):
    """What Sync wrote, the shots it left out (with '...and N more'), and where."""

    SHOW = 20

    def __init__(self, result, folder: Path, project_name: str, olive_open: bool, parent=None,
                 olive_installed: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Lineup written")
        self.setMinimumWidth(520)
        self.folder = Path(folder)
        layout = QVBoxLayout(self)
        heading = QLabel(f"{project_name}: {result.summary()}")
        heading.setWordWrap(True)
        heading.setStyleSheet(f"font-weight: 600; color: {Gate.TEXT};")
        layout.addWidget(heading)
        where = QLabel(f"Written to {self.folder}")
        where.setWordWrap(True)
        where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        where.setStyleSheet(f"color: {Gate.TEXT_2};")
        layout.addWidget(where)
        self.skipped_text = ""
        if result.skipped:
            shown = result.skipped[:self.SHOW]
            more = len(result.skipped) - len(shown)
            text = ", ".join(shown) + (f" … and {more} more" if more else "")
            self.skipped_text = text
            layout.addWidget(QLabel("No scan on disk yet, so left out:"))
            box = QPlainTextEdit(text)
            box.setReadOnly(True)
            box.setMaximumHeight(90)
            box.setFont(self.font())          # the dialog's font, not a code font
            layout.addWidget(box)
            if more:
                self.btn_all = make_button("Show all", "ghost",
                                           on_click=lambda: box.setPlainText(", ".join(result.skipped)))
                layout.addWidget(self.btn_all, 0, Qt.AlignmentFlag.AlignLeft)
        if olive_open:
            layout.addWidget(QLabel("Olive still shows the previous version until it is reloaded."))
        elif olive_installed:
            layout.addWidget(QLabel("Launch Olive to open the combined timeline."))
        row = QHBoxLayout()
        self.btn_open = make_button("Open folder", "secondary", icon="folder",
                                    on_click=lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder))))
        self.btn_copy = make_button("Copy path", "secondary", icon="copy",
                                    on_click=lambda: QApplication.clipboard().setText(str(self.folder)))
        self.btn_close = make_button("Close", "primary", on_click=self.accept)
        row.addWidget(self.btn_open)
        row.addWidget(self.btn_copy)
        row.addStretch()
        row.addWidget(self.btn_close)
        layout.addLayout(row)
        set_default_button(self, self.btn_close)


INCLUDE, REEL, SHOT, FRAMES, FPS, LAYERS, VERSION = range(7)
HEADERS = ("", "Reel", "Shot", "Frames", "FPS", "Layers", "Scan version")
ROW_ROLE = Qt.ItemDataRole.UserRole + 10


class LineupEditorMode(QWidget):
    """The lineup, its player, and Olive."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.shots = []
        self.rows = []
        self.lineup = []
        self.olive_process = None
        self.olive_hwnd = None
        self.embed_timer = None
        self.health_timer = None
        self.embed_attempts = 0
        self.project_name = ""
        self.project_path = None
        self.prefer_proxy_media = True
        self.project_root = None
        self.folder_resolver = None
        self.last_result = None
        self.output_path = None
        self.proxy_worker = None
        self._scan_job = None
        self._plan_job = None
        self._olive_path = self._find_olive_executable()
        self.setup_ui()
        self._update_actions()

    # ------------------------------------------------------------- layout
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # The Olive stage: takes all the room while Olive is shown.
        self.olive_container = QFrame()
        self.olive_container.setObjectName("OliveStage")
        self.olive_container.setStyleSheet(f"QFrame#OliveStage {{ background: {Gate.GROUND}; }}")
        self.olive_container.hide()
        self.olive_container.installEventFilter(self)
        stage = QVBoxLayout(self.olive_container)
        stage.addStretch(1)
        self.stage_label = QLabel("Starting Olive…")
        self.stage_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stage_label.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 14px;")
        stage.addWidget(self.stage_label)
        stage_buttons = QHBoxLayout()
        stage_buttons.addStretch()
        self.btn_show_window = make_button("Show Olive window", "secondary",
                                           on_click=self._show_olive_unembedded)
        self.btn_retry_embed = make_button("Try again", "primary", on_click=self._retry_embed)
        stage_buttons.addWidget(self.btn_show_window)
        stage_buttons.addWidget(self.btn_retry_embed)
        stage_buttons.addStretch()
        stage.addLayout(stage_buttons)
        stage.addStretch(1)
        self.btn_show_window.hide()
        self.btn_retry_embed.hide()
        layout.addWidget(self.olive_container, 1)

        self.dashboard_widget = QWidget()
        self.setup_dashboard(self.dashboard_widget)
        layout.addWidget(self.dashboard_widget, 1)

        self.compact_toolbar = QWidget()
        self.compact_toolbar.setObjectName("OliveToolbar")
        self.compact_toolbar.hide()
        self.setup_compact_toolbar(self.compact_toolbar)
        layout.addWidget(self.compact_toolbar, 0)

    def setup_dashboard(self, parent):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.status_label = QLabel("No shots loaded - click Refresh from Dashboard.")
        self.status_label.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600;")
        self.status_label.setWordWrap(True)
        bar.addWidget(self.status_label, 1)
        self.sync_time_label = QLabel("Not synced yet")
        self.sync_time_label.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        bar.addWidget(self.sync_time_label)
        layout.addLayout(bar)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_sync = make_button("Sync to Olive", "primary", icon="timeline",
                                    tooltip="Write the ticked shots as Olive timelines: one per "
                                            "reel and one with every reel")
        self.btn_sync.clicked.connect(self.sync_lineup)
        self.btn_proxy = make_button("Make review proxies", "secondary", icon="film",
                                     tooltip="Make an MP4 beside each plate and render so Olive, "
                                             "RV and the player here play smoothly")
        self.btn_proxy.clicked.connect(self.build_proxies)
        self.btn_launch = make_button("Launch Olive", "secondary", icon="external")
        self.btn_launch.clicked.connect(self.launch_olive)
        self.btn_return = make_button("Return to Olive", "secondary", icon="arrow-right",
                                      tooltip="Olive is still open")
        self.btn_return.clicked.connect(self.return_to_olive)
        self.btn_return.hide()
        self.chk_prefer_proxy = QCheckBox("Use proxies in Olive")
        self.chk_prefer_proxy.setChecked(self.prefer_proxy_media)
        self.chk_prefer_proxy.setToolTip("Use the MP4 review proxies where they exist, for "
                                         "smoother playback in Olive")
        self.chk_prefer_proxy.toggled.connect(self._on_prefer_proxy_toggled)
        for widget in (self.btn_sync, self.btn_proxy, self.btn_launch, self.btn_return):
            actions.addWidget(widget)
        actions.addWidget(self.chk_prefer_proxy)
        actions.addStretch(1)
        self.proxy_status = QLabel("")
        self.proxy_status.setStyleSheet(f"color: {Gate.TEXT_2};")
        actions.addWidget(self.proxy_status)
        layout.addLayout(actions)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.combo_reel = QComboBox()
        self.combo_reel.addItem("All reels", "")
        self.combo_reel.currentIndexChanged.connect(self._apply_filter)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search shots…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.btn_all = make_button("Tick all", "ghost", on_click=lambda: self._tick_all(True))
        self.btn_none = make_button("Untick all", "ghost", on_click=lambda: self._tick_all(False))
        filters.addWidget(self.combo_reel)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.btn_all)
        filters.addWidget(self.btn_none)
        layout.addLayout(filters)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        from ...core.table_style import style_table
        # The shot name always shows whole; Layers takes what is left and
        # elides (NEW-media-5: at 1280 every row read "SEQ01...").
        style_table(self.table, {0: ("fixed", 34), "Reel": "contents", "Shot": "contents",
                                 "Frames": "contents", "FPS": "contents", "Layers": "stretch",
                                 "Scan version": "contents"}, sortable=False)
        self.table.horizontalHeader().setMinimumSectionSize(40)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.currentCellChanged.connect(lambda row, *_: self._preview_row(row))
        # Back-compat name: the list of shots.
        self.shot_list = self.table
        splitter.addWidget(self.table)
        self.preview = LineupPreview()
        self.preview.shot_changed.connect(self._select_entry)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([560, 520])
        layout.addWidget(splitter, 1)

    def setup_compact_toolbar(self, parent):
        """Slim bar shown while Olive is on screen."""
        parent.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        parent.setStyleSheet(f"QWidget#OliveToolbar {{ background: {Gate.PANEL}; "
                             f"border-top: 1px solid {Gate.LINE}; }}")
        layout = QHBoxLayout(parent)
        layout.setContentsMargins(10, 6, 10, 6)
        self.compact_status = QLabel("Olive")
        self.compact_status.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600;")
        layout.addWidget(self.compact_status)
        layout.addStretch()
        self.btn_sync_mini = make_button("Re-sync", "primary", icon="refresh",
                                         tooltip="Write the timelines again from the current shots",
                                         on_click=self.sync_lineup)
        self.btn_back = make_button("Back to lineup", "secondary", icon="arrow-left",
                                    tooltip="Olive keeps running; Return to Olive brings it back",
                                    on_click=self.back_to_lineup)
        self.btn_close_olive = make_button("Close Olive", "danger", icon="close",
                                           on_click=self.close_olive)
        for widget in (self.btn_sync_mini, self.btn_back, self.btn_close_olive):
            layout.addWidget(widget)

    # ------------------------------------------------------------- state
    def _set_status(self, text):
        self.status_label.setText(text)
        self.compact_status.setText(text)

    def olive_running(self) -> bool:
        return bool(self.olive_process is not None and self.olive_process.poll() is None)

    def _update_actions(self):
        has_lineup = bool(self.included_lineup())
        self.btn_sync.setEnabled(has_lineup and self._scan_job is None)
        self.btn_proxy.setEnabled(bool(self.lineup) and self._scan_job is None)
        written = bool(self.output_path and Path(self.output_path).exists())
        if not self._olive_path:
            self.btn_launch.setEnabled(False)
            self.btn_launch.setToolTip(OLIVE_MISSING)
        else:
            self.btn_launch.setEnabled(written and not self.olive_running())
            self.btn_launch.setToolTip("Open the combined timeline in Olive" if written else
                                       "Sync to Olive first - there is no timeline to open yet")
        self.btn_return.setVisible(self.olive_running() and not self.olive_container.isVisible())

    def set_project_context(self, project_name: str = "", project_path: Path = None):
        self.project_name = (project_name or "").strip()
        self.project_path = project_path
        # Another project: the last one's timeline is not this one's (MED2-040).
        self.output_path = None
        self.last_result = None

    def set_project_source(self, project_root=None, folder_resolver=None):
        self.project_root = project_root
        self.folder_resolver = folder_resolver

    def _on_prefer_proxy_toggled(self, checked: bool):
        self.prefer_proxy_media = bool(checked)

    def _infer_project_name(self) -> str:
        if self.project_name:
            return self.project_name
        if self.project_path:
            try:
                return Path(self.project_path).name
            except Exception:
                pass
        for shot in self.shots:
            name = getattr(shot, "project_name", "") or ""
            if name:
                return name
        return "Project"

    def _sanitize_project_key(self, name: str) -> str:
        clean = "".join(ch if (ch.isalnum() or ch in ("_", "-")) else "_" for ch in (name or "").strip())
        return clean.strip("._ ") or "Project"

    # ------------------------------------------------------------- shots
    def set_shots(self, all_shots: list):
        """Take the dashboard's shots; read their folders on a thread (MED-091)."""
        from slate.core.domain.olive_lineup import lineup_rows
        self.shots = list(all_shots or [])
        root, resolver = self.project_root, self.folder_resolver
        if not self.shots:
            self._fill([])
            return
        self._set_status(f"Looking for media of {len(self.shots)} shots…")
        self.table.setEnabled(False)
        job = _Job(lambda: lineup_rows(self.shots, root, resolver), self)
        job.done.connect(self._on_rows)
        job.finished.connect(job.deleteLater)
        self._scan_job = job
        self._update_actions()
        job.start()

    def wait_until_ready(self, timeout_ms=10000) -> bool:
        """For callers that need the list filled (tests, the quick command)."""
        job = self._scan_job
        if job is not None:
            job.wait(timeout_ms)
            QApplication.processEvents()
        return self._scan_job is None

    def _on_rows(self, rows, error):
        self._scan_job = None
        self.table.setEnabled(True)
        if error is not None:
            self._fill([])
            # After the fill, which says "no shots loaded": a share that cannot
            # be read is not an empty project (MED2-045).
            self._set_status("The shot folders could not be read - check that the project "
                             "share is reachable, then Refresh from Dashboard.")
            return
        self._fill(rows or [])

    def _fill(self, rows):
        from slate.core.domain.olive_lineup import department_labels, fps_mismatches, lineup_fps, plural
        from ...core.table_style import dim_cell, set_cell_status
        self.rows = list(rows)
        self.lineup = [r.entry for r in self.rows if r.entry is not None]
        rate = lineup_fps(self.lineup)
        odd = set(fps_mismatches(self.lineup))
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        reels = []
        for i, row in enumerate(self.rows):
            entry = row.entry
            if row.reel not in reels:
                reels.append(row.reel)
            include = QTableWidgetItem()
            include.setData(ROW_ROLE, i)
            cells = [row.reel or "—", row.name]
            if entry is None:
                include.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                cells += ["no scan yet", "—", "—", "—"]
            else:
                include.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                                 | Qt.ItemFlag.ItemIsUserCheckable)
                include.setCheckState(Qt.CheckState.Checked)
                fps = f"{entry.fps:g}"
                cells += [entry.frames_text(), fps, department_labels(entry.layers),
                          entry.scan_version or "—"]
            self.table.setItem(i, INCLUDE, include)
            for col, text in enumerate(cells, start=1):
                item = QTableWidgetItem(str(text))
                item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                if entry is None or (col == FRAMES and not entry.length_known):
                    dim_cell(item)
                if col == FPS and entry is not None and entry.name in odd:
                    set_cell_status(item, "warn", background=False)
                    item.setToolTip(f"This shot runs at {entry.fps:g} fps; the lineup is {rate:g}.")
                if col == FRAMES and entry is not None and not entry.length_known:
                    item.setToolTip("No frame range is known yet; a placeholder length is used.")
                self.table.setItem(i, col, item)
        self.table.blockSignals(False)

        self.combo_reel.blockSignals(True)
        current = self.combo_reel.currentData()
        self.combo_reel.clear()
        self.combo_reel.addItem("All reels", "")
        for reel in reels:
            self.combo_reel.addItem(reel or "No reel", reel)
        index = self.combo_reel.findData(current)
        self.combo_reel.setCurrentIndex(max(0, index))
        self.combo_reel.blockSignals(False)
        self._apply_filter()

        missing = len(self.rows) - len(self.lineup)
        if not self.rows:
            self._set_status("No shots loaded - click Refresh from Dashboard.")
        elif not self.lineup:
            self._set_status(f"None of the {plural(len(self.rows), 'shot')} has a scan on disk yet.")
        else:
            text = (f"{plural(len(self.lineup), 'shot')} across "
                    f"{plural(len({e.reel for e in self.lineup}), 'reel')}")
            if missing:
                text += f" · {plural(missing, 'shot')} without a scan"
            if odd:
                text += f" · {plural(len(odd), 'shot')} not at {rate:g} fps"
            self._set_status(f"{text} ({self._infer_project_name()})")
        self.preview.set_lineup(self.included_lineup())
        self._read_last_sync()
        self._update_actions()

    def _apply_filter(self, *_):
        reel = self.combo_reel.currentData() or ""
        words = self.search.text().strip().lower().split()
        for i, row in enumerate(self.rows):
            show = (not reel or row.reel == reel) and all(w in row.name.lower() for w in words)
            self.table.setRowHidden(i, not show)

    def _tick_all(self, on):
        self.table.blockSignals(True)
        for i, row in enumerate(self.rows):
            item = self.table.item(i, INCLUDE)
            if row.entry is not None and not self.table.isRowHidden(i):
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._on_item_changed(None)

    def _on_item_changed(self, item):
        if item is not None and item.column() != INCLUDE:
            return
        self.preview.set_lineup(self.included_lineup())
        self._update_actions()

    def included_lineup(self):
        """The shots with a scan that are ticked, in edit order."""
        out = []
        for i, row in enumerate(self.rows):
            item = self.table.item(i, INCLUDE) if i < self.table.rowCount() else None
            if row.entry is not None and item is not None and item.checkState() == Qt.CheckState.Checked:
                out.append(row.entry)
        return out

    def _preview_row(self, row):
        if not (0 <= row < len(self.rows)) or self.rows[row].entry is None:
            return
        entry = self.rows[row].entry
        lineup = self.preview.entries
        if entry in lineup:
            index = lineup.index(entry)
            if index != self.preview.index:
                self.preview.show_shot(index)

    def _select_entry(self, index):
        if not (0 <= index < len(self.preview.entries)):
            return
        entry = self.preview.entries[index]
        for i, row in enumerate(self.rows):
            if row.entry is entry and self.table.currentRow() != i:
                self.table.blockSignals(True)
                self.table.setCurrentCell(i, SHOT)
                self.table.blockSignals(False)
                break

    # ------------------------------------------------------------- sync
    def _read_last_sync(self):
        """When the combined timeline was last written - from the file itself (MED-105)."""
        from slate.core.domain.olive_lineup import _safe_name, lineup_folder
        folder = lineup_folder(self.project_root, self._infer_project_name())
        combined = folder / f"{_safe_name(self._infer_project_name())}_All_Reels.ovexml"
        try:
            if combined.exists():
                stamp = datetime.datetime.fromtimestamp(combined.stat().st_mtime)
                self.sync_time_label.setText(f"Synced {friendly_time(stamp)}")
                self.output_path = combined
                return
        except OSError:
            pass
        self.sync_time_label.setText("Not synced yet")
        self.output_path = None
        self.last_result = None

    def sync_lineup(self):
        """Write the ticked shots: one timeline per reel and one with every reel."""
        from slate.core.domain.olive_lineup import generate_timelines, lineup_folder
        from ...components.feedback import show_error, toast
        lineup = self.included_lineup()
        if not lineup:
            toast(self, "No shots loaded - click Refresh from Dashboard." if not self.rows
                  else "Tick at least one shot with a scan.", "warning")
            return None
        project_name = self._infer_project_name()
        folder = lineup_folder(self.project_root, project_name)
        try:
            result = generate_timelines(self.shots, folder, project_name,
                                        project_root=self.project_root,
                                        folder_resolver=self.folder_resolver,
                                        prefer_proxy_media=self.prefer_proxy_media,
                                        lineup=lineup)
        except Exception as e:
            logger.error("Sync failed: %s", e, exc_info=True)
            show_error(self, "The lineup could not be written.", exc=e)
            return None
        self.last_result = result
        if not result.ok:
            toast(self, result.summary(), "warning")
            return result
        self.output_path = result.combined or next(iter(result.per_reel.values()), None)
        self._read_last_sync()
        self._update_actions()
        olive_open = self.olive_running()
        SyncResultDialog(result, folder, project_name, olive_open, self,
                         olive_installed=bool(self._olive_path)).exec()
        if olive_open and self._ask_reload():
            self.reload_olive()
        return result

    def _ask_reload(self) -> bool:
        from ...components.feedback import confirm
        return confirm(self, "Reload in Olive", "Reload the new timeline in Olive?",
                       yes_label="Reload", no_label="Not now",
                       informative="Olive is closed and opened again with the new file. "
                                   "Save anything you changed in Olive first.")

    # ------------------------------------------------------------- proxies
    def build_proxies(self, rebuild=False):
        """Plan (on a thread) and make the MP4 proxies of the ticked shots."""
        from slate.core.domain.proxy_builder import plan
        from ...components.feedback import toast
        if self.proxy_worker is not None and self.proxy_worker.isRunning():
            self.proxy_worker.stop()
            self.btn_proxy.setText("Stopping…")
            self.btn_proxy.setEnabled(False)
            return
        if not self.shots:
            toast(self, "Load a project first - there are no shots to make proxies for.", "info")
            return
        names = {e.name for e in self.included_lineup()}
        shots = [s for s in self.shots if str(getattr(s, "shot_name", "")) in names]
        root, resolver = self.project_root, self.folder_resolver
        self.proxy_status.setText("Looking for what needs a proxy…")
        self.btn_proxy.setEnabled(False)
        job = _Job(lambda: plan(shots, root, resolver, rebuild=rebuild), self)
        job.done.connect(lambda jobs, error: self._on_plan(jobs or [], error, rebuild))
        job.finished.connect(job.deleteLater)
        self._plan_job = job
        job.start()

    def _on_plan(self, jobs, error, rebuild):
        from slate.core.domain.olive_lineup import plural
        from ...components.feedback import confirm
        self._plan_job = None
        self.btn_proxy.setEnabled(True)
        if error is not None:
            self.proxy_status.setText("The shot folders could not be read.")
            return
        if not jobs:
            self.proxy_status.setText("All proxies are up to date.")
            if not rebuild and confirm(self, "Make review proxies",
                                       "Every proxy for the ticked shots is already there.",
                                       yes_label="Rebuild all", no_label="Close"):
                self.build_proxies(rebuild=True)
            return
        count = plural(len(jobs), "proxy", "proxies")
        if not confirm(self, "Make review proxies", f"Make {count}?",
                       yes_label=f"Make {count}", no_label="Cancel",
                       informative="ffmpeg runs over each plate and render; a full reel takes a "
                                   "while. You can stop at any point."):
            self.proxy_status.setText("")
            return
        self.btn_proxy.setText("Stop making proxies")
        self.proxy_worker = ProxyBuildWorker(jobs, self, overwrite=rebuild)
        self.proxy_worker.progress_signal.connect(self._on_proxy_progress)
        self.proxy_worker.finished_signal.connect(self._on_proxy_finished)
        self.proxy_worker.start()

    def _on_proxy_progress(self, done, total, label):
        self.proxy_status.setText(f"Proxy {done} of {total}: {label}")

    def _on_proxy_finished(self, result):
        from ...components.feedback import toast
        self.btn_proxy.setText("Make review proxies")
        self.btn_proxy.setEnabled(True)
        self.proxy_status.setText(result.summary())
        if result.error:
            toast(self, result.error, "error")
        elif result.failed:
            toast(self, result.summary(), "warning",
                  details="Not made:\n" + "\n".join(result.failed))
        elif result.built and not result.cancelled:
            toast(self, result.summary() + ". Re-sync to have Olive use them.", "success")
        self.preview.show_shot(self.preview.index)

    # ------------------------------------------------------------- Olive
    def _find_olive_executable(self):
        """Find olive-editor.exe using config/env + install locations."""
        candidates = []
        configured = GlobalConfig.get("olive_path")
        if configured:
            candidates.append(configured)
        env_path = os.environ.get("OLIVE_EDITOR_PATH") or os.environ.get("OLIVE_EDITOR")
        if env_path:
            candidates.append(env_path)
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
            candidates.append(os.path.join(base_dir, "_internal", "olive-editor", OLIVE_EXE))
            if hasattr(sys, '_MEIPASS'):
                candidates.append(os.path.join(sys._MEIPASS, "olive-editor", OLIVE_EXE))
            candidates.append(os.path.join(base_dir, "olive-editor", OLIVE_EXE))
            candidates.append(os.path.join(base_dir, "..", "olive-editor", OLIVE_EXE))
        else:
            module_root = Path(__file__).resolve().parents[3]
            project_root = module_root.parent
            candidates.extend([
                str(project_root / "external" / "olive-editor" / OLIVE_EXE),
                str(module_root / "external" / "olive-editor" / OLIVE_EXE),
                str(project_root / "olive-editor" / OLIVE_EXE),
            ])
        candidates.append(str(Path.home() / "AppData/Local/Slate Production/olive-editor" / OLIVE_EXE))
        candidates.append(str(Path.home() / "AppData/Local/Slate/olive-editor" / OLIVE_EXE))
        system_path = shutil.which("olive-editor") or shutil.which(OLIVE_EXE)
        if system_path:
            candidates.append(system_path)
        for candidate in candidates:
            try:
                if Path(candidate).exists():
                    return Path(candidate)
            except OSError:
                continue
        logger.info("Olive not found. Tried: %s", candidates)
        return None

    def launch_olive(self):
        """Open the combined timeline in Olive, embedded in this tab."""
        from ...components.feedback import confirm, show_error, warn
        if not self._olive_path:
            warn(self, "Launch Olive", OLIVE_MISSING)
            return
        if not (self.output_path and Path(self.output_path).exists()):
            if not confirm(self, "Launch Olive", "There is no timeline to open yet.",
                           yes_label="Sync and launch", no_label="Cancel"):
                return
            if not self.sync_lineup() or not self.output_path:
                return
        if self.olive_running():
            self.return_to_olive()
            return
        # Somebody's own Olive is never closed for them (MED-080).
        if other_olive_running() and not confirm(
                self, "Launch Olive", "Olive is already open on this machine.",
                yes_label="Open another", no_label="Cancel",
                informative="Slate opens a second Olive for the lineup; the one already open "
                            "is left as it is."):
            return
        env = os.environ.copy()
        for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_FONTS_PATH",
                    "PYTHONPATH", "PYTHONHOME"):
            env.pop(key, None)
        args = [str(self._olive_path), str(self.output_path)]
        try:
            logger.info("Launching Olive: %s", args)
            self.olive_process = subprocess.Popen(args, cwd=self._olive_path.parent, env=env)
        except Exception as e:
            logger.error("Launch failed: %s", e)
            show_error(self, "Olive could not be started.", exc=e)
            return
        self._show_stage("Starting Olive…")
        self._start_embedding()

    def _show_stage(self, message):
        self.dashboard_widget.hide()
        self.compact_toolbar.show()
        self.olive_container.show()
        self.stage_label.setText(message)
        self.stage_label.show()
        self._set_status(message)
        self._update_actions()

    def _start_embedding(self):
        self.embed_attempts = 0
        self.btn_show_window.hide()
        self.btn_retry_embed.hide()
        if self.embed_timer is None:
            self.embed_timer = QTimer(self)
            self.embed_timer.timeout.connect(self.try_embed_olive)
        self.embed_timer.start(500)

    def try_embed_olive(self):
        """Poll for the Olive window we started, and take it in."""
        self.embed_attempts += 1
        proc = self.olive_process
        if proc is not None and proc.poll() is not None:
            rc = proc.returncode
            self.embed_timer.stop()
            self.olive_process = None
            self._olive_gone(f"Olive closed while starting (code {rc}).")
            return
        if self.embed_attempts > EMBED_ATTEMPTS:
            self.embed_timer.stop()
            text = "Olive is running, but its window could not be brought into Slate."
            self.stage_label.setText(text)
            self._set_status("Could not embed Olive")
            self.btn_show_window.show()
            self.btn_retry_embed.show()
            return
        if proc is None:
            self.embed_timer.stop()
            return
        hwnd = find_olive_window(target_pid=proc.pid)
        if hwnd:
            self.embed_timer.stop()
            self.stage_label.hide()
            self.embed_window(hwnd)
            self._set_status(f"Olive: {Path(str(self.output_path)).name}")
            self._start_health_monitor()

    def _retry_embed(self):
        self.stage_label.setText("Looking for the Olive window…")
        self._start_embedding()

    def _show_olive_unembedded(self):
        """Leave Olive in its own window; back to the lineup here."""
        self.btn_show_window.hide()
        self.btn_retry_embed.hide()
        self.back_to_lineup()

    def _start_health_monitor(self):
        if self.health_timer is None:
            self.health_timer = QTimer(self)
            self.health_timer.timeout.connect(self._check_olive_health)
        self.health_timer.start(2000)

    def _check_olive_health(self):
        proc = self.olive_process
        if not proc:
            return
        rc = proc.poll()
        if rc is None:
            return
        if self.health_timer:
            self.health_timer.stop()
        self.olive_process = None
        self.olive_hwnd = None
        self._olive_gone("Olive has closed." if rc == 0 else f"Olive closed unexpectedly (code {rc}).")

    def _olive_gone(self, message):
        from ...components.feedback import toast
        self._switch_to_dashboard_ui()
        self._fill(self.rows)
        toast(self, message, "info" if "unexpectedly" not in message else "warning")

    def embed_window(self, hwnd):
        """Take Olive's window into the stage (Windows only)."""
        user32 = _user32()
        if user32 is None:
            return
        self.olive_hwnd = hwnd
        user32.SetParent(hwnd, int(self.olive_container.winId()))
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        style = style & ~WS_CAPTION & ~WS_THICKFRAME | WS_CHILD
        user32.SetWindowLongW(hwnd, GWL_STYLE, style)
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FRAMECHANGED | SWP_NOACTIVATE | SWP_NOZORDER)
        self.resize_embedded()

    def back_to_lineup(self):
        """Show the lineup again; Olive keeps running (MED-082)."""
        self._switch_to_dashboard_ui()
        self._update_actions()

    def return_to_olive(self):
        if not self.olive_running():
            self._update_actions()
            return
        self.dashboard_widget.hide()
        self.compact_toolbar.show()
        self.olive_container.show()
        if self.olive_hwnd:
            self.stage_label.hide()
            self.resize_embedded()
        self._update_actions()

    def close_olive(self):
        from ...components.feedback import confirm
        if not confirm(self, "Close Olive", "Close Olive?", yes_label="Close Olive",
                       no_label="Keep it open", destructive=True,
                       informative="Save your work in Olive first - anything unsaved is lost."):
            return
        self._stop_olive_process()
        self._switch_to_dashboard_ui()
        self._update_actions()

    def reload_olive(self):
        """Close our Olive and open it again with the new timeline (MED-096)."""
        self._stop_olive_process()
        self.launch_olive()

    # Kept for older callers.
    def return_to_dashboard(self):
        self.back_to_lineup()

    def _switch_to_dashboard_ui(self):
        self.olive_container.hide()
        self.compact_toolbar.hide()
        self.dashboard_widget.show()

    def _stop_olive_process(self, timeout_s: int = 3):
        """Stop the Olive we started (never anyone else's)."""
        if self.embed_timer and self.embed_timer.isActive():
            self.embed_timer.stop()
        if self.health_timer and self.health_timer.isActive():
            self.health_timer.stop()
        proc = self.olive_process
        if not proc:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    logger.warning("Olive process did not exit in %ss; killing.", timeout_s)
                    proc.kill()
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        logger.warning("Olive process kill wait timed out.")
        except Exception as exc:
            logger.warning("Failed to stop Olive process cleanly: %s", exc)
        finally:
            self.olive_process = None
            self.olive_hwnd = None

    def eventFilter(self, obj, event):
        if obj is self.olive_container and event.type() == QEvent.Type.Resize:
            self.resize_embedded()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_embedded()

    def resize_embedded(self):
        user32 = _user32()
        if user32 and self.olive_hwnd and self.olive_container.isVisible():
            dpr = self.olive_container.devicePixelRatio()
            user32.MoveWindow(self.olive_hwnd, 0, 0, int(self.olive_container.width() * dpr),
                              int(self.olive_container.height() * dpr), True)

    # ------------------------------------------------------------- lifetime
    def busy_reason(self):
        worker = self.proxy_worker
        if worker is not None and worker.isRunning():
            return "The Timeline Viewer is still making review proxies."
        return self.preview.busy_reason()

    def shutdown(self, timeout_ms=15000) -> bool:
        self._stop_proxy_worker(timeout_ms)
        return not (self.proxy_worker is not None and self.proxy_worker.isRunning())

    def _stop_proxy_worker(self, timeout_ms=5000):
        worker = getattr(self, "proxy_worker", None)
        if worker is not None and worker.isRunning():
            worker.stop()
            worker.wait(timeout_ms)

    def cleanup_resources(self):
        for job in (self._scan_job, self._plan_job):
            if job is not None:
                job.wait(5000)
        self.preview.cleanup()

    def closeEvent(self, event):
        if self.embed_timer is not None and self.embed_timer.isActive():
            self.embed_timer.stop()
        if self.health_timer and self.health_timer.isActive():
            self.health_timer.stop()
        self._stop_proxy_worker()
        self._stop_olive_process()
        self.cleanup_resources()
        super().closeEvent(event)
