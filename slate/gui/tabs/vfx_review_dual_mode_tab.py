"""
Timeline Viewer - the lineup: watched here, in RV, and as EDLs for editorial.

The shots come from the dashboard, so the timeline is always built from what
the production is actually tracking: plates laid out reel by reel, with each
department's render on its own layer beneath the plate it came from. The tab
plays the lineup itself (LineupPreview), opens it in RV and writes EDLs;
reviewing a single shot happens in OpenRV from the dashboard.

On first show it loads the stored project by itself instead of opening on an
empty list with enabled buttons (MED-086).
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .shot_review.lineup_editor_mode import LineupEditorMode
from slate.core.domain.lineup import project_fps
from ..core.controls import make_button
from slate.core.infra.gate import Gate

logger = logging.getLogger(__name__)


class VFXReviewDualModeTab(QWidget):
    """The lineup, built from the dashboard, played here, in RV and as EDLs."""

    def __init__(self, config_manager, user_data=None):
        super().__init__()
        self.config = config_manager
        self.user_data = user_data or {}
        self._is_closing = False
        self._is_cleaned = False
        self._loaded_once = False
        self.lineup_editor = LineupEditorMode()
        self.setup_ui()
        logger.info("Timeline Viewer initialized")

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.create_mode_toggle())
        layout.addWidget(self.lineup_editor, 1)

    def create_mode_toggle(self):
        """The header: title, what it is, Refresh."""
        header = QWidget()
        header.setObjectName("TimelineHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Scoped by name, so the line under the header does not land under
        # every label inside it (MED-099).
        header.setStyleSheet(f"QWidget#TimelineHeader {{ background: {Gate.PANEL}; "
                             f"border-bottom: 1px solid {Gate.LINE}; }}")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 10, 14, 10)
        title = QLabel("Timeline")
        title.setStyleSheet(f"color: {Gate.TEXT}; font-size: 16px; font-weight: 600;")
        layout.addWidget(title)
        subtitle = QLabel("The lineup from the dashboard: watch it here or in RV, export an EDL")
        subtitle.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        layout.addWidget(subtitle)
        layout.addStretch()
        self.refresh_btn = make_button("Refresh from Dashboard", "secondary", icon="refresh",
                                       tooltip="Re-read the shots the dashboard is tracking "
                                               "and rebuild the list")
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        layout.addWidget(self.refresh_btn)
        return header

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded_once and not self.lineup_editor.shots:
            self._loaded_once = True
            self._on_refresh_clicked(quiet=True)

    def refresh(self):
        """F5 / Ctrl+R / "Refresh this screen": what the Refresh button does (it had no refresh)."""
        self._on_refresh_clicked()

    def _on_refresh_clicked(self, quiet=False):
        """Busy while it reads, then say what happened."""
        from PySide6.QtWidgets import QApplication
        from ..components.feedback import report
        button = getattr(self, "refresh_btn", None)
        if button is not None:
            button.setEnabled(False)
            button.setText("Refreshing…")
            QApplication.processEvents()
        try:
            result = self.refresh_from_dashboard()
        finally:
            if button is not None:
                button.setEnabled(True)
                button.setText("Refresh from Dashboard")
        if not quiet or not result:
            if quiet and not result:
                return  # the status line already says it; no toast on opening the tab
            report(self, result)

    def set_shots(self, shots, project_root=None, folder_resolver=None,
                  project_name="", project_path=None, sequence_fps=24.0):
        """Hand the timeline the shots the dashboard is tracking."""
        self.lineup_editor.set_project_context(project_name, project_path)
        self.lineup_editor.set_project_source(project_root, folder_resolver, sequence_fps)
        self.lineup_editor.set_shots(shots)

    def refresh_from_dashboard(self):
        """
        Pull the current shots - from the dashboard tab if it is open, and from
        the database if it is not. Returns a Result saying what really happened.
        """
        from PySide6.QtWidgets import QApplication
        from ..components.feedback import Result

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            dashboard = self._find_dashboard()
            if dashboard is not None:
                project = getattr(dashboard, "current_project", None)
                shots = getattr(dashboard, "all_shots", None) or []
                self.set_shots(
                    shots,
                    project_root=getattr(project, "folder_base", "") or None,
                    folder_resolver=getattr(dashboard, "_shot_folder_resolver", None),
                    project_name=getattr(project, "code", "") or "",
                    sequence_fps=project_fps(project),
                )
                if not shots:
                    return Result.failure(
                        "The dashboard has no shots loaded, so there is no lineup to build.")
                return Result.success(
                    f"Timeline rebuilt from the dashboard: {len(shots)} shot{'s' if len(shots) != 1 else ''}.")
            return self._refresh_from_database()
        finally:
            QApplication.restoreOverrideCursor()

    def _refresh_from_database(self):
        """Load the stored project's shots without the dashboard tab."""
        from ..components.feedback import Result
        try:
            from .vfx_dashboard_pro.core.project_manager import ProjectManager
            from .vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler

            manager = ProjectManager()
            projects = manager.get_all_projects()
            if not projects:
                message = ("No projects yet. Build one in Build & Ingest, or add it on "
                           "the VFX Dashboard.")
                self.lineup_editor._set_status(message)
                return Result.failure(message)

            # The project this person last had open on the dashboard. It asked
            # the manager for a default_project that was always None, so the
            # first project in the list won.
            from .vfx_dashboard_pro.ui.components.column_layout_manager import settings_factory
            from .vfx_dashboard_pro.ui.components.dashboard_project_mixin import DashboardProjectMixin
            code = (str(settings_factory().value(DashboardProjectMixin._last_project_key(self)) or "")
                    or projects[0].code)
            project = manager.get_project(code) or projects[0]

            shots = SQLiteHandler(project.code).read_shots()
            if not shots:
                message = f"{project.code} has no shots yet, so there is no lineup to build."
                self.lineup_editor._set_status(message)
                return Result.failure(message)

            self.set_shots(
                shots,
                project_root=getattr(project, "folder_base", "") or None,
                folder_resolver=None,
                project_name=project.code or "",
                sequence_fps=project_fps(project),
            )
            return Result.success(f"Timeline rebuilt for {project.code}: "
                                  f"{len(shots)} shot{'s' if len(shots) != 1 else ''}.")
        except Exception as exc:
            logger.warning("Could not load the lineup from the database: %s", exc)
            message = ("Could not read the project from the database. Open the VFX "
                       "Dashboard to load it, or check the connection.")
            self.lineup_editor._set_status(message)
            return Result.failure(message, detail=str(exc))

    def _find_dashboard(self):
        """The dashboard widget, wherever this tab has been put."""
        window = self.window()
        getter = getattr(window, "_get_tab_instance", None)
        if getter is None:
            return None
        try:
            tab = getter("VFX Dashboard", create=False)
        except Exception as exc:
            logger.debug("Could not reach the dashboard: %s", exc)
            return None
        if tab is None:
            return None
        if hasattr(tab, "all_shots"):
            return tab
        return getattr(tab, "dashboard_widget", None)

    # ------------------------------------------------------------- work guard
    def busy_reason(self):
        return self.lineup_editor.busy_reason()

    def shutdown(self, timeout_ms=15000) -> bool:
        return self.lineup_editor.shutdown(timeout_ms)

    def closeEvent(self, event):
        self.cleanup_resources()
        super().closeEvent(event)

    def cleanup_resources(self):
        if self._is_cleaned:
            return
        self._is_closing = True
        if hasattr(self, "lineup_editor"):
            self.lineup_editor.close()
        self._is_cleaned = True
