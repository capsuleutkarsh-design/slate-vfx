import os
import re
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional
import logging
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QCheckBox, QPushButton, QTableWidget, QTableWidgetItem, 
    QHeaderView, QFileDialog, QMessageBox, QGroupBox,
    QProgressBar, QAbstractItemView, QSpinBox, QTabWidget
)
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QBrush

from ..core.infra.config_manager import ConfigManager
from ..core.infra.design_tokens import ColorTokens as C, TypographyTokens as T, RadiusTokens as R, SpacingTokens as S
from .core.icons import icon as draw_icon

class RenameWorker(QThread):
    """
    Worker thread to handle mass file renaming with UNDO script generation.
    """
    progress_signal = Signal(int, str)
    finished_signal = Signal(bool, str, int) # Success, Message, Count

    def __init__(self, rename_pairs: List[Tuple[Path, Path]]):
        super().__init__()
        self.rename_pairs = rename_pairs
        self.is_running = True

    def run(self):
        count = 0
        errors = 0
        total = len(self.rename_pairs)
        undo_lines = ["@echo off", "chcp 65001 > nul", 'echo Restoring files...']
        
        try:
            # Determine location for undo script (use first file's directory)
            undo_dir = self.rename_pairs[0][0].parent if self.rename_pairs else Path.home()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            undo_file = undo_dir / f"undo_rename_{timestamp}.bat"

            for i, (old_path, new_path) in enumerate(self.rename_pairs):
                if not self.is_running:
                    break
                
                try:
                    if old_path != new_path:
                        if not new_path.parent.exists():
                            new_path.parent.mkdir(parents=True, exist_ok=True)
                        
                        os.rename(old_path, new_path)
                        
                        # Add to Undo Script (Use 'move' for safety with full paths)
                        # escape quotes just in case
                        src = str(new_path).replace('"', '')
                        dst = str(old_path).replace('"', '')
                        undo_lines.append(f'move "{src}" "{dst}" > nul')
                        
                        count += 1
                except Exception as e:
                    logging.exception(f"Failed to rename {old_path.name}: {e}")
                    errors += 1
                
                progress = int(((i + 1) / total) * 100)
                self.progress_signal.emit(progress, f"Renaming: {old_path.name} -> {new_path.name}")
            
            # Write Undo Script
            if count > 0:
                with open(undo_file, "w", encoding="utf-8") as f:
                    f.write("\n".join(undo_lines))
                    f.write(f'\necho Restore complete ({count} files).\npause')
            
            msg = f"Completed. Renamed {count} files.\nUndo script saved to:\n{undo_file.name}"
            if errors > 0:
                msg += f"\n({errors} errors occurred - check logs)"
            
            self.finished_signal.emit(True, msg, count)
            
        except Exception as e:
            self.finished_signal.emit(False, str(e), count)

    def stop(self):
        self.is_running = False


