
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QPushButton, QGridLayout, QLabel, QSpinBox, 
    QMessageBox, QInputDialog, QFileDialog, QFrame, QLineEdit,
    QScrollArea, QApplication, QDoubleSpinBox
)
from PySide6.QtCore import Signal, Qt, QUrl
from PySide6.QtGui import QPixmap, QDesktopServices

from ...core.worker_threads import ReportWorker

from ...core.infra.database_manager import database_manager
from ...core.infra.config_manager import ConfigManager
from ...utils.error_handler import error_handler
from ...utils.backup_recovery import BackupManager
from ...core.infra.theme_manager import ThemeManager
from ...core.infra.global_config import GlobalConfig
from ...core.updater.update_checker import UpdateChecker
from ..dialogs.update_available_dialog import UpdateAvailableDialog

# Import design tokens for theming
from ...core.infra.design_tokens import ColorTokens as C, TypographyTokens as T, SpacingTokens as S, RadiusTokens as R

# Import shared PyToggle widget (no more duplication!)
from ...gui.widgets.py_toggle import PyToggle
from ..core.controls import make_button

try:
    import shiboken6
except Exception:
    shiboken6 = None

# --- MODERN UI COMPONENTS ---

class ActionCard(QPushButton):
    """Large clickable card for actions like Backups/Reports"""
    def __init__(self, icon_char, title, desc, callback):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self.clicked.connect(callback)
        
        # Internal Layout
        lay = QVBoxLayout(self)
        # A drawn icon rather than an emoji character. The four cards used
        # four emoji that Windows rendered at different sizes and weights, so
        # the row never lined up.
        self.lbl_icon = QLabel()
        self.lbl_icon.setStyleSheet("border:none; background:transparent;")
        self._set_glyph(icon_char)
        self.lbl_title = QLabel(title); self.lbl_title.setStyleSheet(f"font-size: {T.SIZE_MD}px; font-weight: {T.WEIGHT_STYLE_BOLD}; border:none; background:transparent;")
        self.lbl_desc = QLabel(desc); self.lbl_desc.setStyleSheet(f"color: {C.TEXT_GRAY_LIGHT}; border:none; background:transparent; line-height: 120%;")
        self.lbl_desc.setWordWrap(True)
        
        lay.addWidget(self.lbl_icon); lay.addWidget(self.lbl_title); lay.addWidget(self.lbl_desc)
        
        # Style
        self.setStyleSheet(f"""
            ActionCard {{ background-color: {C.BG_SURFACE}; border: 1px solid {C.BORDER_DEFAULT}; border-radius: {R.MD}px; text-align: left; padding: {S.MD}px; }}
            ActionCard:hover {{ background-color: {C.BG_ELEVATED}; border: 1px solid {C.ACCENT_PRIMARY}; }}
            ActionCard:pressed {{ background-color: {C.BG_SIDEBAR}; }}
        """)

    def _set_glyph(self, name):
        from ...gui.core.icons import icon as draw_icon, has_icon
        if has_icon(name):
            self.lbl_icon.setPixmap(draw_icon(name, C.ACCENT_PRIMARY, 22).pixmap(22, 22))
            self.lbl_icon.setText("")
        else:
            self.lbl_icon.setPixmap(QPixmap())
            self.lbl_icon.setText(str(name))

    def update_content(self, icon_char, title, desc):
        self._set_glyph(icon_char)
        self.lbl_title.setText(title)
        self.lbl_desc.setText(desc)

class SettingsCard(QFrame):
    """Wrapper for a section of settings"""
    def __init__(self, title):
        super().__init__()
        self.setStyleSheet(f".SettingsCard {{ background-color: {C.BG_DARK}; border-radius: {S.MD}px; border: 1px solid #1D1D22; }}")
        self.main_layout = QVBoxLayout(self)
        
        lbl = QLabel(title)
        lbl.setStyleSheet(f"font-size: {T.SIZE_LG}px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: {C.ACCENT_PRIMARY}; margin-bottom: {S.MD}px; border:none;")
        self.main_layout.addWidget(lbl)

