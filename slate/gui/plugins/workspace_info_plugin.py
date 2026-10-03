"""
Workspace Info: the facts IT asks for when something is wrong - which version,
installed where, which database, which shared folder - with a button that
copies them for a ticket. Shown to everybody; no passwords or other
credentials are ever on it.

It is also the shipped example of a plugin tab (see plugin_interface.py).
Diagnostics (Ctrl+Shift+D) shows the same facts: workstation_facts below.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .plugin_interface import SlatePlugin
from slate.core.infra.gate import Gate


# Every fact, in order, with its label - Workspace Info and Diagnostics both.
FIELDS = (
    ("version", "Slate version"),
    ("installed", "Installed in"),
    ("user", "Signed in as"),
    ("role", "Role"),
    ("database", "Database"),
    ("database_server", "Database server"),
    ("shared_folder", "Shared folder"),
    ("shared_reachable", "Shared folder reachable"),
    ("exr", "EXR previews"),
    ("config", "Settings folder"),
    ("plugins", "Plugins"),
)
PAGE_NAME = "Workspace Info"


def shared_folder() -> str:
    from slate.core.infra.global_config import GlobalConfig
    return str(GlobalConfig.get("SERVER_ROOT", "") or "").strip()


def check_shared_folder(on_result, owner=None):
    """Whether the shared folder answers, worked out on a worker: an offline
    share made the window freeze for the network timeout."""
    from slate.core.infra.db_worker import run_db_async
    root = shared_folder()
    return run_db_async(lambda: bool(root) and os.path.isdir(root),
                        on_success=on_result, on_error=lambda _text: on_result(False), owner=owner)


def workstation_facts(context=None, reachable=None) -> dict:
    """
    Every fact IT asks for, as text. Never a password. reachable: whether the
    shared folder answered (None while that is still being checked).
    """
    from slate import __version__
    from slate.core.infra.global_config import GlobalConfig
    from slate.gui.login_dialog import version_text

    context = context or {}
    user_data = context.get("user_data", {}) or {}
    main_window = context.get("main_window")
    name = user_data.get("display_name") or user_data.get("username") or "Unknown"
    username = user_data.get("user_id") or user_data.get("username") or ""
    roles = user_data.get("roles") or user_data.get("role") or []
    roles = [roles] if isinstance(roles, str) else [str(r) for r in roles]
    title = getattr(main_window, "suite_title", "")

    if getattr(sys, "frozen", False):
        installed = str(Path(sys.executable).parent)
    else:
        installed = str(Path(__file__).resolve().parents[3])

    try:
        from slate.core.infra.database_manager import database_manager
        status = database_manager.get_runtime_status() or {}
    except Exception:
        status = {}
    mode = str(status.get("active_mode", "")).lower()
    if status.get("fallback_used"):
        database = "This machine's local copy (LOCAL MODE) - the studio database could not be reached"
    elif mode == "postgres":
        database = "Studio database (PostgreSQL)"
    else:
        database = mode.title() or "Unknown"
    host = str(GlobalConfig.get("db_host", "") or "").strip()
    port = str(GlobalConfig.get("db_port", "") or "").strip()
    db_name = str(GlobalConfig.get("db_name", "") or "").strip()
    server = ":".join(p for p in (host, port) if p)
    if db_name:
        server = f"{server} / {db_name}" if server else db_name

    try:
        config = GlobalConfig._instance or GlobalConfig()
        config_dir = str(config.local_app_data)
    except Exception:
        config_dir = str(Path(os.getenv("LOCALAPPDATA", "")) / "Slate")

    # Other plugins: this page listed itself, on every machine.
    plugins = [p for p in (getattr(main_window, "loaded_plugins", []) or []) if p != PAGE_NAME]
    root = shared_folder()
    version = version_text(__version__)

    return {
        "version": f"{version} ({title})" if title else version,
        "installed": installed,
        "user": f"{name} ({username})" if username and username != name else str(name),
        "role": ", ".join(roles) or "Unknown",
        "database": database,
        "database_server": server or "Not set",
        "shared_folder": root or "Not set",
        "shared_reachable": ("Checking\u2026" if reachable is None else "Yes" if reachable else "No")
                            if root else "No",
        "exr": "On" if GlobalConfig.exr_loading_enabled() else "Off",
        "config": config_dir or "Unknown",
        "plugins": ", ".join(plugins) or "None",
    }


def facts_text(facts: dict) -> str:
    return "\n".join(f"{label}: {facts.get(key, '')}" for key, label in FIELDS)


class WorkspaceInfoPlugin(SlatePlugin):
    """Version, install, database and shared folder for this workstation."""

    plugin_description = "Version, database and shared folder - for IT tickets"

    @property
    def plugin_name(self) -> str:
        return PAGE_NAME

    @property
    def plugin_icon(self) -> str:
        return "info"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._context = {}
        self._rows = {}
        self._reachable = None
        self._status_label: QLabel | None = None
        self._build_ui()

    def initialize(self, context: dict):
        self._context = dict(context or {})
        self._refresh()

    # ------------------------------------------------------------------ build
    FIELDS = FIELDS

    def _build_ui(self):
        from slate.gui.core.controls import form_layout, make_button, page_title

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(page_title(
            PAGE_NAME,
            "What IT needs to know about this workstation. Copy it into a ticket."))

        card = QFrame(self)
        card.setObjectName("InfoCard")
        # Scoped to the card itself: a bare QFrame rule also boxed every label
        # inside it (a QLabel is a QFrame).
        card.setStyleSheet(
            f"QFrame#InfoCard {{ background-color: {Gate.RAISED}; "
            f"border: 1px solid {Gate.LINE}; border-radius: 8px; }}")
        self.form = form_layout(card)
        self.form.setContentsMargins(16, 14, 16, 14)
        # Values take the card's width: paths wrapped at ~950 px with a third
        # of the card empty.
        from PySide6.QtWidgets import QFormLayout
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for key, label in self.FIELDS:
            value = QLabel("")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            value.setStyleSheet("background: transparent;")
            name = QLabel(label)
            name.setStyleSheet(f"color: {Gate.TEXT_2}; background: transparent;")
            self.form.addRow(name, value)
            self._rows[key] = value
        layout.addWidget(card)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(f"color: {Gate.TEXT_2};")
        self._status_label.setVisible(False)       # no gap above the buttons while empty
        layout.addWidget(self._status_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.copy_button = make_button("Copy details", "primary", icon="copy",
                                       on_click=self.copy_details,
                                       tooltip="Copy all of this, to paste into an IT ticket")
        self.refresh_button = make_button("Refresh", "secondary", icon="refresh", on_click=self._refresh)
        self.settings_button = make_button("Open Settings", "secondary", on_click=self._open_settings)
        for button in (self.settings_button, self.refresh_button, self.copy_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        layout.addStretch()

    # ------------------------------------------------------------------ facts
    def details(self) -> dict:
        """Every fact on the page, as text. Never a password."""
        return workstation_facts(self._context, self._reachable)

    def details_text(self) -> str:
        return facts_text(self.details())

    # ---------------------------------------------------------------- actions
    def _refresh(self):
        self._reachable = None
        self._show_facts()
        self._say("")
        check_shared_folder(self._on_reachable, owner=self)

    def _on_reachable(self, ok):
        self._reachable = bool(ok)
        self._show_facts()

    def _show_facts(self):
        facts = self.details()
        for key, widget in self._rows.items():
            widget.setText(facts.get(key, ""))

    def _say(self, text):
        if self._status_label is not None:
            self._status_label.setText(text)
            self._status_label.setVisible(bool(text))

    def copy_details(self):
        QApplication.clipboard().setText(self.details_text())
        self._say("Copied. Paste it into your IT ticket.")

    def _open_settings(self):
        main_window = self._context.get("main_window")
        if main_window and hasattr(main_window, "show_settings_tab"):
            main_window.show_settings_tab()
        else:
            self._say("Settings is not available here.")
