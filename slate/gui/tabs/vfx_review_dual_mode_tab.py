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
        
        # --- NOTIFICATION BELL ---
        self.btn_notif = QPushButton("N")
        self.btn_notif.setFixedSize(40, 36)
        self.btn_notif.setStyleSheet("""
            QPushButton { background: transparent; border: none; font-size: 20px; color: #87857F; }
            QPushButton:hover { color: white; background: #26262D; border-radius: 4px; }
        """)
        self.btn_notif.clicked.connect(self.show_notifications)
        layout.addWidget(self.btn_notif)
        
        # Badge Label (Hidden by default)
        self.lbl_badge = QLabel("0", self.btn_notif)
        self.lbl_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_badge.hide()
        self.lbl_badge.setStyleSheet("""
            background-color: red; color: white; border-radius: 8px; 
            font-size: 10px; font-weight: bold; padding: 2px;
        """)
        self.lbl_badge.resize(16, 16)
        self.lbl_badge.move(22, 2)
        
        # -------------------------
        
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

        # Initialize Notification Polling
        try:
            self.init_notifications()
        except Exception as e:
            logger.warning(f"Notification init failed (non-critical): {e}")
        
        return header

    def init_notifications(self):
        self.notifier = None
        self.current_user_ids = self._resolve_notification_user_ids()
        
        try:
            from ...core.domain.notification_manager import NotificationManager
            self.notifier = NotificationManager()
            
            # Start timer only if successful
            from PySide6.QtCore import QTimer
            self.notif_timer = QTimer(self)
            self.notif_timer.timeout.connect(self.check_notifications)
            self.notif_timer.start(10000) # Check every 10s
            self.check_notifications() # Initial check
        except Exception as e:
            logging.exception(f"Notification System Init Failed: {e}")

    def _resolve_notification_user_ids(self):
        ids = []
        for key in ("user_id", "username", "display_name"):
            value = self.user_data.get(key) if isinstance(self.user_data, dict) else None
            if isinstance(value, str) and value.strip():
                ids.append(value.strip())

        # Backward-compatible fallback.
        if not ids:
            ids = ["Artist"]

        deduped = []
        seen = set()
        for item in ids:
            norm = item.lower()
            if norm in seen:
                continue
            seen.add(norm)
            deduped.append(item)
        return deduped

    def _get_unread_notifications(self):
        if not self.notifier:
            return []

        merged = {}
        for user_id in self.current_user_ids:
            for note in self.notifier.get_unread(user_id):
                note_id = note.get("id")
                if note_id:
                    merged[note_id] = note

        return sorted(merged.values(), key=lambda x: x.get("timestamp", 0), reverse=True)

    def check_notifications(self):
        if self._is_closing or not self.notifier:
            return
        try:
            notes = self._get_unread_notifications()
            count = len(notes)
            
            if count > 0:
                self.btn_notif.setStyleSheet("QPushButton { background: transparent; border: none; font-size: 20px; color: #D9A441; }")
                self.lbl_badge.setText(str(count) if count < 9 else "9+")
                self.lbl_badge.show()
                self.lbl_badge.raise_()
            else:
                self.btn_notif.setStyleSheet("QPushButton { background: transparent; border: none; font-size: 20px; color: #87857F; }")
                self.lbl_badge.hide()
        except Exception as e:
            logger.warning(f"Failed to update notification badge: {e}")

    def show_notifications(self):
        if self._is_closing or not self.notifier:
            return
        from PySide6.QtWidgets import QDialog, QListWidget, QListWidgetItem, QVBoxLayout, QPushButton
        
        d = QDialog(self)
        d.setWindowTitle("Notifications")
        d.setMinimumSize(400, 300)
        d.resize(400, 300)
        d.setStyleSheet("background: #1D1D22; color: #E8E6E1;")
        l = QVBoxLayout(d)
        
        notes = self._get_unread_notifications()
        list_w = QListWidget()
        list_w.setStyleSheet("QListWidget { border: none; background: #1D1D22; } QListWidget::item { padding: 8px; border-bottom: 1px solid #26262D; }")
        
        ids_to_clear = []
        for n in notes:
            item = QListWidgetItem(f"[{n['type'].upper()}] {n['message']}")
            list_w.addItem(item)
            ids_to_clear.append(n['id'])
            
        if not notes:
            list_w.addItem("No new notifications.")
            
        l.addWidget(list_w)
        
        btn_clear = QPushButton("Mark All Read")
        btn_clear.setStyleSheet("background: #2C2C34; color: white; padding: 6px; border: none;") 
        
        def close_and_clear():
            if self._is_closing or not self.notifier:
                d.accept()
                return
            self.notifier.mark_read(ids_to_clear)
            self.check_notifications() # Refresh UI
            d.accept()
            
        btn_clear.clicked.connect(close_and_clear)
        l.addWidget(btn_clear)
        
        d.exec()
    
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
        """Pull the current shots straight from the dashboard tab."""
        dashboard = self._find_dashboard()
        if dashboard is None:
            self.lineup_editor.status_label.setText(
                "Open the VFX Dashboard and pick a project first."
            )
            return

        project = getattr(dashboard, "current_project", None)
        self.set_shots(
            getattr(dashboard, "all_shots", None) or [],
            project_root=getattr(project, "folder_base", "") or None,
            folder_resolver=getattr(dashboard, "_shot_folder_resolver", None),
            project_name=getattr(project, "code", "") or "",
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

