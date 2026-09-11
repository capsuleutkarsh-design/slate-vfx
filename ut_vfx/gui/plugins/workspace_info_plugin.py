"""
Built-in plugin tab showing runtime context and quick navigation actions.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from .plugin_interface import UTVFXPlugin


class WorkspaceInfoPlugin(UTVFXPlugin):
    """Simple built-in plugin so plugin infrastructure has a shipped example."""

    @property
    def plugin_name(self) -> str:
        return "Workspace Info"

    @property
    def plugin_icon(self) -> str:
        return "P"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._context = {}
        self._meta_label: QLabel | None = None
        self._status_label: QLabel | None = None
        self._build_ui()

    def initialize(self, context: dict):
        self._context = dict(context or {})
        self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Plugin Runtime Status")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title)

        card = QFrame(self)
        card.setStyleSheet(
            "QFrame {"
            "background-color: #1D1D22;"
            "border: 1px solid #2C2C34;"
            "border-radius: 8px;"
            "}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(8)

        self._meta_label = QLabel("")
        self._meta_label.setWordWrap(True)
        card_layout.addWidget(self._meta_label)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #B4B1AA;")
        card_layout.addWidget(self._status_label)

        layout.addWidget(card)

        refresh_btn = QPushButton("Refresh Context")
        refresh_btn.clicked.connect(self._refresh)
        layout.addWidget(refresh_btn)

        settings_btn = QPushButton("Open Settings")
        settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(settings_btn)

        layout.addStretch()

    def _refresh(self):
        user_data = self._context.get("user_data", {}) or {}
        username = user_data.get("display_name") or user_data.get("username") or "Unknown"
        user_role = self._context.get("user_role") or user_data.get("role") or "Unknown"
        config_manager = self._context.get("config_manager")

        config_root = ""
        if config_manager is not None:
            try:
                config_root = str(Path(config_manager.config_path).parent)
            except Exception:
                config_root = "Unavailable"

        if self._meta_label is not None:
            self._meta_label.setText(
                "Loaded plugin context:\n"
                f"- User: {username}\n"
                f"- Role: {user_role}\n"
                f"- Config root: {config_root or 'Unavailable'}"
            )

        if self._status_label is not None:
            self._status_label.setText("Plugin is active and connected to host context.")

    def _open_settings(self):
        main_window = self._context.get("main_window")
        if main_window and hasattr(main_window, "show_settings_tab"):
            main_window.show_settings_tab()
            if self._status_label is not None:
                self._status_label.setText("Opened Settings tab from plugin.")
        elif self._status_label is not None:
            self._status_label.setText("Settings tab is not available in this runtime context.")

