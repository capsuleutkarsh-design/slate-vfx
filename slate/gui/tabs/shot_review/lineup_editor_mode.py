"""
The Timeline Viewer's lineup: a table of the shots, a player to watch them
in, RV, and the EDLs for editorial.

What changed, by finding:
  * The shots are a table - include box, reel, shot, frames, fps, layers,
    scan version - with a reel filter and a search; shots with no scan are
    listed greyed out with "no scan yet" instead of vanishing; unknown lengths
    and odd frame rates are marked (MED-087, MED-088, MED-089).
  * Reading the shot folders and planning proxies run on a thread with a
    busy line; the window does not freeze on a large show (MED-091).
  * Export writes only the ticked shots into <project>/editorial/lineups and
    reports in a dialog with "...and N more", Open folder and Copy path
    (MED-086, MED-092, MED-093). The last export is read from the written
    files, so it survives a restart (MED-105).
  * Proxies already made are not offered again; "Rebuild" is there when all
    are up to date (MED-090).
  * Olive is gone: it is no longer developed. "Open in RV" plays the ticked
    shots in edit order, and the EDL takes the lineup to Resolve, Premiere or
    Avid - both show the layer chosen beside the player.
"""

import datetime
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from slate.core.infra.gate import Gate
from ...core.controls import make_button, set_default_button
from .lineup_preview import LineupPreview

logger = logging.getLogger(__name__)

# Said to the person; the reason is in the log for IT (MED2-063).
RV_FAILED = "RV is not installed on this machine, or could not start - tell IT."


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

    def __init__(self, jobs, parent=None, overwrite=False, fps=24.0):
        super().__init__(parent)
        self.jobs = list(jobs or [])
        self.overwrite = overwrite
        self.fps = fps
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from slate.core.domain.proxy_builder import build
        result = build(self.jobs, overwrite=self.overwrite, fps=self.fps,
                       progress=lambda done, total, label: self.progress_signal.emit(done, total, label),
                       should_stop=lambda: self._stop)
        self.finished_signal.emit(result)


