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
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QRadioButton, QScrollArea, QSplitter, QTextEdit, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ...core.infra.config_manager import ConfigManager
from ...core.worker_threads import FolderCreationWorker
from ...core.domain.naming import name_problem
from ...utils.text_utils import normalize_name
from ...gui.dialogs.custom_template_dialog import CustomTemplateDialog, template_key
from ...gui.dialogs.stitch_confirm_dialog import StitchConfirmDialog
from ...gui.dialogs.ingest_preflight_dialog import IngestPreflightDialog, size_text
from slate.core.infra.gate import Gate
from slate.gui.core.controls import form_layout, make_button, plain

logger = logging.getLogger(__name__)

IDLE_STATUS = "Ready"
IDLE_HINT = "Enter a project code, choose the projects folder and the client drive."
DEFAULT_SHOT_FOLDERS = ("01_Scan", "07_Comp", "08_Output")
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

    def __init__(self, manifest, fast_mode=False):
        super().__init__()
        self.manifest = manifest
        self.fast_mode = fast_mode
        self._stop = False
        self.result = None

    def stop(self):
        self._stop = True

    def run(self):
        from slate.core.domain.ingest_retry import RetryResult, retry_failures
        try:
            self.result = retry_failures(self.manifest, fast_mode=self.fast_mode,
                                         progress=lambda d, t, n: self.progress.emit(d, t, n),
                                         should_stop=lambda: self._stop)
        except Exception as exc:
            logger.exception("Retry failed: %s", exc)
            self.result = RetryResult(error=str(exc))
        self.done.emit(self.result)


