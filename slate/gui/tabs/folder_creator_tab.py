from pathlib import Path
from typing import Dict, Any, Tuple
import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QTextEdit, QProgressBar,
    QCheckBox, QComboBox, QGroupBox, QScrollArea, QFrame,
    QFileDialog, QMessageBox, QSplitter, QTreeWidget, QTreeWidgetItem, QDialog
)
from PySide6.QtCore import Qt, Signal, QTimer

from ...core.infra.config_manager import ConfigManager
from ...core.worker_threads import FolderCreationWorker
from ...utils.security import SecurityValidator, SecurityError
from ...utils.text_utils import get_resolved_project_root

# Import design tokens for theming
from ...core.infra.design_tokens import ColorTokens as C, TypographyTokens as T
from ...gui.dialogs.custom_template_dialog import CustomTemplateDialog
from ...gui.dialogs.stitch_confirm_dialog import StitchConfirmDialog



class FolderCreatorTab(QWidget):
    """Builds the project folder structure and moves the client scans into it."""

    # Signal to notify other tabs about template changes
    template_changed = Signal(dict)

    def __init__(self, config_manager: ConfigManager = None):
        super().__init__()
        if config_manager is None:
            config_manager = ConfigManager()
        self.config_manager = config_manager
        self.format_mapping = getattr(self.config_manager, "format_mapping", {})
        self.is_processing = False
        self.folder_creation_thread = None
        self.security_validator = SecurityValidator()
        self.folder_preview_tree = None
        # Manifest of the last run, so a failed file can be retried on its own.
        self._last_manifest = None
        self._ingest_lock = None

        self.setup_ui()
        self.load_templates_to_ui()
        self.restore_last_paths()
        settings_dict = getattr(self.config_manager, "settings", {}) or {}
        if isinstance(settings_dict, dict):
            self.apply_global_settings(settings_dict.get("global_settings", {}))

    def apply_global_settings(self, global_settings: Dict[str, Any]):
        """Apply global app settings relevant to this tab."""
        if not isinstance(global_settings, dict):
            return
        if hasattr(self, "dry_run_cb"):
            self.dry_run_cb.setChecked(bool(global_settings.get("dry_run_enabled", False)))

    def _extract_template_lists(self, template_info: Dict[str, Any]) -> Tuple[list, list, list, list]:
        """
        Normalize template schema to flat folder lists.

        Supports both:
        - Flat config schema: template['base_folders']
        - Nested schema: template['structure']['base_folders']
        """
        if not isinstance(template_info, dict):
            return [], [], [], []

        structure = template_info.get("structure")
        source = structure if isinstance(structure, dict) else template_info

        def _safe_list(key: str) -> list:
            value = source.get(key, [])
            return value if isinstance(value, list) else []

        base_folders = _safe_list("base_folders")
        production_subfolders = _safe_list("production_subfolders")
        outsource_subfolders = _safe_list("outsource_subfolders")
        shot_folders = _safe_list("shot_folders")

        return base_folders, production_subfolders, outsource_subfolders, shot_folders

    def setup_ui(self):
        """Setup the user interface."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)

        # Create main splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel - Controls
        left_panel = self.create_left_panel()
        splitter.addWidget(left_panel)

        # Right panel - Preview and logs
        right_panel = self.create_right_panel()
        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 4) # 40% Control Panel
        splitter.setStretchFactor(1, 6) # 60% Logs/Preview
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        layout.addWidget(splitter)

    def create_left_panel(self):
        """Create the left panel with controls."""
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(10)

        # 1. Project Settings
        self.project_card = QGroupBox("Project Settings")
        project_layout = QFormLayout(self.project_card)
        
        self.project_name_input = QLineEdit()
        self.project_name_input.setPlaceholderText("Enter Project Code / Name (e.g. PRJ_001)")
        
        project_layout.addRow("Project Code:", self.project_name_input)

        project_dir_layout = QHBoxLayout()
        self.project_dir_input = QLineEdit()
        self.project_dir_input.setReadOnly(True)
        
        # Debounce timer for status check
        self.typing_timer = QTimer()
        self.typing_timer.setSingleShot(True)
        self.typing_timer.setInterval(500) # 500ms delay
        self.typing_timer.timeout.connect(self.check_destination_status)

        # Connect signals to timer
        self.project_name_input.textChanged.connect(self.start_typing_timer)
        self.project_dir_input.textChanged.connect(self.start_typing_timer)

        browse_project_btn = QPushButton("Browse")
        browse_project_btn.setMinimumWidth(80)
        browse_project_btn.clicked.connect(self.browse_project_directory)
        project_dir_layout.addWidget(self.project_dir_input)
        project_dir_layout.addWidget(browse_project_btn)
        project_layout.addRow("Target Root:", project_dir_layout)
        left_layout.addWidget(self.project_card)

        # 2. Template Selection
        template_card = QGroupBox("Template Configuration")
        template_layout = QVBoxLayout(template_card)
        combo_layout = QHBoxLayout()
        self.template_combo = QComboBox()
        combo_layout.addWidget(QLabel("Pipeline Template:"))
        combo_layout.addWidget(self.template_combo)
        template_layout.addLayout(combo_layout)
        self.template_description_label = QLabel()
        self.template_description_label.setStyleSheet(f"color: {C.TEXT_GRAY_LIGHT}; font-style: italic;")
        template_layout.addWidget(self.template_description_label)
        self.template_combo.currentTextChanged.connect(self.on_template_change)
        left_layout.addWidget(template_card)

        # 3. Source Inputs (Stacked - one visible at a time)
        
        # 3. Client scan source
        self.scan_card = QGroupBox("Client Scan Source")
        scan_layout = QFormLayout(self.scan_card)
        scan_layout.setContentsMargins(12, 16, 12, 12)
        scan_layout.setSpacing(10)
        scan_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        
        # Source Folder Input
        scan_input_layout = QHBoxLayout()
        self.scan_source_input = QLineEdit()
        self.scan_source_input.setPlaceholderText("Select the Client/Incoming Drive...")
        browse_scan_btn = QPushButton("Browse")
        browse_scan_btn.setMinimumWidth(80)
        browse_scan_btn.clicked.connect(self.browse_scan_source)
        scan_input_layout.addWidget(self.scan_source_input)
        scan_input_layout.addWidget(browse_scan_btn)
        scan_layout.addRow("Client Drive:", scan_input_layout)
        
        # Target Reel Input (New Feature)
        self.target_reel_input = QLineEdit()
        self.target_reel_input.setPlaceholderText("e.g. REEL_01 (Leave empty to Auto-Detect)")
        scan_layout.addRow("Target Reel:", self.target_reel_input)
        
        # Options - Clean 2x2 Grid Layout
        options_layout = QGridLayout()
        options_layout.setHorizontalSpacing(14)
        options_layout.setVerticalSpacing(8)
        self.overwrite_cb = QCheckBox("Overwrite Existing")
        self.dry_run_cb = QCheckBox("Dry Run (Simulate)")
        
        # --- NEW: FAST MODE TOGGLE ---
        self.fast_mode_cb = QCheckBox("Fast Mode (verify by size)")
        self.fast_mode_cb.setToolTip(
            "Every copy is still checked against the source size, which "
            "catches a truncated or partial file.\n"
            "Only the slower MD5 comparison is skipped. "
            "Leave this off for a client delivery."
        )
        self.fast_mode_cb.setChecked(False)

        self.add_to_dashboard_cb = QCheckBox("Add shots to Dashboard")
        self.add_to_dashboard_cb.setToolTip(
            "Create a tracking record for every shot found on the client "
            "drive.\nShots already tracked are left exactly as they are."
        )
        self.add_to_dashboard_cb.setChecked(True)
        # -----------------------------
        
        options_layout.addWidget(self.overwrite_cb, 0, 0)
        options_layout.addWidget(self.dry_run_cb, 0, 1)
        options_layout.addWidget(self.fast_mode_cb, 1, 0)
        options_layout.addWidget(self.add_to_dashboard_cb, 1, 1)
        scan_layout.addRow("Options:", options_layout)
        
        # Add a note explaining what happens
        note_label = QLabel("Info: Scans Drive > Finds Shots > Builds Structure > Moves Files")
        note_label.setWordWrap(True)
        note_label.setStyleSheet(f"color: {C.ACCENT_CYAN_ALT}; font-size: {T.SIZE_XS}pt; margin-top: 4px;")
        scan_layout.addRow(note_label)
        
        left_layout.addWidget(self.scan_card)

        # 4. Actions
        action_card = QGroupBox("Execution")
        action_layout = QHBoxLayout(action_card)
        action_layout.setContentsMargins(12, 16, 12, 12)
        action_layout.setSpacing(8)
        
        self.create_custom_btn = QPushButton("Edit Template")
        self.create_custom_btn.clicked.connect(self.create_custom_template)
        action_layout.addWidget(self.create_custom_btn)

        self.create_btn = QPushButton("Run Process")
        self.create_btn.setObjectName("primaryButton")
        self.create_btn.clicked.connect(self.start_creation_process)
        action_layout.addWidget(self.create_btn)
        self.create_button = self.create_btn

        # --- PAUSE BUTTON (NEW) ---
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.pause_btn.setEnabled(False)
        action_layout.addWidget(self.pause_btn)
        # --------------------------

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_creation_process)
        self.stop_btn.setEnabled(False)
        action_layout.addWidget(self.stop_btn)

        clear_btn = QPushButton("Reset")
        clear_btn.clicked.connect(self.clear_all)
        action_layout.addWidget(clear_btn)
        left_layout.addWidget(action_card)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        left_layout.addWidget(self.progress_bar)
        self.progress_label = QLabel("Ready")
        left_layout.addWidget(self.progress_label)
        self.stats_label = QLabel("Waiting for input...")
        left_layout.addWidget(self.stats_label)

        left_layout.addStretch()

        # Wrap in smooth scroll area to eliminate squashing on smaller screens
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_widget)
        return left_scroll

    # --- SMART BUTTON UPDATE ---
    def closeEvent(self, event):
        """Ensure background workers are stopped when tab closes."""
        self.stop_creation_process()
        self._cleanup_worker("folder_creation_thread", timeout_ms=2000)
        self._release_ingest_lock()
        if hasattr(self, "typing_timer") and self.typing_timer.isActive():
            self.typing_timer.stop()
        super().closeEvent(event)

    def _cleanup_worker(self, attr_name: str, timeout_ms: int = 3000):
        worker = getattr(self, attr_name, None)
        if worker is None:
            return

        if worker.isRunning():
            stop = getattr(worker, "stop", None)
            if callable(stop):
                stop()
            else:
                worker.requestInterruption()
            worker.wait(timeout_ms)

        worker.deleteLater()
        if getattr(self, attr_name, None) is worker:
            setattr(self, attr_name, None)

    def _release_finished_worker(self, attr_name: str, worker) -> bool:
        if worker is not getattr(self, attr_name, None):
            return False
        setattr(self, attr_name, None)
        worker.deleteLater()
        return True

    def check_destination_status(self):
        """Dynamically check if project exists and update button text to 'Update'."""
        name = self.project_name_input.text().strip()
        root = self.project_dir_input.text().strip()
        
        if not name or not root:
            return

        # Sanitize name to match what the worker uses
        _, sanitized_name, _ = self.security_validator.sanitize_filename(name)
        
        try:
            resolved_root = get_resolved_project_root(root, sanitized_name)
            target_path = resolved_root / sanitized_name
            
            # Check if this specific project folder already exists
            if target_path.exists() and target_path.is_dir():
                # IT EXISTS -> SWITCH TO UPDATE MODE VISUALS
                self.create_btn.setText("Update & Ingest New Files")
                
                # Make it look distinct (Green for safe update)
                self.create_btn.setStyleSheet(f"background-color: {C.ACCENT_TEAL}; color: white; font-weight: {T.WEIGHT_STYLE_BOLD}; border: 1px solid #16323A;")
                self.stats_label.setText("Info: Project exists. Running in SAFE UPDATE mode (No overwrites).")
                self.stats_label.setStyleSheet(f"color: {C.ACCENT_TEAL}; font-weight: {T.WEIGHT_STYLE_BOLD};")
                
            else:
                # IT DOES NOT EXIST -> SWITCH TO CREATE MODE VISUALS
                self.create_btn.setText("Build & Move Files")
                
                # Revert to default primary button style (preserve gradient effect)
                self.create_btn.setStyleSheet("background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3EA8BF, stop:1 #3EA8BF); border: 1px solid #3EA8BF; color: white; font-weight: bold;")
                self.stats_label.setText("Ready to create new project.")
                self.stats_label.setStyleSheet(f"color: {C.TEXT_GRAY_LIGHTER};")
                
        except (OSError, ValueError) as exc:
            logging.debug("Project path status update skipped while typing: %s", exc)

    def toggle_pause(self):
        """Toggle pause state of the worker."""
        if getattr(self, "folder_creation_thread", None) is None:
            return

        if self.pause_btn.text() == "Pause":
            self.folder_creation_thread.pause()
            self.pause_btn.setText("Resume")
            self.progress_label.setText("Paused")
        else:
            self.folder_creation_thread.resume()
            self.pause_btn.setText("Pause")
            self.progress_label.setText("Resuming...")

    def create_right_panel(self):
        """Create the right panel with preview and logs."""
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        preview_card = QGroupBox("Structure Preview")
        preview_layout = QVBoxLayout(preview_card)
        self.folder_preview_tree = QTreeWidget()
        self.folder_preview_tree.setHeaderLabel("Template Structure")
        preview_layout.addWidget(self.folder_preview_tree)
        self.preview_tree = self.folder_preview_tree
        right_layout.addWidget(preview_card, 1)

        log_card = QGroupBox("Process Logs")
        log_layout = QVBoxLayout(log_card)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        right_layout.addWidget(log_card, 2)

        return right_widget

    def create_custom_template(self):
        """SECURE: Create a custom template with security validation."""
        dialog = CustomTemplateDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                template_data = dialog.get_template_data()
                if template_data["name"]:
                    # SECURITY: Validate template key
                    template_key = template_data["name"].lower().replace(" ", "_")
                    key_valid, sanitized_key, key_error = self.security_validator.sanitize_filename(template_key)
                    
                    if not key_valid:
                        QMessageBox.critical(self, "Security Error", 
                                           f"Invalid template key:\n{key_error}")
                        return
                    
                    # Add to config manager (this will save it)
                    self.config_manager.templates[sanitized_key] = template_data

                    # Save to file
                    success = self.config_manager.save_templates(self.config_manager.templates)
                    if success:
                        # Add to combo box and select it
                        self.template_combo.addItem(template_data["name"], sanitized_key)
                        self.template_combo.setCurrentText(template_data["name"])

                        # Notify other tabs
                        self.template_changed.emit(template_data)

                        QMessageBox.information(self, "Success", f"Custom template '{template_data['name']}' created and saved successfully!")
                    else:
                        QMessageBox.critical(self, "Error", f"Could not save custom template '{template_data['name']}'. Check logs.")
                else:
                    QMessageBox.warning(self, "Invalid Name", "Please enter a template name.")
            except SecurityError as e:
                QMessageBox.critical(self, "Security Error", f"Security violation:\n{str(e)}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Unexpected error creating template:\n{str(e)}")

    def load_templates_to_ui(self):
        """Load available templates into the combobox."""
        # Clear existing items
        self.template_combo.clear()

        # Get all available templates (defaults + user-defined)
        if hasattr(self.config_manager, "get_templates") and callable(self.config_manager.get_templates):
            available_templates = self.config_manager.get_templates() or []
        elif hasattr(self.config_manager, "get_available_templates") and callable(self.config_manager.get_available_templates):
            available_templates = self.config_manager.get_available_templates() or []
        else:
            available_templates = []

        # Add templates to combo box
        for key in available_templates:
            template_info = None
            if hasattr(self.config_manager, "templates") and isinstance(self.config_manager.templates, dict):
                template_info = self.config_manager.templates.get(key)
            display_name = template_info.get("name", key) if template_info else str(key)
            self.template_combo.addItem(display_name, key)

        # Set default selection to "Standard" if it exists
        standard_index = self.template_combo.findData("standard")
        if standard_index >= 0:
            self.template_combo.setCurrentIndex(standard_index)
            # Trigger change to update preview and description
            self.on_template_change(self.template_combo.currentText())
        elif self.template_combo.count() > 0:
            # If standard doesn't exist, select the first available
            self.template_combo.setCurrentIndex(0)
            self.on_template_change(self.template_combo.currentText())

    def on_template_change(self, text):
        """Update template description and preview when selection changes."""
        current_index = self.template_combo.currentIndex()
        template_key = self.template_combo.itemData(current_index)

        if template_key:
            template_info = None
            if hasattr(self.config_manager, "templates") and isinstance(self.config_manager.templates, dict):
                template_info = self.config_manager.templates.get(template_key)
            if template_info and isinstance(template_info, dict):
                description = str(template_info.get("description", "No description available"))
            else:
                description = "No description available"
            self.template_description_label.setText(str(description))

            # Update preview
            self.update_preview(template_key)

            # Persist template preference for cross-tab/session consistency.
            try:
                if hasattr(self.config_manager, "settings") and isinstance(self.config_manager.settings, dict):
                    global_settings = self.config_manager.settings.setdefault("global_settings", {})
                    global_settings["last_template_used"] = template_key
                    if hasattr(self.config_manager, "save_settings") and callable(self.config_manager.save_settings):
                        self.config_manager.save_settings(self.config_manager.settings)
            except Exception as e:
                logging.debug(f"Failed to persist last template '{template_key}': {e}")

            # Notify other tabs about template change
            if template_info and isinstance(template_info, dict):
                self.template_changed.emit(template_info)
            else:
                self.template_changed.emit({"name": str(template_key)})

    def update_preview(self, template_key):
        """Update the folder structure preview."""
        # Check if folder_preview_tree exists before trying to use it
        if not hasattr(self, 'folder_preview_tree') or self.folder_preview_tree is None:
            return

        # Clear existing tree
        self.folder_preview_tree.clear()

        template_info = None
        if hasattr(self.config_manager, "templates") and isinstance(self.config_manager.templates, dict):
            template_info = self.config_manager.templates.get(template_key)
        if not template_info or not isinstance(template_info, dict):
            return

        # Create root item
        root_item = QTreeWidgetItem(self.folder_preview_tree)
        root_item.setText(0, "Project_Root")
        root_item.setExpanded(True)

        # Add base folders
        base_folders, production_subfolders, outsource_subfolders, shot_folders = self._extract_template_lists(template_info)
        for folder in base_folders:
            base_item = QTreeWidgetItem(root_item)
            base_item.setText(0, folder)

        # Add production / outsource subfolders (both live under 04_Production)
        if production_subfolders or outsource_subfolders:
            prod_item = QTreeWidgetItem(root_item)
            prod_item.setText(0, "04_Production")
            for folder in list(production_subfolders) + list(outsource_subfolders):
                sub_item = QTreeWidgetItem(prod_item)
                sub_item.setText(0, folder)

        # Add shot folders (example under a reel)
        if shot_folders:
            reels_item = QTreeWidgetItem(root_item)
            reels_item.setText(0, "05_Reels")
            shot_root = QTreeWidgetItem(reels_item)
            shot_root.setText(0, "SHOT_XXX")
            for folder in shot_folders:
                shot_item = QTreeWidgetItem(shot_root)
                shot_item.setText(0, folder)

    def browse_project_directory(self):
        """Browse for project directory."""
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Project Directory",
            self.config_manager.settings.get('last_project_directory', str(Path.home()))
        )
        if directory:
            # SECURITY: Validate directory path
            dir_path = Path(directory)
            dir_valid, dir_error = self.security_validator.validate_directory_path(dir_path, must_exist=True)
            
            if not dir_valid:
                QMessageBox.critical(self, "Security Error", f"Invalid directory:\n{dir_error}")
                return
                
            self.project_dir_input.setText(directory)
            self.config_manager.settings['last_project_directory'] = directory
            self.config_manager.save_settings(self.config_manager.settings)

    def browse_scan_source(self):
        """SECURE: Browse for Client Scan directory."""
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Client/Scan Source Directory",
            self.config_manager.settings.get('last_scan_source_directory', str(Path.home()))
        )
        if directory:
            dir_path = Path(directory)
            dir_valid, dir_error = self.security_validator.validate_directory_path(dir_path, must_exist=True)
            
            if not dir_valid:
                QMessageBox.critical(self, "Security Error", f"Invalid directory:\n{dir_error}")
                return
            
            self.scan_source_input.setText(directory)
            self.config_manager.settings['last_scan_source_directory'] = directory
            self.config_manager.save_settings(self.config_manager.settings)

    def log_message(self, message: str):
        """Log a message to the log area with timestamp."""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}"
        self.log_text.append(formatted_message)
        logging.info(message)

    def update_folder_creator_progress(self, value: int, text: str):
        """Update the folder creator progress bar and label."""
        self.progress_bar.setValue(value)
        self.progress_label.setText(text)
        if hasattr(self.parent(), 'statusBar'):
            self.parent().statusBar().showMessage(text, 2000)

    def _on_folder_worker_progress(self, value: int, text: str):
        if self.sender() is not self.folder_creation_thread:
            return
        self.update_folder_creator_progress(value, text)

    def _on_folder_worker_finished(self, success: bool, total_projects: int,
                                   reels_created: int, shots_created: int,
                                   folders_created: int, message: str):
        worker = self.sender()
        self._release_ingest_lock()

        # Read the detailed counters off the worker before it is released.
        stats = {
            "moved": getattr(worker, "files_moved", 0),
            "skipped": getattr(worker, "files_skipped", 0),
            "errors": getattr(worker, "errors", 0),
            "dry_run": bool(getattr(worker, "dry_run", False)),
        }
        ingested_shots = list(getattr(worker, "ingested_shots", []) or [])
        stats["report"] = self._write_delivery_report(worker)

        if success and not stats["dry_run"]:
            stats["dashboard"] = self._register_shots_on_dashboard(ingested_shots)
        if not self._release_finished_worker("folder_creation_thread", worker):
            return
        self.on_folder_creation_finished(
            success, total_projects, reels_created, shots_created, folders_created,
            message, stats
        )

    def retry_failed_files(self):
        """Re-attempt the files that failed on the last run, and nothing else."""
        from ...core.domain.ingest_retry import retry_failures

        manifest = getattr(self, "_last_manifest", None)
        if not manifest:
            QMessageBox.information(self, "Nothing to retry",
                                    "No record of a failed run was found.")
            return

        self.log_message("Retrying the files that failed...")
        result = retry_failures(
            manifest,
            fast_mode=self.fast_mode_cb.isChecked(),
            progress=lambda done, total, name: self.update_folder_creator_progress(
                int((done / max(total, 1)) * 100), f"Retrying {name}"
            ),
        )

        self.log_message(f"Retry: {result.summary()}")
        for entry in result.still_failing[:10]:
            self.log_message(f"  [ERR] {entry['file']}: {entry['error']}")

        self.progress_bar.setValue(100)
        self.progress_label.setText("Ready")

        if result.still_failing:
            QMessageBox.warning(self, "Some files still failing",
                                result.summary())
        else:
            QMessageBox.information(self, "Retry complete", result.summary())

    def _release_ingest_lock(self):
        """Let go of the project lock, however the run ended."""
        lock = getattr(self, "_ingest_lock", None)
        if lock is None:
            return
        try:
            lock.release()
        except Exception as exc:
            logging.warning("Could not release the ingest lock: %s", exc)
        finally:
            self._ingest_lock = None

    def _write_delivery_report(self, worker):
        """
        Record what arrived, so the client can be answered the same day.

        A report that cannot be written must never fail the ingest that
        produced it.
        """
        try:
            from ...core.domain.delivery_report import (
                build_report, frame_summary, write_report,
            )

            project_code = getattr(self, "_pending_project_code", "")
            project_root = getattr(self, "_pending_project_root", "")
            if not project_code or not project_root:
                return None

            report = build_report(
                worker, project_code, getattr(worker, "source_scan_path", ""),
            )
            paths = write_report(report, Path(project_root) / project_code)

            self.log_message(f"Delivery: {report.headline()}")
            for entry in report.incomplete[:10]:
                self.log_message(
                    f"  SHORT: {entry['shot']} / {entry['name']} "
                    f"missing {frame_summary(entry['missing'])}"
                )
            if paths:
                self.log_message(f"Report saved: {paths['report']}")
                self._last_manifest = paths.get("manifest")
            return {"report": report, "paths": paths}
        except Exception as exc:
            logging.warning("Delivery report failed: %s", exc)
            return None

    def _register_shots_on_dashboard(self, ingested_shots):
        """Create dashboard tracking records for the shots just ingested."""
        if not ingested_shots:
            return None
        if not getattr(self, "add_to_dashboard_cb", None) or                 not self.add_to_dashboard_cb.isChecked():
            return None

        project_code = getattr(self, "_pending_project_code", "")
        if not project_code:
            return None

        from ...core.domain.shot_registry import register_ingested_shots

        result = register_ingested_shots(
            project_code=project_code,
            shots=ingested_shots,
            project_name=project_code,
            folder_base=getattr(self, "_pending_project_root", ""),
        )
        self.log_message(result.summary())
        return result

    def on_folder_creation_finished(self, success: bool, total_projects: int,
                                   reels_created: int, shots_created: int,
                                   folders_created: int, message: str,
                                   stats: Dict[str, Any] = None):
        """Handle folder creation completion."""
        stats = stats or {}
        moved = stats.get("moved", 0)
        skipped = stats.get("skipped", 0)
        errors = stats.get("errors", 0)
        dry_run = stats.get("dry_run", False)

        self.is_processing = False
        self.create_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False) # Disable pause
        self.pause_btn.setText("Pause") # Reset text
        self.progress_bar.setValue(100)
        self.progress_label.setText("Ready")

        if not success:
            self.log_message(f"ERROR: Process failed: {message}")
            QMessageBox.critical(self, "Error", f"Operation failed:\n{message}")
            self.check_destination_status()
            return

        prefix = "DRY RUN - nothing was moved" if dry_run else "Completed"
        verb = "would move" if dry_run else "moved"

        summary_lines = [
            f"Reels: {reels_created}",
            f"Shots: {shots_created}",
            f"Folders: {folders_created}",
            f"Files {verb}: {moved}",
        ]
        if skipped:
            summary_lines.append(f"Skipped (already present): {skipped}")
        if errors:
            summary_lines.append(f"FAILED: {errors}")

        delivery = stats.get("report") or {}
        report = delivery.get("report")
        if report is not None:
            if report.incomplete:
                summary_lines.append(
                    f"SHORT DELIVERY: {len(report.incomplete)} sequence(s) "
                    f"missing {report.missing_frame_count} frame(s)"
                )
            if delivery.get("paths"):
                summary_lines.append("Delivery report saved to the project")

        dashboard = stats.get("dashboard")
        if dashboard is not None:
            if dashboard.ok:
                summary_lines.append(f"Dashboard: {len(dashboard.created)} new shot(s)")
                if dashboard.already_present:
                    summary_lines.append(
                        f"Already tracked: {len(dashboard.already_present)}"
                    )
            else:
                summary_lines.append(f"Dashboard update FAILED: {dashboard.error}")

        self.log_message(f"{prefix}: {message}")
        self.stats_label.setText(" | ".join(summary_lines))

        body = f"{prefix}.\n\n" + "\n".join(summary_lines)

        short_delivery = bool(report is not None and report.incomplete)

        if errors or short_delivery:
            # Files did not make it across, or the client shorted us frames.
            # Never dress either up as a clean run.
            self.stats_label.setStyleSheet(f"color: {C.ERROR_BRIGHT}; font-weight: {T.WEIGHT_STYLE_BOLD};")
            if errors and self._last_manifest:
                answer = QMessageBox.question(
                    self,
                    "Completed with errors",
                    body + "\n\nRetry just the files that failed?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self.retry_failed_files()
                    return

            QMessageBox.warning(
                self, "Completed with errors" if errors else "Short delivery",
                body + "\n\nCheck the Process Logs for the files that failed."
            )
        elif dry_run:
            self.stats_label.setStyleSheet(f"color: {C.ACCENT_CYAN_ALT};")
            QMessageBox.information(
                self, "Dry Run Complete",
                body + "\n\nUncheck 'Dry Run (Simulate)' to run it for real."
            )
        else:
            self.stats_label.setStyleSheet(f"color: {C.ACCENT_TEAL}; font-weight: {T.WEIGHT_STYLE_BOLD};")
            QMessageBox.information(self, "Success", body)

        # Project now exists - refresh the Create/Update button.
        self.check_destination_status()

    def finalize_creation_process(self, source_path=None):
        """Phase 2: Start the actual FolderCreationWorker."""
        
        # --- RE-GATHER SETTINGS (Guaranteed to be valid as UI was locked) ---
        project_name = self.project_name_input.text().strip()
        _, sanitized_name, _ = self.security_validator.sanitize_filename(project_name)
        
        project_dir_str = self.project_dir_input.text().strip()

        # --- SMART PATH FIX ---
        final_target_dir = get_resolved_project_root(project_dir_str, sanitized_name)
        if str(final_target_dir) != project_dir_str:
            self.log_message(f"Info: Smart Fix: Detected project folder selected directly. Adjusted root to: {final_target_dir}")

        # --- TEMPLATE DATA ---
        template_key = self.template_combo.currentData()
        template_info = self.config_manager.templates.get(template_key)
        base_folders, production_subfolders, outsource_subfolders, shot_folders = self._extract_template_lists(template_info)
        if not shot_folders:
            # Guardrail: avoid creating empty shot trees when template schema is mismatched.
            logging.warning(
                "Template '%s' has no shot_folders in resolved schema; using minimal defaults.",
                template_key
            )
            shot_folders = ["01_Scan", "07_Comp", "08_Output"]

        template_data = (
            base_folders,
            production_subfolders,
            outsource_subfolders,
            shot_folders,
        )
        
        target_reel = self.target_reel_input.text().strip()
        fast_mode = self.fast_mode_cb.isChecked()

        # Ask about stitch shots before anything moves. Cancelling here has to
        # leave the run un-started, not half-done.
        stitch_mapping = self._confirm_stitch_shots(source_path, target_reel)
        if stitch_mapping is None:
            self.is_processing = False
            self.create_btn.setEnabled(True)
            self.log_message("Ingest cancelled at the stitch confirmation.")
            return

        # Folders to create inside each scan version (e.g. Denoise).
        structure = template_info.get("structure") if isinstance(template_info, dict) else None
        source = structure if isinstance(structure, dict) else (template_info or {})
        scan_version_folders = source.get("scan_version_folders") or ["Denoise"]

        # Update UI for Phase 2
        self.create_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.pause_btn.setEnabled(True)
        self.log_text.clear()
        
        self.log_message("Building project structure and moving scans")
        self.log_message(f"Target: {final_target_dir / sanitized_name}")

        # Needed by the finish handler to register shots against the project.
        self._pending_project_code = sanitized_name
        self._pending_project_root = str(final_target_dir)

        # One ingest per project: two runs would both claim the same scan
        # version and mix two deliveries into one folder.
        from ...core.domain.ingest_lock import IngestLock, IngestLocked

        project_path = final_target_dir / sanitized_name
        try:
            self._ingest_lock = IngestLock(project_path).acquire()
        except IngestLocked as exc:
            self.is_processing = False
            self.create_btn.setEnabled(True)
            QMessageBox.warning(self, "Ingest already running", str(exc))
            return

        # --- START WORKER ---
        self._cleanup_worker("folder_creation_thread")
        self.folder_creation_thread = FolderCreationWorker(
            target_dir=final_target_dir,
            source_scan_path=source_path,
            project_name=sanitized_name,
            template_data=template_data,
            mode="full",
            template_type=template_key,
            target_reel_name=target_reel,
            overwrite=self.overwrite_cb.isChecked(),
            dry_run=self.dry_run_cb.isChecked(),
            format_mapping=getattr(self.config_manager, 'format_mapping', {}) or {},
            fast_mode=fast_mode,
            scan_version_folders=scan_version_folders,
            stitch_mapping=stitch_mapping,
        )

        self.folder_creation_thread.log_signal.connect(self.log_message)
        self.folder_creation_thread.progress_signal.connect(self._on_folder_worker_progress)
        self.folder_creation_thread.finished_signal.connect(self._on_folder_worker_finished)
        self.folder_creation_thread.start()

    def _confirm_stitch_shots(self, source_path, target_reel):
        """
        Ask which folders are parts of one shot, before any file moves.

        Returns the confirmed mapping, or None if the coordinator cancelled the
        whole ingest. An empty mapping is a normal answer: it means nothing
        looked like a stitch, or every suggestion was rejected.
        """
        if not source_path:
            return {}

        from ...core.domain.stitch_detect import survey_source

        try:
            groups = survey_source(source_path, target_reel)
        except Exception as exc:
            # A drive we cannot survey is not a reason to block an ingest; it
            # just means no merging, which is the old behaviour.
            logging.warning("Stitch survey failed on %s: %s", source_path, exc)
            self.log_message(f"Warning: could not check for stitch shots: {exc}")
            return {}

        if not groups:
            return {}

        self.log_message(
            f"Found {len(groups)} possible stitch shot(s) - asking before moving."
        )

        dialog = StitchConfirmDialog(groups, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        mapping = dialog.mapping()
        for group in dialog.accepted_groups():
            self.log_message(f"[STITCH] {group.describe()}")
        if not mapping:
            self.log_message("[STITCH] Nothing merged - every folder stays its own shot.")
        return mapping

    def start_creation_process(self):
        """SECURE: Start the folder creation process with security validation."""
        
        if hasattr(self, 'folder_creation_thread') and self.folder_creation_thread is not None:
            if self.folder_creation_thread.isRunning():
                QMessageBox.warning(self, "Please Wait", "The previous process is still stopping.\nPlease wait a few seconds and try again.")
                return

        if self.is_processing:
            QMessageBox.warning(self, "Already Processing", "A process is already running.")
            return

        # SECURITY: Validate project name
        project_name = self.project_name_input.text().strip()
        name_valid, sanitized_name, name_error = self.security_validator.sanitize_filename(project_name)
        if not name_valid:
            QMessageBox.critical(self, "Security Error", f"Invalid project name:\n{name_error}")
            return
        self.project_name_input.setText(sanitized_name)

        # SECURITY: Validate project directory
        project_dir_str = self.project_dir_input.text().strip()
        if not project_dir_str:
            QMessageBox.critical(self, "Validation Error", "Please select a target directory.")
            return

        target_path_obj = Path(project_dir_str)
        dir_valid, dir_error = self.security_validator.validate_directory_path(target_path_obj, must_exist=True)
        if not dir_valid:
            QMessageBox.critical(self, "Security Error", f"Invalid project directory:\n{dir_error}")
            return
            
        # Validate Template
        template_key = self.template_combo.currentData()
        if not template_key:
            QMessageBox.critical(self, "Template Error", "No template selected.")
            return

        scan_source = self.scan_source_input.text().strip()
        if not scan_source:
            QMessageBox.warning(self, "Missing Input", "Please select the Client Source folder.")
            return

        source_path = Path(scan_source)
        if not source_path.exists():
            QMessageBox.critical(self, "Error", "Source folder does not exist.")
            return

        # Ingesting a drive into itself would move files onto themselves.
        try:
            same_place = source_path.resolve() == target_path_obj.resolve()
        except OSError:
            same_place = False
        if same_place:
            QMessageBox.critical(
                self, "Error",
                "The client source and the target root are the same folder."
            )
            return

        self.is_processing = True
        self.create_btn.setEnabled(False)   # Lock UI only once we are committed
        self.finalize_creation_process(source_path=source_path)

    def stop_creation_process(self):
        """Stop the folder creation process."""
        if self.is_processing and self.folder_creation_thread is not None:
            self.folder_creation_thread.stop()
            if hasattr(self.parent(), "statusBar"):
                self.parent().statusBar().showMessage("Stopping process...", 3000)
            self.log_message("Sending stop signal to worker...")
            self.stop_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)

    def clear_all(self):
        """Clear all inputs on the Folder Creator tab."""
        self.project_name_input.clear()
        self.scan_source_input.clear()
        self.target_reel_input.clear()
        self.log_text.clear()
        self.progress_bar.setValue(0)
        self.progress_label.setText("Ready to start")
        self.stats_label.setText("-")
        self.stats_label.setStyleSheet(f"color: {C.TEXT_GRAY_LIGHTER};")
        self.check_destination_status()

    def restore_last_paths(self):
        """SECURE: Restore last used paths from settings with validation."""
        if self.config_manager.settings.get('global_settings', {}).get("restore_last_paths", True):
            try:
                # Restore project directory
                project_dir = self.config_manager.settings.get('last_project_directory', '')
                if project_dir and Path(project_dir).exists():
                    self.project_dir_input.setText(project_dir)

                # Restore Scan Source
                scan_source = self.config_manager.settings.get('last_scan_source_directory', '')
                if scan_source and Path(scan_source).exists():
                    self.scan_source_input.setText(scan_source)

                # Restore template selection
                last_template = self.config_manager.settings.get('global_settings', {}).get('last_template_used', 'standard')
                if last_template in self.config_manager.get_available_templates():
                    index = self.template_combo.findData(last_template)
                    if index >= 0:
                        self.template_combo.setCurrentIndex(index)

                logging.info("Restored last used paths")
            except Exception as e:
                logging.warning(f"Could not restore last paths: {e}")

    # --- DEBOUNCE HELPERS ---
    def start_typing_timer(self):
        """Restart the typing timer for debounced validation."""
        if hasattr(self, 'typing_timer'):
            self.typing_timer.start()
