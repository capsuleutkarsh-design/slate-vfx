"""
Workspace Info: the facts IT asks for when something is wrong - which version,
installed where, which database, which shared folder - with a button that
copies them for a ticket. Shown to everybody; no passwords or other
credentials are ever on it.

It is also the shipped example of a plugin tab (see plugin_interface.py).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .plugin_interface import SlatePlugin
from slate.core.infra.gate import Gate


class WorkspaceInfoPlugin(SlatePlugin):
    """Version, install, database and shared folder for this workstation."""

    plugin_description = "Version, database and shared folder - for IT tickets"

    @property
    def plugin_name(self) -> str:
        return "Workspace Info"

    @property
    def plugin_icon(self) -> str:
        return "info"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._context = {}
        self._rows = {}
        self._status_label: QLabel | None = None
        self._build_ui()

    def initialize(self, context: dict):
        self._context = dict(context or {})
        self._refresh()

    # ------------------------------------------------------------------ build
    FIELDS = (
        ("version", "Slate version"),
        ("installed", "Installed in"),
        ("program_date", "Program date"),
        ("user", "Signed in as"),
        ("role", "Role"),
        ("database", "Database"),
        ("database_server", "Database server"),
        ("shared_folder", "Shared folder"),
        ("shared_reachable", "Shared folder reachable"),
        ("config", "Settings folder"),
        ("plugins", "Plugins"),
    )

    def _build_ui(self):
        from slate.gui.core.controls import form_layout, make_button, page_title

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(page_title(
            "Workspace info",
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
        from slate import __version__
        from slate.core.infra.global_config import GlobalConfig

        user_data = self._context.get("user_data", {}) or {}
        main_window = self._context.get("main_window")
        name = user_data.get("display_name") or user_data.get("username") or "Unknown"
        username = user_data.get("user_id") or user_data.get("username") or ""

        if getattr(sys, "frozen", False):
            program = Path(sys.executable)
            installed = str(program.parent)
        else:
            program = Path(__file__).resolve().parents[2] / "__init__.py"
            installed = str(program.parents[1])
        try:
            from slate.core.domain.dates import format_date
            program_date = format_date(datetime.fromtimestamp(program.stat().st_mtime))
        except Exception:
            program_date = "Unknown"

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

        root = str(GlobalConfig.get("SERVER_ROOT", "") or "").strip()
        reachable = bool(root) and os.path.isdir(root)

        try:
            config = GlobalConfig._instance or GlobalConfig()
            config_dir = str(config.local_app_data)
        except Exception:
            config_dir = str(Path(os.getenv("LOCALAPPDATA", "")) / "Slate")

        plugins = list(getattr(main_window, "loaded_plugins", []) or []) or [self.plugin_name]

        return {
            "version": str(__version__),
            "installed": installed,
            "program_date": str(program_date),
            "user": f"{name} ({username})" if username and username != name else str(name),
            "role": str(self._context.get("user_role") or user_data.get("role") or "Unknown"),
            "database": database,
            "database_server": server or "Not set",
            "shared_folder": root or "Not set",
            "shared_reachable": "Yes" if reachable else "No",
            "config": config_dir or "Unknown",
            "plugins": ", ".join(plugins),
        }

    def details_text(self) -> str:
        facts = self.details()
        return "\n".join(f"{label}: {facts.get(key, '')}" for key, label in self.FIELDS)

    # ---------------------------------------------------------------- actions
    def _refresh(self):
        facts = self.details()
        for key, widget in self._rows.items():
            widget.setText(facts.get(key, ""))
        if self._status_label is not None:
            self._status_label.setText("")

    def copy_details(self):
        QApplication.clipboard().setText(self.details_text())
        if self._status_label is not None:
            self._status_label.setText("Copied. Paste it into your IT ticket.")

    def _open_settings(self):
        main_window = self._context.get("main_window")
        if main_window and hasattr(main_window, "show_settings_tab"):
            main_window.show_settings_tab()
        elif self._status_label is not None:
            self._status_label.setText("Settings is not available here.")