class CapRenameTab(QWidget):
    """
    A PowerRename-style utility with VFX-specific features.
    """
    
    def __init__(self, config_manager: Optional[ConfigManager] = None):
        super().__init__()
        self.config_manager = config_manager
        self.files: List[Path] = []
        self.preview_map: List[Tuple[Path, Path]] = [] 
        self.worker = None
        self._is_closing = False
        self._is_cleaned = False
        self.setup_ui()
        if config_manager:
            self.apply_global_settings(config_manager.settings.get("global_settings", {}))

    def _cleanup_worker(self, timeout_ms: int = 2000):
        worker = self.worker
        if worker is None:
            return
        worker.stop()
        if worker.isRunning():
            worker.requestInterruption()
            worker.wait(timeout_ms)
        try:
            worker.deleteLater()
        except RuntimeError as exc:
            logging.debug("CAP rename worker deleteLater skipped: %s", exc)
        self.worker = None

    def setup_ui(self):
        # A. Modern Styling
        self.setStyleSheet(f"""
            QWidget {{
                font-family: {T.FONT_FAMILY};
                color: {C.TEXT_PRIMARY};
            }}
            QGroupBox {{
                border: 1px solid {C.BORDER_SUBTLE};
                border-radius: {R.LG}px;
                margin-top: 1.2em; /* Leave space for title */
                padding: {S.LG}px;
                background-color: {C.BG_SURFACE}; 
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 5px;
                color: {C.ACCENT_PRIMARY};
                font-weight: {T.WEIGHT_STYLE_BOLD};
                font-size: 11pt;
            }}
            QLineEdit, QSpinBox {{
                padding: 12px;
                border-radius: {R.SM}px;
                background-color: {C.BG_INPUT};
                border: 1px solid {C.BORDER_DEFAULT};
                font-size: 13px;
                color: {C.TEXT_PRIMARY};
                selection-background-color: {C.ACCENT_PRIMARY};
            }}
            QLineEdit:focus, QSpinBox:focus {{
                border: 1px solid {C.ACCENT_PRIMARY};
                background-color: {C.BG_DARKER};
            }}
            QTabWidget::pane {{
                border: 1px solid {C.BORDER_SUBTLE};
                border-radius: {R.MD}px;
                background-color: {C.BG_SURFACE};
                top: -1px; 
            }}
            QTabBar::tab {{
                background: {C.BG_ELEVATED};
                border: 1px solid {C.BORDER_SUBTLE};
                padding: 12px 24px;
                margin-right: 4px;
                border-top-left-radius: {R.MD}px;
                border-top-right-radius: {R.MD}px;
                color: {C.TEXT_SECONDARY};
                font-weight: {T.WEIGHT_STYLE_BOLD};
            }}
            QTabBar::tab:selected {{
                background: {C.BG_SURFACE};
                color: {C.TEXT_WHITE};
                border-bottom: 3px solid {C.ACCENT_PRIMARY};
            }}
            QTabBar::tab:hover:!selected {{
                background: {C.BG_HOVER};
                color: {C.TEXT_PRIMARY};
            }}
            QCheckBox {{
                spacing: 10px;
                color: {C.TEXT_PRIMARY};
                background: transparent;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border: 1px solid {C.BORDER_LIGHT};
                border-radius: {R.XS}px;
                background: {C.BG_INPUT};
            }}
            QCheckBox::indicator:hover {{
                border: 1px solid {C.ACCENT_HOVER};
            }}
            QCheckBox::indicator:checked {{
                background: {C.ACCENT_PRIMARY};
                border: 1px solid {C.ACCENT_PRIMARY};
            }}
            QLabel {{
                background: transparent;
                color: {C.TEXT_PRIMARY};
            }}
            QLabel#headerLabel {{
                font-size: 15px;
                font-weight: {T.WEIGHT_STYLE_BOLD};
                color: {C.TEXT_WHITE};
                padding-bottom: 5px;
                background: transparent;
            }}
            QLabel#descLabel {{
                color: {C.TEXT_SECONDARY};
                font-style: italic;
                margin-bottom: 10px;
                background: transparent;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # --- 1. MODE SELECTION (TABS) ---
        self.mode_tabs = QTabWidget()
        layout.addWidget(self.mode_tabs)

        # === TAB A: SEARCH & REPLACE ===
        self.tab_replace = QWidget()
        self.setup_replace_ui()
        self.mode_tabs.addTab(self.tab_replace, "Search and replace")
        self.mode_tabs.setTabIcon(0, draw_icon("search"))

        # === TAB B: SERIALIZE (SEQUENCE) ===
        self.tab_sequence = QWidget()
        self.setup_sequence_ui()
        self.mode_tabs.addTab(self.tab_sequence, "Serialize (make sequence)")
        self.mode_tabs.setTabIcon(1, draw_icon("film"))
        
        self.mode_tabs.currentChanged.connect(self.update_preview)

        # --- 2. PREVIEW TABLE ---
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Original Filename", "New Filename", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 120)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)  # Cleaner look
        self.table.setStyleSheet(f"QTableWidget {{ background-color: {C.BG_SURFACE}; gridline-color: transparent; border: 1px solid {C.BORDER_SUBTLE}; border-radius: {R.MD}px; }} QTableWidget::item {{ padding: 5px; border-bottom: 1px solid {C.BORDER_SUBTLE}; }} QTableWidget::item:selected {{ background-color: rgba(62, 168, 191, 0.15); }} QHeaderView::section {{ background-color: {C.BG_ELEVATED}; padding: 8px; border: none; border-bottom: 1px solid {C.BORDER_SUBTLE}; font-weight: bold; color: {C.TEXT_SECONDARY}; }}")
        layout.addWidget(self.table)
        
        # --- 3. ACTIONS ---
        action_layout = QHBoxLayout()
        self.load_btn = QPushButton("Load Files")
        self.load_btn.setMinimumHeight(40)
        self.load_btn.setStyleSheet(f"""
            QPushButton {{ background-color: {C.BG_ELEVATED}; color: {C.TEXT_PRIMARY}; border: 1px solid {C.BORDER_LIGHT}; border-radius: {R.SM}px; }}
            QPushButton:hover {{ background-color: {C.BG_HOVER}; border: 1px solid {C.ACCENT_PRIMARY}; }}
        """)
        self.load_btn.clicked.connect(self.load_files)
        
        self.rename_btn = QPushButton("Rename Files")
        self.rename_btn.setObjectName("primaryButton")
        self.rename_btn.setMinimumHeight(40)
        self.rename_btn.setMinimumWidth(150)
        self.rename_btn.setStyleSheet(f"""
            QPushButton {{ background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {C.ACCENT_HOVER}, stop:1 {C.ACCENT_DARK}); color: white; border: none; border-radius: {R.SM}px; font-weight: bold; font-size: 14px; }}
            QPushButton:hover {{ background-color: {C.ACCENT_PRIMARY}; }}
            QPushButton:disabled {{ background-color: {C.BG_INPUT}; color: {C.TEXT_DISABLED}; }}
        """)
        self.rename_btn.clicked.connect(self.execute_rename)
        self.rename_btn.setEnabled(False)
        
        action_layout.addWidget(self.load_btn)
        
        self.help_btn = QPushButton("Help")
        self.help_btn.setMinimumHeight(40)
        self.help_btn.setStyleSheet(f"""
            QPushButton {{ background-color: {C.BG_ELEVATED}; color: {C.TEXT_PRIMARY}; border: 1px solid {C.BORDER_LIGHT}; border-radius: {R.SM}px; }}
            QPushButton:hover {{ background-color: {C.BG_HOVER}; border: 1px solid {C.ACCENT_PRIMARY}; }}
        """)
        self.help_btn.clicked.connect(self.show_help_dialog)
        action_layout.addWidget(self.help_btn)
        
        action_layout.addStretch()
        action_layout.addWidget(self.rename_btn)
        layout.addLayout(action_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(4) # Slim progress bar
        layout.addWidget(self.progress_bar)

    def show_help_dialog(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("Cap Rename Help")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText("""
        <h3>Cap Rename Tool Guide</h3>
        <p>This tool offers two powerful modes for renaming files:</p>
        
        <h4>1. Search & Replace</h4>
        <ul>
            <li><b>Find/Replace:</b> Standard text substitution.</li>
            <li><b>VFX Utilities:</b> Quick fixers for common pipeline tasks.
                <ul>
                    <li><i>Sanitize:</i> Removes spaces and special chars.</li>
                    <li><i>Re-Pad:</i> Corrects frame numbers (e.g., _1 -> _0001).</li>
                </ul>
            </li>
            <li><b>Regex:</b> Supports Python Regular Expressions for advanced matching.</li>
        </ul>

        <h4>2. Serialize (Make Sequence)</h4>
        <p>Perfect for creating clean image sequences from messy files.</p>
        <ul>
            <li><b>Base Name:</b> The prefix (e.g., 'sc01_sh010_').</li>
            <li><b>Start:</b> The first frame number (default 1001).</li>
            <li><b>Step:</b> Increment size (usually 1).</li>
        </ul>

        <p><b>⚠️ Safety Feature:</b> Every rename operation generates an <code>undo_rename.bat</code> file in the folder, allowing you to instantly revert changes if needed.</p>
        """)
        msg.exec()

    def setup_replace_ui(self):
        layout = QVBoxLayout(self.tab_replace)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # Header Info
        head = QLabel("Find and Replace Text")
        head.setObjectName("headerLabel")
        layout.addWidget(head)
        
        desc = QLabel("Search for patterns in filenames and replace them with new text. Use 'VFX Utilities' for common cleanup tasks.")
        desc.setObjectName("descLabel")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # A. Inputs
        bg_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Find (e.g. 'shot_v01')...")
        self.search_edit.setToolTip("Enter the text you want to find in the filenames. Supports Regex if enabled below.")
        self.search_edit.textChanged.connect(self.update_preview)
        
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("Replace with (e.g. 'shot_v02')...")
        self.replace_edit.setToolTip("Enter the text to replace the found text with.")
        self.replace_edit.textChanged.connect(self.update_preview)
        
        bg_layout.addWidget(QLabel("Find:"))
        bg_layout.addWidget(self.search_edit, 1)
        bg_layout.addWidget(QLabel("Replace:"))
        bg_layout.addWidget(self.replace_edit, 1)
        layout.addLayout(bg_layout)

        # B. VFX Modifiers
        mod_group = QGroupBox("VFX Utilities")
        mod_layout = QHBoxLayout(mod_group)
        mod_layout.setContentsMargins(15, 25, 15, 15)
        mod_layout.setSpacing(20)

        self.lower_cb = QCheckBox("To Lowercase")
        self.lower_cb.setToolTip("Forces the entire filename to lowercase letters.")
        self.lower_cb.toggled.connect(self.update_preview)

        self.cleanup_cb = QCheckBox("Sanitize Name")
        self.cleanup_cb.setToolTip("Replaces spaces and dots with underscores (_), and removes special characters for safety.")
        self.cleanup_cb.toggled.connect(self.update_preview)

        self.padding_cb = QCheckBox("Re-Pad Numbers")
        self.padding_cb.setToolTip("Finds the last number sequence in the filename and formats it to the specified digit count.")
        self.padding_cb.toggled.connect(self.toggle_padding)
        
        self.padding_spin = QSpinBox()
        self.padding_spin.setRange(1, 8)
        self.padding_spin.setValue(4)
        # The label sits beside the box, not inside it. As a prefix it shared
        # the same 100px with the value and the arrows, and ran underneath them.
        self.padding_spin.setToolTip("Target number of digits (e.g. 4 -> 0001).")
        self.padding_spin.setFixedWidth(74)
        self.padding_spin.setEnabled(False)
        self.padding_spin.valueChanged.connect(self.update_preview)

        mod_layout.addWidget(self.lower_cb)
        mod_layout.addWidget(self.cleanup_cb)
        
        pad_layout = QHBoxLayout()
        pad_layout.setSpacing(8)
        pad_layout.addWidget(self.padding_cb)
        self.padding_label = QLabel("Digits")
        self.padding_label.setStyleSheet("background: transparent; border: none;")
        pad_layout.addWidget(self.padding_label)
        pad_layout.addWidget(self.padding_spin)
        pad_layout.addStretch(1)
        mod_layout.addLayout(pad_layout)
        
        mod_layout.addStretch()
        layout.addWidget(mod_group)
        
        # C. Options
        options_layout = QHBoxLayout()
        options_layout.setContentsMargins(5, 0, 0, 0)
        
        self.regex_cb = QCheckBox("Use Regex")
        self.regex_cb.setToolTip("Enable Regular Expressions for advanced pattern matching.")
        self.regex_cb.toggled.connect(self.update_preview)
        
        self.case_cb = QCheckBox("Case Sensitive")
        self.case_cb.setToolTip("If checked, 'Shot' will not match 'shot'.")
        self.case_cb.toggled.connect(self.update_preview)
        
        self.file_only_cb = QCheckBox("File Name Only")
        self.file_only_cb.setToolTip("Modify only the filename, leaving the extension (.jpg, .exr) untouched.")
        self.file_only_cb.setChecked(False)
        self.file_only_cb.toggled.connect(self.update_preview)
        
        options_layout.addWidget(self.regex_cb)
        options_layout.addWidget(self.case_cb)
        options_layout.addWidget(self.file_only_cb)
        options_layout.addStretch()
        layout.addLayout(options_layout)
        layout.addStretch()

    def setup_sequence_ui(self):
        layout = QVBoxLayout(self.tab_sequence)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # Header Info
        head = QLabel("Create Numbered Sequence")
        head.setObjectName("headerLabel")
        layout.addWidget(head)
        
        desc = QLabel("Rename all loaded files into a consistent, numbered sequence. The files will be renamed in the order they appear in the list below.")
        desc.setObjectName("descLabel")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        
        # Row 1: Base Name (Full Width)
        row1 = QHBoxLayout()
        self.seq_base = QLineEdit()
        self.seq_base.setPlaceholderText("e.g. shot_010_v01_")
        self.seq_base.setToolTip("The common prefix for all files (e.g. 'shot_01_').")
        self.seq_base.textChanged.connect(self.update_preview)
        row1.addWidget(QLabel("Base Name:"))
        row1.addWidget(self.seq_base)
        layout.addLayout(row1)
        
        # Row 2: Numbering Controls
        row2 = QHBoxLayout()
        row2.setSpacing(20)
        
        # Start
        self.seq_start = QSpinBox()
        self.seq_start.setRange(0, 999999)
        self.seq_start.setValue(1001)
        self.seq_start.setPrefix("Start: ")
        self.seq_start.setToolTip("The number to start counting from (usually 1001 for VFX).")
        self.seq_start.setMinimumWidth(120)
        self.seq_start.valueChanged.connect(self.update_preview)
        
        # Step
        self.seq_step = QSpinBox()
        self.seq_step.setRange(1, 100)
        self.seq_step.setValue(1)
        self.seq_step.setPrefix("Inc: ")
        self.seq_step.setToolTip("The increment for each file (e.g. 10 for 1010, 1020, 1030).")
        self.seq_step.setMinimumWidth(100)
        self.seq_step.valueChanged.connect(self.update_preview)
        
        # Padding
        self.seq_padding = QSpinBox()
        self.seq_padding.setRange(1, 8)
        self.seq_padding.setValue(4)
        self.seq_padding.setPrefix("Pad: ")
        self.seq_padding.setToolTip("The minimum number of digits to use (e.g. 4 -> 0001).")
        self.seq_padding.setMinimumWidth(100)
        self.seq_padding.valueChanged.connect(self.update_preview)
        
        row2.addWidget(self.seq_start)
        row2.addWidget(self.seq_step)
        row2.addWidget(self.seq_padding)
        row2.addStretch()
        
        layout.addLayout(row2)
        layout.addStretch()

    def toggle_padding(self, checked):
        self.padding_spin.setEnabled(checked)
        self.update_preview()

    def apply_global_settings(self, global_settings: Dict[str, Any]):
        """Apply global app settings relevant to this tab."""
        if not isinstance(global_settings, dict):
            return
        # Rename tab doesn't have specific global settings yet, but this hook
        # ensures compatibility with the main app's settings system

    def update_preview(self):
        self.table.setRowCount(0)
        self.preview_map = []
        self.table.setRowCount(len(self.files))
        
        # Check current mode
        is_sequence_mode = (self.mode_tabs.currentIndex() == 1)

        if is_sequence_mode:
            # === SEQUENCE LOGIC ===
            base_name = self.seq_base.text()
            start_num = self.seq_start.value()
            step_num = self.seq_step.value()
            pad = self.seq_padding.value()
            
            for row, file_path in enumerate(self.files):
                ext = file_path.suffix
                current_num = start_num + (row * step_num)
                num_str = str(current_num).zfill(pad)
                
                # Construct new name
                if base_name:
                    new_filename = f"{base_name}{num_str}{ext}"
                else:
                    # If empty, just number? No, unsafe. Use original stem + number
                    new_filename = f"{file_path.stem}_{num_str}{ext}"

                self._add_row(row, file_path, new_filename)

        else:
            # === SEARCH/REPLACE LOGIC ===
            search_txt = self.search_edit.text()
            replace_txt = self.replace_edit.text()
            use_regex = self.regex_cb.isChecked()
            item_only = self.file_only_cb.isChecked()
            
            # VFX Flags
            do_lower = self.lower_cb.isChecked()
            do_cleanup = self.cleanup_cb.isChecked()
            do_padding = self.padding_cb.isChecked()
            pad_target = self.padding_spin.value()
            flags = 0 if self.case_cb.isChecked() else re.IGNORECASE

            for row, file_path in enumerate(self.files):
                original = file_path.name
                stem = file_path.stem
                ext = file_path.suffix
                
                target = stem if item_only else original
                processed = target
                
                # 1. Search & Replace
                if search_txt:
                    try:
                        if use_regex:
                            processed = re.sub(search_txt, replace_txt, processed, flags=flags)
                        else:
                            pattern = re.escape(search_txt)
                            processed = re.sub(pattern, replace_txt, processed, flags=flags)
                    except re.error:
                         processed = "REGEX ERROR"

                # 2. VFX Actions
                if do_cleanup:
                    processed = re.sub(r'[\s\.]+', '_', processed)
                    processed = re.sub(r'[^a-zA-Z0-9_\-]', '', processed)
                
                if do_lower:
                    processed = processed.lower()
                    
                if do_padding:
                    match = re.search(r'(\d+)$', processed)
                    if match:
                        num_str = match.group(1)
                        padded_num = num_str.zfill(pad_target)
                        processed = processed[:match.start()] + padded_num + processed[match.end():]

                new_filename = processed + ext if item_only else processed
                self._add_row(row, file_path, new_filename)

    def _add_row(self, row, file_path, new_name):
        """Helper to add row to table"""
        original = file_path.name
        status = "Unchanged"
        color = None
        
        if new_name != original and "ERROR" not in new_name:
            status = "Will Rename"
            color = QColor(0, 180, 216, 40) # Cyan highlight
            self.preview_map.append((file_path, file_path.parent / new_name))
        
        self.table.setItem(row, 0, QTableWidgetItem(original))
        item_new = QTableWidgetItem(new_name)
        if color:
            item_new.setBackground(QBrush(color))
        self.table.setItem(row, 1, item_new)
        self.table.setItem(row, 2, QTableWidgetItem(status))

    def load_files(self):
        """Open file dialog to select files."""
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Files to Rename", str(Path.home())
        )
        if files:
            self.files = [Path(f) for f in files]
            self.update_preview()
            self.rename_btn.setEnabled(True)

    def clear_list(self):
        self.files = []
        self.table.setRowCount(0)
        self.rename_btn.setEnabled(False)

    def execute_rename(self):
        """Starts the Worker Thread to perform renaming."""
        if not self.preview_map:
            QMessageBox.information(self, "No Changes", "No files need renaming based on your current rules.")
            return
            
        confirm = QMessageBox.question(
            self, "Confirm Rename", 
            f"Are you sure you want to rename {len(self.preview_map)} files?\nThis action cannot be undone easily.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if confirm == QMessageBox.StandardButton.Yes:
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)
            
            # Disable inputs during processing
            self.rename_btn.setEnabled(False)

            self._cleanup_worker()
            self.worker = RenameWorker(self.preview_map)
            self.worker.progress_signal.connect(self.update_progress)
            self.worker.finished_signal.connect(self.on_rename_finished)
            self.worker.finished.connect(self._on_worker_thread_finished)
            self.worker.finished.connect(self.worker.deleteLater)
            self.worker.start()

    @Slot(int, str)
    def update_progress(self, value, msg):
        self.progress_bar.setValue(value)
        # Optional: Status bar update could go here

    @Slot(bool, str, int)
    def on_rename_finished(self, success, msg, count):
        if self.sender() is not self.worker:
            return
        self.progress_bar.setVisible(False)
        self.rename_btn.setEnabled(True)
        
        if success:
            QMessageBox.information(self, "Success", msg)
            # Clear list or reload? Usually clear to prevent double rename errors
            self.clear_list() 
        else:
            QMessageBox.critical(self, "Error", f"Renaming failed: {msg}")

    def _on_worker_thread_finished(self):
        if self.sender() is self.worker:
            self.worker = None

    def closeEvent(self, event):
        self._is_closing = True
        self._cleanup_worker()
        super().closeEvent(event)
