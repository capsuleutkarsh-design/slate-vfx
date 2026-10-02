"""
CAP Rename: rename many files at once, with a preview you can trust.

The rules - what each file would be called, which renames can work, doing it
and undoing it - live in slate/core/domain/batch_rename.py, without Qt. This
screen shows the preview, keeps it honest while a rename runs, and offers
"Undo last rename" from Slate's own journal instead of a .bat beside the plates.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QEvent, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QSizePolicy, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..core.domain import batch_rename as br
from ..core.infra.config_manager import ConfigManager
from .core.controls import make_button, form_layout
from .core.icons import icon as draw_icon
from .core.table_style import style_table, set_cell_status
from .core.empty_state import EmptyState
from slate.core.infra.gate import Gate

logger = logging.getLogger(__name__)

# Files nobody means to rename when a whole folder is added.
_SKIP_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}

SETTINGS_KEY = "cap_rename"


class RenameWorker(QThread):
    """Renames the files in the background (see batch_rename.rename_files)."""

    progress_signal = Signal(int, str)
    finished_signal = Signal(bool, str, int)   # success, message, count

    def __init__(self, rename_pairs, user: str = "", journal_dir=None):
        super().__init__()
        self.rename_pairs = list(rename_pairs)
        self.user = user
        self.journal_dir = journal_dir
        self._is_running = True
        self.outcome: Optional[br.RenameOutcome] = None
        self.journal: Optional[Path] = None

    def stop(self):
        """Stop between files. Files already staged go back under their own names."""
        self._is_running = False

    def run(self):
        total = len(self.rename_pairs)

        def progress(done, _total, name):
            pct = int(done / max(1, total) * 100)
            self.progress_signal.emit(pct, f"Renaming {min(done, total)} of {total}…")

        try:
            outcome = br.rename_files(self.rename_pairs,
                                      should_stop=lambda: not self._is_running,
                                      progress=progress)
        except Exception as exc:   # a disk vanishing mid-run
            logger.exception("Rename run failed: %s", exc)
            self.outcome = br.RenameOutcome()
            self.finished_signal.emit(False, "The rename stopped because of an error - see the log.", 0)
            return

        self.outcome = outcome
        # The journal is written only once something was renamed, so a run
        # that renamed nothing leaves nothing behind.
        self.journal = br.write_journal(outcome.renamed, user=self.user,
                                        app_dir=self.journal_dir)

        if outcome.cancelled:
            self.finished_signal.emit(False, "Rename cancelled - nothing was renamed.", 0)
        elif outcome.failed:
            message = (f"{len(outcome.failed)} file(s) could not be renamed - see the log. "
                       f"{outcome.count} renamed.")
            for path, why in outcome.failed:
                logger.warning("CAP Rename could not rename %s: %s", path, why)
            self.finished_signal.emit(False, message, outcome.count)
        else:
            self.finished_signal.emit(True, f"Renamed {outcome.count} file(s).", outcome.count)


class CapRenameTab(QWidget):
    """Batch renaming: find and replace, or number as a sequence."""

    def __init__(self, config_manager: Optional[ConfigManager] = None, user_data: Optional[dict] = None):
        super().__init__()
        self.config_manager = config_manager
        self.user_data = dict(user_data or {})
        self.files: List[Path] = []
        self.preview_map = []
        self._plan: br.RenamePlan = br.RenamePlan()
        self._conflicts: List[str] = []
        self.worker: Optional[RenameWorker] = None
        self._is_closing = False
        self._last_folder = ""
        self._busy = False
        self.setAcceptDrops(True)
        self.setup_ui()
        if config_manager is not None:
            self.apply_global_settings(getattr(config_manager, "settings", {}) or {})
        self.update_preview()

    # ------------------------------------------------------------ settings
    def _settings(self) -> Dict[str, Any]:
        settings = getattr(self.config_manager, "settings", None)
        if not isinstance(settings, dict):
            return {}
        value = settings.get(SETTINGS_KEY)
        return value if isinstance(value, dict) else {}

    def apply_global_settings(self, settings: Dict[str, Any]):
        """The last folder, 'Keep extension' and the last mode, remembered per machine."""
        if not isinstance(settings, dict):
            return
        mine = settings.get(SETTINGS_KEY) if isinstance(settings.get(SETTINGS_KEY), dict) else {}
        self._last_folder = str(mine.get("last_folder") or "")
        widgets = (self.file_only_cb, self.mode_tabs)
        for w in widgets:
            w.blockSignals(True)
        try:
            self.file_only_cb.setChecked(bool(mine.get("keep_extension", True)))
            self.mode_tabs.setCurrentIndex(1 if mine.get("mode") == br.MODE_SEQUENCE else 0)
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _remember(self, **values):
        settings = getattr(self.config_manager, "settings", None)
        if not isinstance(settings, dict):
            return
        mine = dict(self._settings())
        if all(mine.get(k) == v for k, v in values.items()):
            return
        mine.update(values)
        settings[SETTINGS_KEY] = mine
        save = getattr(self.config_manager, "save_settings", None)
        if callable(save):
            try:
                save(settings)
            except Exception as exc:
                logger.debug("CAP Rename settings not saved: %s", exc)

    # ------------------------------------------------------------ closing
    def busy_reason(self):
        worker = self.worker
        try:
            if worker is not None and worker.isRunning():
                return "CAP Rename is still renaming files."
        except RuntimeError:
            pass
        return None

    def shutdown(self, timeout_ms: int = 15000) -> bool:
        """Let a running rename finish: stopping between passes is handled, but a clean finish is better."""
        worker = self.worker
        try:
            if worker is None or not worker.isRunning():
                return True
            from PySide6.QtCore import QDeadlineTimer
            from PySide6.QtWidgets import QApplication
            deadline = QDeadlineTimer(int(timeout_ms))
            while worker.isRunning() and not deadline.hasExpired():
                worker.wait(100)
                QApplication.processEvents()
            return not worker.isRunning()
        except RuntimeError:
            return True

    def _cleanup_worker(self, timeout_ms: int = 2000):
        """Never deleteLater a thread that is still renaming."""
        worker = self.worker
        if worker is None:
            return
        try:
            if worker.isRunning():
                worker.stop()
                worker.wait(timeout_ms)
            if not worker.isRunning():
                worker.deleteLater()
        except RuntimeError as exc:
            logger.debug("CAP rename worker cleanup skipped: %s", exc)
        self.worker = None

    # ------------------------------------------------------------ layout
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Gate.SPACE_4, Gate.SPACE_3, Gate.SPACE_4, Gate.SPACE_3)
        layout.setSpacing(Gate.SPACE_2)

        self.mode_tabs = QTabWidget()
        self.mode_tabs.setDocumentMode(True)
        self.tab_replace = QWidget()
        self.setup_replace_ui()
        self.mode_tabs.addTab(self.tab_replace, "Find and replace")
        self.mode_tabs.setTabIcon(0, draw_icon("search"))
        self.mode_tabs.setTabToolTip(0, "Find text in the names and replace it. The clean-up options act on "
                                        "the name before the extension.")
        self.tab_sequence = QWidget()
        self.setup_sequence_ui()
        self.mode_tabs.addTab(self.tab_sequence, "Number as a sequence")
        self.mode_tabs.setTabIcon(1, draw_icon("sequence"))
        self.mode_tabs.setTabToolTip(1, "Rename the files into one numbered sequence, in the order of the list.")
        self.mode_tabs.currentChanged.connect(self._mode_changed)
        self.mode_tabs.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        # The table is the main content: the options sit in a splitter above
        # it and give way, so a laptop screen still shows a screenful of rows.
        table_panel = QWidget()
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(Gate.SPACE_2)
        table_layout.addLayout(self._build_list_toolbar())

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Original name", "New name", "Status"])
        style_table(self.table, {"Original name": "stretch", "New name": "stretch",
                                 "Status": ("fixed", 130)})
        from slate.gui.components.table_tools import setup_table
        setup_table(self.table, sortable=False)
        self.table.setAcceptDrops(True)
        self.table.viewport().setAcceptDrops(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.table.itemSelectionChanged.connect(self._sync_list_buttons)
        table_layout.addWidget(self.table, 1)
        self.table_empty = EmptyState.over(
            self.table, "Nothing to rename yet",
            "Add files, or drop files or a folder here, to preview their new names.", glyph="sequence")

        # Delete takes the selected files out of the list (never off the disk).
        self.table.installEventFilter(self)


        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.mode_tabs)
        self.splitter.addWidget(table_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        layout.addWidget(self.splitter, 1)

        # Actions
        action_layout = QHBoxLayout()
        action_layout.setSpacing(Gate.SPACE_2)
        self.help_btn = make_button("Help", "ghost", on_click=self.show_help_dialog,
                                    tooltip="How CAP Rename works")
        self.undo_btn = make_button("Undo last rename", "secondary", on_click=self.undo_last_rename,
                                    icon="undo")
        action_layout.addWidget(self.help_btn)
        action_layout.addWidget(self.undo_btn)
        action_layout.addSpacing(Gate.SPACE_3)
        # How many files, how many change, how many cannot - at a glance.
        self.summary_label = QLabel()
        self.summary_label.setStyleSheet(f"color: {Gate.TEXT_2};")
        action_layout.addWidget(self.summary_label)
        action_layout.addStretch()

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(f"color: {Gate.TEXT_2};")
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        action_layout.addWidget(self.progress_label)
        action_layout.addWidget(self.progress_bar)

        self.cancel_btn = make_button("Cancel", "secondary", on_click=self.cancel_rename,
                                      tooltip="Stop renaming. Files not yet renamed keep their names.")
        self.cancel_btn.setVisible(False)
        action_layout.addWidget(self.cancel_btn)
        self.rename_btn = make_button("Rename files", "primary", on_click=self.execute_rename)
        self.rename_btn.setMinimumWidth(140)
        self.rename_btn.setEnabled(False)
        action_layout.addWidget(self.rename_btn)
        layout.addLayout(action_layout)

        self._sync_undo_button()

    def _build_list_toolbar(self):
        bar = QHBoxLayout()
        bar.setSpacing(Gate.SPACE_2)
        self.load_btn = make_button("Add files…", "secondary", on_click=self.load_files, icon="plus",
                                    tooltip="Add files to the list (they are added to what is already there).")
        self.add_folder_btn = make_button("Add folder…", "secondary", on_click=self.load_folder, icon="folder",
                                          tooltip="Add every file in a folder (not its sub-folders).")
        self.remove_btn = make_button("Remove selected", "ghost", on_click=self.remove_selected,
                                      tooltip="Take the selected files out of the list (Delete). Nothing on disk changes.")
        self.clear_btn = make_button("Clear list", "ghost", on_click=self.clear_list,
                                     tooltip="Empty the list. Nothing on disk changes.")
        self.up_btn = make_button("", "ghost", on_click=lambda: self.move_selected(-1), icon="chevron-up",
                                  tooltip="Move the selected files up (the order decides the numbering).")
        self.down_btn = make_button("", "ghost", on_click=lambda: self.move_selected(1), icon="chevron-down",
                                    tooltip="Move the selected files down (the order decides the numbering).")
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Sort by name", "Sort by date modified", "Custom order"])
        self.sort_combo.setToolTip("The order of the list - and of the numbers in 'Number as a sequence'.")
        self.sort_combo.activated.connect(self._sort_chosen)
        for widget in (self.load_btn, self.add_folder_btn, self.remove_btn, self.clear_btn):
            bar.addWidget(widget)
        bar.addStretch()
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["Show all files", "Show changes", "Show conflicts"])
        self.filter_combo.setToolTip("Show every file, only the ones whose name changes, or only the ones that cannot be renamed.")
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.filter_combo)
        bar.addWidget(self.sort_combo)
        bar.addWidget(self.up_btn)
        bar.addWidget(self.down_btn)
        return bar

    def setup_replace_ui(self):
        layout = QVBoxLayout(self.tab_replace)
        layout.setContentsMargins(Gate.SPACE_2, Gate.SPACE_2, Gate.SPACE_2, Gate.SPACE_1)
        layout.setSpacing(Gate.SPACE_2)

        row = QHBoxLayout()
        row.setSpacing(Gate.SPACE_2)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("e.g. shot_v01")
        self.search_edit.setToolTip(
            "Text to find. Matching ignores upper and lower case unless 'Case sensitive' is ticked.\n"
            "With 'Use regex' this is a Python regular expression, e.g. _(\\d+)$")
        self.search_edit.textChanged.connect(self.update_preview)
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("e.g. shot_v02")
        self.replace_edit.setToolTip(
            "What to put in its place.\nWith 'Use regex', refer to a bracketed group as \\1, \\2 "
            "(Python style - $1 does not work).")
        self.replace_edit.textChanged.connect(self.update_preview)
        row.addWidget(QLabel("Find"))
        row.addWidget(self.search_edit, 1)
        row.addWidget(QLabel("Replace with"))
        row.addWidget(self.replace_edit, 1)
        layout.addLayout(row)

        self.pattern_error_label = QLabel("")
        self.pattern_error_label.setWordWrap(True)
        self.pattern_error_label.setStyleSheet(f"color: {Gate.BAD};")
        self.pattern_error_label.setVisible(False)
        layout.addWidget(self.pattern_error_label)

        options = QHBoxLayout()
        options.setSpacing(Gate.SPACE_4)
        self.regex_cb = QCheckBox("Use regex")
        self.regex_cb.setToolTip("Treat Find as a Python regular expression. Groups are written \\1 in Replace with.")
        self.regex_cb.toggled.connect(self.update_preview)
        self.case_cb = QCheckBox("Case sensitive")
        self.case_cb.setToolTip("When ticked, 'Shot' does not match 'shot'. Off by default.")
        self.case_cb.toggled.connect(self.update_preview)
        self.file_only_cb = QCheckBox("Keep extension")
        self.file_only_cb.setChecked(True)
        self.file_only_cb.setToolTip("Leave .exr, .jpg ... exactly as they are. Untick to let Find and replace "
                                     "change the extension too; rows where it changes are flagged.")
        self.file_only_cb.toggled.connect(self._keep_extension_toggled)
        for w in (self.regex_cb, self.case_cb, self.file_only_cb):
            options.addWidget(w)
        options.addSpacing(Gate.SPACE_4)

        self.lower_cb = QCheckBox("To lowercase")
        self.lower_cb.setToolTip("Lower-case the name.")
        self.lower_cb.toggled.connect(self.update_preview)
        self.cleanup_cb = QCheckBox("Sanitize")
        self.cleanup_cb.setToolTip("Spaces and dots become _, symbols are removed. Letters in any language are kept.")
        self.cleanup_cb.toggled.connect(self.update_preview)
        self.padding_cb = QCheckBox("Re-pad numbers to")
        self.padding_cb.setToolTip("Write the last number in the name with this many digits: _1 -> _0001.")
        self.padding_cb.toggled.connect(self.toggle_padding)
        self.padding_spin = QSpinBox()
        self.padding_spin.setRange(1, 8)
        self.padding_spin.setValue(4)
        self.padding_spin.setToolTip("Digits, e.g. 4 -> 0001.")
        self.padding_spin.setEnabled(False)
        self.padding_spin.valueChanged.connect(self.update_preview)
        self.padding_label = QLabel("digits")
        for w in (self.lower_cb, self.cleanup_cb, self.padding_cb, self.padding_spin, self.padding_label):
            options.addWidget(w)
        options.addStretch()
        layout.addLayout(options)

    def setup_sequence_ui(self):
        layout = QVBoxLayout(self.tab_sequence)
        layout.setContentsMargins(Gate.SPACE_2, Gate.SPACE_2, Gate.SPACE_2, Gate.SPACE_1)
        layout.setSpacing(Gate.SPACE_2)

        row = QHBoxLayout()
        row.setSpacing(Gate.SPACE_2)
        self.seq_base = QLineEdit()
        self.seq_base.setPlaceholderText("Required, e.g. shot_010_v01_")
        self.seq_base.setToolTip("The name every file gets before its number.")
        self.seq_base.textChanged.connect(self.update_preview)
        self.seq_start = QSpinBox()
        self.seq_start.setRange(0, 999999)
        self.seq_start.setValue(1001)
        self.seq_start.setToolTip("The number of the first file (VFX plates usually start at 1001).")
        self.seq_start.valueChanged.connect(self.update_preview)
        self.seq_step = QSpinBox()
        self.seq_step.setRange(1, 100)
        self.seq_step.setValue(1)
        self.seq_step.setToolTip("How much each number goes up by (10 gives 1010, 1020 ...).")
        self.seq_step.valueChanged.connect(self.update_preview)
        self.seq_padding = QSpinBox()
        self.seq_padding.setRange(1, 8)
        self.seq_padding.setValue(4)
        self.seq_padding.setToolTip("How many digits each number is written with (4 -> 0001).")
        self.seq_padding.valueChanged.connect(self.update_preview)
        row.addWidget(QLabel("Base name"))
        row.addWidget(self.seq_base, 1)
        for label, spin in (("Start frame", self.seq_start), ("Step", self.seq_step),
                            ("Padding", self.seq_padding)):
            row.addSpacing(Gate.SPACE_2)
            row.addWidget(QLabel(label))
            row.addWidget(spin)
        layout.addLayout(row)

        warn_row = QHBoxLayout()
        self.padding_warning = QLabel("")
        self.padding_warning.setStyleSheet(f"color: {Gate.WARN};")
        self.padding_fix_btn = make_button("Use more digits", "ghost", on_click=self._raise_padding)
        warn_row.addWidget(self.padding_warning)
        warn_row.addWidget(self.padding_fix_btn)
        warn_row.addStretch()
        layout.addLayout(warn_row)
        self.padding_warning.setVisible(False)
        self.padding_fix_btn.setVisible(False)

    # ------------------------------------------------------------ options
    def toggle_padding(self, checked):
        self.padding_spin.setEnabled(checked)
        self.update_preview()

    def _keep_extension_toggled(self, checked):
        self._remember(keep_extension=bool(checked))
        self.update_preview()

    def _mode_changed(self, index):
        self._remember(mode=br.MODE_SEQUENCE if index == 1 else br.MODE_REPLACE)
        self.update_preview()

    def _raise_padding(self):
        needed = getattr(self._plan, "padding_needed", 0)
        if needed:
            self.seq_padding.setValue(needed)

    def rules(self) -> br.RenameRules:
        if self.mode_tabs.currentIndex() == 1:
            return br.RenameRules(mode=br.MODE_SEQUENCE, base_name=self.seq_base.text(),
                                  start=self.seq_start.value(), step=self.seq_step.value(),
                                  padding=self.seq_padding.value())
        return br.RenameRules(
            mode=br.MODE_REPLACE, search=self.search_edit.text(), replace=self.replace_edit.text(),
            use_regex=self.regex_cb.isChecked(), case_sensitive=self.case_cb.isChecked(),
            keep_extension=self.file_only_cb.isChecked(), lowercase=self.lower_cb.isChecked(),
            sanitize=self.cleanup_cb.isChecked(), repad=self.padding_cb.isChecked(),
            repad_digits=self.padding_spin.value())

    # ------------------------------------------------------------ preview
    def update_preview(self, *_):
        plan = br.plan(self.files, self.rules())
        self._plan = plan
        self.preview_map = plan.renames
        self._conflicts = [f"{row.source.name} - {row.reason}" for row in plan.conflicts]

        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(plan.rows))
            for row_index, row in enumerate(plan.rows):
                self._add_row(row_index, row)
        finally:
            self.table.setUpdatesEnabled(True)

        error = plan.pattern_error
        self.pattern_error_label.setVisible(bool(error))
        self.pattern_error_label.setText(f"The Find pattern is not valid: {error}" if error else "")
        self.search_edit.setStyleSheet(f"QLineEdit {{ border: 1px solid {Gate.BAD}; }}" if error else "")

        needed = plan.padding_needed
        self.padding_warning.setVisible(bool(needed))
        self.padding_fix_btn.setVisible(bool(needed))
        if needed:
            self.padding_warning.setText(
                f"The last numbers need {needed} digits - with {self.seq_padding.value()} the "
                f"sequence mixes widths and tools stop seeing it as one sequence.")
            self.padding_fix_btn.setText(f"Use {needed} digits")

        self._update_summary()
        self._apply_filter()
        self._sync_rename_button()
        self._sync_list_buttons()

    def _add_row(self, row_index: int, row: br.RowPlan):
        original = QTableWidgetItem(row.source.name)
        original.setToolTip(str(row.source))
        new_item = QTableWidgetItem(row.new_name)
        status = QTableWidgetItem(row.status)
        tip = row.reason or row.warning
        if row.status == br.CONFLICT:
            set_cell_status(new_item, "bad")
            set_cell_status(status, "bad", background=False)
        elif row.warning:
            set_cell_status(new_item, "warn")
            set_cell_status(status, "warn", background=False)
            status.setText(f"{br.WILL_RENAME} - check")
        elif row.status == br.WILL_RENAME:
            set_cell_status(new_item, "accent")
            set_cell_status(status, "accent", background=False)
        if tip:
            new_item.setToolTip(tip)
            status.setToolTip(tip)
        self.table.setItem(row_index, 0, original)
        self.table.setItem(row_index, 1, new_item)
        self.table.setItem(row_index, 2, status)

    def _update_summary(self):
        counts = self._plan.counts
        if not counts["files"]:
            self.summary_label.setText("")
            return
        parts = [f"{counts['files']} file(s)", f"{counts['rename']} will be renamed"]
        if counts["conflict"]:
            parts.append(f"{counts['conflict']} conflict(s)")
        if counts["warning"]:
            parts.append(f"{counts['warning']} to check")
        self.summary_label.setText("  ·  ".join(parts))

    def _apply_filter(self, *_):
        choice = self.filter_combo.currentIndex()
        for index, row in enumerate(self._plan.rows):
            if choice == 1:
                hide = row.status == br.UNCHANGED
            elif choice == 2:
                hide = row.status != br.CONFLICT
            else:
                hide = False
            self.table.setRowHidden(index, hide)

    def _sync_rename_button(self):
        """Rename only when every row can really be renamed - and never twice at once."""
        running = self._running()
        can = self._plan.can_run and not running
        self.rename_btn.setEnabled(can)
        if running:
            self.rename_btn.setToolTip("A rename is already running.")
        elif can:
            self.rename_btn.setToolTip(f"Rename {len(self.preview_map)} file(s) on disk.")
        else:
            self.rename_btn.setToolTip(self._plan.why_not())

    def _sync_list_buttons(self):
        running = self._running()
        has_files = bool(self.files)
        selected = bool(self.table.selectionModel().selectedRows()) if self.table.selectionModel() else False
        self.remove_btn.setEnabled(has_files and selected and not running)
        self.clear_btn.setEnabled(has_files and not running)
        self.up_btn.setEnabled(selected and not running)
        self.down_btn.setEnabled(selected and not running)
        self.sort_combo.setEnabled(has_files and not running)

    def _sync_undo_button(self):
        journal = br.latest_journal(self._journal_dir()) if not self._running() else None
        self.undo_btn.setEnabled(journal is not None)
        if journal is None:
            self.undo_btn.setToolTip("Nothing to undo yet.")
            return
        try:
            record = br.read_journal(journal)
            self.undo_btn.setToolTip(
                f"Put back the names from the last rename: {record.get('count', 0)} file(s) "
                f"in {record.get('folder') or 'several folders'}.")
        except (OSError, ValueError):
            self.undo_btn.setToolTip("Put back the names from the last rename.")

    def _running(self) -> bool:
        if self._busy:
            return True
        worker = self.worker
        try:
            return worker is not None and worker.isRunning()
        except RuntimeError:
            return False

    # ------------------------------------------------------------ the list
    def _add_paths(self, paths):
        known = {str(p).lower() for p in self.files}
        added = []
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                try:
                    children = [c for c in path.iterdir() if c.is_file()]
                except OSError:
                    children = []
                candidates = br.natural_sorted(children)
            else:
                candidates = [path]
            for candidate in candidates:
                if candidate.name.lower() in _SKIP_NAMES or candidate.name.startswith(".slate_rename_"):
                    continue
                key = str(candidate).lower()
                if key in known or not candidate.is_file():
                    continue
                known.add(key)
                added.append(candidate)
        if not added:
            return 0
        self.files.extend(added)
        if self.sort_combo.currentIndex() == 0:
            self.files = br.natural_sorted(self.files)
        elif self.sort_combo.currentIndex() == 1:
            self._sort_by_date()
        self._remember(last_folder=str(added[-1].parent))
        self._last_folder = str(added[-1].parent)
        self.update_preview()
        return len(added)

    def _start_folder(self) -> str:
        folder = self._last_folder
        if folder and Path(folder).is_dir():
            return folder
        return str(Path.home())

    def load_files(self):
        """Add files to the list (appended, duplicates ignored)."""
        files, _ = QFileDialog.getOpenFileNames(self, "Add files to rename", self._start_folder())
        if files:
            self._add_paths(files)

    def load_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Add every file in a folder", self._start_folder())
        if folder:
            self._add_paths([folder])

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self._running():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths and not self._running():
            self._add_paths(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def eventFilter(self, watched, event):
        if (watched is getattr(self, "table", None) and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)):
            self.remove_selected()
            return True
        return super().eventFilter(watched, event)

    def _selected_indexes(self) -> List[int]:
        model = self.table.selectionModel()
        if model is None:
            return []
        return sorted({index.row() for index in model.selectedRows()
                       if not self.table.isRowHidden(index.row())})

    def remove_selected(self):
        if self._running():
            return
        rows = set(self._selected_indexes())
        if not rows:
            return
        self.files = [f for i, f in enumerate(self.files) if i not in rows]
        self.table.clearSelection()
        self.update_preview()

    def move_selected(self, step: int):
        rows = self._selected_indexes()
        if not rows or self._running():
            return
        files = list(self.files)
        order = rows if step < 0 else list(reversed(rows))
        moved = []
        for row in order:
            target = row + step
            if 0 <= target < len(files) and target not in moved:
                files[row], files[target] = files[target], files[row]
                moved.append(target)
            else:
                moved.append(row)
        self.files = files
        self.sort_combo.setCurrentIndex(2)
        self.update_preview()
        self.table.clearSelection()
        mode = self.table.selectionMode()
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for row in moved:
            self.table.selectRow(row)
        self.table.setSelectionMode(mode)

    def _sort_by_date(self):
        def mtime(path):
            try:
                return path.stat().st_mtime
            except OSError:
                return 0
        self.files = sorted(self.files, key=lambda p: (mtime(p), br.natural_key(p)))

    def _sort_chosen(self, index):
        if index == 0:
            self.files = br.natural_sorted(self.files)
        elif index == 1:
            self._sort_by_date()
        self.update_preview()

    def clear_list(self):
        if self._running():
            return
        self.files = []
        self.update_preview()

    # ------------------------------------------------------------ renaming
    def _confirm(self, title: str, text: str, yes_label: str) -> bool:
        from slate.gui.components.feedback import confirm
        return confirm(self, title, text, yes_label=yes_label, no_label="Cancel")

    def _set_running(self, running: bool):
        for widget in (self.mode_tabs, self.load_btn, self.add_folder_btn, self.filter_combo):
            widget.setEnabled(not running)
        self.cancel_btn.setVisible(running)
        self.cancel_btn.setEnabled(running)
        self.progress_bar.setVisible(running)
        if not running:
            self.progress_label.setText("")
        self._sync_rename_button()
        self._sync_list_buttons()
        self.undo_btn.setEnabled(False if running else self.undo_btn.isEnabled())
        if not running:
            self._sync_undo_button()

    def execute_rename(self):
        """Ask, showing what will happen, then rename in the background."""
        if self._running():
            return
        self.update_preview()
        if not self._plan.can_run:
            from slate.gui.components.feedback import warn
            warn(self, "Rename files", self._plan.why_not() or "Nothing to rename.")
            return

        pairs = self.preview_map
        folders = sorted({str(old.parent) for old, _ in pairs})
        where = folders[0] if len(folders) == 1 else f"{len(folders)} folders"
        examples = "\n".join(f"  {old.name}  ->  {new.name}" for old, new in pairs[:4])
        more = f"\n  ... and {len(pairs) - 4} more" if len(pairs) > 4 else ""
        text = (f"Rename {len(pairs)} file(s) in {where}?\n\n{examples}{more}\n\n"
                "You can put the old names back with 'Undo last rename'.")
        if not self._confirm("Rename files", text, f"Rename {len(pairs)} file(s)"):
            return

        self._cleanup_worker()
        self.worker = RenameWorker(pairs, user=self._username(), journal_dir=self._journal_dir())
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.finished_signal.connect(self.on_rename_finished)
        self.progress_bar.setValue(0)
        self._busy = True
        self._set_running(True)
        self.progress_label.setText(f"Renaming 0 of {len(pairs)}…")
        self.worker.start()

    def cancel_rename(self):
        if self.worker is not None:
            self.worker.stop()
            self.cancel_btn.setEnabled(False)
            self.progress_label.setText("Cancelling…")

    @Slot(int, str)
    def update_progress(self, value, msg):
        self.progress_bar.setValue(value)
        if self.cancel_btn.isEnabled():
            self.progress_label.setText(msg)

    @Slot(bool, str, int)
    def on_rename_finished(self, success, msg, count):
        worker = self.worker
        if self.sender() is not None and self.sender() is not worker:
            return
        outcome = getattr(worker, "outcome", None)
        renamed = dict((str(old), new) for old, new in (outcome.renamed if outcome else []))
        if renamed:
            # The list now shows the files under their new names, ready for
            # another pass - clearing it hid what had just happened.
            self.files = [renamed.get(str(f), f) for f in self.files]
            self._audit(outcome, getattr(worker, "journal", None))

        from slate.gui.components.feedback import toast
        if success:
            toast(self, msg, "success", action=("Undo", self.undo_last_rename))
        elif count:
            toast(self, msg, "warning", action=("Undo", self.undo_last_rename))
        else:
            toast(self, msg, "info" if outcome and outcome.cancelled else "error")
        self._finish_run()

    def _finish_run(self):
        worker = self.worker
        self.worker = None
        try:
            if worker is not None:
                worker.wait(2000)
                if not worker.isRunning():
                    worker.deleteLater()
        except RuntimeError:
            pass
        self._busy = False
        self._set_running(False)
        self.update_preview()

    # ------------------------------------------------------------ undo
    def _journal_dir(self):
        """Slate's own folder (the journals go in its rename_undo sub-folder)."""
        app_dir = getattr(self.config_manager, "app_data_dir", None)
        return Path(app_dir) if isinstance(app_dir, (str, Path)) and str(app_dir) else None

    def undo_last_rename(self):
        if self._running():
            return
        from slate.gui.components.feedback import toast, warn
        journal = br.latest_journal(self._journal_dir())
        if journal is None:
            warn(self, "Undo last rename", "There is no rename to undo.")
            self._sync_undo_button()
            return
        try:
            record = br.read_journal(journal)
        except (OSError, ValueError) as exc:
            warn(self, "Undo last rename", f"The undo record could not be read: {exc}")
            return
        count = int(record.get("count") or 0)
        if not self._confirm("Undo last rename",
                             f"Put back the old names of {count} file(s) in "
                             f"{record.get('folder') or 'several folders'}?",
                             "Undo rename"):
            return
        result = br.undo(journal)
        if result.refused:
            warn(self, "Undo last rename", result.refused)
        else:
            new_to_old = {}
            for entry in record.get("pairs") or []:
                new_to_old[str(entry.get("new", "")).lower()] = Path(entry.get("old", ""))
            self.files = [new_to_old.get(str(f).lower(), f) for f in self.files]
            if result.failed:
                toast(self, f"{result.restored} name(s) put back; {len(result.failed)} could not be.", "warning")
            else:
                toast(self, f"{result.restored} name(s) put back.", "success")
            self._audit_undo(record, result)
        self._sync_undo_button()
        self.update_preview()

    # ------------------------------------------------------------ record
    def _username(self) -> str:
        data = self.user_data or {}
        return str(data.get("username") or data.get("user_id") or "")

    def _audit(self, outcome, journal):
        """Who renamed what, in the studio audit log (the tab is open to artists)."""
        folders = sorted({str(old.parent) for old, _ in outcome.renamed})
        details = (f"Renamed {outcome.count} file(s) in {', '.join(folders[:3])}"
                   + (f" (+{len(folders) - 3} more folders)" if len(folders) > 3 else "")
                   + (f"; undo journal {Path(journal).stem}" if journal else ""))
        logger.info("CAP Rename: %s", details)
        try:
            from slate.core.infra.audit_logger import AuditLogger
            AuditLogger().log_event("RENAME", self._username() or "unknown", details)
        except Exception as exc:
            logger.debug("Rename not written to the audit log: %s", exc)

    def _audit_undo(self, record, result):
        details = (f"Undid rename {record.get('id', '')}: {result.restored} name(s) put back "
                   f"in {record.get('folder') or 'several folders'}")
        try:
            from slate.core.infra.audit_logger import AuditLogger
            AuditLogger().log_event("RENAME", self._username() or "unknown", details)
        except Exception as exc:
            logger.debug("Undo not written to the audit log: %s", exc)

    # ------------------------------------------------------------ help
    def help_text(self) -> str:
        return """
        <h3>CAP Rename</h3>
        <p>Rename many files at once. Nothing changes on disk until you press
        <b>Rename files</b>, and the preview shows every new name first.</p>
        <h4>Find and replace</h4>
        <ul>
          <li><b>Find / Replace with:</b> plain text, matched without regard to case unless
              <i>Case sensitive</i> is ticked.</li>
          <li><b>Use regex:</b> Find is a Python regular expression; refer to groups as
              <code>\\1</code>, <code>\\2</code> in Replace with (not <code>$1</code>).</li>
          <li><b>Keep extension</b> (on by default): the extension is never touched.</li>
          <li><b>Sanitize</b>, <b>To lowercase</b> and <b>Re-pad numbers</b> act on the name
              before the extension.</li>
        </ul>
        <h4>Number as a sequence</h4>
        <p>Every file becomes <i>base name + number</i>, in the order of the list. Sort the
        list by name or date, or move files up and down, to set the order.</p>
        <h4>Conflicts</h4>
        <p>When two files would end up with the same name, a name is not allowed, or a file
        with that name is already in the folder, the row says <b>Conflict</b> and nothing is
        renamed until it is fixed. Remove files with <b>Remove selected</b> or the Delete key.</p>
        <h4>Undo</h4>
        <p><b>Undo last rename</b> puts the old names back, even when names were swapped or
        shifted. It refuses, and changes nothing, if the files were moved or renamed again
        since.</p>
        """

    def show_help_dialog(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("CAP Rename help")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(self.help_text())
        msg.exec()

    def closeEvent(self, event):
        self._is_closing = True
        self._cleanup_worker()
        super().closeEvent(event)
