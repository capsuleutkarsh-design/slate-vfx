"""
Build & Ingest: turn a client drive into a project, and keep feeding it.

The order of a run:

1. **Check.** The project code, the projects folder and the client drive are
   checked as they are typed; the button stays off, with the reason beside
   the field, until they make sense. Nothing is changed silently.
2. **Survey.** The drive is walked once, in the background (Stop cancels it).
3. **Stitches.** Folders that look like parts of one shot are offered for
   merging.
4. **Pre-flight.** A summary of exactly what will happen - copy or move, every
   shot and the name it gets, anything odd - and nothing starts without it.
5. **Run.** The worker does what the pre-flight showed. Pause and Stop act
   between files. The progress, the status and the buttons stay on screen.
6. **Result.** A last-run line that stays, the delivery report one click
   away, and "Retry failed files" for whatever did not make it - also after a
   restart.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import html
import logging

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QRadioButton, QScrollArea, QSplitter, QTextEdit, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ...core.infra.config_manager import ConfigManager
from ...core.worker_threads import FolderCreationWorker
from ...core.workers.structure import DEFAULT_SHOT_FOLDERS, DEFAULT_VERSION_FOLDERS, LOCK_TOUCH_SECONDS
from ...core.domain.ingest_survey import client_folder_for
from ...core.domain.naming import name_problem, shot_name_problem
from ...utils.text_utils import normalize_name
from ...gui.dialogs.custom_template_dialog import CustomTemplateDialog, template_key
from ...gui.dialogs.stitch_confirm_dialog import StitchConfirmDialog
from ...gui.dialogs.ingest_preflight_dialog import IngestPreflightDialog, size_text
from slate.core.infra.gate import Gate
from slate.gui.core.controls import form_layout, make_button, plain

logger = logging.getLogger(__name__)

IDLE_STATUS = "Ready"
NOT_READY = "Not ready"
IDLE_HINT = "Enter a project code, choose the projects folder and the client drive."
COPY, MOVE = "copy", "move"

_LOG_COLOURS = {"ERR": "BAD", "SKIP": "WARN", "WARN": "WARN", "STOP": "WARN", "WAIT": "WARN",
                "DRY": "INFO", "START": "ACCENT", "DOCS": "TEXT_2", "STITCH": "TEXT_2"}


class SurveyWorker(QThread):
    """Walks the client drive in the background (see ingest_survey.survey_drive)."""

    progress = Signal(int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, source, target_reel=""):
        super().__init__()
        self.source = source
        self.target_reel = target_reel
        self._stop = False
        self.survey = None

    def stop(self):
        self._stop = True

    def run(self):
        from slate.core.domain.ingest_survey import survey_drive
        try:
            self.survey = survey_drive(self.source, self.target_reel,
                                       should_stop=lambda: self._stop,
                                       progress=lambda n, folder: self.progress.emit(n, folder))
            self.done.emit(self.survey)
        except Exception as exc:
            logger.exception("Survey of %s failed: %s", self.source, exc)
            self.failed.emit(str(exc))


class RetryWorker(QThread):
    """Re-attempts the files a run could not bring in (ingest_retry.retry_failures)."""

    progress = Signal(int, int, str)
    done = Signal(object)

    def __init__(self, manifests, fast_mode=False):
        super().__init__()
        # Every run of the project with files waiting, newest first.
        self.manifests = [manifests] if isinstance(manifests, (str, Path)) else list(manifests or [])
        self.fast_mode = fast_mode
        self._stop = False
        self.result = None

    def stop(self):
        self._stop = True

    def run(self):
        from slate.core.domain.ingest_retry import RetryResult, retry_failures
        try:
            self.result = RetryResult()
            for manifest in self.manifests:
                if self._stop:
                    break
                self.result.merge(retry_failures(manifest, fast_mode=self.fast_mode,
                                                 progress=lambda d, t, n: self.progress.emit(d, t, n),
                                                 should_stop=lambda: self._stop))
        except Exception as exc:
            logger.exception("Retry failed: %s", exc)
            self.result = RetryResult(error=str(exc))
        self.done.emit(self.result)


def wrap_path(text: str) -> str:
    """A path that may wrap after its separators (a zero-width space after each)."""
    return str(text).replace("\\", "\\\u200b").replace("/", "/\u200b")


def find_project_folder(root: str, code: str) -> Optional[Path]:
    """
    The folder that is this project, by name - 'My-Show' for MYSHOW: the
    projects folder itself or one above it (the coordinator may have picked
    a folder inside the project), else a folder inside the projects folder.
    """
    if not root or not code:
        return None
    wanted = normalize_name(code)
    current = Path(root)
    while True:
        if normalize_name(current.name) == wanted:
            return current
        if current.parent == current:
            break
        current = current.parent
    exact = Path(root) / code
    if exact.is_dir():
        return exact
    try:
        return next((child for child in sorted(Path(root).iterdir())
                     if child.is_dir() and normalize_name(child.name) == wanted), None)
    except OSError:
        return None


class FolderCreatorTab(QWidget):
    """Builds the project folder structure and brings the client scans into it."""

    # A template was created, changed or deleted here (a person did it).
    template_changed = Signal(dict)

    def __init__(self, config_manager: ConfigManager = None, user_data: Optional[dict] = None):
        super().__init__()
        if config_manager is None:
            config_manager = ConfigManager()
        self.config_manager = config_manager
        self.user_data = dict(user_data or {})
        self.format_mapping = getattr(self.config_manager, "format_mapping", {})
        self.is_processing = False
        self.folder_creation_thread = None
        self.survey_thread = None
        self.retry_thread = None
        self.folder_preview_tree = None
        self._phase = "idle"
        self._paused = False
        self._last_manifest = None
        self._last_report = None
        self._ingest_lock = None
        self._last_survey = None
        self._retry_manifests: List[Path] = []
        self._log_lines: List[Tuple[str, str]] = []
        self._pending: Dict[str, Any] = {}
        # Keeps the ingest lock fresh while a run is on screen - paused or
        # not - so a run paused overnight is never taken for an abandoned one.
        self._lock_timer = QTimer(self)
        self._lock_timer.setInterval(LOCK_TOUCH_SECONDS * 1000)
        self._lock_timer.timeout.connect(self._touch_lock)

        self.setup_ui()
        self._sync_studio_templates()
        self.load_templates_to_ui()
        self.restore_last_paths()
        settings_dict = getattr(self.config_manager, "settings", {}) or {}
        if isinstance(settings_dict, dict):
            self.apply_global_settings(settings_dict.get("global_settings", {}))
        self.check_destination_status()

    # ================================================================ settings
    def _settings(self) -> dict:
        settings = getattr(self.config_manager, "settings", None)
        return settings if isinstance(settings, dict) else {}

    def _save_settings(self):
        save = getattr(self.config_manager, "save_settings", None)
        if callable(save):
            try:
                save(self._settings())
            except Exception as exc:
                logger.debug("Settings not saved: %s", exc)

    def apply_global_settings(self, global_settings: Dict[str, Any]):
        """Apply global app settings relevant to this tab."""
        if not isinstance(global_settings, dict):
            return
        if hasattr(self, "dry_run_cb"):
            self.dry_run_cb.setChecked(bool(global_settings.get("dry_run_enabled", False)))

    @staticmethod
    def _extract_template_lists(template_info: Dict[str, Any]) -> Tuple[list, list, list, list]:
        """Flat folder lists from either template shape (flat, or under 'structure')."""
        if not isinstance(template_info, dict):
            return [], [], [], []
        structure = template_info.get("structure")
        source = structure if isinstance(structure, dict) else template_info

        def _safe_list(key: str) -> list:
            value = source.get(key, [])
            return value if isinstance(value, list) else []

        return (_safe_list("base_folders"), _safe_list("production_subfolders"),
                _safe_list("outsource_subfolders"), _safe_list("shot_folders"))

    # ================================================================ layout
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Gate.SPACE_3, Gate.SPACE_3, Gate.SPACE_3, Gate.SPACE_2)
        layout.setSpacing(Gate.SPACE_2)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.create_left_panel())
        splitter.addWidget(self.create_right_panel())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        layout.addWidget(splitter, 1)

        # Everything needed during a run stays on screen, under both panels:
        # it used to sit at the bottom of the scrolling left panel, off-screen
        # at 1600x900 and below.
        layout.addWidget(self._create_run_bar())

    def create_left_panel(self):
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, Gate.SPACE_2, 0)
        left_layout.setSpacing(Gate.SPACE_3)

        # 1. Project
        self.project_card = QGroupBox("Project")
        project_layout = form_layout(self.project_card)

        self.project_name_input = QLineEdit()
        self.project_name_input.setPlaceholderText("e.g. PRJ_001")
        self.project_name_input.setToolTip("The project code. It becomes the project's top folder and its code "
                                           "on the dashboard, exactly as typed.")
        self.project_error = self._error_label()
        project_layout.addRow("Project code", self.project_name_input)
        project_layout.addRow("", self.project_error)

        root_row = QHBoxLayout()
        self.project_dir_input = QLineEdit()
        self.project_dir_input.setPlaceholderText("Select the studio projects folder…")
        self.project_dir_input.setToolTip("The folder the project folder goes in (or already is in). "
                                          "Paste a path or use Browse.")
        browse_project_btn = make_button("Browse…", "secondary", on_click=self.browse_project_directory,
                                         tooltip="Choose the projects folder.")
        root_row.addWidget(self.project_dir_input, 1)
        root_row.addWidget(browse_project_btn)
        self.root_error = self._error_label()
        self.destination_label = QLabel("")
        self.destination_label.setWordWrap(True)
        self.destination_label.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        project_layout.addRow("Projects folder", root_row)
        project_layout.addRow("", self.root_error)
        project_layout.addRow("", self.destination_label)

        template_row = QHBoxLayout()
        self.template_combo = QComboBox()
        self.template_combo.setToolTip("The folder structure every project and shot gets.")
        self.create_custom_btn = make_button("Templates…", "secondary",
                                             tooltip="New, edit, duplicate or delete templates.")
        self.templates_menu = QMenu(self.create_custom_btn)
        self.templates_menu.aboutToShow.connect(self._fill_templates_menu)
        self.create_custom_btn.setMenu(self.templates_menu)
        template_row.addWidget(self.template_combo, 1)
        template_row.addWidget(self.create_custom_btn)
        project_layout.addRow("Template", template_row)
        self.template_description_label = QLabel()
        self.template_description_label.setWordWrap(True)
        self.template_description_label.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        project_layout.addRow("", self.template_description_label)
        self.template_combo.activated.connect(self.on_template_activated)
        left_layout.addWidget(self.project_card)

        # 2. Client drive
        self.scan_card = QGroupBox("Client drive")
        scan_layout = form_layout(self.scan_card)

        scan_row = QHBoxLayout()
        self.scan_source_input = QLineEdit()
        self.scan_source_input.setPlaceholderText("Select the client drive…")
        self.scan_source_input.setToolTip("The client's delivery: a drive or a folder with reels and shots in it.")
        browse_scan_btn = make_button("Browse…", "secondary", on_click=self.browse_scan_source,
                                      tooltip="Choose the client drive.")
        scan_row.addWidget(self.scan_source_input, 1)
        scan_row.addWidget(browse_scan_btn)
        self.source_error = self._error_label()
        scan_layout.addRow("Client drive", scan_row)
        scan_layout.addRow("", self.source_error)

        self.target_reel_input = QLineEdit()
        self.target_reel_input.setPlaceholderText("Leave empty to use the reels on the drive")
        self.target_reel_input.setToolTip("Put every shot in this reel (e.g. REEL_01) instead of the reel "
                                          "folders found on the client drive.")
        self.reel_error = self._error_label()
        scan_layout.addRow("Target reel", self.target_reel_input)
        scan_layout.addRow("", self.reel_error)

        op_row = QHBoxLayout()
        self.copy_radio = QRadioButton("Copy")
        self.copy_radio.setToolTip("Copy and check every file; the client drive is left exactly as it was.")
        self.move_radio = QRadioButton("Move")
        self.move_radio.setToolTip("Copy and check every file, then remove it from the client drive.")
        self.operation_group = QButtonGroup(self)
        self.operation_group.addButton(self.copy_radio)
        self.operation_group.addButton(self.move_radio)
        self.copy_radio.setChecked(True)
        op_row.setSpacing(Gate.SPACE_4)
        op_row.addWidget(self.copy_radio)
        op_row.addWidget(self.move_radio)
        op_row.addStretch()
        scan_layout.addRow("Files", op_row)

        options = QGridLayout()
        options.setHorizontalSpacing(Gate.SPACE_4)
        options.setVerticalSpacing(Gate.SPACE_2)
        self.dry_run_cb = QCheckBox("Dry run")
        self.dry_run_cb.setToolTip("Go through everything and write a report, without copying or "
                                   "creating anything.")
        self.fast_mode_cb = QCheckBox("Fast mode (check by size)")
        self.fast_mode_cb.setToolTip(
            "Every copy is still checked against the source size, which catches a truncated or partial "
            "file.\nOnly the slower MD5 comparison is skipped. Leave this off for a client delivery.")
        self.add_to_dashboard_cb = QCheckBox("Add shots to the Dashboard")
        self.add_to_dashboard_cb.setToolTip(
            "Create a tracking record for every new shot.\nShots already tracked are left exactly as "
            "they are; a new scan is noted on them.")
        self.add_to_dashboard_cb.setChecked(True)
        options.addWidget(self.dry_run_cb, 0, 0)
        options.addWidget(self.fast_mode_cb, 0, 1)
        options.addWidget(self.add_to_dashboard_cb, 1, 0, 1, 2)
        scan_layout.addRow("Options", options)

        note_label = QLabel("Slate looks at the drive, shows you the plan, then builds the folders "
                            "and brings the plates in.")
        note_label.setWordWrap(True)
        note_label.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        scan_layout.addRow("", note_label)
        self.note_label = note_label
        left_layout.addWidget(self.scan_card)
        left_layout.addStretch()

        # One label column for both cards, so their fields line up.
        labels = []
        for form in (project_layout, scan_layout):
            for row in range(form.rowCount()):
                item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                if item is not None and item.widget() is not None:
                    labels.append(item.widget())
        width = max((label.sizeHint().width() for label in labels), default=0)
        for label in labels:
            label.setMinimumWidth(width)

        # Typing is checked after a short pause, not on every key.
        self.typing_timer = QTimer(self)
        self.typing_timer.setSingleShot(True)
        self.typing_timer.setInterval(400)
        self.typing_timer.timeout.connect(self.check_destination_status)
        for field in (self.project_name_input, self.project_dir_input, self.scan_source_input,
                      self.target_reel_input):
            field.textChanged.connect(self.start_typing_timer)
            field.returnPressed.connect(self._enter_pressed)
        for path_field in (self.project_dir_input, self.scan_source_input):
            path_field.textChanged.connect(lambda text, f=path_field: f.setToolTip(text or f.placeholderText()))
        self.project_name_input.textChanged.connect(self._refresh_preview_root)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_widget)
        # Wide enough for the fields and their Browse buttons; the preview
        # gives way instead (it was clipping the buttons at 1366 px).
        left_scroll.setMinimumWidth(min(max(left_widget.minimumSizeHint().width() + 16, 440), 560))
        return left_scroll

    @staticmethod
    def _error_label() -> QLabel:
        label = QLabel("")
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {Gate.BAD};")
        label.setVisible(False)
        return label

    def create_right_panel(self):
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)

        preview_card = QGroupBox("Structure preview")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(Gate.SPACE_2, Gate.SPACE_3, Gate.SPACE_2, Gate.SPACE_2)
        self.folder_preview_tree = QTreeWidget()
        self.folder_preview_tree.setHeaderHidden(True)
        # Small minimums, so on a 720 px screen the page fits and the run bar
        # under it stays visible.
        self.folder_preview_tree.setMinimumHeight(60)
        self.folder_preview_tree.setToolTip("What a project built from this template looks like.")
        preview_layout.addWidget(self.folder_preview_tree)
        self.preview_warning = QLabel("")
        self.preview_warning.setWordWrap(True)
        self.preview_warning.setStyleSheet(f"color: {Gate.WARN};")
        self.preview_warning.setVisible(False)
        preview_layout.addWidget(self.preview_warning)
        self.preview_tree = self.folder_preview_tree
        self.right_splitter.addWidget(preview_card)

        log_card = QGroupBox("Process log")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(Gate.SPACE_2, Gate.SPACE_3, Gate.SPACE_2, Gate.SPACE_2)
        tools = QHBoxLayout()
        self.errors_only_cb = QCheckBox("Problems only")
        self.errors_only_cb.setToolTip("Show only failures, skips and warnings.")
        self.errors_only_cb.toggled.connect(self._render_log)
        self.save_log_btn = make_button("Save log…", "ghost", on_click=self.save_log,
                                        tooltip="Save the log as a text file.")
        self.save_log_btn.setEnabled(False)
        self.open_report_btn = make_button("Open report", "ghost", on_click=self.open_last_report,
                                           tooltip="Open the delivery report of the last run.")
        self.open_report_btn.setEnabled(False)
        tools.addWidget(self.errors_only_cb)
        tools.addStretch()
        tools.addWidget(self.save_log_btn)
        tools.addWidget(self.open_report_btn)
        log_layout.addLayout(tools)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("What each run did appears here.")
        # Six lines at least: the log is what a coordinator reads during a run.
        self.log_text.setMinimumHeight(self.log_text.fontMetrics().lineSpacing() * 6 + 12)
        log_layout.addWidget(self.log_text)
        self.right_splitter.addWidget(log_card)
        self.right_splitter.setStretchFactor(0, 3)
        self.right_splitter.setStretchFactor(1, 2)
        sizes = self._settings().get("build_ingest_split")
        valid = isinstance(sizes, list) and len(sizes) == 2 and all(isinstance(v, int) and v > 0 for v in sizes)
        self.right_splitter.setSizes(sizes if valid else [460, 240])
        # Where the person drags it is kept (saved once the dragging stops).
        self._split_timer = QTimer(self)
        self._split_timer.setSingleShot(True)
        self._split_timer.setInterval(600)
        self._split_timer.timeout.connect(self._remember_split)
        self.right_splitter.splitterMoved.connect(lambda *_: self._split_timer.start())
        return self.right_splitter

    def _remember_split(self):
        self._settings()["build_ingest_split"] = [int(v) for v in self.right_splitter.sizes()]
        self._save_settings()

    def _create_run_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("ingestRunBar")
        bar.setStyleSheet(f"#ingestRunBar {{ background: {Gate.PANEL}; border: 1px solid {Gate.LINE}; "
                          f"border-radius: {Gate.RADIUS_MD}px; }}")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(Gate.SPACE_3, Gate.SPACE_2, Gate.SPACE_3, Gate.SPACE_2)
        outer.setSpacing(Gate.SPACE_1)

        status_row = QHBoxLayout()
        self.progress_label = QLabel(IDLE_STATUS)
        self.progress_label.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600;")
        # No word wrap in the run bar: a wrapping label makes the page ask
        # for its preferred height, which pushed the bar off a 720 px screen.
        self.stats_label = QLabel(IDLE_HINT)
        self.stats_label.setMinimumWidth(10)
        self.stats_label.setStyleSheet(f"color: {Gate.TEXT_2};")
        status_row.addWidget(self.progress_label)
        status_row.addSpacing(Gate.SPACE_3)
        status_row.addWidget(self.stats_label, 1)
        outer.addLayout(status_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        outer.addWidget(self.progress_bar)

        self.last_run_label = QLabel("")
        self.last_run_label.setTextFormat(Qt.TextFormat.RichText)
        self.last_run_label.setOpenExternalLinks(False)
        self.last_run_label.linkActivated.connect(lambda *_: self.open_last_report())
        self.last_run_label.setMinimumWidth(10)
        self.last_run_label.setVisible(False)
        outer.addWidget(self.last_run_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(Gate.SPACE_2)
        self.clear_btn = make_button("Reset", "ghost", on_click=self.clear_all,
                                     tooltip="Clear the project code, client drive, target reel and options. "
                                             "The projects folder and the log are kept.")
        self.retry_btn = make_button("Retry failed files", "secondary", on_click=self.retry_failed_files,
                                     icon="refresh",
                                     tooltip="Bring in the files the last run of this project could not.")
        self.retry_btn.setEnabled(False)
        self.pause_btn = make_button("Pause", "secondary", on_click=self.toggle_pause, icon="pause",
                                     tooltip="Pause after the file being copied now.")
        self.pause_btn.setEnabled(False)
        self.stop_btn = make_button("Stop", "secondary", on_click=self.stop_creation_process, icon="stop",
                                    tooltip="Stop after the file being copied now. Nothing is left half "
                                            "copied; the report lists what did not come in.")
        self.stop_btn.setEnabled(False)
        self.create_btn = make_button("Build project", "primary", on_click=self.start_creation_process,
                                      tooltip="Look at the client drive and show the plan before anything is copied.")
        self.create_btn.setMinimumWidth(170)
        self.create_button = self.create_btn
        buttons.addWidget(self.clear_btn)
        buttons.addWidget(self.retry_btn)
        buttons.addStretch()
        buttons.addWidget(self.pause_btn)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(self.create_btn)
        outer.addLayout(buttons)
        return bar

    # ================================================================ closing
    def busy_reason(self):
        """What would be cut short if Slate closed now, or None when idle."""
        for attr, text in (("folder_creation_thread", "Build & Ingest is still bringing files into {p}."),
                           ("retry_thread", "Build & Ingest is retrying failed files for {p}."),
                           ("survey_thread", "Build & Ingest is looking at the client drive.")):
            thread = getattr(self, attr, None)
            try:
                if thread is not None and thread.isRunning():
                    project = self.project_name_input.text().strip() or "the project"
                    return text.format(p=project)
            except RuntimeError:
                continue
        return None

    def shutdown(self, timeout_ms: int = 15000) -> bool:
        """Stop at the next file boundary and wait, so nothing is left half-copied."""
        from PySide6.QtCore import QDeadlineTimer
        from PySide6.QtWidgets import QApplication
        deadline = QDeadlineTimer(int(timeout_ms))
        stopped = True
        for attr in ("survey_thread", "retry_thread", "folder_creation_thread"):
            thread = getattr(self, attr, None)
            try:
                if thread is None or not thread.isRunning():
                    continue
                thread.stop()
                while thread.isRunning() and not deadline.hasExpired():
                    thread.wait(100)
                    QApplication.processEvents()
                stopped = stopped and not thread.isRunning()
            except RuntimeError:
                continue
        if stopped:
            self._release_ingest_lock()
        return stopped

    def closeEvent(self, event):
        self.shutdown(2000)
        if hasattr(self, "typing_timer") and self.typing_timer.isActive():
            self.typing_timer.stop()
        super().closeEvent(event)

    def _cleanup_worker(self, attr_name: str, timeout_ms: int = 3000):
        worker = getattr(self, attr_name, None)
        if worker is None:
            return
        try:
            if worker.isRunning():
                worker.stop()
                worker.wait(timeout_ms)
            if not worker.isRunning():
                worker.deleteLater()
        except RuntimeError:
            pass
        if getattr(self, attr_name, None) is worker:
            setattr(self, attr_name, None)

    # ================================================================ checking
    def start_typing_timer(self, *_):
        if hasattr(self, "typing_timer"):
            self.typing_timer.start()

    def _inputs(self):
        return (self.project_name_input.text().strip(), self.project_dir_input.text().strip(),
                self.scan_source_input.text().strip(), self.target_reel_input.text().strip())

    def _field_problems(self) -> Dict[str, str]:
        """What is wrong with what has been typed. Empty fields are not 'wrong'."""
        code, root, source, reel = self._inputs()
        out = {}
        if code:
            # The shot rule: the code is the top folder and the Dashboard
            # code, typed into render paths and Nuke scripts like a shot name.
            problem = shot_name_problem(code, "The project code")
            if problem:
                out["code"] = problem
        if root:
            root_path = Path(root)
            if not root_path.exists():
                out["root"] = "This folder does not exist."
            elif not root_path.is_dir():
                out["root"] = "This is a file, not a folder."
        if reel:
            problem = name_problem(reel, "The target reel")
            if problem:
                out["reel"] = problem
        if source:
            source_path = Path(source)
            if not source_path.exists():
                out["source"] = "This folder does not exist."
            elif not source_path.is_dir():
                out["source"] = "This is a file, not a folder."
            elif root and code and "root" not in out and "code" not in out:
                clash = self._source_clash(source_path, self._planned_project_path())
                if clash:
                    out["source"] = clash
        return out

    @staticmethod
    def _source_clash(source: Path, project: Path) -> str:
        """A drive that is, or is inside, the project - or holds it - cannot be ingested into it."""
        try:
            source, project = source.resolve(), project.resolve()
        except OSError:
            return ""
        if source == project or source == project.parent:
            return "The client drive and the project are the same folder."
        if source.is_relative_to(project):
            return "The client drive is inside the project - choose the client's folder."
        if project.is_relative_to(source):
            return "The project would be inside the client drive."
        return ""

    def _planned_project_path(self) -> Path:
        """Where the project folder is (or will be), before any question is asked."""
        code, root, _source, _reel = self._inputs()
        found = find_project_folder(root, code)
        if found is not None:
            return found
        return Path(root) / code

    def _missing(self) -> str:
        code, root, source, _reel = self._inputs()
        if not code:
            return "Enter a project code."
        if not root:
            return "Choose the projects folder."
        if not source:
            return "Choose the client drive."
        if self.template_combo.currentData() is None:
            return "Choose a template."
        return ""

    def check_destination_status(self):
        """Check the inputs, say where the project goes, and set the button."""
        problems = self._field_problems()
        for key, label in (("code", self.project_error), ("root", self.root_error),
                           ("source", self.source_error), ("reel", self.reel_error)):
            text = problems.get(key, "")
            label.setText(text)
            label.setVisible(bool(text))
        self.project_name_input.setStyleSheet(
            f"QLineEdit {{ border: 1px solid {Gate.BAD}; }}" if "code" in problems else "")

        code, root, _source, _reel = self._inputs()
        exists = False
        if code and root and "code" not in problems and "root" not in problems:
            target = self._planned_project_path()
            exists = target.is_dir()
            found = find_project_folder(root, code)
            if found is not None and found.name != code:
                self.destination_label.setText(
                    f"Found the project folder '{found.name}' at {found.parent}. You will be asked "
                    f"whether to use it or create '{code}'.")
            elif exists:
                self.destination_label.setText(f"Updates {target}")
            else:
                self.destination_label.setText(f"Builds {target}")
        else:
            self.destination_label.setText("")
        # Paths have no spaces: let them wrap after a separator instead of
        # forcing the panel wider than the screen.
        self.destination_label.setText(wrap_path(self.destination_label.text()))
        self.destination_label.setVisible(bool(self.destination_label.text()))

        busy = self._phase != "idle"
        self.create_btn.setText("Update project" if exists else "Build project")
        missing = self._missing()
        blocked = next(iter(problems.values()), "") or missing
        self.create_btn.setEnabled(not blocked and not busy)
        if busy:
            self.create_btn.setToolTip("A run is in progress.")
        elif blocked:
            self.create_btn.setToolTip(blocked)
        else:
            self.create_btn.setToolTip("Look at the client drive and show the plan before anything is copied.")

        if not busy:
            # 'Ready' never sits next to a problem.
            if problems:
                self.progress_label.setText(NOT_READY)
            elif self.progress_label.text() == NOT_READY:
                self.progress_label.setText(IDLE_STATUS)
            self.stats_label.setStyleSheet(f"color: {Gate.WARN if problems else Gate.TEXT_2};")
            if blocked:
                self.stats_label.setText(blocked if problems else IDLE_HINT)
            elif exists:
                self.stats_label.setText("Project exists - new files go into a new scan version (v002, "
                                         "v003 …). Nothing in the project is overwritten.")
            else:
                self.stats_label.setText("New project - Slate builds the folders and brings the plates in.")
        self._refresh_retry_button()
        self._refresh_preview_root()

    def _enter_pressed(self):
        self.check_destination_status()
        if self.create_btn.isEnabled():
            self.start_creation_process()

    # ================================================================ templates
    def _templates(self) -> dict:
        templates = getattr(self.config_manager, "templates", None)
        return templates if isinstance(templates, dict) else {}

    def _builtin_keys(self) -> set:
        builtin = getattr(self.config_manager, "default_templates", None)
        return set(builtin) if isinstance(builtin, dict) else {"standard"}

    def _user_keys(self) -> set:
        return set(self._templates()) - self._builtin_keys()

    def _sync_studio_templates(self):
        """Templates are the studio's (the database); this PC's file is the offline copy."""
        sync = getattr(self.config_manager, "sync_studio_templates", None)
        if callable(sync) and not sync(by=self._username()):
            logger.info("Studio templates unavailable - using this PC's copy.")

    def _share_templates(self, name: str):
        """Save the templates for the whole studio; say so when only this PC has them."""
        share = getattr(self.config_manager, "share_templates", None)
        if callable(share) and not share(by=self._username()):
            from slate.gui.components.feedback import warn
            warn(self, "Templates", f"'{name}' is saved on this PC only - the studio database could not "
                 "be reached. It is shared the next time Slate starts with the database.")

    def _username(self) -> str:
        return str(self.user_data.get("username") or self.user_data.get("user_id") or "")

    def load_templates_to_ui(self, select_key: Optional[str] = None):
        """
        Fill the template combo from the configuration without firing anything.

        The old version re-emitted 'template changed' from in here; the main
        window answered by reloading this tab, which re-emitted - an endless
        loop that crashed Slate when a studio had a second template.
        """
        previous = self.template_combo.currentData()
        self.template_combo.blockSignals(True)
        try:
            self.template_combo.clear()
            if hasattr(self.config_manager, "get_templates") and callable(self.config_manager.get_templates):
                keys = self.config_manager.get_templates() or []
            elif callable(getattr(self.config_manager, "get_available_templates", None)):
                keys = self.config_manager.get_available_templates() or []
            else:
                keys = []
            builtin = self._builtin_keys()
            for key in sorted(keys, key=lambda k: (k not in builtin, str(k).lower())):
                info = self._templates().get(key)
                name = info.get("name", key) if isinstance(info, dict) else str(key)
                # Built-ins first and marked: they cannot be edited, the
                # studio's own templates can.
                self.template_combo.addItem(f"{name} (built-in)" if key in builtin else str(name), key)
            last = ((self._settings().get("global_settings") or {}).get("last_template_used")
                    if isinstance(self._settings().get("global_settings"), dict) else None)
            for wanted in (select_key, previous, last, "standard"):
                index = self.template_combo.findData(wanted) if wanted else -1
                if index >= 0:
                    self.template_combo.setCurrentIndex(index)
                    break
            else:
                if self.template_combo.count():
                    self.template_combo.setCurrentIndex(0)
        finally:
            self.template_combo.blockSignals(False)
        self._show_template(self.template_combo.currentData())

    def on_template_activated(self, index: int):
        """A person picked a template: show it and remember it."""
        key = self.template_combo.itemData(index)
        self._show_template(key)
        settings = self._settings()
        global_settings = settings.setdefault("global_settings", {})
        if isinstance(global_settings, dict) and global_settings.get("last_template_used") != key:
            global_settings["last_template_used"] = key
            self._save_settings()
        self.check_destination_status()

    def on_template_change(self, text=None):
        """Older name, kept for callers: show the selected template."""
        self._show_template(self.template_combo.currentData())

    def _show_template(self, key):
        info = self._templates().get(key) if key else None
        description = str(info.get("description", "")) if isinstance(info, dict) else ""
        self.template_description_label.setText(description)
        self.template_description_label.setVisible(bool(description))
        self.update_preview(key)

    def _fill_templates_menu(self):
        menu = self.templates_menu
        menu.clear()
        key = self.template_combo.currentData()
        name = self._template_name(key)
        builtin = key in self._builtin_keys()
        menu.addAction("New template…", self.create_custom_template)
        edit = menu.addAction(plain(f"Edit '{name}'…"), self.edit_current_template)
        edit.setEnabled(bool(key) and not builtin)
        if builtin:
            edit.setToolTip("Built-in templates cannot be changed - duplicate it instead.")
        dup = menu.addAction(plain(f"Duplicate '{name}'…"), self.duplicate_current_template)
        dup.setEnabled(bool(key))
        menu.addSeparator()
        delete = menu.addAction(plain(f"Delete '{name}'"), self.delete_current_template)
        delete.setEnabled(bool(key) and not builtin)
        menu.setToolTipsVisible(True)

    def _template_name(self, key) -> str:
        info = self._templates().get(key)
        return str(info.get("name", key)) if isinstance(info, dict) else str(key or "")

    @staticmethod
    def _version_folders(info) -> list:
        """
        What goes inside each scan version: the template's list - an empty
        list means none - or Denoise for templates from before the list.
        """
        structure = info if isinstance(info, dict) else {}
        if isinstance(structure.get("structure"), dict):
            structure = structure["structure"]
        folders = structure.get("scan_version_folders")
        return list(DEFAULT_VERSION_FOLDERS if folders is None else folders)

    def _template_dialog(self, template=None, original_key="", title=""):
        return CustomTemplateDialog(self, template, original_key=original_key,
                                    builtin_keys=self._builtin_keys(), user_keys=self._user_keys(),
                                    title=title)

    def create_custom_template(self):
        self._edit_template(None, "", "New template")

    def edit_current_template(self):
        key = self.template_combo.currentData()
        if key and key not in self._builtin_keys():
            self._edit_template(self._templates().get(key), key, "Edit template")

    def duplicate_current_template(self):
        key = self.template_combo.currentData()
        info = dict(self._templates().get(key) or {})
        base = info.get("structure") if isinstance(info.get("structure"), dict) else info
        copy = {k: list(v) if isinstance(v, list) else v for k, v in base.items()}
        copy["name"] = f"{info.get('name', key)} copy"
        copy["description"] = info.get("description", "")
        self._edit_template(copy, "", "Duplicate template")

    def _edit_template(self, template, original_key, title):
        from slate.gui.components.feedback import confirm, toast, warn
        dialog = self._template_dialog(template, original_key, title)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data = dialog.get_template_data()
        key = dialog.template_key()
        if key in self._builtin_keys():
            warn(self, "Save template", f"'{data['name']}' is the name of a built-in template.")
            return
        if dialog.overwrites_another() and not confirm(
                self, "Save template", f"A template called '{data['name']}' already exists. Replace it?",
                yes_label="Replace it", destructive=True):
            return
        templates = dict(self._templates())
        templates[key] = data
        if original_key and original_key != key:
            templates.pop(original_key, None)          # renamed
        if not self.config_manager.save_templates(templates) or key not in self._templates():
            warn(self, "Save template", f"'{data['name']}' could not be saved. Nothing was changed.")
            return
        self._share_templates(data["name"])
        self.load_templates_to_ui(select_key=key)
        self.on_template_activated(self.template_combo.currentIndex())
        self.template_changed.emit(data)
        toast(self, f"Template '{data['name']}' saved.", "success")

    def delete_current_template(self):
        from slate.gui.components.feedback import confirm, toast, warn
        key = self.template_combo.currentData()
        if not key or key in self._builtin_keys():
            return
        name = self._template_name(key)
        if not confirm(self, "Delete template", f"Delete the template '{name}'? Projects already built "
                       "with it are not changed.", yes_label="Delete template", destructive=True):
            return
        templates = dict(self._templates())
        templates.pop(key, None)
        if not self.config_manager.save_templates(templates) or key in self._templates():
            warn(self, "Delete template", f"'{name}' could not be deleted.")
            return
        self._share_templates(name)
        self.load_templates_to_ui(select_key="standard")
        self.on_template_activated(self.template_combo.currentIndex())
        self.template_changed.emit({"name": name, "deleted": True})
        toast(self, f"Template '{name}' deleted.", "success")

    # ================================================================ preview
    def _reels_relative(self, code: str) -> Path:
        from slate.core.workers.structure import reels_root_for
        root = Path("/__root__")
        try:
            return reels_root_for(root / (code or "PROJECT"), code or "PROJECT", root).relative_to(
                root / (code or "PROJECT"))
        except Exception:
            return Path("05_Reels")

    def _refresh_preview_root(self, *_):
        tree = self.folder_preview_tree
        if tree is not None and tree.topLevelItemCount():
            tree.topLevelItem(0).setText(0, self._preview_code() or "Project")

    def _preview_code(self) -> str:
        """The typed code, when it can be a folder name; '' otherwise."""
        code = self.project_name_input.text().strip()
        return code if code and not shot_name_problem(code) else ""

    def update_preview(self, template_key, survey=None):
        """The project this template builds, folder by folder, merged by path."""
        tree = self.folder_preview_tree
        if tree is None:
            return
        tree.clear()
        info = self._templates().get(template_key)
        if not isinstance(info, dict):
            self.preview_warning.setVisible(False)
            return
        base, production, outsource, shots = self._extract_template_lists(info)
        version_folders = self._version_folders(info)
        code = self._preview_code()
        root = QTreeWidgetItem(tree, [code or "Project"])
        bold = QFont(root.font(0))
        bold.setBold(True)
        root.setFont(0, bold)

        def add(parent, path, note=""):
            node = parent
            for part in [p for p in str(path).replace("\\", "/").split("/") if p]:
                found = next((node.child(i) for i in range(node.childCount())
                              if node.child(i).text(0) == part), None)
                if found is None:
                    found = QTreeWidgetItem(node, [part])
                node = found
            if note:
                node.setText(0, f"{node.text(0)}   ({note})")
                node.setForeground(0, Gate.qcolor(Gate.TEXT_DIM))
            return node

        for folder in base:
            add(root, folder)
        for folder in list(production) + list(outsource):
            add(root, f"04_Production/{folder}")
        add(root, client_folder_for(info), "client deliveries and reports")

        defaulted = not shots
        shot_folders = list(shots) or list(DEFAULT_SHOT_FOLDERS)
        reels = add(root, self._reels_relative(code).as_posix())
        examples = []
        survey = survey or self._last_survey
        if survey is not None:
            for shot in survey.active_shots()[:4]:
                examples.append((shot.reel, shot.name))
        examples = examples or [("<reel>", "<shot>")]
        for reel, shot in examples:
            shot_node = add(reels, f"{reel}/{shot}")
            for folder in shot_folders:
                add(shot_node, folder, "added by Slate" if defaulted else "")
            scan_root = next((f.split("/")[0] for f in shot_folders if "scan" in f.lower()), "01_Scan")
            version = add(shot_node, f"{scan_root}/v001")
            for folder in version_folders:
                add(version, folder)
            add(version, "EXR")
        tree.expandToDepth(3)
        self.preview_warning.setText(
            "This template has no shot folders - every shot gets " + ", ".join(DEFAULT_SHOT_FOLDERS) + "."
            if defaulted else "")
        self.preview_warning.setVisible(defaulted)

    # ================================================================ paths
    def browse_project_directory(self):
        start = self.project_dir_input.text().strip() or self._settings().get('last_project_directory',
                                                                               str(Path.home()))
        directory = QFileDialog.getExistingDirectory(self, "Choose the projects folder", start)
        if directory:
            self.project_dir_input.setText(directory)
            self._settings()['last_project_directory'] = directory
            self._save_settings()

    def browse_scan_source(self):
        start = self.scan_source_input.text().strip() or self._settings().get('last_scan_source_directory',
                                                                              str(Path.home()))
        directory = QFileDialog.getExistingDirectory(self, "Choose the client drive", start)
        if directory:
            self.scan_source_input.setText(directory)
            self._settings()['last_scan_source_directory'] = directory
            self._save_settings()

    def restore_last_paths(self):
        settings = self._settings()
        if not (settings.get('global_settings') or {}).get("restore_last_paths", True):
            return
        try:
            for key, field in (('last_project_directory', self.project_dir_input),
                               ('last_scan_source_directory', self.scan_source_input)):
                value = settings.get(key, '')
                if value and Path(value).exists():
                    field.setText(value)
            # The code too: the retry of a run looks for that project's
            # failures, so they show after a restart without retyping it.
            if settings.get('last_project_code') and self.project_dir_input.text():
                self.project_name_input.setText(str(settings['last_project_code']))
        except Exception as e:
            logger.warning(f"Could not restore last paths: {e}")

    # ================================================================ the log
    def log_message(self, message: str):
        """Add a line to the log, coloured by what it says; the view follows the newest lines."""
        stamp = datetime.now().strftime("%H:%M:%S")
        text = str(message)
        tag = text[1:text.index("]")] if text.startswith("[") and "]" in text else ""
        self._log_lines.append((tag, f"[{stamp}] {text}"))
        if len(self._log_lines) > 5000:
            self._log_lines = self._log_lines[-4000:]
        if self._log_visible(tag):
            bar = self.log_text.verticalScrollBar()
            # Follow unless the person scrolled up to read; a new run always
            # brings its header into view.
            follow = tag == "RUN" or bar.value() >= bar.maximum() - 4
            self.log_text.append(self._log_html(tag, f"[{stamp}] {text}"))
            if follow:
                bar.setValue(bar.maximum())
        self.save_log_btn.setEnabled(True)
        logger.info(text)

    def _log_visible(self, tag: str) -> bool:
        if not self.errors_only_cb.isChecked():
            return True
        return tag in ("ERR", "SKIP", "WARN", "STOP", "RUN")

    @staticmethod
    def _log_html(tag: str, text: str) -> str:
        if tag == "RUN":
            return (f"<p style='margin-top:8px; color:{Gate.ACCENT}; font-weight:600'>"
                    f"{html.escape(text)}</p>")
        colour = getattr(Gate, _LOG_COLOURS.get(tag, "TEXT"), Gate.TEXT)
        return f"<span style='color:{colour}'>{html.escape(text)}</span>"

    def _render_log(self, *_):
        self.log_text.clear()
        for tag, text in self._log_lines:
            if self._log_visible(tag):
                self.log_text.append(self._log_html(tag, text))

    def _log_run_header(self, text: str):
        self.log_message(f"[RUN] ---- {text} ----")

    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save the log", str(Path.home() / "ingest_log.txt"),
                                              "Text files (*.txt)")
        if not path:
            return
        from slate.gui.components.feedback import toast, warn
        try:
            Path(path).write_text("\n".join(text for _tag, text in self._log_lines), encoding="utf-8")
            toast(self, "Log saved.", "success")
        except OSError as exc:
            warn(self, "Save the log", f"The log could not be saved: {exc}")

    def open_last_report(self):
        if self._last_report and Path(self._last_report).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_report)))

    def _status_bar(self):
        window = self.window()
        if isinstance(window, QMainWindow):
            try:
                return window.statusBar()
            except RuntimeError:
                return None
        return None

    # ================================================================ progress
    def update_folder_creator_progress(self, value: int, text: str):
        self.progress_bar.setValue(value)
        if self._paused:
            return                       # stays "Paused" until resumed
        self.progress_label.setText(text)
        bar = self._status_bar()
        if bar is not None:
            bar.showMessage(text, 2000)

    def _on_folder_worker_progress(self, value: int, text: str):
        if self.sender() is not None and self.sender() is not self.folder_creation_thread:
            return
        self.update_folder_creator_progress(value, text)

    def _on_worker_state(self, state: str):
        from slate.gui.core.icons import icon as draw_icon
        self._paused = state == "paused"
        bar = self._status_bar()
        if self._paused:
            self.progress_label.setText("Paused")
            self.progress_label.setStyleSheet(f"color: {Gate.WARN}; font-weight: 600;")
            if bar is not None:
                bar.showMessage("Build & Ingest is paused")      # stays until it resumes
        else:
            self.progress_label.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600;")
            if bar is not None:
                bar.clearMessage()
        self.pause_btn.setText("Resume" if self._paused else "Pause")
        self.pause_btn.setIcon(draw_icon("play" if self._paused else "pause", Gate.TEXT, 16))

    def toggle_pause(self):
        worker = self.folder_creation_thread
        if worker is None:
            return
        if not self._paused:
            worker.pause()
            self._on_worker_state("paused")
        else:
            worker.resume()
            self._on_worker_state("running")
            self.progress_label.setText("Resuming…")

    # ================================================================ phases
    def _set_phase(self, phase: str):
        """One place that says what the buttons do in each phase."""
        self._phase = phase
        busy = phase != "idle"
        self.is_processing = busy
        self.stop_btn.setEnabled(phase in ("survey", "run", "retry"))
        self.pause_btn.setEnabled(phase == "run")
        if phase != "run" and self._paused:
            self._on_worker_state("running")
        if phase != "run":
            self.progress_label.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600;")
        for widget in (self.project_card, self.scan_card, self.clear_btn):
            widget.setEnabled(not busy)
        # The last run's line belongs to that run: not shown under a new one.
        self.last_run_label.setVisible(not busy and bool(self.last_run_label.text()))
        self.check_destination_status()
        hints = {"survey": "Looking at every folder on the client drive. Stop cancels; nothing is copied yet.",
                 "run": "Pause and Stop act between files - nothing is ever left half copied.",
                 "retry": "Retrying the files the last run could not bring in."}
        if phase in hints:
            self.stats_label.setText(hints[phase])

    def _reset_run_ui(self, status: str = IDLE_STATUS):
        """Back to idle after any early return - the log and last run are kept."""
        self._set_phase("idle")
        self.progress_label.setText(status)

    # ================================================================ the run
    def start_creation_process(self):
        """Check, then survey the drive in the background."""
        if self._phase != "idle":
            return
        self.check_destination_status()
        problems = self._field_problems()
        blocked = next(iter(problems.values()), "") or self._missing()
        if blocked:
            from slate.gui.components.feedback import warn
            warn(self, "Build project", blocked)
            return

        code, root, source, reel = self._inputs()
        self._pending = {"code": code, "root": root, "source": Path(source), "reel": reel,
                         "template": self.template_combo.currentData()}
        self._set_phase("survey")
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet("")
        self.progress_label.setText("Looking at the client drive…")
        self._settings()["last_project_code"] = code
        self._save_settings()
        self._log_run_header(f"{datetime.now():%d %b %Y %H:%M}  {code} from {source}")

        self._cleanup_worker("survey_thread")
        worker = SurveyWorker(Path(source), reel)
        worker.progress.connect(self._on_survey_progress)
        worker.done.connect(self._on_survey_done)
        worker.failed.connect(self._on_survey_failed)
        self.survey_thread = worker
        worker.start()

    def _on_survey_progress(self, seen: int, folder: str):
        if self._phase == "survey":
            self.progress_label.setText(f"Looking at the client drive… {seen:,} files so far")

    def _on_survey_failed(self, message: str):
        from slate.gui.components.feedback import show_error
        self.survey_thread = None
        self._reset_run_ui()
        self.log_message(f"[ERR] Could not read the client drive: {message}")
        show_error(self, "Could not read the client drive.", message)

    def _on_survey_done(self, survey):
        self.survey_thread = None
        if survey.cancelled:
            self.log_message("[STOP] Stopped while looking at the drive - nothing was copied.")
            self._reset_run_ui("Stopped")
            return
        self._last_survey = survey
        self.log_message(f"[INFO] Found {len(survey.shots)} shot folder(s), {survey.total_files:,} file(s), "
                         f"{size_text(survey.total_bytes)} in {survey.seconds:.1f} s.")
        self.update_preview(self._pending.get("template"), survey)
        try:
            self.finalize_creation_process(survey)
        except Exception as exc:
            logger.exception("Could not start the ingest: %s", exc)
            from slate.gui.components.feedback import show_error
            show_error(self, "Could not start the ingest.", exc=exc)
            self._release_ingest_lock()
            self._reset_run_ui()

    def _template_data(self, key):
        info = self._templates().get(key)
        base, production, outsource, shots = self._extract_template_lists(info)
        defaulted = not shots
        return ((base, production, outsource, list(shots) or list(DEFAULT_SHOT_FOLDERS)), defaulted,
                self._version_folders(info), (info or {}).get("name", key))

    def finalize_creation_process(self, survey=None):
        """Stitches, pre-flight, lock - then the worker. Any 'no' leaves everything untouched."""
        pending = self._pending
        code, root = pending["code"], pending["root"]
        template_data, defaulted, version_folders, template_name = self._template_data(pending["template"])
        client = client_folder_for(self._templates().get(pending["template"]))

        found = find_project_folder(root, code)
        project_choice = None
        if found is not None and found.name != code:
            project_choice = {"existing": found, "created": found.parent / code}
            project_path = found
        elif found is not None:
            project_path = found
        else:
            project_path = Path(root) / code
        target_root = project_path.parent

        stitch_mapping, decisions = self._confirm_stitch_shots(survey, project_path, client)
        if stitch_mapping is None:
            self.log_message("[STOP] Cancelled at the stitch question - nothing was copied.")
            self._reset_run_ui()
            return

        from slate.core.workers.structure import reels_root_for
        reels_root = reels_root_for(project_path, code, target_root)
        scan_root = next((f.split("/")[0] for f in template_data[3] if "scan" in f.lower()), "01_Scan")
        if project_path.is_dir():
            survey.mark_unchanged(reels_root, scan_root, stitch_mapping)
            unchanged = {(s.reel, survey.destination_of(s, stitch_mapping)) for s in survey.shots
                         if s.unchanged_from}
            if unchanged:
                self.log_message(f"[INFO] {len(unchanged)} shot(s) are already in the project unchanged.")
            from slate.core.domain.ingest_survey import mark_documents_filed
            if mark_documents_filed(survey, project_path / client):
                self.log_message("[INFO] The documents on the drive were filed by an earlier run.")
        untracked = self._untracked_shots(survey, code, stitch_mapping, reels_root)

        dialog = IngestPreflightDialog(
            survey, project_code=code, project_path=project_path,
            operation=MOVE if self.move_radio.isChecked() else COPY,
            dry_run=self.dry_run_cb.isChecked(), template_name=template_name,
            shot_folders_defaulted=defaulted, stitch_mapping=stitch_mapping,
            long_paths=survey.long_paths(project_path, reels_root, scan_root),
            project_choice=project_choice, project_exists=project_path.is_dir(),
            client_folder=client, untracked=len(untracked), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.log_message("[INFO] Nothing new on this drive - nothing was copied." if dialog._nothing_new()
                             else "[STOP] Cancelled at the check - nothing was copied.")
            self._reset_run_ui()
            return
        if dialog.problems():
            from slate.gui.components.feedback import warn
            warn(self, "Build project", dialog.problems()[0])
            self._reset_run_ui()
            return

        dry_run = dialog.dry_run
        operation = dialog.operation
        project_path = dialog.chosen_project_dir()
        target_root = project_path.parent
        register_existing = untracked if dialog.add_untracked() else []

        # One ingest per project: two runs would both claim the same scan
        # version. Taken before anything on screen says "running".
        if not dry_run:
            if not self._take_lock(project_path):
                self._reset_run_ui()
                return
            self._save_stitch_decisions(project_path, client, decisions)

        self._pending.update({"project_path": project_path, "dry_run": dry_run, "operation": operation})
        self._set_phase("run")
        verb = "Simulating" if dry_run else ("Moving" if operation == MOVE else "Copying")
        self.log_message(f"[START] {verb} into {project_path}")
        self._cleanup_worker("folder_creation_thread")
        worker = FolderCreationWorker(
            target_dir=target_root, source_scan_path=pending["source"], project_name=code,
            project_dir=project_path, template_data=template_data, mode="full",
            template_type=pending["template"], target_reel_name=pending["reel"],
            dry_run=dry_run, format_mapping=getattr(self.config_manager, 'format_mapping', {}) or {},
            fast_mode=self.fast_mode_cb.isChecked(), scan_version_folders=version_folders,
            stitch_mapping=stitch_mapping, operation=operation, survey=survey,
            lock=self._ingest_lock, register_shots=self.add_to_dashboard_cb.isChecked(),
            client_folder=client, register_existing=register_existing,
        )
        worker.log_signal.connect(self.log_message)
        worker.progress_signal.connect(self._on_folder_worker_progress)
        worker.state_signal.connect(self._on_worker_state)
        worker.finished_signal.connect(self._on_folder_worker_finished)
        self.folder_creation_thread = worker
        worker.start()

    def _untracked_shots(self, survey, code, stitch_mapping, reels_root) -> List[dict]:
        """
        Shots already in the project, unchanged, that the Dashboard does not
        have (an earlier run had 'Add shots to the Dashboard' off): the
        pre-flight offers to add them, even when there is nothing to copy.
        """
        unchanged = [s for s in survey.shots if s.unchanged_from]
        if not unchanged or not self.add_to_dashboard_cb.isChecked():
            return []
        try:
            from slate.core.workers import structure        # the database the run registers into
            rows = structure.database_manager.get_tracking_shots(code) or []
        except Exception as exc:
            logger.debug("Dashboard shots not read: %s", exc)
            return []
        tracked = {(str(r.get("reel_episode") or r.get("reel") or "").lower(),
                    str(r.get("shot_name") or "").lower()) for r in rows if isinstance(r, dict)}
        out, seen = [], set()
        for shot in unchanged:
            dest = survey.destination_of(shot, stitch_mapping)
            key = (shot.reel.lower(), dest.lower())
            if key in tracked or key in seen:
                continue
            seen.add(key)
            out.append({"reel": shot.reel, "shot": dest, "path": str(Path(reels_root) / shot.reel / dest),
                        "scan_version": shot.unchanged_from, "source_folder": shot.source_name,
                        "client_version": shot.client_version, "is_media": True})
        return out

    # ---------------------------------------------------------------- stitches
    @staticmethod
    def _stitch_key(group) -> str:
        return f"{group.reel}|{'+'.join(sorted(p.lower() for p in group.parts))}".lower()

    @staticmethod
    def _stitch_file(project_path: Path, client: str) -> Path:
        from slate.core.domain.delivery_report import REPORT_DIRNAME
        return Path(project_path) / client / REPORT_DIRNAME / "stitch_decisions.json"

    def _confirm_stitch_shots(self, survey, project_path=None, client=""):
        """
        ({(reel, folder): shot} for the confirmed stitches, the decisions to
        remember); (None, None) when cancelled. A group answered in an
        earlier run of this project is answered the same way again, and only
        new groups are asked about.
        """
        import json
        from slate.core.domain.stitch_detect import StitchGroup, apply_groups
        groups = list(getattr(survey, "stitch_groups", []) or [])
        if not groups:
            return {}, {}
        remembered = {}
        if project_path is not None:
            try:
                remembered = json.loads(self._stitch_file(project_path, client).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                remembered = {}
        known = [g for g in groups if self._stitch_key(g) in remembered]
        new = [g for g in groups if self._stitch_key(g) not in remembered]
        accepted = []
        for group in known:
            answer = remembered[self._stitch_key(group)]
            if answer.get("merge"):
                accepted.append(StitchGroup(shot_name=answer.get("name") or group.shot_name,
                                            parts=list(group.parts), reel=group.reel))
            self.log_message(f"[STITCH] As in an earlier run: {' + '.join(group.parts)} "
                             + (f"become {accepted[-1].shot_name}" if answer.get("merge") else "stay separate"))
        decisions = {}
        if new:
            self.log_message(f"[INFO] {len(new)} possible stitch shot(s) - asking before anything is copied.")
            dialog = StitchConfirmDialog(new, self, survey=survey)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None, None
            chosen = dialog.accepted_groups()
            names = {self._stitch_key(g): g.shot_name for g in chosen}
            for group in new:
                key = self._stitch_key(group)
                decisions[key] = {"merge": key in names, "name": names.get(key, "")}
            for group in chosen:
                self.log_message(f"[STITCH] {group.describe()}")
            accepted += chosen
        mapping = apply_groups([], accepted)
        if not mapping:
            self.log_message("[STITCH] Nothing merged - every folder stays its own shot.")
        return mapping, decisions

    def _save_stitch_decisions(self, project_path: Path, client: str, decisions: dict):
        if not decisions:
            return
        import json
        path = self._stitch_file(project_path, client)
        try:
            known = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError):
            known = {}
        known.update(decisions)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(known, indent=1), encoding="utf-8")
        except OSError as exc:
            logger.warning("Stitch answers not remembered: %s", exc)

    # ---------------------------------------------------------------- the lock
    def _holder(self) -> str:
        username = str(self.user_data.get("username") or self.user_data.get("user_id") or "")
        if not username:
            return ""
        try:
            from slate.core.domain.people import label
            return label(username) or username
        except Exception:
            return username

    def _is_admin(self) -> bool:
        roles = self.user_data.get("roles") or self.user_data.get("role") or []
        try:
            from slate.core.domain.access import is_superuser
            return is_superuser(roles)
        except Exception:
            return False

    def _take_lock(self, project_path: Path) -> bool:
        from slate.core.domain.ingest_lock import IngestLock, IngestLocked, clear_lock
        from slate.gui.components.feedback import confirm, warn
        try:
            self._ingest_lock = IngestLock(project_path, holder=self._holder()).acquire()
            self._lock_timer.start()
            return True
        except IngestLocked as exc:
            info = exc.info
            # The same Slate user: on a shared workstation another person's
            # lock on this machine is still theirs.
            mine = bool(info.holder and info.holder == self._holder())
            # The person (or machine) holding it, an admin, or anyone once
            # nobody has touched it for hours - always asked, never silent.
            if mine or info.stale or self._is_admin():
                if mine:
                    why = ("The lock is yours - left behind if Slate crashed or was closed during a run "
                           f"(on {info.machine or 'a machine'}).")
                elif info.stale:
                    why = (f"Nobody has touched it for hours - the run on {info.machine or 'that machine'} "
                           "has probably stopped.")
                else:
                    why = "If that ingest is not really running (Slate crashed or was closed), you can clear the lock."
                if confirm(self, "Ingest already running",
                           f"{exc}\n\n{why} Clearing a lock that is in use lets two deliveries mix in one "
                           "scan version.", yes_label="Clear the lock and continue",
                           no_label="Cancel", destructive=True):
                    if clear_lock(project_path):
                        self.log_message(f"[WARN] Ingest lock held by {info.describe()} cleared by hand.")
                        return self._take_lock(project_path)
                return False
            warn(self, "Ingest already running",
                 f"{exc}\n\nWait for it to finish. If it is not really running, the person who started "
                 "it, or an admin, can clear the lock.")
            return False

    def _touch_lock(self):
        lock = getattr(self, "_ingest_lock", None)
        if lock is not None:
            try:
                lock.touch()
            except Exception as exc:  # pragma: no cover
                logger.debug("Ingest lock not touched: %s", exc)

    def _release_ingest_lock(self):
        self._lock_timer.stop()
        lock = getattr(self, "_ingest_lock", None)
        if lock is None:
            return
        try:
            lock.release()
        except Exception as exc:
            logger.warning("Could not release the ingest lock: %s", exc)
        finally:
            self._ingest_lock = None

    # ---------------------------------------------------------------- finished
    def _on_folder_worker_finished(self, success, total_projects, reels_created, shots_created,
                                   folders_created, message):
        worker = self.sender() if isinstance(self.sender(), FolderCreationWorker) else self.folder_creation_thread
        if worker is None or worker is not self.folder_creation_thread:
            return
        self._release_ingest_lock()
        self.folder_creation_thread = None
        try:
            worker.wait(2000)
            if not worker.isRunning():
                worker.deleteLater()
        except RuntimeError:
            pass
        report = self._write_delivery_report(worker)
        self.on_folder_creation_finished(worker, report, message)

    def _write_delivery_report(self, worker):
        """Write the report; a report that cannot be written never fails the ingest."""
        try:
            from ...core.domain.delivery_report import build_report, frame_summary, write_report
            project_path = getattr(worker, "project_dir", None) or self._pending.get("project_path")
            if not project_path:
                return None
            report = build_report(worker, worker.project_name, getattr(worker, "source_scan_path", ""))
            paths = write_report(report, project_path, getattr(worker, "client_folder", ""))
            self.log_message(f"[INFO] Delivery: {report.headline()}")
            for entry in report.incomplete[:10]:
                self.log_message(f"[WARN] SHORT: {entry['shot']} / {entry['name']} missing "
                                 f"{frame_summary(entry['missing'])}")
            if paths:
                self.log_message(f"[INFO] Report saved: {paths['report']}")
                self._last_report = paths["report"]
                self.open_report_btn.setEnabled(True)
            return {"report": report, "paths": paths}
        except Exception as exc:
            logger.warning("Delivery report failed: %s", exc)
            return None

    def on_folder_creation_finished(self, worker, delivery, message=""):
        """Say what happened - on screen for good, and once in a box with the report a click away."""
        report = (delivery or {}).get("report")
        paths = (delivery or {}).get("paths") or {}
        outcome = getattr(worker, "outcome", "completed")
        dry = bool(getattr(worker, "dry_run", False))
        moved = getattr(worker, "files_moved", 0)
        total = getattr(worker, "_total_files", 0)
        errors = getattr(worker, "errors", 0)
        skipped = getattr(worker, "files_skipped", 0)
        verb = "would be " if dry else ""
        verb += {"move": "moved", "copy": "copied"}.get(getattr(worker, "operation", "copy"), "copied")

        lines = [f"Shots: {getattr(worker, 'shots_count', 0)} in {getattr(worker, 'reels_count', 0)} reel(s)",
                 f"Files {verb}: {moved:,} of {total:,}"]
        if skipped:
            lines.append(f"Skipped (already there): {skipped:,}")
        if errors:
            lines.append(f"Failed: {errors:,} - 'Retry failed files' tries them again")
        if getattr(worker, "documents_filed", None):
            lines.append(f"Documents filed: {len(worker.documents_filed)}")
        if getattr(worker, "skipped_shots", None):
            from ...core.domain.delivery_report import DeliveryReport
            lines.append(f"Shots already in the project: "
                         f"{DeliveryReport.distinct_shots(worker.skipped_shots)}")
        new_folders = getattr(worker, "folders_created", 0)
        lines.append(f"New folders: {new_folders:,}")
        if report is not None and report.incomplete:
            lines.append(f"Short delivery: {len(report.incomplete)} sequence(s) missing "
                         f"{report.missing_frame_count} frame(s)")
        registration = getattr(worker, "registration", None)
        if registration is not None:
            if registration.ok:
                lines.append(f"Dashboard: {len(registration.created)} new shot(s)")
                for name in registration.new_scans[:8]:
                    entry = next((e for e in worker.ingested_shots if e.get("shot") == name), {})
                    lines.append(f"  {name}: new scan {entry.get('scan_version', '')}".rstrip())
                if registration.already_present:
                    lines.append(f"Already tracked: {len(registration.already_present)}")
            else:
                lines.append(f"Dashboard not updated: {registration.error}")
        elif getattr(worker, "registration_error", ""):
            lines.append(f"Dashboard not updated: {worker.registration_error}")
        if delivery is None or not paths:
            lines.append("The delivery report could not be written - see the log.")

        if outcome == "stopped":
            left = max(total - moved - errors - skipped, 0)
            where = "still on the client drive" if getattr(worker, "operation", "copy") == "move" else "not copied"
            title, level = "Stopped", "warn"
            head = f"Stopped - {moved:,} of {total:,} files {verb}, {left:,} {where}."
        elif outcome == "failed":
            title, level = "Ingest failed", "bad"
            head = f"The ingest stopped with an error: {message}"
        elif dry:
            title, level = "Dry run finished", "info"
            head = "Dry run - nothing was copied or created."
        elif errors:
            title, level = "Finished with problems", "bad"
            head = f"{errors:,} file(s) could not be brought in."
        elif report is not None and report.incomplete:
            title, level = "Short delivery", "warn"
            head = f"Frames are missing in {len(report.incomplete)} sequence(s) - see the report."
        elif not moved and not getattr(worker, "documents_filed", None):
            title, level = "Nothing new", "ok"
            head = "Nothing new - the project already has everything on this drive."
        else:
            title, level = "Ingest finished", "ok"
            head = "Everything arrived."

        colour = {"ok": Gate.OK, "warn": Gate.WARN, "bad": Gate.BAD, "info": Gate.INFO}[level]
        link = " &middot; <a href='report'>Open report</a>" if paths.get("report") else ""
        self._set_last_run(
            f"<span style='color:{colour}; font-weight:600'>{html.escape(self._last_run_stamp(worker))}: "
            f"{html.escape(title)}</span> &middot; {html.escape(lines[1])}"
            + (f" &middot; failed {errors:,}" if errors else "") + link)
        self.log_message(("[STOP] " if outcome != "completed" else "[INFO] ") + head)

        # The bar shows what came in, in the colour of the outcome - a run
        # with failures never ends full and teal.
        if total:
            self.progress_bar.setValue(int((moved + skipped) / total * 100))
        elif outcome == "completed":
            self.progress_bar.setValue(100)
        self.progress_bar.setStyleSheet(
            "" if level in ("ok", "info") else f"QProgressBar::chunk {{ background: {colour}; }}")
        self._reset_run_ui("Ready")
        self._refresh_retry_button()
        self._message(title, head + "\n\n" + "\n".join(lines), level,
                      report=paths.get("report"), folder=paths.get("folder"))

    def _last_run_stamp(self, worker=None, what="Last run") -> str:
        code = getattr(worker, "project_name", "") or self._pending.get("code") or self._inputs()[0]
        return f"{what} {code}, {datetime.now():%d %b %H:%M}".replace("  ", " ")

    def _set_last_run(self, text: str):
        self.last_run_label.setText(text)
        self.last_run_label.setVisible(self._phase == "idle")

    def _message(self, title, text, level, report=None, folder=None):
        """The completion box, with the report and its folder a click away."""
        box = QMessageBox(self)
        box.setIcon({"bad": QMessageBox.Icon.Warning, "warn": QMessageBox.Icon.Warning}.get(
            level, QMessageBox.Icon.Information))
        box.setWindowTitle(title)
        box.setText(text)
        open_report = box.addButton("Open report", QMessageBox.ButtonRole.ActionRole) if report else None
        open_folder = box.addButton("Open folder", QMessageBox.ButtonRole.ActionRole) if folder else None
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        clicked = box.clickedButton()
        if open_report is not None and clicked is open_report:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(report)))
        elif open_folder is not None and clicked is open_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ---------------------------------------------------------------- stop
    def stop_creation_process(self):
        """Stop the survey, the run or the retry, at the next file."""
        for attr in ("survey_thread", "folder_creation_thread", "retry_thread"):
            worker = getattr(self, attr, None)
            if worker is not None:
                try:
                    if worker.isRunning():
                        worker.stop()
                except RuntimeError:
                    continue
        if self._phase != "idle":
            self.progress_label.setText("Stopping after the current file…")
            self.stop_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            bar = self._status_bar()
            if bar is not None:
                bar.showMessage("Stopping Build & Ingest…", 3000)

    # ---------------------------------------------------------------- retry
    def _project_path_for_retry(self) -> Optional[Path]:
        code, root, _s, _r = self._inputs()
        if not code or not root:
            return None
        return self._planned_project_path()

    def _refresh_retry_button(self):
        """What is waiting in every run of this project: failed files, and files a stopped run never reached."""
        if not hasattr(self, "retry_btn"):
            return
        from slate.core.domain.ingest_retry import manifests_with_failures
        path = self._project_path_for_retry()
        waiting = manifests_with_failures(path) if path is not None and path.is_dir() else []
        self._retry_manifests = [item["manifest"] for item in waiting]
        failed = sum(item["failed"] for item in waiting)
        stopped = sum(item["pending"] for item in waiting)
        self.retry_btn.setEnabled(bool(waiting) and self._phase == "idle")
        if stopped:
            self.retry_btn.setText(f"Finish the stopped run ({failed + stopped:,} files)")
            self.retry_btn.setToolTip("Bring in the files a stopped run did not reach (and any that failed), "
                                      "into the same scan version.")
        elif failed:
            self.retry_btn.setText(f"Retry {failed:,} failed file(s)")
            self.retry_btn.setToolTip(f"Bring in the files that failed, from {len(waiting)} run(s) of this project.")
        else:
            self.retry_btn.setText("Retry failed files")
            self.retry_btn.setToolTip("Nothing is waiting for this project.")

    def retry_failed_files(self):
        """Re-attempt what every run of this project could not bring in, in the background."""
        manifests = list(self._retry_manifests)
        if not manifests or self._phase != "idle":
            return
        project = self._project_path_for_retry()
        if project is None or not self._take_lock(project):
            return
        self._set_phase("retry")
        self._log_run_header(f"{datetime.now():%d %b %Y %H:%M}  retry")
        worker = RetryWorker(manifests, fast_mode=self.fast_mode_cb.isChecked())
        worker.progress.connect(lambda d, t, n: self.update_folder_creator_progress(
            int(d / max(t, 1) * 100), f"Retrying {d} of {t}: {n}"))
        worker.done.connect(self._on_retry_done)
        self.retry_thread = worker
        worker.start()

    def _on_retry_done(self, result):
        self.retry_thread = None
        self._release_ingest_lock()
        self.log_message(f"[INFO] Retry: {result.summary()}")
        for entry in result.still_failing[:10]:
            self.log_message(f"[ERR] {entry['file']}: {entry['error']}")
        if result.reports:
            self._last_report = result.reports[0]
            self.open_report_btn.setEnabled(True)
            self.log_message(f"[INFO] Report updated: {result.reports[0]}")
        self._reset_run_ui()
        self._refresh_retry_button()
        level = "ok" if result.ok and not result.still_failing and not result.missing else "warn"
        if level == "ok":
            outcome = f"all {len(result.recovered):,} recovered - delivery complete"
        else:
            outcome = result.summary()
        colour = Gate.OK if level == "ok" else Gate.WARN
        link = " &middot; <a href='report'>Open report</a>" if result.reports else ""
        self._set_last_run(f"<span style='color:{colour}; font-weight:600'>"
                           f"{html.escape(self._last_run_stamp(what='Retry'))}: {html.escape(outcome)}</span>{link}")
        self.progress_bar.setStyleSheet("")
        self._message("Retry finished" if level == "ok" else "Some files still failing",
                      result.summary(), level, report=result.reports[0] if result.reports else None)

    # ---------------------------------------------------------------- reset
    def clear_all(self):
        """Back to a clean form; the projects folder and the log stay."""
        if self._phase != "idle":
            return
        for field in (self.project_name_input, self.scan_source_input, self.target_reel_input):
            field.clear()
        self.copy_radio.setChecked(True)
        global_settings = self._settings().get("global_settings") or {}
        self.dry_run_cb.setChecked(bool(global_settings.get("dry_run_enabled", False))
                                   if isinstance(global_settings, dict) else False)
        self.fast_mode_cb.setChecked(False)
        self.add_to_dashboard_cb.setChecked(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText(IDLE_STATUS)
        self._last_survey = None
        self.check_destination_status()
        self.stats_label.setText(IDLE_HINT)
        self.update_preview(self.template_combo.currentData())
