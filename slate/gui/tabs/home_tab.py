import logging
import os
import time
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QStackedLayout, QGraphicsDropShadowEffect, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, QUrl, QTimer, Signal, QThread, QMetaObject, Q_ARG, Slot
from PySide6.QtGui import QColor, QFont

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False

from ..components.qt_safety import safe_single_shot
from slate.core.domain.central_attendance import CentralAttendance
from slate.core.infra.database_manager import database_manager
from slate.core.system.adaptation_engine import system_engine

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else keeps the fallback it already had.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""


class QuickActionBtn(QFrame):
    clicked = Signal()
    def __init__(self, title, subtitle, glyph=""):
        super().__init__()
        self.glyph = glyph
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setMinimumHeight(85)
        self.setObjectName("QuickBtn")
        self.setStyleSheet("""
            QFrame#QuickBtn {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255, 255, 255, 0.08), stop:1 rgba(255, 255, 255, 0.02));
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-top: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 12px;
            }
            QFrame#QuickBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255, 255, 255, 0.15), stop:1 rgba(255, 255, 255, 0.06));
                border: 1px solid rgba(255, 255, 255, 0.3);
                border-top: 1px solid rgba(255, 255, 255, 0.5);
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(9)

        if glyph:
            from ..core.icons import icon as draw_icon, has_icon
            if has_icon(glyph):
                mark = QLabel()
                mark.setPixmap(draw_icon(glyph, "#E8E6E1", 17).pixmap(17, 17))
                mark.setStyleSheet("background: transparent; border: none;")
                mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                title_row.addWidget(mark)

        lbl_title = QLabel(title)
        lbl_title.setStyleSheet("background: none; background-color: transparent; border: none; color: white; font-size: 16px; font-weight: 800; letter-spacing: 1px;")
        lbl_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        title_row.addWidget(lbl_title)
        title_row.addStretch(1)
        
        lbl_sub = QLabel(subtitle)
        lbl_sub.setStyleSheet("background: none; background-color: transparent; border: none; color: #B4B1AA; font-size: 12px; font-weight: 500;")
        lbl_sub.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        
        layout.addLayout(title_row)
        layout.addWidget(lbl_sub)
        
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setStyleSheet("""
                QFrame#QuickBtn {
                    background: rgba(255, 255, 255, 0.05);
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-radius: 12px;
                }
            """)
        super().mousePressEvent(event)
        
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setStyleSheet("""
                QFrame#QuickBtn {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255, 255, 255, 0.15), stop:1 rgba(255, 255, 255, 0.06));
                    border: 1px solid rgba(255, 255, 255, 0.3);
                    border-top: 1px solid rgba(255, 255, 255, 0.5);
                    border-radius: 12px;
                }
            """)
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class HomeLoaderWorker(QThread):
    progress = Signal(int, str)
    data_loaded = Signal(list, dict)
    telemetry_loaded = Signal(str, str, str, str, str)
    
    def __init__(self, username, app_context, parent=None, mode="vfx"):
        super().__init__(parent)
        # The login, not the name on screen. Attendance rows are keyed by the
        # username; looking them up by display name meant Home and the
        # Attendance tab maintained two separate records for one person, and
        # each screen reported the other's punches as missing.
        self.username = username
        self.app_context = app_context
        self.mode = mode
        
    def run(self):
        # 1. Warmup
        time.sleep(0.4)
        self.progress.emit(20, "Mounting File Systems...")
        
        # 2. Ping Database
        time.sleep(0.3)
        self.progress.emit(40, "Connecting to PostgreSQL...")
        try:
            database_manager.ping_sync()
        except DatabaseUnavailableError:
            raise
        except Exception:
            pass
            
        # 3. Syncing
        time.sleep(0.3)
        self.progress.emit(60, "Syncing Central Database...")
        
        items = []
        punch_status = {}
        
        if self.mode == "ops":
            # Operations Mode: Fetch Attendance for today & recent HRMS pulse
            self.progress.emit(80, "Verifying User Attendance...")
            try:
                uid = (self.username or "").lower().strip()
                sql = "SELECT punch_in, punch_out FROM attendance_log WHERE user_id = %s AND day_date = %s"
                from datetime import datetime
                today_str = datetime.now().strftime('%Y-%m-%d')
                res = database_manager.execute_query(sql, (uid, today_str), fetch="all")
                if res:
                    row = res[0]
                    punch_status['punch_in'] = row['punch_in'] if isinstance(row, dict) else row[0]
                    punch_status['punch_out'] = row['punch_out'] if isinstance(row, dict) else row[1]
            except DatabaseUnavailableError:
                raise
            except Exception:
                pass

            try:
                # The columns are user_id and type. This asked for
                # applicant_name and leave_type, neither of which exists, so on
                # PostgreSQL it raised UndefinedColumn every time the Home tab
                # loaded and the leave items were silently dropped from the feed.
                # The name is looked up from ut_users, falling back to the id.
                sql_leaves = (
                    "SELECT COALESCE(u.display_name, u.username, l.user_id) AS applicant, "
                    "       l.type AS leave_type, l.status "
                    "FROM leave_requests l "
                    "LEFT JOIN ut_users u ON u.username = l.user_id "
                    "ORDER BY l.id DESC LIMIT 4"
                )
                res_leaves = database_manager.execute_query(sql_leaves, fetch="all")
                if res_leaves:
                    for row in res_leaves:
                        if isinstance(row, dict):
                            items.append({
                                "title": f"{row.get('applicant') or 'User'} - {row.get('leave_type') or 'Leave'}",
                                "status": row.get('status', 'Pending')
                            })
                        else:
                            items.append({
                                "title": f"{row[0]} - {row[1]}",
                                "status": str(row[2])
                            })
            except DatabaseUnavailableError:
                raise
            except Exception as e:
                logging.debug(f"Ops home recent items fetch: {e}")

        else:
            # VFX Mode: Preload stock library & fetch assigned shots (NO attendance!)
            try:
                if self.app_context:
                    self.app_context.library_manager().load_library()
            except Exception as e:
                logging.debug(f"Error preloading stock library: {e}")
                
            self.progress.emit(80, "Loading Production Tasks...")
            try:
                sql = "SELECT shot_name, status FROM tracking_shots LIMIT 5"
                res = database_manager.execute_query(sql, fetch="all")
                if res:
                    for row in res:
                        if isinstance(row, dict):
                            items.append({"title": row.get("shot_name", "Shot"), "status": row.get("status", "WIP")})
                        else:
                            items.append({"title": str(row[0]), "status": str(row[1])})
            except DatabaseUnavailableError:
                raise
            except Exception as e:
                logging.debug(f"Error async fetching shots: {e}")

        # 5. Fetch Studio Telemetry
        try:
            from datetime import datetime
            today_str = datetime.now().strftime('%Y-%m-%d')
            active_projects = database_manager.execute_query("SELECT COUNT(*) AS c FROM tracking_projects WHERE active = 1", fetch="one")
            # Shots waiting to be looked at. This used to count every row in
            # the projects table, so a card labelled "pending review" reported
            # how many projects the studio had ever had.
            pending_review = database_manager.execute_query(
                "SELECT COUNT(*) AS c FROM tracking_shots "
                "WHERE UPPER(COALESCE(status, '')) IN ('REVIEW', 'SENT FOR REVIEW', 'PENDING REVIEW')",
                fetch="one")
            artists_online = database_manager.execute_query("SELECT COUNT(DISTINCT user_id) AS c FROM attendance_log WHERE day_date = %s AND punch_out IS NULL", (today_str,), fetch="one")
            # Open means open, and the service desk decides what that is. The
            # test was status != 'Resolved', which counted every Closed ticket
            # the studio had ever finished.
            from slate.core.domain.service_desk import OPEN_STATUSES
            open_tickets = database_manager.execute_query(
                "SELECT COUNT(*) AS c FROM it_tickets WHERE status IN (%s)"
                % ", ".join(["%s"] * len(OPEN_STATUSES)),
                tuple(OPEN_STATUSES), fetch="one")
            upcoming_leaves = database_manager.execute_query("SELECT COUNT(*) AS c FROM leave_requests WHERE status = 'Approved' AND end_date >= %s", (today_str,), fetch="one")
            
            ap_count = active_projects['c'] if isinstance(active_projects, dict) else (active_projects[0] if active_projects else 0)
            pr_count = pending_review['c'] if isinstance(pending_review, dict) else (pending_review[0] if pending_review else 0)
            ao_count = artists_online['c'] if isinstance(artists_online, dict) else (artists_online[0] if artists_online else 0)
            ot_count = open_tickets['c'] if isinstance(open_tickets, dict) else (open_tickets[0] if open_tickets else 0)
            ul_count = upcoming_leaves['c'] if isinstance(upcoming_leaves, dict) else (upcoming_leaves[0] if upcoming_leaves else 0)
            
            self.telemetry_loaded.emit(str(ap_count), str(pr_count), str(ao_count), str(ot_count), str(ul_count))
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            logging.debug(f"Error async fetching telemetry: {e}")

        # 6. Finalize
        time.sleep(0.2)
        self.progress.emit(100, "Ready!")
        self.data_loaded.emit(items, punch_status)


class HomeTab(QWidget):
    """
    Cinematic Home Tab combining WebGL background with Glassmorphic PySide6 UI.
    Supports mode='vfx' (production hub without attendance) and mode='ops' (operations hub with attendance).
    """
    def __init__(self, user_data=None, app_context=None, main_window=None, parent=None, mode="vfx"):
        super().__init__(parent)
        self.mode = (mode or "vfx").lower()
        self.main_window = main_window
        self.user_data = user_data or {}
        self.app_context = app_context
        default_name = 'Artist' if self.mode == 'vfx' else 'Operator'
        self.user_display_name = self.user_data.get('display_name', self.user_data.get('username', default_name))
        # What the attendance record is keyed by, which is not what is greeted
        # on screen. The Attendance tab uses exactly this, and the two screens
        # have to agree or the studio ends up with two records per person.
        self.username = str(self.user_data.get('user_id')
                            or self.user_data.get('username') or '').strip()
        
        # Only initialize CentralAttendance if in Ops mode
        if self.mode == "ops":
            self.attendance = CentralAttendance()
        else:
            self.attendance = None
        
        self._is_loaded = False
        self._cinematic_ended = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.init_ui()
        
        # Start real background loading sequence
        self.loader_worker = HomeLoaderWorker(self.username, self.app_context, self, mode=self.mode)
        self.loader_worker.progress.connect(self._on_load_progress)
        self.loader_worker.data_loaded.connect(self._on_data_loaded)
        self.loader_worker.telemetry_loaded.connect(self._update_telemetry_ui)
        if os.getenv("HEADLESS_TESTING") != "1":
            if HAS_WEBENGINE:
                self.web_view.loadFinished.connect(lambda ok: self.loader_worker.start())
            else:
                self.loader_worker.start()

    @Slot(str, str, str, str, str)
    def _update_telemetry_ui(self, ap_count: str, pr_count: str, ao_count: str, ot_count: str, ul_count: str):
        if hasattr(self, 'lbl_active_projects'):
            self.lbl_active_projects.setText(ap_count)
        if hasattr(self, 'lbl_pending_review'):
            self.lbl_pending_review.setText(pr_count)
        if hasattr(self, 'lbl_artists_online'):
            self.lbl_artists_online.setText(ao_count)
        if hasattr(self, 'lbl_open_tickets'):
            self.lbl_open_tickets.setText(ot_count)
        if hasattr(self, 'lbl_upcoming_leaves'):
            self.lbl_upcoming_leaves.setText(ul_count)

    def init_ui(self):
        self.stack = QStackedLayout(self)
        self.stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        
        # --- LAYER 0: WEBGL BACKGROUND ---
        if HAS_WEBENGINE:
            self.web_view = QWebEngineView()
            self.web_view.page().setBackgroundColor(QColor("#0D0D0F"))
            self.web_view.setStyleSheet("background-color: #0D0D0F;")
            # Operations runs its own sky - blue through red into yellow - so
            # the two modes are told apart before any text is read. Production
            # keeps the green and cyan. If the ops file is missing for any
            # reason we fall back to the shared one rather than to nothing.
            assets_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "assets"
            )
            html_path = os.path.join(assets_dir, "cinematic_bg.html")
            if self.mode == "ops":
                ops_path = os.path.join(assets_dir, "cinematic_bg_ops.html")
                if os.path.exists(ops_path):
                    html_path = ops_path

            if os.path.exists(html_path):
                self.web_view.setUrl(QUrl.fromLocalFile(html_path))
            else:
                self.web_view.setHtml("<html><body style='background:#0D0D0F; color:#E8E6E1;'>Background missing</body></html>")
                
            self.web_view.titleChanged.connect(self._on_title_changed)
            self.stack.addWidget(self.web_view)
        else:
            self.web_view = QWidget()
            self.web_view.setStyleSheet("background-color: #0D0D0F;")
            fallback_layout = QVBoxLayout(self.web_view)
            fallback_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl = QLabel("Background missing (QtWebEngine disabled)")
            lbl.setStyleSheet("color: #2C2C34;")
            fallback_layout.addWidget(lbl)
            self.stack.addWidget(self.web_view)
        
        # --- LAYER 1: FOREGROUND UI (TRANSPARENT) ---
        self.overlay_widget = QWidget()
        self.overlay_widget.setStyleSheet("background: transparent;")
        self.overlay_widget.hide()
        
        # --- LAYER 1.5: INVISIBLE CLICK CATCHER ---
        self.click_catcher = QPushButton()
        self.click_catcher.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.click_catcher.setStyleSheet("background: rgba(0, 0, 0, 1); border: none;")
        self.click_catcher.setCursor(Qt.CursorShape.PointingHandCursor)
        self.click_catcher.clicked.connect(self._end_cinematic_mode)
        self.stack.addWidget(self.click_catcher)
        
        overlay_layout = QVBoxLayout(self.overlay_widget)
        overlay_layout.setContentsMargins(40, 40, 40, 40)
        overlay_layout.setSpacing(20)
        
        # Top Bar
        top_bar = QHBoxLayout()
        greeting = QLabel(f"Welcome back, {self.user_display_name}")
        greeting.setStyleSheet("""
            color: white; 
            font-size: 28px; 
            font-weight: 300; 
            font-family: 'Inter', sans-serif;
            letter-spacing: 2px;
        """)
        top_bar.addWidget(greeting)
        top_bar.addStretch()
        
        if self.mode == "ops":
            # Operations Mode: Punch In / Punch Out Panel
            self.attendance_panel = self._build_glass_panel()
            att_layout = QHBoxLayout(self.attendance_panel)
            att_layout.setContentsMargins(20, 10, 20, 10)
            
            self.lbl_punch_status = QLabel("Status: Unknown")
            self.lbl_punch_status.setStyleSheet("color: #E8E6E1; font-size: 14px;")
            
            self.btn_punch_in = self._build_glass_button("PUNCH IN", "#5FBF8F")
            self.btn_punch_out = self._build_glass_button("PUNCH OUT", "#D9635F")
            
            self.btn_punch_in.clicked.connect(lambda: self.do_punch("in"))
            self.btn_punch_out.clicked.connect(lambda: self.do_punch("out"))
            
            att_layout.addWidget(self.lbl_punch_status)
            att_layout.addSpacing(20)
            att_layout.addWidget(self.btn_punch_in)
            att_layout.addWidget(self.btn_punch_out)
            
            top_bar.addWidget(self.attendance_panel)
        else:
            # VFX Mode: Production Hub Badge (NO attendance widgets)
            hub_badge = self._build_glass_panel()
            badge_layout = QHBoxLayout(hub_badge)
            badge_layout.setContentsMargins(18, 10, 18, 10)
            lbl_hub = QLabel("VFX PRODUCTION HUB")
            lbl_hub.setStyleSheet("color: #3EA8BF; font-size: 13px; font-weight: 800; letter-spacing: 2px; background: transparent; border: none;")
            badge_layout.addWidget(lbl_hub)
            top_bar.addWidget(hub_badge)

        overlay_layout.addLayout(top_bar)
        overlay_layout.addStretch()
        
        # --- MIDDLE SECTION: QUICK LAUNCH & RIGHT PANELS ---
        middle_layout = QHBoxLayout()
        middle_layout.setSpacing(40)
        
        # 1. Quick Launch Pad (Left side)
        quick_launch_panel = self._build_glass_panel()
        quick_launch_panel.setMinimumWidth(400)
        ql_layout = QVBoxLayout(quick_launch_panel)
        ql_layout.setContentsMargins(20, 20, 20, 20)
        
        ql_title = QLabel("OPERATIONS LAUNCH" if self.mode == "ops" else "QUICK LAUNCH")
        ql_title.setStyleSheet("color: #3EA8BF; font-size: 14px; font-weight: 800; letter-spacing: 3px; background: transparent; border: none;")
        ql_layout.addWidget(ql_title)
        
        grid_layout = QVBoxLayout()
        grid_layout.setSpacing(15)
        
        if self.mode == "ops":
            # Operations Quick Actions
            row1 = QHBoxLayout()
            btn_attendance = self._build_quick_action_btn("Attendance", "Biometric & Timesheets", "clock")
            btn_attendance.clicked.connect(lambda: self._trigger_tab("Attendance"))
            btn_leave = self._build_quick_action_btn("Leave Management", "PTO & Requests", "leave")
            btn_leave.clicked.connect(lambda: self._trigger_tab("Leave Management"))
            row1.addWidget(btn_attendance)
            row1.addWidget(btn_leave)
            
            row2 = QHBoxLayout()
            btn_tickets = self._build_quick_action_btn("IT Ticketing", "Helpdesk & Support", "ticket")
            btn_tickets.clicked.connect(lambda: self._trigger_tab("Ticketing"))
            btn_users = self._build_quick_action_btn("Users & Roles", "RBAC & Permissions", "users")
            btn_users.clicked.connect(lambda: self._trigger_tab("Users & Roles"))
            row2.addWidget(btn_tickets)
            row2.addWidget(btn_users)
            
            grid_layout.addLayout(row1)
            grid_layout.addLayout(row2)
        else:
            # VFX Quick Actions (Completely without attendance!)
            row1 = QHBoxLayout()
            btn_rename = self._build_quick_action_btn("File Renamer", "CAP Rename Utility", "tag")
            btn_rename.clicked.connect(lambda: self._trigger_tab("CAP Rename"))
            btn_dashboard = self._build_quick_action_btn("VFX Dashboard", "Project Tracking & Shots", "chart")
            btn_dashboard.clicked.connect(lambda: self._trigger_tab("VFX Dashboard"))
            row1.addWidget(btn_rename)
            row1.addWidget(btn_dashboard)
            
            row2 = QHBoxLayout()
            btn_folder = self._build_quick_action_btn("Build & Ingest", "Build structure + move scans", "folder")
            btn_folder.clicked.connect(lambda: self._trigger_tab("Build & Ingest"))
            btn_review = self._build_quick_action_btn("Timeline Viewer", "Reel lineup in Olive", "clapper")
            btn_review.clicked.connect(lambda: self._trigger_tab("Timeline Viewer"))
            row2.addWidget(btn_folder)
            row2.addWidget(btn_review)
            
            grid_layout.addLayout(row1)
            grid_layout.addLayout(row2)

        ql_layout.addLayout(grid_layout)
        middle_layout.addWidget(quick_launch_panel, 0, Qt.AlignmentFlag.AlignLeft)
        middle_layout.addStretch()
        
        # 2. Right Side Panels (Tasks & Stats)
        right_panel_layout = QVBoxLayout()
        right_panel_layout.setSpacing(20)
        
        # A. Contextual Tasks / Pulse Panel
        tasks_panel = self._build_glass_panel()
        tasks_panel.setMinimumWidth(340)
        tasks_layout = QVBoxLayout(tasks_panel)
        tasks_layout.setContentsMargins(20, 20, 20, 20)
        
        tasks_title_text = "HRMS & OPERATIONS PULSE" if self.mode == "ops" else "MY RECENT TASKS"
        tasks_title = QLabel(tasks_title_text)
        tasks_title.setStyleSheet("color: #3EA8BF; font-size: 14px; font-weight: 800; letter-spacing: 3px; background: transparent; border: none;")
        tasks_layout.addWidget(tasks_title)
        
        self.tasks_container_layout = QVBoxLayout()
        empty_text = "Loading operations status..." if self.mode == "ops" else "Loading assigned shots..."
        empty_lbl = QLabel(empty_text)
        empty_lbl.setStyleSheet("background: none; background-color: transparent; border: none; color: #87857F; font-style: italic;")
        self.tasks_container_layout.addWidget(empty_lbl)
        tasks_layout.addLayout(self.tasks_container_layout)
        right_panel_layout.addWidget(tasks_panel)
        
        # B. Studio Stats Panel
        stats_panel = self._build_glass_panel()
        stats_panel.setMinimumWidth(340)
        stats_layout = QVBoxLayout(stats_panel)
        stats_layout.setContentsMargins(20, 20, 20, 20)
        
        stats_title = QLabel("LIVE STUDIO TELEMETRY")
        stats_title.setStyleSheet("color: #3EA8BF; font-size: 14px; font-weight: 800; letter-spacing: 3px; background: transparent; border: none;")
        stats_layout.addWidget(stats_title)
        
        stat_row = QHBoxLayout()
        w1, self.lbl_active_projects = self._build_stat_item("Active Projects", "-")
        w2, self.lbl_pending_review = self._build_stat_item("Pending Review", "-")
        w3, self.lbl_artists_online = self._build_stat_item("Artists Online", "-")
        stat_row.addWidget(w1)
        stat_row.addWidget(w2)
        stat_row.addWidget(w3)
        stats_layout.addLayout(stat_row)
        
        stat_row_2 = QHBoxLayout()
        w4, self.lbl_open_tickets = self._build_stat_item("Open IT Tickets", "-")
        w5, self.lbl_upcoming_leaves = self._build_stat_item("Upcoming Leaves", "-")
        stat_row_2.addWidget(w4)
        stat_row_2.addWidget(w5)
        stat_row_2.addStretch()
        stats_layout.addLayout(stat_row_2)
        
        right_panel_layout.addWidget(stats_panel)
        middle_layout.addLayout(right_panel_layout)
        overlay_layout.addLayout(middle_layout)
        
        overlay_layout.addSpacing(100)
        self.stack.addWidget(self.overlay_widget)

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_cinematic_ended', False):
            host = self.window()
            if hasattr(host, 'set_cinematic_mode'):
                host.set_cinematic_mode(True)

    def _build_glass_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("GlassPanel")
        # A dark scrim, not a white one.
        #
        # These panels tinted with white over the animated sky, which on a
        # bright frame added glare instead of contrast - the headings and the
        # small labels under the telemetry figures washed out completely. A
        # dark backing keeps the text readable whatever the animation is doing
        # behind it, and matters more now Operations runs a brighter palette.
        frame.setStyleSheet("""
            QFrame#GlassPanel {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                            stop:0 rgba(13, 13, 15, 0.78), stop:1 rgba(13, 13, 15, 0.62));
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-top: 1px solid rgba(255, 255, 255, 0.22);
                border-radius: 16px;
            }
        """)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(25)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 8)
        frame.setGraphicsEffect(shadow)
        return frame

    def _build_glass_button(self, text: str, color_hex: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid {color_hex};
                color: {color_hex};
                border-radius: 6px;
                padding: 6px 16px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background-color: {color_hex};
                color: black;
            }}
            QPushButton:pressed {{
                background-color: rgba(255, 255, 255, 0.2);
            }}
        """)
        return btn

    def _build_quick_action_btn(self, title: str, subtitle: str, glyph: str = "") -> QFrame:
        return QuickActionBtn(title, subtitle, glyph)

    def _build_stat_item(self, label: str, value: str):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        v = QLabel(value)
        v.setStyleSheet("background: none; background-color: transparent; border: none; color: #5FBF8F; font-size: 28px; font-weight: 900; font-family: monospace;")
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        l = QLabel(label)
        l.setStyleSheet("background: none; background-color: transparent; border: none; color: #B4B1AA; font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 1px;")
        l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        lay.addWidget(v)
        lay.addWidget(l)
        return w, v

    def _trigger_tab(self, tab_label: str):
        host = self.window()
        if hasattr(host, "_switch_to_tab_label"):
            host._switch_to_tab_label(tab_label)

    def _on_load_progress(self, pct, msg):
        if HAS_WEBENGINE and hasattr(self, "web_view") and self.web_view is not None:
            try:
                page = self.web_view.page()
                if page:
                    safe_msg = str(msg or "").replace("'", "\\'")
                    page.runJavaScript(
                        f"if (typeof window.setLoadingProgress === 'function') {{ window.setLoadingProgress({pct}, '{safe_msg}'); }}"
                    )
            except Exception:
                pass

    def closeEvent(self, event):
        if hasattr(self, "loader_worker") and self.loader_worker and self.loader_worker.isRunning():
            try:
                self.loader_worker.terminate()
                self.loader_worker.wait(300)
            except Exception:
                pass
        super().closeEvent(event)
        
    def _on_data_loaded(self, items, punch_status):
        self._is_loaded = True
        self.setFocus()

        # Land on the work, not on the logo.
        #
        # Home is the first thing everyone sees each session, and it was a
        # full-screen logo animation that sat there until somebody pressed
        # Enter - a keyboard affordance in a mouse-driven tool, occupying the
        # most valuable area in the application while showing nothing about the
        # day. The animation still plays; it just gets out of the way by itself
        # once the shots are ready. Clicking or pressing Enter still skips it.
        if not self._cinematic_ended:
            safe_single_shot(2200, self, self._end_cinematic_mode)
        
        # Clear loading label
        from ..core.layout_utils import clear_layout
        clear_layout(self.tasks_container_layout)
                
        # Populate items
        if not items:
            no_data_msg = "All operations up to date." if self.mode == "ops" else "No shots assigned to you currently."
            empty_lbl = QLabel(no_data_msg)
            empty_lbl.setStyleSheet("background: none; background-color: transparent; border: none; color: #87857F; font-style: italic;")
            self.tasks_container_layout.addWidget(empty_lbl)
        else:
            for it in items[:5]:
                row = QHBoxLayout()
                lbl_name = QLabel(it.get('title', 'Item'))
                lbl_name.setStyleSheet("background: none; background-color: transparent; border: none; color: white; font-weight: bold;")
                
                status_str = it.get('status', 'Active')
                badge_color = "#5FBF8F" if status_str in ("Approved", "Done", "Resolved") else ("#D9A441" if status_str in ("Pending", "WIP") else "#3EA8BF")
                lbl_status = QLabel(status_str)
                lbl_status.setStyleSheet(f"color: {badge_color}; font-weight: bold; padding: 2px 8px; border-radius: 4px; background-color: rgba(255, 255, 255, 0.08);")
                
                row.addWidget(lbl_name)
                row.addStretch()
                row.addWidget(lbl_status)
                self.tasks_container_layout.addLayout(row)
                
        # Update punch status in Ops mode only
        if self.mode == "ops":
            self._refresh_punch_buttons(punch_status)

    def _on_title_changed(self, title):
        if title == "CINEMATIC_ENDED" and not self._cinematic_ended:
            self._end_cinematic_mode()

    def keyPressEvent(self, event):
        if self._is_loaded and not self._cinematic_ended:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._end_cinematic_mode()
        super().keyPressEvent(event)

    def _end_cinematic_mode(self):
        self._cinematic_ended = True
        if HAS_WEBENGINE:
            self.web_view.page().runJavaScript("window.triggerEnter()")
        
        host = getattr(self, "main_window", None) or self.window()
        if hasattr(host, 'set_cinematic_mode'):
            try:
                host.set_cinematic_mode(False)
            except Exception as e:
                logging.exception(f"Error ending cinematic mode: {e}")
            
        if hasattr(self, 'click_catcher'):
            self.click_catcher.hide()
            
        self._show_overlay()

    def _show_overlay(self):
        self.overlay_widget.show()
        self.overlay_widget.raise_()

    def _refresh_punch_buttons(self, punch_status):
        if self.mode != "ops" or not hasattr(self, 'lbl_punch_status'):
            return
        pi = punch_status.get('punch_in')
        po = punch_status.get('punch_out')
        
        if not pi and not po:
            self.lbl_punch_status.setText("Status: Not Punched In")
            self.btn_punch_in.setEnabled(True)
            self.btn_punch_out.setEnabled(False)
        elif pi and not po:
            self.lbl_punch_status.setText(f"Status: Punched In ({pi})")
            self.btn_punch_in.setEnabled(False)
            self.btn_punch_out.setEnabled(True)
        elif pi and po:
            self.lbl_punch_status.setText(f"Status: Punched Out ({po})")
            self.btn_punch_in.setEnabled(False)
            self.btn_punch_out.setEnabled(False)
            
    def _read_todays_punch(self) -> dict:
        """
        Today's punch row, read now rather than remembered.

        This used to paint from a copy taken when the tab was built, so the
        moment somebody punched in the buttons disagreed with the database
        until the tab was rebuilt - and the obvious next move, pressing Punch
        In again, was refused by a screen that still said they were out.
        """
        from datetime import datetime

        status = {}
        uid = (self.username or "").lower().strip()
        if not uid:
            return status
        try:
            row = database_manager.execute_query(
                "SELECT punch_in, punch_out FROM attendance_log "
                "WHERE user_id = %s AND day_date = %s",
                (uid, datetime.now().strftime('%Y-%m-%d')), fetch="one")
            if row:
                status['punch_in'] = row['punch_in'] if isinstance(row, dict) else row[0]
                status['punch_out'] = row['punch_out'] if isinstance(row, dict) else row[1]
        except DatabaseUnavailableError:
            # An outage is not an empty day. Letting it past means the caller
            # can say so, rather than the buttons quietly resetting to "not
            # punched in" while the database is down.
            raise
        except Exception as exc:
            logging.debug("Could not re-read today's attendance: %s", exc)
        return status

    def _update_punch_ui(self):
        if self.mode != "ops":
            return
        self._refresh_punch_buttons(self._read_todays_punch())

    def do_punch(self, action: str):
        if self.mode != "ops" or not self.attendance:
            return
        host = self.window()

        try:
            self.attendance.log_action(self.username, action)
        except Exception as e:
            if host and hasattr(host, "show_feedback"):
                host.show_feedback(f"Punch failed: {e}", "error", 4000)
            return

        # The punch is in the database. Failing to read it back is a different
        # problem, and reporting it as a failed punch would send somebody to
        # press the button again for a punch that already landed.
        try:
            self._update_punch_ui()
        except Exception as e:
            logging.debug("Punched, but could not refresh the buttons: %s", e)
            if host and hasattr(host, "show_feedback"):
                host.show_feedback(
                    f"Punched {action.upper()}. The screen could not be "
                    "refreshed - reopen the tab to confirm.", "warning", 5000)
            return

        if host and hasattr(host, "show_feedback"):
            host.show_feedback(f"Successfully Punched {action.upper()}", "success", 4000)


class VfxHomeTab(HomeTab):
    """Cinematic VFX Home Hub without attendance."""
    def __init__(self, user_data=None, app_context=None, main_window=None, parent=None):
        super().__init__(user_data=user_data, app_context=app_context, main_window=main_window, parent=parent, mode="vfx")


class OpsHomeTab(HomeTab):
    """Cinematic Studio Operations Hub with biometric attendance and HRMS/IT quick actions."""
    def __init__(self, user_data=None, app_context=None, main_window=None, parent=None):
        super().__init__(user_data=user_data, app_context=app_context, main_window=main_window, parent=parent, mode="ops")
