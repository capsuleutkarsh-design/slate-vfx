"""
Settings: your own preferences, and - for the people allowed to change them -
the studio's settings.

One Save for the page: every preference and path on it, and the studio cards,
are saved together, a bar says when something is unsaved, Discard puts the
saved values back and Reset to defaults restores Slate's own. One message
says what was saved.

Who sees what: everybody has the preferences for the tools they can open, the
studio policy (as a summary unless they may change it), the runtime status
and the maintenance actions that only touch their own machine. Server root,
database connection, studio logo and updates need the studio_settings
ability (admin, developer, IT); the money card is for studio_settings and
approve_bid holders.

'Back up a project' is gone: it copied a whole project to this PC's system
drive, zipped and encrypted it with this PC's key, with no size check, no
Cancel and no Restore. Project folders live on the studio's file server and
belong to its backup; Slate's own data is backed up by the Slate Server
(verified pg_dump backups).
"""

import logging
import re
import socket
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QGridLayout, QLabel,
    QMessageBox, QInputDialog, QFileDialog, QFrame, QLineEdit,
    QScrollArea, QApplication, QDoubleSpinBox, QComboBox
)
from PySide6.QtCore import Signal, Qt, QUrl, QThread
from PySide6.QtGui import QPixmap, QDesktopServices, QIntValidator, QFontMetrics

from ...core.worker_threads import ReportWorker

from ...core.infra.database_manager import database_manager
from ...core.infra.config_manager import ConfigManager
from ...utils.error_handler import error_handler
from ...core.infra.theme_manager import ThemeManager
from ...core.infra.global_config import GlobalConfig
from ...core.updater.update_checker import UpdateChecker
from ..dialogs.update_available_dialog import UpdateAvailableDialog

# Import design tokens for theming
from ...core.infra.design_tokens import ColorTokens as C, TypographyTokens as T, SpacingTokens as S, RadiusTokens as R

from ...gui.widgets.py_toggle import PyToggle
from ..core.controls import make_button
from slate.core.infra.gate import Gate

try:
    import shiboken6
except Exception:
    shiboken6 = None

# Every control in the right-hand column of a settings row has this width.
CONTROL_WIDTH = 160
UI_SCALE_MIN = 0.75
UI_SCALE_MAX = 1.50

_HOSTNAME = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")


def valid_db_host(text) -> bool:
    """A host name, an IPv4 address or an IPv6 address."""
    host = str(text or "").strip()
    if not host:
        return False
    try:
        socket.inet_pton(socket.AF_INET6, host)
        return True
    except (OSError, ValueError, AttributeError):
        pass
    if re.match(r"^\d+(\.\d+){3}$", host):
        return all(0 <= int(part) <= 255 for part in host.split("."))
    return bool(_HOSTNAME.match(host))


def snap_ui_scale(value: float) -> float:
    """0 is Auto; anything between 0 and 0.75 is raised to 0.75, the smallest the engine applies."""
    value = float(value or 0.0)
    if value <= 0:
        return 0.0
    return round(max(UI_SCALE_MIN, min(UI_SCALE_MAX, value)), 2)


class ScaleSpinBox(QDoubleSpinBox):
    """
    UI scale: Auto (0) or 0.75-1.50. The arrows step from Auto straight to
    0.75 and back - they used to walk through 0.05 ... 0.70, values Slate
    ignores.
    """

    def stepBy(self, steps):
        value = round(self.value(), 2)
        if steps > 0 and value < UI_SCALE_MIN:
            self.setValue(UI_SCALE_MIN)
            return
        if steps < 0 and value <= UI_SCALE_MIN:
            self.setValue(0.0)
            return
        super().stepBy(steps)


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_") or "Project"


def runtime_lines(status: dict, server_root: str, server_ok: bool, exr_enabled: bool) -> dict:
    """The Runtime status card in plain words (it printed 'fallback False')."""
    active = str(status.get("active_mode", "")).lower()
    fallback = bool(status.get("fallback_used", False))
    connected = active == "postgres" and not fallback
    error = str(status.get("bootstrap_error", "") or "").strip()
    database = ("Connected to the studio database." if connected else
                "Working locally - changes are not shared with other workstations.")
    if error and not connected:
        database += f" The studio database could not be reached: {error.splitlines()[0]}"
    if not server_root:
        folder = "Studio folder: not set."
    else:
        folder = f"Studio folder: {server_root} - {'reachable' if server_ok else 'not reachable'}."
    if connected and server_ok:
        sync = "Sharing: everything is shared with the studio."
    elif connected:
        sync = "Sharing: limited - the database is shared, but the studio folder is not reachable."
    else:
        sync = "Sharing: limited - this workstation is working locally."
    return {"database": database, "folder": folder,
            "exr": f"EXR previews: {'on' if exr_enabled else 'off'}.", "sync": sync}


def _roles_of_window(widget):
    window = widget.window() if widget is not None else None
    roles = getattr(window, "user_roles", None)
    if roles is None:
        data = getattr(window, "user_data", None) or {}
        roles = data.get("roles", data.get("role"))
    if roles is None:
        return None
    return [roles] if isinstance(roles, str) else list(roles)


# --- MODERN UI COMPONENTS ---

class ActionCard(QPushButton):
    """Large clickable card for actions like Backups/Reports"""
    def __init__(self, icon_char, title, desc, callback):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self.clicked.connect(callback)

        lay = QVBoxLayout(self)
        self.lbl_icon = QLabel()
        self.lbl_icon.setStyleSheet("border:none; background:transparent;")
        self._set_glyph(icon_char)
        self.lbl_title = QLabel(title); self.lbl_title.setStyleSheet(f"font-size: {T.SIZE_MD}px; font-weight: {T.WEIGHT_STYLE_BOLD}; border:none; background:transparent;")
        self.lbl_desc = QLabel(desc); self.lbl_desc.setStyleSheet(f"color: {C.TEXT_GRAY_LIGHT}; border:none; background:transparent; line-height: 120%;")
        self.lbl_desc.setWordWrap(True)

        lay.addWidget(self.lbl_icon); lay.addWidget(self.lbl_title); lay.addWidget(self.lbl_desc)
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

    def title(self) -> str:
        return self.lbl_title.text()


