"""
Dashboard Tab - Integration wrapper for VFX Dashboard

This module provides a QWidget wrapper that embeds the VFX Dashboard MainWindow
as a tab within the main UT_VFX application. It maintains API compatibility
with the existing tab interface while providing error handling and isolation.
"""

import importlib.util
import logging
from typing import List

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from ...core.infra.app_context import AppContext
from ...core.infra.design_tokens import ColorTokens as C
from ...core.infra.design_tokens import RadiusTokens as R
from ...core.infra.design_tokens import SpacingTokens as S
from ...core.infra.design_tokens import TypographyTokens as T

logger = logging.getLogger(__name__)

_DASHBOARD_REQUIRED_IMPORTS = (
    "openpyxl",
)

_DASHBOARD_OPTIONAL_IMPORTS = (
    "PIL",
    "plotly",
    "reportlab",
)


class DashboardTab(QWidget):
    """
    Wrapper widget that embeds the VFX Dashboard as a tab.

    This class serves as an adapter between the main application's tab interface
    and the dashboard implementation. It handles user context passing,
    error isolation, and provides a consistent API.
    """

    status_changed = Signal(str)

    def __init__(self, user_data=None, parent=None, app_context=None):
        super().__init__(parent)
        self.app_context = app_context or AppContext()
        self.user_data = user_data or {}
        self.dashboard = None
        self._is_closing = False
        self._is_cleaned = False
        self.init_ui()

    def init_ui(self):
        """Initialize and embed the dashboard widget."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        missing = self._find_missing_dashboard_dependencies()
        if missing:
            missing_text = ", ".join(sorted(missing))
            logger.error("Dashboard prerequisites missing: %s", missing_text)
            self._show_error(
                "Missing dashboard dependencies:\n"
                f"{missing_text}\n\n"
                "Install these packages in the active environment and retry."
            )
            return
        self._log_optional_dashboard_dependencies()

        try:
            from .vfx_dashboard_pro.ui.dashboard_widget import DashboardWidget

            dash_user = {**self.user_data, "inherit_app_theme": True}
            self.dashboard = DashboardWidget(
                user_data=dash_user,
                parent=self,
                user_manager=self.app_context.user_manager(),
                app_context=self.app_context,
            )
            layout.addWidget(self.dashboard)
            logger.info("Dashboard tab initialized with VFX Dashboard Pro")
        except ImportError as exc:
            missing_module = getattr(exc, "name", "") or str(exc)
            logger.error("Dashboard import failed due to missing module: %s", missing_module, exc_info=True)
            self._show_error(
                "Failed to initialize dashboard because a module is missing:\n"
                f"{missing_module}\n\n"
                "Install the missing package and retry."
            )
        except Exception as exc:
            logger.error("Failed to initialize DashboardWidget in DashboardTab: %s", exc, exc_info=True)
            self._show_error(f"Failed to initialize dashboard:\n{exc}")

    @staticmethod
    def _find_missing_dashboard_dependencies() -> List[str]:
        missing: List[str] = []
        for module_name in _DASHBOARD_REQUIRED_IMPORTS:
            if importlib.util.find_spec(module_name) is None:
                missing.append(module_name)
        return missing

    @staticmethod
    def _log_optional_dashboard_dependencies() -> None:
        missing_optional: List[str] = []
        for module_name in _DASHBOARD_OPTIONAL_IMPORTS:
            if importlib.util.find_spec(module_name) is None:
                missing_optional.append(module_name)
        if missing_optional:
            logger.warning(
                "Dashboard optional dependencies missing: %s. "
                "Core dashboard remains available; related optional features may be limited.",
                ", ".join(sorted(missing_optional)),
            )

    def _show_error(self, message: str):
        """Display a user-friendly error message when dashboard fails to load."""
        error_frame = QFrame()
        error_frame.setStyleSheet(
            f"QFrame {{ "
            f"background-color: #3A1F1E; "
            f"border: 2px solid #D9635F; "
            f"border-radius: {S.MD}px; "
            f"padding: {S.XL}px; "
            f"}}"
        )

        error_layout = QVBoxLayout(error_frame)

        title = QLabel("Dashboard Unavailable")
        title.setStyleSheet(
            f"color: #D9635F; "
            f"font-size: {T.SIZE_LG}px; "
            f"font-weight: {T.WEIGHT_STYLE_BOLD}; "
            f"background: transparent; "
            f"border: none;"
        )

        msg = QLabel(message)
        msg.setStyleSheet(
            f"color: {C.TEXT_GRAY_LIGHTER}; "
            f"background: transparent; "
            f"border: none;"
        )
        msg.setWordWrap(True)

        retry_btn = QPushButton("Retry")
        retry_btn.clicked.connect(self.retry_load)
        retry_btn.setStyleSheet(
            f"QPushButton {{ "
            f"background-color: {C.BG_ELEVATED}; "
            f"color: {C.TEXT_WHITE}; "
            f"border: 1px solid {C.TEXT_DISABLED}; "
            f"padding: {S.SM}px {S.LG}px; "
            f"border-radius: {R.SM}px; "
            f"}}"
            f"QPushButton:hover {{ "
            f"background-color: {C.BG_HOVER}; "
            f"}}"
        )

        error_layout.addWidget(title)
        error_layout.addWidget(msg)
        error_layout.addWidget(retry_btn)
        error_layout.addStretch()

        layout = self.main_layout()
        if layout is not None:
            layout.addWidget(error_frame)

    def retry_load(self):
        """Retry loading the dashboard (clears error and reinitializes)."""
        if self._is_closing:
            return

        layout = self.main_layout()
        if layout is None:
            return

        from ..core.layout_utils import clear_layout
        clear_layout(layout)

        self.init_ui()

    def refresh(self):
        """Refresh dashboard data if the embedded widget supports it."""
        if self._is_closing:
            return
        if self.dashboard and hasattr(self.dashboard, "refresh_data"):
            try:
                self.dashboard.refresh_data()
                logger.debug("Dashboard data refreshed")
            except Exception as exc:
                logger.error("Failed to refresh dashboard: %s", exc)

    def get_dashboard_instance(self):
        """Get the underlying dashboard instance."""
        return self.dashboard

    def closeEvent(self, event):
        """Handle widget close event."""
        self.cleanup_resources()
        super().closeEvent(event)

    def cleanup_resources(self):
        """Deterministically stop embedded dashboard resources."""
        if self._is_cleaned:
            return

        self._is_closing = True
        if self.dashboard:
            try:
                if hasattr(self.dashboard, "cleanup_resources"):
                    self.dashboard.cleanup_resources()
                self.dashboard.close()
            except Exception as exc:
                logger.error("Error closing dashboard: %s", exc)
            finally:
                self.dashboard = None

        self._is_cleaned = True
