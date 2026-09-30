"""
Timeline Viewer - the lineup, in Olive.

This used to be two modes: a Shot Checker built into the software, and the
Olive lineup editor. The Shot Checker is gone - reviewing happens in OpenRV,
which is what the studio actually reviews in, and which the dashboard opens
directly through its "Review in RV" button.

What is left is the timeline: plates laid out reel by reel, with each
department's render on its own layer directly beneath the plate it came from.
The shots come from the dashboard, so the timeline is always built from what
the production is actually tracking.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
)
from PySide6.QtCore import Qt
import logging

from .shot_review.lineup_editor_mode import LineupEditorMode

logger = logging.getLogger(__name__)


class VFXReviewDualModeTab(QWidget):
    """The lineup, built from the dashboard and opened in Olive."""

    def __init__(self, config_manager, user_data=None):
        super().__init__()

        self.config = config_manager
        self.user_data = user_data or {}
        self._is_closing = False
        self._is_cleaned = False

        self.lineup_editor = LineupEditorMode()

        self.setup_ui()

        logger.info("Timeline Viewer initialized")

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self.create_mode_toggle())
        layout.addWidget(self.lineup_editor)
    
    def create_mode_toggle(self):
        """Create mode toggle buttons & Notification Bell"""
        header = QWidget()
        header.setStyleSheet("""
            QWidget {
                background-color: #16161A;
                border-bottom: 2px solid #3EA8BF;
            }
        """)
        
        layout = QHBoxLayout(header)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Title
        title = QLabel("TIMELINE VIEWER")
        title.setStyleSheet("QLabel { color: white; font-size: 16px; font-weight: bold; }")
        layout.addWidget(title)
        
        layout.addStretch()
        
        # Notifications are in the header now, beside Help, for everybody
        # (slate/gui/components/notification_center.py). The "N" bell that
        # lived here was only seen by people with this tab open.

        refresh_btn = QPushButton("Refresh from Dashboard")
        refresh_btn.setToolTip(
            "Re-read the shots the dashboard is tracking and rebuild the list."
        )
        refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #16323A;
                color: white;
                font-weight: bold;
                padding: 10px 20px;
                border-radius: 4px;
                border: 2px solid #16323A;
            }
            QPushButton:hover {
                background-color: #3EA8BF;
            }
        """)
        refresh_btn.clicked.connect(self.refresh_from_dashboard)
        layout.addWidget(refresh_btn)

        return header

    def show_notifications(self):
        """Open the header's notification list (kept for anything that calls it)."""
        from ..components.notification_center import open_notifications
        open_notifications(self)

    def set_shots(self, shots, project_root=None, folder_resolver=None,
                  project_name="", project_path=None):
        """
        Hand the timeline the shots the dashboard is tracking.

        This is the only way shots get here now. The Shot Checker used to be
        the source, which meant the timeline could only ever hold what somebody
        had approved inside this tab.
        """
        self.lineup_editor.set_project_context(project_name, project_path)
        self.lineup_editor.set_project_source(project_root, folder_resolver)
        self.lineup_editor.set_shots(shots)

    def refresh_from_dashboard(self):
        """
        Pull the current shots - from the dashboard tab if it is open, and from
        the database if it is not.

        Tabs are built on first use, so this used to work only if somebody had
        already visited the dashboard this session. Otherwise it said "open the
        VFX Dashboard and pick a project first", which is a reasonable sentence
        and a poor answer: the project is already chosen and stored, and this
        tab can read it.
        """
        dashboard = self._find_dashboard()
        if dashboard is not None:
            project = getattr(dashboard, "current_project", None)
            self.set_shots(
                getattr(dashboard, "all_shots", None) or [],
                project_root=getattr(project, "folder_base", "") or None,
                folder_resolver=getattr(dashboard, "_shot_folder_resolver", None),
                project_name=getattr(project, "code", "") or "",
            )
            return

        self._refresh_from_database()

    def _refresh_from_database(self):
        """Load the stored project's shots without the dashboard tab."""
        try:
            from .vfx_dashboard_pro.core.project_manager import ProjectManager
            from .vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler

            manager = ProjectManager()
            projects = manager.get_all_projects()
            if not projects:
                self.lineup_editor.status_label.setText(
                    "No projects yet. Build one in Build & Ingest, or add it on "
                    "the VFX Dashboard."
                )
                return

            code = getattr(manager, "default_project", None) or projects[0].code
            project = manager.get_project(code) or projects[0]

            shots = SQLiteHandler(project.code).read_shots()
            if not shots:
                self.lineup_editor.status_label.setText(
                    "%s has no shots yet, so there is no lineup to build."
                    % project.code)
                return

            self.set_shots(
                shots,
                project_root=getattr(project, "folder_base", "") or None,
                folder_resolver=None,
                project_name=project.code or "",
            )
        except Exception as exc:
            logger.warning("Could not load the lineup from the database: %s", exc)
            self.lineup_editor.status_label.setText(
                "Could not read the project from the database. Open the VFX "
                "Dashboard to load it, or check the connection."
            )

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
        # The tab may wrap the widget that actually holds the shots.
        if hasattr(tab, "all_shots"):
            return tab
        return getattr(tab, "dashboard_widget", None)

    def closeEvent(self, event):
        """Propagate close event to child widgets for cleanup."""
        self.cleanup_resources()
        super().closeEvent(event)

    def cleanup_resources(self):
        """Release timers/widgets used by both review modes."""
        if self._is_cleaned:
            return

        self._is_closing = True

        if hasattr(self, "notif_timer") and self.notif_timer.isActive():
            self.notif_timer.stop()

        if hasattr(self, "lineup_editor"):
            lineup = self.lineup_editor
            if hasattr(lineup, "cleanup_resources"):
                lineup.cleanup_resources()
            lineup.close()

        self._is_cleaned = True