def find_project_folder(root: str, code: str) -> Optional[Path]:
    """
    A folder at or above `root` that is this project, by name - 'My-Show'
    for MYSHOW. The coordinator may have picked a folder inside the project.
    """
    if not root or not code:
        return None
    wanted = normalize_name(code)
    current = Path(root)
    while True:
        if normalize_name(current.name) == wanted:
            return current
        if current.parent == current:
            return None
        current = current.parent


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
        self._log_lines: List[Tuple[str, str]] = []
        self._pending: Dict[str, Any] = {}

        self.setup_ui()
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
        log_layout.addWidget(self.log_text)
        self.right_splitter.addWidget(log_card)
        self.right_splitter.setStretchFactor(0, 3)
        self.right_splitter.setStretchFactor(1, 1)
        self.right_splitter.setSizes([520, 160])
        return self.right_splitter

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
        self.stats_label = QLabel(IDLE_HINT)
        self.stats_label.setWordWrap(True)
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
        self.last_run_label.setWordWrap(True)
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
            problem = name_problem(code, "The project code")
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
            for key in keys:
                info = self._templates().get(key)
                name = info.get("name", key) if isinstance(info, dict) else str(key)
                self.template_combo.addItem(str(name), key)
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
        name = self.template_combo.currentText()
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
        self.load_templates_to_ui(select_key=key)
        self.on_template_activated(self.template_combo.currentIndex())
        self.template_changed.emit(data)
        toast(self, f"Template '{data['name']}' saved.", "success")

    def delete_current_template(self):
        from slate.gui.components.feedback import confirm, toast, warn
        key = self.template_combo.currentData()
        if not key or key in self._builtin_keys():
            return
        name = self.template_combo.currentText()
        if not confirm(self, "Delete template", f"Delete the template '{name}'? Projects already built "
                       "with it are not changed.", yes_label="Delete template", destructive=True):
            return
        templates = dict(self._templates())
        templates.pop(key, None)
        if not self.config_manager.save_templates(templates) or key in self._templates():
            warn(self, "Delete template", f"'{name}' could not be deleted.")
            return
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
            tree.topLevelItem(0).setText(0, self.project_name_input.text().strip() or "Project")

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
        structure = info.get("structure") if isinstance(info.get("structure"), dict) else info
        version_folders = structure.get("scan_version_folders") or ["Denoise"]
        code = self.project_name_input.text().strip()
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
        except Exception as e:
            logger.warning(f"Could not restore last paths: {e}")

    # ================================================================ the log
    def log_message(self, message: str):
        """Add a line to the log, coloured by what it says."""
        stamp = datetime.now().strftime("%H:%M:%S")
        text = str(message)
        tag = text[1:text.index("]")] if text.startswith("[") and "]" in text else ""
        self._log_lines.append((tag, f"[{stamp}] {text}"))
        if len(self._log_lines) > 5000:
            self._log_lines = self._log_lines[-4000:]
        if self._log_visible(tag):
            self.log_text.append(self._log_html(tag, f"[{stamp}] {text}"))
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
        self._paused = state == "paused"
        if self._paused:
            self.progress_label.setText("Paused")
            self.progress_label.setStyleSheet(f"color: {Gate.WARN}; font-weight: 600;")
        else:
            self.progress_label.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600;")

    def toggle_pause(self):
        worker = self.folder_creation_thread
        if worker is None:
            return
        if not self._paused:
            worker.pause()
            self._on_worker_state("paused")
            self.pause_btn.setText("Resume")
        else:
            worker.resume()
            self._on_worker_state("running")
            self.pause_btn.setText("Pause")
            self.progress_label.setText("Resuming…")

    # ================================================================ phases
    def _set_phase(self, phase: str):
        """One place that says what the buttons do in each phase."""
        self._phase = phase
        busy = phase != "idle"
        self.is_processing = busy
        self.stop_btn.setEnabled(phase in ("survey", "run", "retry"))
        self.pause_btn.setEnabled(phase == "run")
        if phase != "run":
            self._paused = False
            self.pause_btn.setText("Pause")
            self.progress_label.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600;")
        for widget in (self.project_card, self.scan_card, self.clear_btn):
            widget.setEnabled(not busy)
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
        self.progress_label.setText("Looking at the client drive…")
        self.right_splitter.setSizes([360, 320])
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
        structure = info.get("structure") if isinstance(info, dict) and isinstance(info.get("structure"), dict) \
            else (info or {})
        version_folders = structure.get("scan_version_folders") or ["Denoise"]
        return (base, production, outsource, list(shots) or list(DEFAULT_SHOT_FOLDERS)), defaulted, \
            version_folders, (info or {}).get("name", key)

    def finalize_creation_process(self, survey=None):
        """Stitches, pre-flight, lock - then the worker. Any 'no' leaves everything untouched."""
        pending = self._pending
        code, root = pending["code"], pending["root"]
        template_data, defaulted, version_folders, template_name = self._template_data(pending["template"])

        stitch_mapping = self._confirm_stitch_shots(survey)
        if stitch_mapping is None:
            self.log_message("[STOP] Cancelled at the stitch question - nothing was copied.")
            self._reset_run_ui()
            return

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

        from slate.core.workers.structure import reels_root_for
        reels_root = reels_root_for(project_path, code, target_root)
        scan_root = next((f.split("/")[0] for f in template_data[3] if "scan" in f.lower()), "01_Scan")
        if project_path.is_dir():
            skipped = survey.mark_unchanged(reels_root, scan_root, stitch_mapping)
            if skipped:
                self.log_message(f"[INFO] {skipped} shot(s) are already in the project unchanged.")
            from slate.core.domain.ingest_survey import mark_documents_filed
            client = next((b for b in template_data[0] if b.lower().startswith("01_")), "01_Frm Client")
            if mark_documents_filed(survey, project_path / client):
                self.log_message("[INFO] The documents on the drive were filed by an earlier run.")

        dialog = IngestPreflightDialog(
            survey, project_code=code, project_path=project_path,
            operation=MOVE if self.move_radio.isChecked() else COPY,
            dry_run=self.dry_run_cb.isChecked(), template_name=template_name,
            shot_folders_defaulted=defaulted, stitch_mapping=stitch_mapping,
            long_paths=survey.long_paths(project_path, reels_root, scan_root),
            project_choice=project_choice, project_exists=project_path.is_dir(), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.log_message("[STOP] Cancelled at the check - nothing was copied.")
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

        # One ingest per project: two runs would both claim the same scan
        # version. Taken before anything on screen says "running".
        if not dry_run:
            if not self._take_lock(project_path):
                self._reset_run_ui()
                return

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
        )
        worker.log_signal.connect(self.log_message)
        worker.progress_signal.connect(self._on_folder_worker_progress)
        worker.state_signal.connect(self._on_worker_state)
        worker.finished_signal.connect(self._on_folder_worker_finished)
        self.folder_creation_thread = worker
        worker.start()

    def _confirm_stitch_shots(self, survey):
        """The confirmed {(reel, folder): shot} mapping, {} when none, None when cancelled."""
        groups = list(getattr(survey, "stitch_groups", []) or [])
        if not groups:
            return {}
        self.log_message(f"[INFO] {len(groups)} possible stitch shot(s) - asking before anything is copied.")
        dialog = StitchConfirmDialog(groups, self, survey=survey)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        mapping = dialog.mapping()
        for group in dialog.accepted_groups():
            self.log_message(f"[STITCH] {group.describe()}")
        if not mapping:
            self.log_message("[STITCH] Nothing merged - every folder stays its own shot.")
        return mapping

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
            return True
        except IngestLocked as exc:
            info = exc.info.describe()
            if self._is_admin():
                if confirm(self, "Ingest already running",
                           f"{exc}\n\nIf that ingest is not really running (Slate crashed or was closed), "
                           "you can clear the lock. Clearing a lock that is in use lets two deliveries "
                           "mix in one scan version.", yes_label="Clear the lock and continue",
                           no_label="Cancel", destructive=True):
                    if clear_lock(project_path):
                        self.log_message(f"[WARN] Ingest lock held by {info} cleared by hand.")
                        return self._take_lock(project_path)
                return False
            warn(self, "Ingest already running",
                 f"{exc}\n\nWait for it to finish. If it is not really running, ask an admin to "
                 "clear the lock.")
            return False

    def _release_ingest_lock(self):
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
                if not report.dry_run:
                    self._last_manifest = paths.get("manifest")
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
            lines.append(f"Shots already in the project: {len(worker.skipped_shots)}")
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
        else:
            title, level = "Ingest finished", "ok"
            head = "Everything arrived."

        stamp = datetime.now().strftime("%H:%M")
        colour = {"ok": Gate.OK, "warn": Gate.WARN, "bad": Gate.BAD, "info": Gate.INFO}[level]
        link = " &middot; <a href='report'>Open report</a>" if paths.get("report") else ""
        self.last_run_label.setText(
            f"<span style='color:{colour}; font-weight:600'>Last run {stamp}: {html.escape(title)}</span>"
            f" &middot; {html.escape(lines[1])}"
            + (f" &middot; failed {errors:,}" if errors else "") + link)
        self.last_run_label.setVisible(True)
        self.log_message(("[STOP] " if outcome != "completed" else "[INFO] ") + head)

        if outcome == "completed":
            self.progress_bar.setValue(100)
        self._reset_run_ui("Ready")
        self._refresh_retry_button()
        self._message(title, head + "\n\n" + "\n".join(lines), level,
                      report=paths.get("report"), folder=paths.get("folder"))

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
        if not hasattr(self, "retry_btn"):
            return
        from slate.core.domain.ingest_retry import latest_manifest, load_failures
        path = self._project_path_for_retry()
        manifest = latest_manifest(path) if path is not None and path.is_dir() else None
        failures = load_failures(manifest) if manifest else []
        self._last_manifest = str(manifest) if failures else None
        self.retry_btn.setEnabled(bool(failures) and self._phase == "idle")
        self.retry_btn.setText(f"Retry {len(failures)} failed file(s)" if failures else "Retry failed files")

    def retry_failed_files(self):
        """Re-attempt the files the newest run could not bring in, in the background."""
        manifest = self._last_manifest
        if not manifest or self._phase != "idle":
            return
        project = self._project_path_for_retry()
        if project is None or not self._take_lock(project):
            return
        self._set_phase("retry")
        self._log_run_header(f"{datetime.now():%d %b %Y %H:%M}  retry")
        worker = RetryWorker(manifest, fast_mode=self.fast_mode_cb.isChecked())
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
        self._reset_run_ui()
        self._refresh_retry_button()
        level = "ok" if result.ok and not result.still_failing else "warn"
        self._message("Retry finished" if level == "ok" else "Some files still failing",
                      result.summary(), level)

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