class SettingsCard(QFrame):
    """Wrapper for a section of settings"""
    def __init__(self, title):
        super().__init__()
        self.setStyleSheet(f".SettingsCard {{ background-color: {C.BG_DARK}; border-radius: {S.MD}px; border: 1px solid {Gate.RAISED}; }}")
        self.main_layout = QVBoxLayout(self)

        lbl = QLabel(title)
        lbl.setStyleSheet(f"font-size: {T.SIZE_LG}px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: {C.ACCENT_PRIMARY}; margin-bottom: {S.MD}px; border:none;")
        self.main_layout.addWidget(lbl)


class _Job(QThread):
    """Runs one blocking call off the UI thread and hands its result back."""
    done = Signal(object, object)          # result, error text

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(), None)
        except Exception as exc:
            self.done.emit(None, str(exc))


class SettingsTab(QWidget):
    """
    Standalone Settings Tab containing Global Settings, Maintenance, and reporting tools.
    """
    templates_refresh_requested = Signal()
    global_settings_updated = Signal(dict)
    studio_policy_saved = Signal()

    def __init__(self, config_manager=None, roles=None):
        super().__init__()
        self.config_manager = config_manager or ConfigManager()
        self.settings = self.config_manager.settings
        self.global_settings = self.settings.get("global_settings", self.config_manager.default_global_settings)
        self.update_checker = None
        self.sidecar_engine = None
        self.report_worker = None
        self._jobs = set()
        self._loading = False
        self._dirty = False
        self._roles = roles
        self._gated = False
        self._saving = False
        self._recounted = False
        self.can_studio = False
        self.can_system = False
        self.can_money = False
        self.allowed_tabs = None        # None: not known yet, every preference is shown
        self.init_ui()
        if roles is not None:
            self.apply_access(roles)

    # ------------------------------------------------------------ helpers
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

    def _run_job(self, fn, on_done):
        """fn() on a worker thread; on_done(result, error) back on the UI thread."""
        job = _Job(fn, self)
        self._jobs.add(job)

        def finished(result, error):
            self._jobs.discard(job)
            if self._is_qobject_alive(self):
                on_done(result, error)
        job.done.connect(finished)
        job.start()
        return job

    def _on_report_worker_finished(self, success, message):
        worker = self.sender()
        if worker is not self.report_worker:
            return
        if not self._is_qobject_alive(self):
            return
        if success:
            QMessageBox.information(self, "Project report", message)
        else:
            QMessageBox.warning(self, "Project report", message)

    def _on_report_worker_done(self):
        worker = self.sender()
        if worker is self.report_worker:
            self.report_worker = None

    def _toast(self, message, level="success"):
        self.last_message = message
        try:
            from ..components.feedback import toast
            toast(self, message, level)
        except Exception:
            logging.info(message)

    # ---------------------------------------------------------------- UI
    def _heading(self, label_text, desc_text):
        """A row's bold title and grey description - the same on every row."""
        v = QVBoxLayout()
        l = QLabel(label_text); l.setStyleSheet(f"font-size: {T.SIZE_MD}px; font-weight: {T.WEIGHT_SEMIBOLD}; color: {Gate.TEXT}; border:none;")
        v.addWidget(l)
        if desc_text:
            d = QLabel(desc_text); d.setStyleSheet(f"font-size: 11px; color: {C.TEXT_TERTIARY}; border:none;")
            d.setWordWrap(True)
            v.addWidget(d)
        return v

    def _row(self, layout, label_text, desc_text, control):
        """One preference: title and description left, the control right. Returns the row."""
        box = QWidget()
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addLayout(self._heading(label_text, desc_text), 1)
        # Every right-hand control sits in a box of one width, so the column
        # lines up (they were 110, 130 px and a toggle).
        holder = QWidget()
        holder.setFixedWidth(CONTROL_WIDTH)
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch(1)
        hl.addWidget(control)
        row.addWidget(holder)
        outer.addLayout(row)
        outer.addWidget(self.create_divider())
        layout.addWidget(box)
        return box

    def _path_row(self, layout, title, desc, field, buttons):
        """A path: the same title and description as the other rows, the field under them."""
        box = QWidget()
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self._heading(title, desc))
        row = QHBoxLayout()
        row.addWidget(field, 1)
        for button in buttons:
            row.addWidget(button)
        outer.addLayout(row)
        outer.addWidget(self.create_divider())
        layout.addWidget(box)
        field.textChanged.connect(lambda text, f=field: f.setToolTip(text))
        return box

    @staticmethod
    def _show_start(field):
        """A long path shows its beginning (it was scrolled to its middle)."""
        field.setCursorPosition(0)
        field.setToolTip(field.text())

    def init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ---- the page's one Save, always in view
        self.action_bar = QFrame()
        self.action_bar.setObjectName("SettingsActions")
        self.action_bar.setStyleSheet(f"QFrame#SettingsActions {{ background: {Gate.PANEL}; "
                                      f"border-bottom: 1px solid {Gate.LINE}; }}")
        bar = QHBoxLayout(self.action_bar)
        bar.setContentsMargins(20, 8, 20, 8)
        self.lbl_dirty = QLabel("")
        self.lbl_dirty.setStyleSheet(f"color: {Gate.WARN}; font-weight: 600;")
        bar.addWidget(self.lbl_dirty)
        bar.addStretch(1)
        self.btn_reset = make_button("Reset to defaults", "ghost", on_click=self.reset_to_defaults,
                                     tooltip="Put your preferences back to Slate's defaults")
        self.btn_discard = make_button("Discard", "secondary", on_click=self.discard_changes)
        self.btn_save = make_button("Save changes", "primary", on_click=self.save_all)
        for b in (self.btn_reset, self.btn_discard, self.btn_save):
            bar.addWidget(b)
        root_layout.addWidget(self.action_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        main_layout = QVBoxLayout(content)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        # 1. YOUR PREFERENCES
        card_config = SettingsCard("Your preferences")
        lay = card_config.layout()

        self.restore_paths_cb = PyToggle()
        self.restore_paths_cb.setChecked(self.global_settings.get("restore_last_paths", True))
        self._row(lay, "Remember last folders", "Open the folders you last used when Slate starts",
                  self.restore_paths_cb)

        self.dry_run_default_cb = PyToggle()
        self.dry_run_default_cb.setChecked(self.global_settings.get("dry_run_enabled", False))
        # Rows for one tool: shown only to people who can open it.
        self.ingest_rows = [self._row(
            lay, "Start Build & Ingest in dry run",
            "Build & Ingest starts with Dry run ticked: it shows what it would do and writes nothing",
            self.dry_run_default_cb)]

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(ThemeManager.get_available_themes())
        self.theme_combo.setCurrentText(ThemeManager.get_current_theme())
        self.theme_combo.setFixedWidth(CONTROL_WIDTH)
        self._row(lay, "Theme", "Dark or Light. Takes full effect the next time Slate starts.",
                  self.theme_combo)

        self.ui_scale_sb = ScaleSpinBox()
        self.ui_scale_sb.setDecimals(2)
        # 0 is Auto; the engine applies nothing below 0.75 (snapped on edit).
        self.ui_scale_sb.setRange(0.0, UI_SCALE_MAX)
        self.ui_scale_sb.setSingleStep(0.05)
        self.ui_scale_sb.setSpecialValueText("Auto")
        self.ui_scale_sb.setFixedWidth(CONTROL_WIDTH)
        self.ui_scale_sb.setValue(snap_ui_scale(self.global_settings.get("ui_scale_override", 0.0)))
        self.ui_scale_sb.editingFinished.connect(self._snap_scale)
        scale_row = self._row(lay, "UI scale",
                              f"Auto, or {UI_SCALE_MIN:.2f}-{UI_SCALE_MAX:.2f}. 0.90-1.10 fine-tunes text that overlaps.",
                              self.ui_scale_sb)
        self.lbl_scale_applied = QLabel("")
        self.lbl_scale_applied.setStyleSheet(f"font-size: 11px; color: {Gate.TEXT_DIM};")
        self.lbl_scale_applied.setAlignment(Qt.AlignmentFlag.AlignRight)
        scale_row.layout().insertWidget(1, self.lbl_scale_applied)
        self._show_applied_scale()

        from ...core.dcc_launcher import NUKE_MODES, get_nuke_mode
        self.nuke_mode_combo = QComboBox()
        for mode, (label, _flags) in NUKE_MODES.items():
            self.nuke_mode_combo.addItem(label, mode)
        self.nuke_mode_combo.setCurrentIndex(
            max(0, self.nuke_mode_combo.findData(get_nuke_mode(self.config_manager))))
        self.nuke_mode_combo.setFixedWidth(CONTROL_WIDTH)
        self._row(lay, "Open Nuke as", "Which Nuke opens when you open a shot in Nuke",
                  self.nuke_mode_combo)

        self.project_root_input = QLineEdit(str(self.config_manager.settings.get("last_project_dir", "")))
        self.project_root_input.setPlaceholderText("Project root folder")
        self.ingest_rows.append(self._path_row(
            lay, "Project root", "The project folder Build & Ingest opens with", self.project_root_input, [
                make_button("Browse…", "secondary",
                            on_click=lambda: self._browse_directory(self.project_root_input, "Select project root")),
                make_button("Clear", "ghost", on_click=self.project_root_input.clear)]))
        # (An "Excel tracking file" row was here: Build & Ingest reads no Excel
        # file, and nothing read the setting.)
        main_layout.addWidget(card_config)

        # 2. STUDIO POLICY - everybody reads it, HR and admins change it.
        from .studio_settings_cards import StudioMoneyEditor, StudioPolicyEditor

        card_policy = SettingsCard("Studio policy")
        card_policy.layout().setSpacing(10)
        self.studio_policy_editor = StudioPolicyEditor()
        self.studio_policy_editor.saved.connect(self._on_policy_saved)
        self.card_policy = card_policy
        # Saved with the page's Save bar, like every other field here.
        self.studio_policy_editor.btn_save.hide()
        self.studio_policy_editor.changed.connect(self._mark_dirty)
        card_policy.layout().addWidget(self.studio_policy_editor)
        main_layout.addWidget(card_policy)

        # 3. STUDIO MONEY & HOURS - studio settings only.
        self.card_money = SettingsCard("Studio currency, rates and hours")
        self.card_money.layout().setSpacing(10)
        self.studio_money_editor = StudioMoneyEditor()
        self.studio_money_editor.btn_save.hide()
        self.studio_money_editor.changed.connect(self._mark_dirty)
        self.card_money.layout().addWidget(self.studio_money_editor)
        main_layout.addWidget(self.card_money)

        # 4. SERVER & DATABASE - studio settings only.
        self.card_paths = card_paths = SettingsCard("Server, database and branding")
        pl = card_paths.layout()
        pl.setSpacing(10)

        self.server_root_input = QLineEdit(str(GlobalConfig.get("SERVER_ROOT", "") or ""))
        self.server_root_input.setPlaceholderText("Slate_Central server root")
        self._path_row(pl, "Server root (this workstation)", "The studio folder Slate works from",
                       self.server_root_input, [
            make_button("Browse…", "secondary",
                        on_click=lambda: self._browse_directory(self.server_root_input, "Select Slate_Central root"))])

        from slate.core.infra.studio_settings import studio_logo
        self.brand_logo_input = QLineEdit(studio_logo(self.global_settings.get("branding_logo_path", "")))
        self.brand_logo_input.setPlaceholderText("Optional: studio logo image (.png, .jpg, .svg)")
        self._path_row(pl, "Studio logo (optional)", "Shown in the header", self.brand_logo_input, [
            make_button("Browse…", "secondary",
                        on_click=lambda: self._browse_file(
                            self.brand_logo_input, "Select studio logo",
                            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.svg);;All Files (*)")),
            make_button("Clear", "ghost", on_click=self.brand_logo_input.clear)])

        db_grid = QGridLayout()
        db_grid.setHorizontalSpacing(10)
        db_grid.setVerticalSpacing(8)
        db_grid.setColumnStretch(1, 1)
        self.db_host_input = QLineEdit(str(GlobalConfig.get("db_host", "") or ""))
        self.db_host_input.setPlaceholderText("Host name or IP address")
        # A plain field with a number check, like its neighbours - it was a
        # spin box with arrows.
        self.db_port_input = QLineEdit(str(GlobalConfig.get("db_port", 5440) or 5440))
        self.db_port_input.setValidator(QIntValidator(1, 65535, self))
        # Five digits: it stretched as wide as the host name.
        self.db_port_input.setFixedWidth(90)
        self.db_name_input = QLineEdit(str(GlobalConfig.get("db_name", "") or ""))
        self.db_name_input.setPlaceholderText("Database name")
        self.db_user_input = QLineEdit(str(GlobalConfig.get("db_user", "") or ""))
        self.db_user_input.setPlaceholderText("Database user")
        db_grid.addWidget(QLabel("Host"), 0, 0); db_grid.addWidget(self.db_host_input, 0, 1)
        db_grid.addWidget(QLabel("Port"), 0, 2); db_grid.addWidget(self.db_port_input, 0, 3)
        db_grid.addWidget(QLabel("Name"), 1, 0); db_grid.addWidget(self.db_name_input, 1, 1)
        db_grid.addWidget(QLabel("User"), 1, 2); db_grid.addWidget(self.db_user_input, 1, 3)
        pl.addWidget(QLabel("Studio database"))
        pl.addLayout(db_grid)
        note = QLabel("The database password is not shown or changed here: it is kept in this "
                      "workstation's Slate configuration and set by setup.bat. Changes to the "
                      "server root and database apply the next time Slate starts.")
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: 11px; color: {C.TEXT_TERTIARY};")
        pl.addWidget(note)
        test_row = QHBoxLayout()
        self.lbl_test = QLabel("")
        self.lbl_test.setWordWrap(True)
        test_row.addWidget(self.lbl_test, 1)
        self.btn_test_db = make_button("Test connection", "secondary", on_click=self.test_connection)
        test_row.addWidget(self.btn_test_db)
        pl.addLayout(test_row)
        main_layout.addWidget(card_paths)

        # 5. RUNTIME STATUS
        card_runtime = SettingsCard("Runtime status")
        card_runtime.layout().setSpacing(8)
        self.runtime_db_label = QLabel("")
        self.runtime_server_label = QLabel("")
        self.runtime_exr_label = QLabel("")
        self.runtime_sync_label = QLabel("")
        for label in (self.runtime_db_label, self.runtime_server_label,
                      self.runtime_exr_label, self.runtime_sync_label):
            label.setWordWrap(True)
            # Body size, like the rest of the page (it was the smallest text on it).
            label.setStyleSheet(f"color: {C.TEXT_PRIMARY}; font-size: {Gate.SIZE_MD}px;")
            card_runtime.layout().addWidget(label)
        refresh_row = QHBoxLayout()
        refresh_row.addStretch(1)
        refresh_row.addWidget(make_button("Refresh", "secondary", on_click=self.refresh_runtime_status))
        card_runtime.layout().addLayout(refresh_row)
        main_layout.addWidget(card_runtime)
        self.refresh_runtime_status()

        # 6. MAINTENANCE
        card_maint = SettingsCard("Maintenance")
        grid = QGridLayout(); grid.setSpacing(15)
        self.btn_report = ActionCard("info", "Project report", "Save a PDF summary of a project's history",
                                     self.generate_project_summary_report)
        self.btn_logs = ActionCard("folder", "Open my log folder",
                                   "Slate's log files on this computer, for IT", self.show_error_report)
        self.btn_templates = ActionCard("refresh", "Reload templates",
                                        "Read the folder templates again in the open tabs",
                                        self.request_template_refresh)
        self.btn_update = ActionCard("download", "Check for updates", "See whether a newer Slate is available",
                                     self.check_for_updates)
        self.btn_audit = ActionCard("shield", "Studio logs", "Admin Panel > Audit Logs: every workstation's logs",
                                    self.open_audit_logs)
        self.maint_grid = grid
        self.maint_cards = [self.btn_report, self.btn_logs, self.btn_templates,
                            self.btn_update, self.btn_audit]
        card_maint.layout().addLayout(grid)
        main_layout.addWidget(card_maint)

        main_layout.addStretch()
        scroll.setWidget(content)
        root_layout.addWidget(scroll)

        # Until the person is known (a widget shown on its own in a test) the
        # studio parts stay hidden; apply_access shows them to the right people.
        self._apply_visibility()
        for field in (self.project_root_input, self.server_root_input, self.brand_logo_input):
            self._show_start(field)
        self._snapshot = self.current_values()
        for signal in (self.restore_paths_cb.toggled, self.dry_run_default_cb.toggled,
                       self.theme_combo.currentTextChanged, self.ui_scale_sb.valueChanged,
                       self.nuke_mode_combo.currentIndexChanged,
                       self.project_root_input.textChanged,
                       self.server_root_input.textChanged, self.brand_logo_input.textChanged,
                       self.db_host_input.textChanged, self.db_port_input.textChanged,
                       self.db_name_input.textChanged, self.db_user_input.textChanged):
            signal.connect(self._mark_dirty)
        self._update_dirty()

    # --------------------------------------------------------------- access
    def showEvent(self, event):
        super().showEvent(event)
        # Roles may have come with the constructor; the tabs a person can
        # open are only known from the window.
        if not self._gated or self.allowed_tabs is None:
            roles = self._roles if self._gated else _roles_of_window(self)
            tabs = getattr(self.window(), "allowed_tabs", None)
            if roles is not None and (not self._gated or tabs is not None):
                self.apply_access(roles, tabs)

    def apply_access(self, roles, allowed_tabs=None):
        """
        Studio cards only for studio_settings (the money card also for
        approve_bid); the Audit Logs link only for manage_system; the Build &
        Ingest preferences and Reload templates only for people who can open
        Build & Ingest, and Project report for the production roles.
        """
        from ...core.domain import access
        self._gated = True
        self._roles = list(roles or [])
        self.can_studio = access.can(self._roles, "studio_settings")
        self.can_system = access.can(self._roles, "manage_system")
        self.can_money = self.can_studio or access.can(self._roles, "approve_bid")
        if allowed_tabs is not None:
            self.allowed_tabs = list(allowed_tabs)
        self._apply_visibility()

    def can_open(self, tab_key) -> bool:
        tabs = self.allowed_tabs
        return tabs is None or "ALL" in tabs or tab_key in tabs

    def _apply_visibility(self):
        from ...core.domain import access
        self.card_money.setVisible(self.can_money)
        self.card_paths.setVisible(self.can_studio)
        ingest = self.can_open("Folder Creator")
        for row in self.ingest_rows:
            row.setVisible(ingest)
        reports = self.allowed_tabs is None or access.can(self._roles or [], "dashboard_view_all")
        # The maintenance cards are laid out again so a hidden one leaves no gap.
        shown = [c for c in self.maint_cards
                 if not ((c is self.btn_update and not self.can_studio)
                         or (c is self.btn_audit and not self.can_system)
                         or (c is self.btn_templates and not ingest)
                         or (c is self.btn_report and not reports))]
        for card in self.maint_cards:
            self.maint_grid.removeWidget(card)
            card.setVisible(card in shown)
        for index, card in enumerate(shown):
            self.maint_grid.addWidget(card, index // 2, index % 2)

    # ---------------------------------------------------------- unsaved work
    def current_values(self) -> dict:
        return {
            "restore_last_paths": self.restore_paths_cb.isChecked(),
            "dry_run_enabled": self.dry_run_default_cb.isChecked(),
            "theme": self.theme_combo.currentText(),
            "ui_scale_override": round(float(self.ui_scale_sb.value()), 2),
            "nuke_mode": self.nuke_mode_combo.currentData() or "nukex",
            "last_project_dir": self.project_root_input.text().strip(),
            "server_root": self.server_root_input.text().strip(),
            "branding_logo_path": self.brand_logo_input.text().strip(),
            "db_host": self.db_host_input.text().strip(),
            "db_port": self.db_port_input.text().strip(),
            "db_name": self.db_name_input.text().strip(),
            "db_user": self.db_user_input.text().strip(),
        }

    def set_values(self, values: dict) -> None:
        self._loading = True
        try:
            self.restore_paths_cb.setChecked(bool(values.get("restore_last_paths", True)))
            self.dry_run_default_cb.setChecked(bool(values.get("dry_run_enabled", False)))
            self.theme_combo.setCurrentText(values.get("theme") or self.theme_combo.currentText())
            self.ui_scale_sb.setValue(snap_ui_scale(values.get("ui_scale_override", 0.0)))
            index = self.nuke_mode_combo.findData(values.get("nuke_mode") or "nukex")
            self.nuke_mode_combo.setCurrentIndex(max(0, index))
            for field, key in ((self.project_root_input, "last_project_dir"),
                               (self.server_root_input, "server_root"),
                               (self.brand_logo_input, "branding_logo_path"),
                               (self.db_host_input, "db_host"), (self.db_port_input, "db_port"),
                               (self.db_name_input, "db_name"), (self.db_user_input, "db_user")):
                if key in values:
                    field.setText(str(values.get(key) or ""))
        finally:
            self._loading = False
        self._update_dirty()

    def _mark_dirty(self, *_):
        if not self._loading:
            self._update_dirty()

    def studio_editors(self):
        return [e for e in (getattr(self, "studio_policy_editor", None),
                            getattr(self, "studio_money_editor", None)) if e is not None]

    def dirty_editors(self):
        return [e for e in self.studio_editors() if e.is_dirty()]

    def _update_dirty(self):
        self._dirty = self.current_values() != self._snapshot or bool(self.dirty_editors())
        self.lbl_dirty.setText("Unsaved changes" if self._dirty else "")
        self.btn_save.setEnabled(self._dirty)
        self.btn_discard.setEnabled(self._dirty)

    def has_unsaved_changes(self) -> bool:
        return bool(self._dirty)

    def unsaved_summary(self) -> str:
        parts = []
        if self.current_values() != self._snapshot:
            parts.append("your preferences")
        if self.studio_policy_editor.is_dirty():
            parts.append("the studio policy")
        if self.studio_money_editor.is_dirty():
            parts.append("the studio currency and rates")
        return "unsaved changes to " + " and ".join(parts) if parts else "unsaved changes"

    def discard_changes(self):
        for editor in self.dirty_editors():
            editor.load()
        self.set_values(self._snapshot)

    def reset_to_defaults(self):
        if QMessageBox.question(
                self, "Reset to defaults",
                "Put your preferences back to Slate's defaults? Nothing is saved until you press Save.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        defaults = self.config_manager.default_global_settings
        self.set_values({
            "restore_last_paths": defaults.get("restore_last_paths", True),
            "dry_run_enabled": defaults.get("dry_run_enabled", False),
            "theme": "Dark",
            "ui_scale_override": 0.0,
            "nuke_mode": "nukex",
        })

    # -------------------------------------------------------------- scale
    def _snap_scale(self):
        value = round(float(self.ui_scale_sb.value()), 2)
        snapped = snap_ui_scale(value)
        if snapped != value:
            self.ui_scale_sb.setValue(snapped)
            self.lbl_scale_applied.setText(f"Raised to {snapped:.2f}, the smallest Slate applies.")

    def _show_applied_scale(self):
        try:
            from ...core.system.adaptation_engine import system_engine
            override = float(getattr(system_engine, "user_scale_override", 0) or 0)
        except Exception:
            override = 0.0
        self.lbl_scale_applied.setText(f"Applied: {override:.2f}" if override > 0 else "Applied: Auto")

    # ---------------------------------------------------------------- save
    def save_all(self) -> bool:
        """Save every preference and path on the page; one message says what was saved."""
        self._snap_scale()
        values = self.current_values()
        if self.can_studio and not self._check_studio_fields(values):
            return False
        prefs_changed = values != self._snapshot
        # The studio policy and money cards save to the studio database first;
        # a refusal there stops the save with the reason and keeps the edits.
        # They save quietly: one toast for the page, not a box per card.
        saved_parts = []
        self._recounted = False
        self._saving = True
        try:
            for editor in self.dirty_editors():
                if not editor.save(quiet=True):
                    self._update_dirty()
                    return False
                saved_parts.append("the studio policy" if editor is self.studio_policy_editor
                                   else "the studio currency, rates and hours")
        finally:
            self._saving = False
        try:
            self.global_settings["restore_last_paths"] = values["restore_last_paths"]
            self.global_settings["dry_run_enabled"] = values["dry_run_enabled"]
            self.global_settings["ui_scale_override"] = snap_ui_scale(values["ui_scale_override"])
            self.global_settings["nuke_mode"] = values["nuke_mode"]
            if self.can_studio and values["branding_logo_path"] != self._snapshot.get("branding_logo_path"):
                # One logo for the studio, not one per workstation.
                from slate.core.infra.studio_settings import set_setting
                from slate.gui.tabs.studio_settings_cards import _username_of
                if not set_setting("branding_logo_path", values["branding_logo_path"],
                                   by=_username_of(self) or "Settings"):
                    raise RuntimeError("the studio logo could not be saved for the studio")
                self.global_settings["branding_logo_path"] = values["branding_logo_path"]
            # A cleared path is saved as cleared - it used to be skipped, so
            # a wrong path could never be removed.
            project = values["last_project_dir"]
            if project:
                self.config_manager.settings["last_project_dir"] = project
            else:
                self.config_manager.settings.pop("last_project_dir", None)
            # Build & Ingest opens with "last_project_directory"; this field's
            # key was copied there only while that one was empty, so once
            # Build & Ingest had been used a new Project root changed nothing.
            if project and project != self._snapshot.get("last_project_dir"):
                self.config_manager.settings["last_project_directory"] = project
            self.config_manager.save_settings(self.config_manager.settings)
            saved = self.config_manager.update_global_settings(self.global_settings)
            if not saved:
                raise RuntimeError("the settings file could not be written")
        except Exception as e:
            QMessageBox.critical(self, "Settings not saved", f"Your settings were not saved:\n{e}")
            return False

        if prefs_changed or not saved_parts:
            saved_parts.append("your preferences")
        notes = ["Saved " + " and ".join(saved_parts) + "."]
        # Only what really waits for a restart is named (it listed theme,
        # server and database whatever had changed).
        later = []
        if values["theme"] != self._snapshot.get("theme") and ThemeManager.set_theme(values["theme"]):
            later.append("the theme")
        if self.can_studio and self._save_studio_fields(values):
            later.append("the server and database changes")
        if later:
            notes.append(" and ".join(later).capitalize() + " finish the next time Slate starts.")
        if self._recounted:
            notes.append("Attendance has been recounted.")
        self._snapshot = self.current_values()
        self._update_dirty()
        self.global_settings_updated.emit(dict(self.global_settings))
        self._show_applied_scale()
        self._toast(" ".join(notes))
        return True

    def _check_studio_fields(self, values) -> bool:
        logo = values["branding_logo_path"]
        if logo and not Path(logo).exists():
            QMessageBox.warning(self, "Studio logo", "The studio logo image was not found. Choose another file.")
            return False
        if values["db_host"] and not valid_db_host(values["db_host"]):
            QMessageBox.warning(self, "Studio database",
                                f"'{values['db_host']}' is not a host name or an IP address.")
            return False
        port = values["db_port"]
        if port and not (port.isdigit() and 1 <= int(port) <= 65535):
            QMessageBox.warning(self, "Studio database", "The port must be a number from 1 to 65535.")
            return False
        root = values["server_root"]
        if not root and self._snapshot.get("server_root"):
            # Clearing it was 'saved' while the old root was kept.
            QMessageBox.warning(self, "Server root",
                                "Slate needs the studio folder. Choose the server root rather than "
                                "leaving it empty.")
            self.server_root_input.setFocus()
            return False
        if root and root != self._snapshot.get("server_root") and not Path(root).is_dir():
            # A warning, not a refusal: a share can be down for a moment.
            if QMessageBox.question(
                    self, "Server root",
                    f"{root} cannot be reached from this workstation right now. Save it anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
                return False
        return True

    def _save_studio_fields(self, values) -> bool:
        """Server root and database details. True when they changed (restart needed)."""
        before = self._snapshot
        changed = any(values[k] != before.get(k) for k in ("server_root", "db_host", "db_port", "db_name", "db_user"))
        if not changed:
            return False
        db_changed = any(values[k] != before.get(k) for k in ("db_host", "db_port", "db_name", "db_user"))
        if db_changed:
            from slate.core.infra.local_secrets import write_local_config
            write_local_config({
                "db_host": values["db_host"] or None,
                "db_port": int(values["db_port"]) if values["db_port"] else None,
                "db_name": values["db_name"] or None,
                "db_user": values["db_user"] or None,
            })
        # SERVER_ROOT stays a per-machine setting: two workstations can map
        # the same share to different drive letters.
        if values["server_root"] and values["server_root"] != before.get("server_root"):
            GlobalConfig.set("SERVER_ROOT", values["server_root"])
        self.refresh_runtime_status()
        return True

    # -------------------------------------------------- database connection
    def test_connection(self):
        """Try the database details on the form, off the UI thread, with the stored password."""
        host = self.db_host_input.text().strip()
        port = self.db_port_input.text().strip()
        if not valid_db_host(host) or not port.isdigit():
            self.lbl_test.setText("Enter a valid host and port first.")
            return None
        name = self.db_name_input.text().strip()
        user = self.db_user_input.text().strip()
        from slate.core.infra.local_secrets import find_db_password
        password = find_db_password() or None
        self.btn_test_db.setEnabled(False)
        self.lbl_test.setStyleSheet("")
        self.lbl_test.setText("Connecting…")

        def attempt():
            import psycopg2
            conn = psycopg2.connect(host=host, port=int(port), dbname=name or None, user=user or None,
                                    password=password, connect_timeout=3)
            conn.close()
            return True

        def finished(_result, error):
            self.btn_test_db.setEnabled(True)
            if error:
                self.lbl_test.setStyleSheet(f"color: {Gate.BAD};")
                self.lbl_test.setText(f"Could not connect: {error.strip().splitlines()[0]}")
            else:
                self.lbl_test.setStyleSheet(f"color: {Gate.OK};")
                self.lbl_test.setText(f"Connected to {name or 'the database'} on {host}:{port}.")
        return self._run_job(attempt, finished)

    # ------------------------------------------------------------- runtime
    def refresh_runtime_status(self):
        """The Runtime status card, in plain words."""
        try:
            status = database_manager.get_runtime_status() or {}
        except Exception as exc:
            logging.debug("Runtime status refresh failed: %s", exc)
            status = {}
        server_root_value = str(GlobalConfig.get("SERVER_ROOT", "") or "").strip()
        server_ok = bool(server_root_value and Path(server_root_value).is_dir())
        # The long path elided in its middle, whole in the tooltip (it wrapped awkwardly).
        shown_root = QFontMetrics(self.runtime_server_label.font()).elidedText(
            server_root_value, Qt.TextElideMode.ElideMiddle, 520)
        lines = runtime_lines(status, shown_root, server_ok, bool(GlobalConfig.exr_loading_enabled()))
        self.runtime_db_label.setText(lines["database"])
        self.runtime_server_label.setText(lines["folder"])
        self.runtime_server_label.setToolTip(server_root_value)
        self.runtime_exr_label.setText(lines["exr"])
        self.runtime_sync_label.setText(lines["sync"])

    def create_divider(self):
        line = QFrame(); line.setFrameShape(QFrame.HLine); line.setFrameShadow(QFrame.Sunken); line.setStyleSheet(f"background: {C.BG_ELEVATED}; margin-top: {S.XS}px; margin-bottom: {S.XS}px;")
        return line

    def _browse_directory(self, target_input: QLineEdit, title: str):
        start_dir = target_input.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, title, start_dir)
        if selected:
            target_input.setText(selected)
            self._show_start(target_input)

    def _browse_file(self, target_input: QLineEdit, title: str, file_filter: str):
        start_file = target_input.text().strip() or str(Path.home())
        selected, _ = QFileDialog.getOpenFileName(self, title, start_file, file_filter)
        if selected:
            target_input.setText(selected)
            self._show_start(target_input)

    # -------------------------------------------------------- studio policy
    def _on_policy_saved(self):
        """Recount Attendance at once if it is open, instead of asking people to re-open it."""
        self.studio_policy_saved.emit()
        window = self.window()
        getter = getattr(window, "_get_tab_instance", None)
        tab = getter("Attendance", create=False) if callable(getter) else None
        for name in ("refresh", "load_data", "refresh_data"):
            method = getattr(tab, name, None) if tab is not None else None
            if callable(method):
                try:
                    method()
                    # Part of the page's one message when saved from the page.
                    self._recounted = True
                    if not self._saving:
                        self._toast("Studio policy saved. Attendance has been recounted.")
                except Exception as exc:
                    logging.warning("Attendance refresh after the policy save failed: %s", exc)
                break

    # ----------------------------------------------------------- maintenance
    def request_template_refresh(self):
        self.templates_refresh_requested.emit()
        self._toast("Templates reloaded in the open tabs.")

    def open_audit_logs(self):
        """Admin Panel > Audit Logs, for the people who can open it."""
        window = self.window()
        getter = getattr(window, "_get_tab_instance", None)
        tab = getter("Admin Panel", create=True) if callable(getter) else None
        if tab is None:
            # Slate VFX has no Admin Panel: the card used to do nothing at all.
            self._toast("The studio logs are in the Admin Panel, in Slate Operations. "
                        "Open them there.", "warning")
            return False
        try:
            window.tab_coordinator.select_tab(tab)
        except Exception as exc:
            logging.debug("Could not switch to the Admin Panel: %s", exc)
        shower = getattr(tab, "show_page", None)
        return bool(callable(shower) and shower("Audit Logs"))

    def check_for_updates(self):
        """Manual update check."""
        if not self.can_studio:
            return
        if self.sidecar_engine and hasattr(self.sidecar_engine, 'temp_updater'):
            self._apply_staged_update()
            return

        self.btn_update.update_content("refresh", "Checking…", "Looking for a newer Slate")
        self._cleanup_update_checker()
        self.update_checker = UpdateChecker(self, manual_mode=True, target="client")
        self.update_checker.update_available.connect(self.on_update_found)
        self.update_checker.update_not_found.connect(self.on_no_update)
        self.update_checker.finished.connect(self._cleanup_update_checker)
        self.update_checker.start()

    def _reset_update_card(self):
        self.btn_update.update_content("download", "Check for updates", "See whether a newer Slate is available")

    def on_update_found(self, manifest):
        self._reset_update_card()
        dlg = UpdateAvailableDialog(manifest, self)
        if dlg.exec():
            self._stage_update(manifest)

    def _stage_update(self, manifest):
        """Download and verify the update on a worker; the window stays usable."""
        from ...core.updater.sidecar_engine import SidecarEngine

        self.btn_update.setEnabled(False)
        self.btn_update.update_content("download", "Downloading…", "Getting the update ready")
        self.sidecar_engine = SidecarEngine(manifest)
        engine = self.sidecar_engine

        def finished(success, error):
            self.btn_update.setEnabled(True)
            if success and not error:
                self.btn_update.update_content("check", "Restart to apply", "Update ready. Click to restart.")
                self.btn_update.setStyleSheet(f"""
                    ActionCard {{ background-color: {Gate.OK_SURFACE}; border: 1px solid {Gate.OK}; border-radius: 8px; text-align: left; padding: 15px; }}
                    ActionCard:hover {{ background-color: {Gate.OK}; border: 1px solid {Gate.OK}; }}
                """)
                QMessageBox.information(self, "Check for updates",
                                        "The update is downloaded and verified. Click 'Restart to apply' when you are ready.")
            else:
                self.sidecar_engine = None
                self._reset_update_card()
                QMessageBox.warning(self, "Check for updates",
                                    "The update could not be prepared" + (f":\n{error}" if error else "."))
        return self._run_job(engine.stage_update, finished)

    def _apply_staged_update(self):
        if not self.sidecar_engine:
            return

        reply = QMessageBox.question(self, "Apply update", "Slate will restart to apply the update. Continue?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                database_manager.force_shutdown()
            except Exception:
                pass
            self.sidecar_engine.apply_update()

            app = QApplication.instance()
            top = self.window()
            if top and hasattr(top, "close"):
                top.close()
            elif app:
                app.quit()

    def on_no_update(self, current_ver):
        self._reset_update_card()
        reason = ""
        if self.update_checker:
            reason = str(getattr(self.update_checker, "last_result_reason", "") or "")

        if reason == "missing_latest_pointer" or reason == "manifest_missing":
            QMessageBox.information(
                self,
                "Check for updates",
                f"No update information was found at:\n{GlobalConfig.server_root() / 'Updates' / 'releases' / 'manifest_client.json'}\n\n"
                f"This is Slate {current_ver}.",
            )
            return

        if reason == "invalid_manifest":
            QMessageBox.information(
                self,
                "Check for updates",
                "The update information is incomplete (Updates/releases/manifest_client.json). "
                "Ask IT to publish the release again.",
            )
            return

        QMessageBox.information(self, "Check for updates", f"Slate {current_ver} is the latest version.")

    @staticmethod
    def project_choices(projects):
        """'Name - 12 Sep 2026' per project (it showed the raw timestamp)."""
        from ...core.domain.dates import format_date
        choices = []
        for p in projects:
            when = format_date(p.get("created_at"))
            choices.append((f"{p['name']} - {when}" if when else str(p['name']), p))
        return choices

    def generate_project_summary_report(self):
        try:
            projects = database_manager.get_all_projects_summary()
            if not projects:
                QMessageBox.information(self, "Project report", "There is no project history yet.")
                return

            choices = self.project_choices(projects)
            labels = [label for label, _p in choices]
            selected_item, ok = QInputDialog.getItem(self, "Project report", "Report on:", labels, 0, False)
            if not ok or not selected_item: return

            project = dict(choices)[selected_item]
            default_name = f"Report_{slug(project['name'])}_{datetime.now().strftime('%Y%m%d')}.pdf"
            file_path, _ = QFileDialog.getSaveFileName(self, "Save report", str(Path.home() / default_name), "PDF Files (*.pdf)")

            if file_path:
                self._cleanup_report_worker()
                self.report_worker = ReportWorker(Path(file_path), project_id=project['id'])
                self.report_worker.finished_signal.connect(self._on_report_worker_finished)
                self.report_worker.finished.connect(self._on_report_worker_done)
                self.report_worker.finished.connect(self.report_worker.deleteLater)
                self.report_worker.start()
        except Exception as e:
            QMessageBox.critical(self, "Project report", f"The report could not be made:\n{e}")

    def show_error_report(self):
        log_dir = error_handler.log_directory
        if log_dir.exists(): QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir)))
        else: QMessageBox.information(self, "Open my log folder", "Slate has not written a log on this computer yet.")

    def closeEvent(self, event):
        self._cleanup_report_worker()
        self._cleanup_update_checker()
        for job in list(self._jobs):
            job.wait(2000)
        super().closeEvent(event)