class SettingsTab(QWidget):
    """
    Standalone Settings Tab containing Global Settings, Maintenance, and reporting tools.
    """
    templates_refresh_requested = Signal()
    global_settings_updated = Signal(dict)

    def __init__(self, config_manager=None):
        super().__init__()
        self.config_manager = config_manager or ConfigManager()
        self.settings = self.config_manager.settings
        self.global_settings = self.settings.get("global_settings", self.config_manager.default_global_settings)
        self.backup_manager = BackupManager()
        self.update_checker = None
        self.sidecar_engine = None
        self.report_worker = None
        self.init_ui()

    @staticmethod
    def _is_qobject_alive(obj):
        if obj is None:
            return False
        if shiboken6 is None:
            return True
        try:
            return shiboken6.isValid(obj)
        except Exception:
            return False

    @staticmethod
    def _safe_stop_thread(thread, timeout_ms=2000):
        if thread is None:
            return
        try:
            if hasattr(thread, "isRunning") and thread.isRunning():
                if hasattr(thread, "stop"):
                    thread.stop()
                else:
                    thread.requestInterruption()
                thread.wait(timeout_ms)
        except Exception as exc:
            logging.debug("SettingsTab worker shutdown warning: %s", exc)

    def _cleanup_report_worker(self):
        worker = self.report_worker
        if worker is None:
            return
        self._safe_stop_thread(worker, timeout_ms=2000)
        try:
            worker.deleteLater()
        except RuntimeError as exc:
            logging.debug("Report worker deleteLater skipped: %s", exc)
        self.report_worker = None

    def _cleanup_update_checker(self):
        checker = self.update_checker
        if checker is None:
            return
        self._safe_stop_thread(checker, timeout_ms=2000)
        try:
            checker.deleteLater()
        except RuntimeError as exc:
            logging.debug("Update checker deleteLater skipped: %s", exc)
        self.update_checker = None

    def _on_report_worker_finished(self, success, message):
        worker = self.sender()
        if worker is not self.report_worker:
            return
        if not self._is_qobject_alive(self):
            return
        if success:
            QMessageBox.information(self, "Report", message)
        else:
            QMessageBox.warning(self, "Error", message)

    def _on_report_worker_done(self):
        worker = self.sender()
        if worker is self.report_worker:
            self.report_worker = None

    def init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        main_layout = QVBoxLayout(content)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)
        
        # Helper to create toggles rows
        def add_setting_row(layout, label_text, desc_text, toggle_widget):
            row = QHBoxLayout()
            v = QVBoxLayout()
            l = QLabel(label_text); l.setStyleSheet(f"font-size: {T.SIZE_MD}px; font-weight: {T.WEIGHT_SEMIBOLD}; color: #E8E6E1; border:none;")
            d = QLabel(desc_text); d.setStyleSheet(f"font-size: 11px; color: {C.TEXT_TERTIARY}; border:none;")
            v.addWidget(l); v.addWidget(d)
            row.addLayout(v); row.addStretch(); row.addWidget(toggle_widget)
            layout.addLayout(row)
            layout.addWidget(self.create_divider())

        # --- SECTIONS ---
        
        # 1. CORE CONFIGURATION
        card_config = SettingsCard("Core Configuration")
        
        # Path Restore
        self.restore_paths_cb = PyToggle()
        self.restore_paths_cb.setChecked(self.global_settings.get("restore_last_paths", True))
        add_setting_row(card_config.layout(), "Restore Sessions", "Re-open last used paths on startup", self.restore_paths_cb)
        
        # Dry Run
        self.dry_run_default_cb = PyToggle()
        self.dry_run_default_cb.setChecked(self.global_settings.get("dry_run_enabled", False))
        add_setting_row(card_config.layout(), "Dry Run Mode", "Simulate file operations without writing changes", self.dry_run_default_cb)
        
        # Dark Mode Toggle
        self.dark_mode_cb = PyToggle()
        self.dark_mode_cb.setChecked(ThemeManager.is_dark_mode()) 
        self.dark_mode_cb.toggled.connect(self.toggle_theme_mode)
        
        add_setting_row(card_config.layout(), "Dark Mode", "Toggle between Light and Dark appearance", self.dark_mode_cb)

        # UI Scale Override
        self.ui_scale_sb = QDoubleSpinBox()
        self.ui_scale_sb.setDecimals(2)
        self.ui_scale_sb.setRange(0.0, 2.0)
        self.ui_scale_sb.setSingleStep(0.05)
        self.ui_scale_sb.setSpecialValueText("Auto")
        self.ui_scale_sb.setFixedWidth(110)
        self.ui_scale_sb.setValue(float(self.global_settings.get("ui_scale_override", 0.0) or 0.0))
        self.ui_scale_sb.setStyleSheet(f"background: #1D1D22; color: white; border: 1px solid {C.BORDER_LIGHT}; padding: {S.XS}px;")
        add_setting_row(
            card_config.layout(),
            "UI Scale",
            "0 = Auto detect. Use 0.90-1.10 for fine tuning and overlap fixes.",
            self.ui_scale_sb,
        )
        
        # Save Button (Bottom of Config)
        card_config.layout().addSpacing(10)
        # A saturated bar the full width of the card is the loudest thing on the
        # screen, for an action that saves four switches. It is a normal-sized
        # primary button, aligned right where a form's confirm belongs.
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_btn = make_button("Save changes", "primary",
                               on_click=self.save_global_settings)
        save_row.addWidget(save_btn)
        card_config.layout().addLayout(save_row)
        
        main_layout.addWidget(card_config)

        # 1b. PATHS & CONNECTIONS
        card_paths = SettingsCard("Paths & Connections")
        card_paths.layout().setSpacing(10)

        project_row = QHBoxLayout()
        self.project_root_input = QLineEdit(str(self.config_manager.settings.get("last_project_dir", "")))
        self.project_root_input.setPlaceholderText("Project Root Directory")
        self.project_root_input.setStyleSheet(f"background: #1D1D22; color: white; border: 1px solid {C.BORDER_LIGHT}; padding: {S.XS}px;")
        self.project_root_input.setMinimumHeight(34)
        btn_project_root = QPushButton("Browse")
        btn_project_root.setMinimumHeight(34)
        btn_project_root.clicked.connect(lambda: self._browse_directory(self.project_root_input, "Select Project Root"))
        project_row.addWidget(self.project_root_input, 1)
        project_row.addWidget(btn_project_root)
        card_paths.layout().addWidget(QLabel("Project Root"))
        card_paths.layout().addLayout(project_row)
        card_paths.layout().addWidget(self.create_divider())

        excel_row = QHBoxLayout()
        self.excel_tracking_input = QLineEdit(str(self.config_manager.settings.get("last_excel_file", "")))
        self.excel_tracking_input.setPlaceholderText("Excel Tracking File (.xlsx)")
        self.excel_tracking_input.setStyleSheet(f"background: #1D1D22; color: white; border: 1px solid {C.BORDER_LIGHT}; padding: {S.XS}px;")
        self.excel_tracking_input.setMinimumHeight(34)
        btn_excel = QPushButton("Browse")
        btn_excel.setMinimumHeight(34)
        btn_excel.clicked.connect(lambda: self._browse_file(self.excel_tracking_input, "Select Excel File", "Excel Files (*.xlsx *.xls)"))
        excel_row.addWidget(self.excel_tracking_input, 1)
        excel_row.addWidget(btn_excel)
        card_paths.layout().addWidget(QLabel("Excel Tracking File"))
        card_paths.layout().addLayout(excel_row)
        card_paths.layout().addWidget(self.create_divider())

        server_row = QHBoxLayout()
        self.server_root_input = QLineEdit(str(GlobalConfig.get("SERVER_ROOT", "")))
        self.server_root_input.setPlaceholderText("UT_Central Server Root")
        self.server_root_input.setStyleSheet(f"background: #1D1D22; color: white; border: 1px solid {C.BORDER_LIGHT}; padding: {S.XS}px;")
        self.server_root_input.setMinimumHeight(34)
        btn_server_root = QPushButton("Browse")
        btn_server_root.setMinimumHeight(34)
        btn_server_root.clicked.connect(lambda: self._browse_directory(self.server_root_input, "Select UT_Central Root"))
        server_row.addWidget(self.server_root_input, 1)
        server_row.addWidget(btn_server_root)
        card_paths.layout().addWidget(QLabel("Server Root"))
        card_paths.layout().addLayout(server_row)
        card_paths.layout().addWidget(self.create_divider())

        branding_row = QHBoxLayout()
        self.brand_logo_input = QLineEdit(str(self.global_settings.get("branding_logo_path", "")))
        self.brand_logo_input.setPlaceholderText("Optional: Studio/Company Logo Image (.png/.jpg/.svg)")
        self.brand_logo_input.setStyleSheet(f"background: #1D1D22; color: white; border: 1px solid {C.BORDER_LIGHT}; padding: {S.XS}px;")
        self.brand_logo_input.setMinimumHeight(34)
        btn_brand_logo = QPushButton("Browse")
        btn_brand_logo.setMinimumHeight(34)
        btn_brand_logo.clicked.connect(
            lambda: self._browse_file(
                self.brand_logo_input,
                "Select Studio Logo",
                "Images (*.png *.jpg *.jpeg *.bmp *.webp *.svg);;All Files (*)",
            )
        )
        btn_clear_logo = QPushButton("Clear")
        btn_clear_logo.setMinimumHeight(34)
        btn_clear_logo.clicked.connect(lambda: self.brand_logo_input.setText(""))
        branding_row.addWidget(self.brand_logo_input, 1)
        branding_row.addWidget(btn_brand_logo)
        branding_row.addWidget(btn_clear_logo)
        card_paths.layout().addWidget(QLabel("Studio Logo (Optional)"))
        card_paths.layout().addLayout(branding_row)
        card_paths.layout().addWidget(self.create_divider())

        db_grid = QGridLayout()
        db_grid.setHorizontalSpacing(10)
        db_grid.setVerticalSpacing(8)
        db_grid.setColumnStretch(1, 1)
        db_grid.setColumnStretch(3, 1)
        db_grid.setRowMinimumHeight(0, 38)
        db_grid.setRowMinimumHeight(1, 38)

        self.db_host_input = QLineEdit(str(GlobalConfig.get("db_host", "")))
        self.db_host_input.setPlaceholderText("DB Host")
        self.db_host_input.setStyleSheet(f"background: #1D1D22; color: white; border: 1px solid {C.BORDER_LIGHT}; padding: {S.XS}px;")
        self.db_host_input.setMinimumHeight(34)
        self.db_port_input = QSpinBox()
        self.db_port_input.setRange(1, 65535)
        self.db_port_input.setValue(int(GlobalConfig.get("db_port", 5440) or 5440))
        self.db_port_input.setStyleSheet(f"background: #1D1D22; color: white; border: 1px solid {C.BORDER_LIGHT}; padding: {S.XS}px;")
        self.db_port_input.setMinimumHeight(34)
        self.db_name_input = QLineEdit(str(GlobalConfig.get("db_name", "")))
        self.db_name_input.setPlaceholderText("DB Name")
        self.db_name_input.setStyleSheet(f"background: #1D1D22; color: white; border: 1px solid {C.BORDER_LIGHT}; padding: {S.XS}px;")
        self.db_name_input.setMinimumHeight(34)
        self.db_user_input = QLineEdit(str(GlobalConfig.get("db_user", "")))
        self.db_user_input.setPlaceholderText("DB User")
        self.db_user_input.setStyleSheet(f"background: #1D1D22; color: white; border: 1px solid {C.BORDER_LIGHT}; padding: {S.XS}px;")
        self.db_user_input.setMinimumHeight(34)

        db_grid.addWidget(QLabel("DB Host"), 0, 0)
        db_grid.addWidget(self.db_host_input, 0, 1)
        db_grid.addWidget(QLabel("DB Port"), 0, 2)
        db_grid.addWidget(self.db_port_input, 0, 3)
        db_grid.addWidget(QLabel("DB Name"), 1, 0)
        db_grid.addWidget(self.db_name_input, 1, 1)
        db_grid.addWidget(QLabel("DB User"), 1, 2)
        db_grid.addWidget(self.db_user_input, 1, 3)
        card_paths.layout().addWidget(QLabel("Database Connection"))
        card_paths.layout().addLayout(db_grid)
        card_paths.layout().addWidget(self.create_divider())


        card_paths.layout().addSpacing(8)
        btn_save_paths = QPushButton("Save Paths & Connections")
        btn_save_paths.setStyleSheet(f"background-color: #3EA8BF; color: white; font-weight: {T.WEIGHT_STYLE_BOLD}; padding: {S.SM}px; border-radius: {R.SM}px;")
        btn_save_paths.setMinimumHeight(34)
        btn_save_paths.clicked.connect(self.save_paths_and_connections)
        card_paths.layout().addWidget(btn_save_paths)

        main_layout.addWidget(card_paths)

        # 1c. RUNTIME STATUS (moved from header for artist-friendly UI)
        card_runtime = SettingsCard("Runtime Status")
        card_runtime.layout().setSpacing(8)
        self.runtime_db_label = QLabel("DB Mode: --")
        self.runtime_db_label.setStyleSheet(f"color: {C.TEXT_PRIMARY}; font-size: {T.SIZE_SM}px;")
        self.runtime_server_label = QLabel("Server Root: --")
        self.runtime_server_label.setStyleSheet(f"color: {C.TEXT_PRIMARY}; font-size: {T.SIZE_SM}px;")
        self.runtime_exr_label = QLabel("EXR Policy: --")
        self.runtime_exr_label.setStyleSheet(f"color: {C.TEXT_PRIMARY}; font-size: {T.SIZE_SM}px;")
        self.runtime_sync_label = QLabel("Sync Status: --")
        self.runtime_sync_label.setStyleSheet(f"color: {C.TEXT_PRIMARY}; font-size: {T.SIZE_SM}px;")

        card_runtime.layout().addWidget(self.runtime_db_label)
        card_runtime.layout().addWidget(self.runtime_server_label)
        card_runtime.layout().addWidget(self.runtime_exr_label)
        card_runtime.layout().addWidget(self.runtime_sync_label)

        btn_runtime_refresh = QPushButton("Refresh Runtime Status")
        btn_runtime_refresh.setMinimumHeight(32)
        btn_runtime_refresh.setStyleSheet(
            f"background-color: #3EA8BF; color: white; font-weight: {T.WEIGHT_STYLE_BOLD}; "
            f"padding: {S.SM}px; border-radius: {R.SM}px;"
        )
        btn_runtime_refresh.clicked.connect(self.refresh_runtime_status)
        card_runtime.layout().addSpacing(8)
        card_runtime.layout().addWidget(btn_runtime_refresh)

        main_layout.addWidget(card_runtime)
        self.refresh_runtime_status()

        # 2. SYSTEM & MAINTENANCE ACTIONS
        card_maint = SettingsCard("System Maintenance")
        grid = QGridLayout(); grid.setSpacing(15)
        
        btn_report = ActionCard("info", "Generate Report", "Export PDF summary of project history", self.generate_project_summary_report)
        btn_backup = ActionCard("package", "Create Backup", "Archive current project to ZIP", self.create_backup)
        btn_logs   = ActionCard("alert", "View Logs", "Open error log directory", self.show_error_report)
        self.btn_update = ActionCard("refresh", "Check Updates", "Search Central for new versions", self.check_for_updates)
        
        grid.addWidget(btn_report, 0, 0); grid.addWidget(btn_backup, 0, 1)
        grid.addWidget(btn_logs, 1, 0); grid.addWidget(self.btn_update, 1, 1)
        
        card_maint.layout().addLayout(grid)
        main_layout.addWidget(card_maint)

        # 3. ADVANCED (Collapsible)
        card_adv = SettingsCard("Performance")
        
        h_adv = QHBoxLayout()
        self.chk_adv = QCheckBox("Show Advanced Thread Options")
        self.chk_adv.setChecked(self.global_settings.get("show_advanced_options", False))
        self.chk_adv.setStyleSheet(f"color: {C.TEXT_SECONDARY};")
        self.chk_adv.toggled.connect(self.toggle_advanced_settings)
        h_adv.addWidget(self.chk_adv); h_adv.addStretch()
        card_adv.layout().addLayout(h_adv)
        
        self.adv_container = QWidget(); self.adv_container.setVisible(self.chk_adv.isChecked())
        adv_lay = QVBoxLayout(self.adv_container); adv_lay.setContentsMargins(0,10,0,0)
        
        self.max_concurrent_sb = QSpinBox(); self.max_concurrent_sb.setRange(1, 16); self.max_concurrent_sb.setFixedWidth(60)
        self.max_concurrent_sb.setValue(self.global_settings.get("max_concurrent_operations", 4))
        self.max_concurrent_sb.setStyleSheet(f"background: #1D1D22; color: white; padding: {S.XS}px; border: 1px solid {C.BORDER_LIGHT};")
        
        adv_row = QHBoxLayout() # Changed form to HBox for better alignment
        adv_row.addWidget(QLabel("Max Concurrent Threads:"))
        adv_row.addWidget(self.max_concurrent_sb); adv_row.addStretch()
        adv_lay.addLayout(adv_row)
        
        card_adv.layout().addWidget(self.adv_container)
        main_layout.addWidget(card_adv)
        
        main_layout.addStretch()
        scroll.setWidget(content)
        root_layout.addWidget(scroll)

    def refresh_runtime_status(self):
        """Populate runtime DB/server/exr/sync status in Settings."""
        try:
            status = database_manager.get_runtime_status() or {}
        except Exception as exc:
            logging.debug("Runtime status refresh failed: %s", exc)
            status = {}

        requested_mode = str(status.get("requested_mode", "unknown"))
        active_mode = str(status.get("active_mode", "unknown"))
        fallback_used = bool(status.get("fallback_used", False))
        bootstrap_error = str(status.get("bootstrap_error", "") or "").strip()

        server_root_value = str(GlobalConfig.get("SERVER_ROOT", "")).strip()
        server_root_path = Path(server_root_value) if server_root_value else None
        server_root_ok = bool(server_root_path and server_root_path.exists() and server_root_path.is_dir())
        exr_enabled = bool(GlobalConfig.exr_loading_enabled())
        sync_enabled = active_mode.lower() == "postgres" and not fallback_used

        self.runtime_db_label.setText(
            f"DB Mode: requested {requested_mode} | active {active_mode} | fallback {fallback_used}"
        )
        self.runtime_server_label.setText(
            f"Server Root: {server_root_value or '(not set)'} | reachable {server_root_ok}"
        )
        self.runtime_exr_label.setText(f"EXR Policy: {'Enabled' if exr_enabled else 'Disabled'}")
        self.runtime_sync_label.setText(f"Sync Status: {'Available' if sync_enabled else 'Limited (LOCAL MODE)'}")

        if bootstrap_error:
            self.runtime_sync_label.setText(
                f"Sync Status: {'Available' if sync_enabled else 'Limited (LOCAL MODE)'} | DB error: {bootstrap_error}"
            )

    def create_divider(self):
        line = QFrame(); line.setFrameShape(QFrame.HLine); line.setFrameShadow(QFrame.Sunken); line.setStyleSheet(f"background: {C.BG_ELEVATED}; margin-top: {S.XS}px; margin-bottom: {S.XS}px;")
        return line

    def toggle_advanced_settings(self, checked):
        self.adv_container.setVisible(checked)

    def toggle_theme_mode(self, checked):
        ThemeManager.toggle_mode()

    def save_global_settings(self):
        try:
            self.global_settings["restore_last_paths"] = self.restore_paths_cb.isChecked()
            self.global_settings["dry_run_enabled"] = self.dry_run_default_cb.isChecked()
            self.global_settings["ui_scale_override"] = round(float(self.ui_scale_sb.value()), 2)
            self.global_settings["show_advanced_options"] = self.chk_adv.isChecked()
            self.global_settings["max_concurrent_operations"] = self.max_concurrent_sb.value()
            
            saved = self.config_manager.update_global_settings(self.global_settings)
            if not saved:
                raise RuntimeError("Config manager rejected updated settings")

            self.global_settings_updated.emit(dict(self.global_settings))
            QMessageBox.information(self, "Saved", "Settings updated!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Save failed: {e}")

    def _browse_directory(self, target_input: QLineEdit, title: str):
        start_dir = target_input.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, title, start_dir)
        if selected:
            target_input.setText(selected)

    def _browse_file(self, target_input: QLineEdit, title: str, file_filter: str):
        start_file = target_input.text().strip() or str(Path.home())
        selected, _ = QFileDialog.getOpenFileName(self, title, start_file, file_filter)
        if selected:
            target_input.setText(selected)

    def save_paths_and_connections(self):
        """Persist path and DB connection settings used during daily operations."""
        try:
            project_root = self.project_root_input.text().strip()
            excel_path = self.excel_tracking_input.text().strip()
            server_root = self.server_root_input.text().strip()
            db_host = self.db_host_input.text().strip()
            db_port = int(self.db_port_input.value())
            db_name = self.db_name_input.text().strip()
            db_user = self.db_user_input.text().strip()
            branding_logo_path = self.brand_logo_input.text().strip()

            if branding_logo_path and not Path(branding_logo_path).exists():
                QMessageBox.warning(
                    self,
                    "Invalid Logo Path",
                    "Selected studio logo image was not found. Please choose a valid file.",
                )
                return

            if project_root:
                self.config_manager.settings["last_project_dir"] = project_root
            if excel_path:
                self.config_manager.settings["last_excel_file"] = excel_path
            self.config_manager.save_settings(self.config_manager.settings)

            self.global_settings["branding_logo_path"] = branding_logo_path
            saved_globals = self.config_manager.update_global_settings(self.global_settings)
            if saved_globals:
                self.global_settings_updated.emit(dict(self.global_settings))

            if server_root:
                GlobalConfig.set("SERVER_ROOT", server_root)
            if db_host:
                GlobalConfig.set("db_host", db_host)
            GlobalConfig.set("db_port", db_port)
            if db_name:
                GlobalConfig.set("db_name", db_name)
            if db_user:
                GlobalConfig.set("db_user", db_user)

            QMessageBox.information(
                self,
                "Saved",
                "Paths and connections saved.\nBranding updates applied immediately.\nRestart UT_VFX for DB/server updates.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not save paths/connections:\n{e}")

    def request_template_refresh(self):
        self.templates_refresh_requested.emit()
        QMessageBox.information(self, "Templates", "Templates reloaded successfully.")
        
    def check_for_updates(self):
        """Manual update check."""
        if self.sidecar_engine and hasattr(self.sidecar_engine, 'temp_updater'):
            # Already staged, click means Restart!
            self._apply_staged_update()
            return
            
        self.btn_update.update_content("refresh", "Checking...", "Looking for updates...")
        self._cleanup_update_checker()
        self.update_checker = UpdateChecker(self, manual_mode=True, target="client")
        self.update_checker.update_available.connect(self.on_update_found)
        self.update_checker.update_not_found.connect(self.on_no_update)
        self.update_checker.finished.connect(self._cleanup_update_checker)
        self.update_checker.start()
        
    def on_update_found(self, manifest):
        self.btn_update.update_content("refresh", "Check Updates", "Search Central for new versions")
        dlg = UpdateAvailableDialog(manifest, self)
        if dlg.exec():
            # User wants to download & stage
            self._stage_update(manifest)
            
    def _stage_update(self, manifest):
        from ...core.updater.sidecar_engine import SidecarEngine
        
        self.btn_update.setEnabled(False)
        self.btn_update.update_content("refresh", "Downloading...", "Staging update in background")
        self.sidecar_engine = SidecarEngine(manifest)
        
        # We can run it in a thread if it blocks, but SidecarEngine currently blocks.
        # Since this is a UI tool, let's just let it block for a moment, or better, we can wrap it.
        # But wait, SidecarEngine is a QObject with signals.
        # Let's assume stage_update() is fast enough or blocks slightly.
        QApplication.processEvents()
        
        success = self.sidecar_engine.stage_update()
        self.btn_update.setEnabled(True)
        
        if success:
            self.btn_update.update_content("check", "Restart to Apply", "Update staged. Click to restart.")
            self.btn_update.setStyleSheet(f"""
                ActionCard {{ background-color: #1B3A2C; border: 1px solid #5FBF8F; border-radius: 8px; text-align: left; padding: 15px; }}
                ActionCard:hover {{ background-color: #5FBF8F; border: 1px solid #5FBF8F; }}
            """)
            QMessageBox.information(self, "Update Ready", "Update downloaded and verified. Click 'Restart to Apply' when you are ready to update.")
        else:
            self.btn_update.update_content("refresh", "Check Updates", "Search Central for new versions")
            QMessageBox.warning(self, "Update Failed", "Failed to stage the update. Check the logs.")
            
    def _apply_staged_update(self):
        if not self.sidecar_engine:
            return
            
        reply = QMessageBox.question(self, "Apply Update", "The application will now restart to apply the update. Continue?", 
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                from ...core.infra.database_manager import database_manager
                database_manager.force_shutdown()
            except Exception:
                pass
            self.sidecar_engine.apply_update()
            
            # Close application
            app = QApplication.instance()
            top = self.window()
            if top and hasattr(top, "close"):
                top.close()
            elif app:
                app.quit()
        
    def on_no_update(self, current_ver):
        self.btn_update.update_content("refresh", "Check Updates", "Search Central for new versions")
        reason = ""
        if self.update_checker:
            reason = str(getattr(self.update_checker, "last_result_reason", "") or "")

        if reason == "missing_latest_pointer" or reason == "manifest_missing":
            QMessageBox.information(
                self,
                "Update Feed Missing",
                f"No update manifest was found at:\n{GlobalConfig.server_root() / 'Updates' / 'releases' / 'manifest_client.json'}\n\n"
                f"Current version: {current_ver}",
            )
            return

        if reason == "invalid_manifest":
            QMessageBox.information(
                self,
                "Update Feed Invalid",
                "The update manifest exists but is missing required fields.\n"
                "Please validate Updates/releases/manifest_client.json.",
            )
            return

        QMessageBox.information(self, "Up to Date", f"You are running the latest version: {current_ver}")

    def generate_project_summary_report(self):
        try:
            projects = database_manager.get_all_projects_summary()
            if not projects:
                QMessageBox.information(self, "Info", "No project history found.")
                return

            item_list = [f"{p['name']} ({p['created_at']})" for p in projects]
            id_map = {item: projects[i]['id'] for i, item in enumerate(item_list)}

            selected_item, ok = QInputDialog.getItem(self, "Select Project", "Generate report for:", item_list, 0, False)
            if not ok or not selected_item: return

            selected_id = id_map[selected_item]
            default_name = f"Report_{selected_item.split(' ')[0]}_{datetime.now().strftime('%Y%m%d')}.pdf"
            file_path, _ = QFileDialog.getSaveFileName(self, "Save Report", str(Path.home() / default_name), "PDF Files (*.pdf)")
            
            if file_path:
                self._cleanup_report_worker()
                self.report_worker = ReportWorker(Path(file_path), project_id=selected_id)
                self.report_worker.finished_signal.connect(self._on_report_worker_finished)
                self.report_worker.finished.connect(self._on_report_worker_done)
                self.report_worker.finished.connect(self.report_worker.deleteLater)
                self.report_worker.start()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def create_backup(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Directory to Backup")
        if directory:
            name, ok = QInputDialog.getText(self, "Backup Name", "Enter backup label:")
            if ok and name:
                success, msg, path = self.backup_manager.create_backup([Path(directory)], name)
                if success: QMessageBox.information(self, "Success", f"Backup saved:\n{path}")
                else: QMessageBox.critical(self, "Backup Failed", msg)

    def show_error_report(self):
        log_dir = error_handler.log_directory
        if log_dir.exists(): QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir)))
        else: QMessageBox.information(self, "Info", "Log directory not found.")

    def closeEvent(self, event):
        self._cleanup_report_worker()
        self._cleanup_update_checker()
        super().closeEvent(event)