class ExportResultDialog(QDialog):
    """What the export wrote, the shots it left out (with '...and N more'), and where."""

    SHOW = 20

    def __init__(self, result, folder: Path, project_name: str, layer_note: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("EDL written")
        self.setMinimumWidth(520)
        self.folder = Path(folder)
        layout = QVBoxLayout(self)
        heading = QLabel(f"{project_name}: {result.summary()}")
        heading.setWordWrap(True)
        heading.setStyleSheet(f"font-weight: 600; color: {Gate.TEXT};")
        layout.addWidget(heading)
        layout.addWidget(QLabel(layer_note))
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
    """The lineup, its player, RV and the EDLs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.shots = []
        self.rows = []
        self.lineup = []
        self.project_name = ""
        self.project_path = None
        self.prefer_proxy_media = True
        self.project_root = None
        self.sequence_fps = 24.0
        self.folder_resolver = None
        self.last_result = None
        self.proxy_worker = None
        self._scan_job = None
        self._plan_job = None
        self.setup_ui()
        self._update_actions()

    # ------------------------------------------------------------- layout
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.status_label = QLabel("No shots loaded - click Refresh from Dashboard.")
        self.status_label.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600;")
        self.status_label.setWordWrap(True)
        bar.addWidget(self.status_label, 1)
        self.sync_time_label = QLabel("No EDL yet")
        self.sync_time_label.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        bar.addWidget(self.sync_time_label)
        layout.addLayout(bar)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_rv = make_button("Open in RV", "primary", icon="play",
                                  tooltip="Play the ticked shots in RV, in edit order, showing the "
                                          "layer chosen beside the player (the plate where a shot "
                                          "has no render of it yet)")
        self.btn_rv.clicked.connect(self.open_in_rv)
        self.btn_edl = make_button("Export EDL", "secondary", icon="timeline",
                                   tooltip="Write the ticked shots as EDLs for Resolve, Premiere or "
                                           "Avid: one per reel and one with every reel")
        self.btn_edl.clicked.connect(self.sync_lineup)
        self.btn_proxy = make_button("Make review proxies", "secondary", icon="film",
                                     tooltip="Make an MP4 beside each plate and render so RV and "
                                             "the player here play smoothly")
        self.btn_proxy.clicked.connect(self.build_proxies)
        self.chk_prefer_proxy = QCheckBox("Use proxies in RV")
        self.chk_prefer_proxy.setChecked(self.prefer_proxy_media)
        self.chk_prefer_proxy.setToolTip("Play the MP4 review proxies where they are up to date, "
                                         "for smoother playback in RV")
        self.chk_prefer_proxy.toggled.connect(self._on_prefer_proxy_toggled)
        for widget in (self.btn_rv, self.btn_edl, self.btn_proxy):
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

    # ------------------------------------------------------------- state
    def _set_status(self, text):
        self.status_label.setText(text)

    def _update_actions(self):
        has_lineup = bool(self.included_lineup()) and self._scan_job is None
        self.btn_rv.setEnabled(has_lineup)
        self.btn_edl.setEnabled(has_lineup)
        if self._plan_job is None and self.btn_proxy.text() == "Make review proxies":
            self.btn_proxy.setEnabled(has_lineup)   # a ticked shot is needed; as Stop it stays

    def set_project_context(self, project_name: str = "", project_path: Path = None):
        self.project_name = (project_name or "").strip()
        self.project_path = project_path
        # Another project: the last one's export is not this one's (MED2-040).
        self.last_result = None

    def set_project_source(self, project_root=None, folder_resolver=None, sequence_fps=24.0):
        """Where the shots' folders are, and the rate the project's sequences play at."""
        self.sequence_fps = float(sequence_fps or 24.0)
        self.preview.player.sequence_fps = self.sequence_fps
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

    # ------------------------------------------------------------- shots
    def set_shots(self, all_shots: list):
        """Take the dashboard's shots; read their folders on a thread (MED-091)."""
        from slate.core.domain.lineup import lineup_rows
        self.shots = list(all_shots or [])
        root, resolver = self.project_root, self.folder_resolver
        if not self.shots:
            self._fill([])
            return
        self._set_status(f"Looking for media of {len(self.shots)} shots…")
        self.table.setEnabled(False)
        fps = self.sequence_fps
        job = _Job(lambda: lineup_rows(self.shots, root, resolver, fps), self)
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
        from slate.core.domain.lineup import department_labels, fps_mismatches, lineup_fps, plural
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

    def _layer_note(self) -> str:
        """'Comp (the plate where a shot has no Comp yet)' - what RV and the EDL show."""
        layer, label = self.preview.layer(), self.preview.combo_layer.currentText() or "Scan"
        return label if layer == "scan" else f"{label} (the plate where a shot has no {label} yet)"

    # ------------------------------------------------------------- RV
    def open_in_rv(self):
        """Play the ticked shots in RV, in edit order, at the layer being watched."""
        from slate.core.domain.proxy_builder import proxy_is_current, proxy_path_for
        from slate.core.domain.rv_review import launch
        from ...components.feedback import toast, warn
        lineup = self.included_lineup()
        if not lineup:
            toast(self, "Tick at least one shot with a scan.", "warning")
            return False
        layer = self.preview.layer()
        paths = []
        for entry in lineup:
            clip = entry.clip_for(layer)
            use_proxy = self.prefer_proxy_media and proxy_is_current(clip, entry.name)
            paths.append(str(proxy_path_for(clip, entry.name) if use_proxy else clip.path))
        if not launch(paths):
            warn(self, "Open in RV", RV_FAILED)
            return False
        return True

    # ------------------------------------------------------------- EDL
    def _read_last_sync(self):
        """When an EDL of every reel was last written - from the files themselves (MED-105)."""
        from slate.core.domain.lineup import _safe_name, lineup_folder
        name = self._infer_project_name()
        folder = lineup_folder(self.project_root, name)
        try:
            stamps = [f.stat().st_mtime for f in folder.glob(f"{_safe_name(name)}_All_Reels_*.edl")]
        except OSError:
            stamps = []
        if stamps:
            stamp = datetime.datetime.fromtimestamp(max(stamps))
            self.sync_time_label.setText(f"EDL written {friendly_time(stamp)}")
        else:
            self.sync_time_label.setText("No EDL yet")

    def sync_lineup(self):
        """Write the ticked shots as EDLs: one per reel and one with every reel."""
        from slate.core.domain.lineup import generate_timelines, lineup_folder
        from ...components.feedback import toast
        lineup = self.included_lineup()
        if not lineup:
            toast(self, "No shots loaded - click Refresh from Dashboard." if not self.rows
                  else "Tick at least one shot with a scan.", "warning")
            return None
        project_name = self._infer_project_name()
        folder = lineup_folder(self.project_root, project_name)
        result = generate_timelines(self.shots, folder, project_name,
                                    project_root=self.project_root,
                                    folder_resolver=self.folder_resolver,
                                    layer=self.preview.layer(), lineup=lineup)
        self.last_result = result
        if not result.ok:
            toast(self, result.summary(), "error" if result.error else "warning")
            return result
        self._read_last_sync()
        ExportResultDialog(result, folder, project_name, f"Layer: {self._layer_note()}", self).exec()
        return result

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
        from slate.core.domain.lineup import plural
        from ...components.feedback import confirm
        self._plan_job = None
        self._update_actions()
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
        self.btn_proxy.setEnabled(True)
        self.proxy_worker = ProxyBuildWorker(jobs, self, overwrite=rebuild,
                                             fps=self.sequence_fps)
        self.proxy_worker.progress_signal.connect(self._on_proxy_progress)
        self.proxy_worker.finished_signal.connect(self._on_proxy_finished)
        self.proxy_worker.start()

    def _on_proxy_progress(self, done, total, label):
        self.proxy_status.setText(f"Proxy {done} of {total}: {label}")

    def _on_proxy_finished(self, result):
        from ...components.feedback import toast
        self.btn_proxy.setText("Make review proxies")
        self._update_actions()
        self.proxy_status.setText(result.summary())
        if result.error:
            toast(self, result.error, "error")
        elif result.failed:
            toast(self, result.summary(), "warning",
                  details="Not made:\n" + "\n".join(result.failed))
        elif result.built and not result.cancelled:
            toast(self, result.summary() + ".", "success")
        self.preview.show_shot(self.preview.index)

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
        self._stop_proxy_worker()
        self.cleanup_resources()
        super().closeEvent(event)
