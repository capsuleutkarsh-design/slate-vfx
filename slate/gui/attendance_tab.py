import logging
import calendar
import re
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFrame, QTableWidget, QTableWidgetItem, QMessageBox, QComboBox, QSpinBox, QFileDialog, QDialog, QFormLayout, QLineEdit,
    QSplitter
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont

from ..core.infra.app_context import AppContext
from .attendance_export_worker import ExcelExportWorker
from .attendance_metrics import calculate_hours as compute_hours
from .attendance_metrics import calculate_streak as compute_streak
from slate.core.domain import leave_policy as lp
from slate.gui.core.offline_notice import on_database_error

class AttendanceTab(QWidget):
    """
    Dedicated Attendance Dashboard.
    - Personal: Manual Punch In/Out, View My History.
    - Admin: View All, Edit, Export (Supervisor/Dev only).
    """
    def __init__(self, user_data, attendance=None, user_manager=None, app_context=None, sync_enabled=True):
        super().__init__()
        self.app_context = app_context or AppContext()
        self.sync_enabled = bool(sync_enabled)
        # Standalone / tests may open MainWindow without login; match main_window debug_user.
        if not user_data:
            user_data = {
                "user_id": "debug_user",
                "username": "debug_user",
                "display_name": "Local user",
                "roles": ["Artist"],
            }
        self.user_data = user_data
        self.username = user_data.get('user_id', user_data.get('username', 'Unknown'))
        self._team_row_user_ids = []

        # Handle roles array (new format) and role string (legacy)
        roles_data = user_data.get('roles', user_data.get('role', ['Artist']))
        if isinstance(roles_data, list):
            self.roles = roles_data
            self.role = roles_data[0] if roles_data else 'Artist'  # Keep for display compatibility
        else:
            self.roles = [roles_data]
            self.role = roles_data

        self.display_name = user_data.get('display_name', self.username)

        self.attendance = attendance or self.app_context.attendance()
        self.user_manager = user_manager or self.app_context.user_manager()
        self._export_worker = None

        self.setup_ui()
        self.refresh_personal_view()

        # Personal auto-refresh so running hours update live without reopening.
        self.personal_refresh_timer = QTimer(self)
        self.personal_refresh_timer.timeout.connect(self.refresh_personal_view)
        self.personal_refresh_timer.start(60000)  # 60 seconds

        # Admin Refresh
        if self.is_admin() and self.sync_enabled:
            self.refresh_team_view()

            # AUTO-REFRESH: Poll for changes every 30 seconds
            self.auto_refresh_timer = QTimer(self)
            self.auto_refresh_timer.timeout.connect(self.auto_refresh_team_view)
            self.auto_refresh_timer.start(30000)  # 30 seconds
            self._last_refresh_time = 0  # Track last file modification time

    def is_admin(self):
        """
        Whether this person may see the whole team's grid and correct punches.

        Decided by access.json, not by a list in this file. The list that was
        here (supervisor, developer, admin) left HR out, so the people who
        actually keep the attendance record could not see it.
        """
        from slate.core.domain.access import can
        return can(self.roles, "view_team_attendance")

    def _notify(self, message: str, level: str = "info", details: str = ""):
        """Use host feedback API when available, fallback to dialogs."""
        host = self.window()
        if host and hasattr(host, "show_feedback"):
            try:
                host.show_feedback(message=message, level=level, duration=4500, details=details)
                return
            except Exception:
                pass

        if level == "error" and details:
            QMessageBox.critical(self, "Error", details)
        elif level == "warning":
            QMessageBox.warning(self, "Warning", message)
        else:
            QMessageBox.information(self, "Info", message)


    def _create_elevated_shadow(self):
        """Helper: Multi-layer elevated shadow"""
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        from PySide6.QtGui import QColor
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(40)
        shadow.setXOffset(0)
        shadow.setYOffset(6)
        shadow.setColor(QColor(0, 0, 0, 120))
        return shadow

    def _create_shadow(self, blur=20, offset_y=4, color_alpha=80):
        """Helper: Configurable shadow"""
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        from PySide6.QtGui import QColor
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(blur)
        shadow.setXOffset(0)
        shadow.setYOffset(offset_y)
        shadow.setColor(QColor(0, 0, 0, color_alpha))
        return shadow

    def _create_stat_card(self, label, value, color):
        """Helper: Create ULTRA COMPACT stat card"""
        from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel

        card = QWidget()
        card.setMinimumSize(100, 70) # Much smaller
        card.setStyleSheet("""
            QWidget {
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 12px;
            }
        """)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl = QLabel(label)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("""
            color: rgba(255, 255, 255, 0.5);
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            background: transparent; border: none;
        """)

        val = QLabel(value)
        val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        val.setStyleSheet(f"""
            color: {color};
            font-size: 20px;
            font-weight: 700;
            background: transparent; border: none;
        """)
        val.setObjectName(f"stat_value_{label.lower()}")

        # Store reference for updates
        setattr(self, f"lbl_monthly_{label.lower()}_value", val)

        layout.addWidget(val)
        layout.addWidget(lbl)

        return card

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # MODERN CSS VARIABLES
        self.setStyleSheet("""
            QWidget {
                font-family: "Segoe UI", sans-serif;
            }
            QFrame#PersonalCard {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #16323A, stop:1 #16323A);
                border: 1px solid #16323A;
                border-radius: 16px;
            }
            QFrame#CompactLegend {
                background: rgba(0, 0, 0, 0.3);
                border-radius: 20px;
                border: 1px solid rgba(255,255,255,0.05);
            }
        """)

        # 1. COMPACT HERO SECTION (Horizontal)
        # ------------------------------------
        self.personal_card = QFrame()
        self.personal_card.setObjectName("PersonalCard")
        self.personal_card.setMinimumHeight(110) # Fixed compact height
        self.personal_card.setGraphicsEffect(self._create_shadow(blur=30, offset_y=5, color_alpha=50))

        pc_layout = QHBoxLayout(self.personal_card)
        pc_layout.setContentsMargins(24, 0, 24, 0)
        pc_layout.setSpacing(30)

        # A. LEFT: Profile & Status
        left_box = QWidget()
        left_box.setStyleSheet("background: transparent;")
        lb_layout = QVBoxLayout(left_box)
        lb_layout.setSpacing(4)
        lb_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        name_row = QHBoxLayout()
        name_row.setSpacing(10)

        # Avatar Placeholder (Circle)
        avatar = QLabel(self.display_name[:1])
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #3EA8BF, stop:1 #3EA8BF);
            color: white; font-weight: bold; border-radius: 16px; font-size: 14px;
            border: 1px solid rgba(255,255,255,0.2);
        """)

        self.lbl_welcome = QLabel(self.display_name)
        self.lbl_welcome.setStyleSheet("font-size: 16px; font-weight: 700; color: #6BA4C9; background: transparent;")

        name_row.addWidget(avatar)
        name_row.addWidget(self.lbl_welcome)
        name_row.addStretch()

        self.lbl_status = QLabel("Checking...")
        self.lbl_status.setStyleSheet("font-size: 12px; font-weight: 500; color: #6BA4C9; margin-left: 42px; background: transparent;")

        lb_layout.addLayout(name_row)
        lb_layout.addWidget(self.lbl_status)

        # B. CENTER: Stats Grid
        center_box = QWidget()
        center_box.setStyleSheet("background: transparent;")
        cb_layout = QHBoxLayout(center_box)
        cb_layout.setSpacing(12)
        cb_layout.setContentsMargins(0, 10, 0, 10)

        self.lbl_monthly_present = self._create_stat_card("Present", "-", "#5FBF8F")
        self.lbl_monthly_late = self._create_stat_card("Late", "-", "#D9635F")
        self.lbl_monthly_hours = self._create_stat_card("Hours", "-", "#3EA8BF")
        self.lbl_monthly_wfh = self._create_stat_card("WFH", "-", "#3EA8BF")

        cb_layout.addWidget(self.lbl_monthly_present)
        cb_layout.addWidget(self.lbl_monthly_late)
        cb_layout.addWidget(self.lbl_monthly_hours)
        cb_layout.addWidget(self.lbl_monthly_wfh)

        # C. RIGHT: Actions
        right_box = QWidget()
        right_box.setStyleSheet("background: transparent;")
        rb_layout = QHBoxLayout(right_box)
        rb_layout.setSpacing(12)
        rb_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # WFH Toggle (Compact)
        self.chk_wfh_box = QPushButton("WFH")
        self.chk_wfh_box.setCheckable(True)
        self.chk_wfh_box.setMinimumSize(80, 40)
        self.chk_wfh_box.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.05); color: #6BA4C9;
                border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px; font-weight: 600;
            }
            QPushButton:checked {
                background: #3EA8BF; color: white; border-color: #3EA8BF;
            }
        """)

        # Punch Buttons
        btn_in = QPushButton("PUNCH IN")
        btn_in.setMinimumSize(110, 40)
        btn_in.setStyleSheet("""
            QPushButton {
                background: #5FBF8F; color: white; border: none; border-radius: 8px; font-weight: 700; font-size: 12px;
            }
            QPushButton:hover { background: #5FBF8F; }
        """)
        btn_in.clicked.connect(lambda: self.manual_punch("in"))

        btn_out = QPushButton("PUNCH OUT")
        btn_out.setMinimumSize(110, 40)
        btn_out.setStyleSheet("""
            QPushButton {
                background: #D9635F; color: white; border: none; border-radius: 8px; font-weight: 700; font-size: 12px;
            }
            QPushButton:hover { background: #D9635F; }
        """)
        btn_out.clicked.connect(lambda: self.manual_punch("out"))

        rb_layout.addWidget(self.chk_wfh_box)
        rb_layout.addWidget(btn_in)
        rb_layout.addWidget(btn_out)

        pc_layout.addWidget(left_box)
        pc_layout.addStretch()
        pc_layout.addWidget(center_box)
        pc_layout.addStretch()
        pc_layout.addWidget(right_box)

        layout.addWidget(self.personal_card)

        # 2. SEPARATOR + LEGEND (Compact)
        # -------------------------------
        mid_row = QHBoxLayout()
        mid_row.setContentsMargins(5, 0, 5, 0)

        # Streak Badge (Moved out of header to save space, or just put it here)
        self.lbl_streak = QLabel("Streak: 0")
        self.lbl_streak.setStyleSheet("color: #D9A441; font-weight: 700; font-size: 13px;")
        mid_row.addWidget(self.lbl_streak)

        mid_row.addStretch()

        # Compact Legend
        legend_frame = QFrame()
        legend_frame.setObjectName("CompactLegend")
        legend_frame.setMinimumHeight(36)
        lf_layout = QHBoxLayout(legend_frame)
        lf_layout.setContentsMargins(15, 0, 15, 0)
        lf_layout.setSpacing(20)

        for color, text in [("#5FBF8F", "Punch"), ("#D9A441", "Auto"), ("#D9A441", "Working"), ("#D9635F", "Late")]:
            item = QWidget()
            item.setStyleSheet("background: transparent;")
            il = QHBoxLayout(item); il.setContentsMargins(0,0,0,0); il.setSpacing(6)
            dot = QLabel("●"); dot.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent;")
            lbl = QLabel(text); lbl.setStyleSheet("color: #6BA4C9; font-size: 11px; font-weight: 600; background: transparent;")
            il.addWidget(dot); il.addWidget(lbl)
            lf_layout.addWidget(item)

        mid_row.addWidget(legend_frame)
        layout.addLayout(mid_row)

        # 3. SPLIT CONTENT
        # ----------------
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: #2C2C34; }")

        # My History Table
        self.my_table = QTableWidget()
        self.setup_table(self.my_table)
        splitter.addWidget(self.my_table)

        # Admin View
        if self.is_admin() and self.sync_enabled:
            admin_widget = QWidget()
            aw_layout = QVBoxLayout(admin_widget)
            aw_layout.setContentsMargins(0, 10, 0, 0)
            aw_layout.setSpacing(10)

            # Integrated Admin Header
            ah_layout = QHBoxLayout()
            ah_layout.setContentsMargins(5, 0, 5, 0)

            lbl_adm = QLabel("TEAM OVERVIEW")
            lbl_adm.setStyleSheet("color: #6BA4C9; font-weight: 700; font-size: 13px; letter-spacing: 0.5px;")

            self.lbl_last_refresh = QLabel("Updated: --:--")
            self.lbl_last_refresh.setStyleSheet("color: #6BA4C9; font-size: 11px;")

            ah_layout.addWidget(lbl_adm)
            ah_layout.addWidget(self.lbl_last_refresh)
            ah_layout.addStretch()

            # Controls
            self.combo_month = QComboBox()
            self.combo_month.addItems([calendar.month_name[i] for i in range(1, 13)])
            self.combo_month.setCurrentIndex(datetime.now().month - 1)
            self.combo_month.setMinimumSize(100, 30)

            self.spin_year = QSpinBox()
            # Relative to now. A fixed 2020-2030 is a date this screen stops
            # working on, written five years before anybody would notice.
            this_year = datetime.now().year
            self.spin_year.setRange(this_year - 6, this_year + 2)
            self.spin_year.setValue(this_year)
            self.spin_year.setMinimumSize(100, 30)

            btn_ref = QPushButton("REFRESH")
            btn_ref.setMinimumSize(80, 30)
            btn_ref.clicked.connect(self.refresh_team_view)

            btn_exp = QPushButton("EXPORT")
            btn_exp.setMinimumSize(80, 30)
            btn_exp.clicked.connect(self.export_csv)

            # The holidays this grid shades are HR's to keep, and this is where
            # a wrong one gets noticed - by somebody looking at the month, not
            # by somebody in the Leave tab, which is the only place the editor
            # could be reached from.
            self.btn_holidays = None
            if can(self.roles, "manage_leave"):
                self.btn_holidays = QPushButton("HOLIDAYS")
                self.btn_holidays.setMinimumSize(95, 30)
                self.btn_holidays.setToolTip(
                    "The studio's public holidays: the days shaded here, and "
                    "the days every leave request is charged against.")
                self.btn_holidays.clicked.connect(self.edit_holidays)

            # Common Control Style
            ctrl_style = """
                background: #16323A; color: #B4B1AA; border: 1px solid #2C2C34; border-radius: 6px; font-size: 11px; font-weight: 600;
            """
            controls = [self.combo_month, self.spin_year, btn_ref, btn_exp]
            if self.btn_holidays is not None:
                controls.append(self.btn_holidays)
            for w in controls:
                w.setStyleSheet(ctrl_style)

            ah_layout.addWidget(self.combo_month)
            ah_layout.addWidget(self.spin_year)
            ah_layout.addWidget(btn_ref)
            ah_layout.addWidget(btn_exp)
            if self.btn_holidays is not None:
                ah_layout.addWidget(self.btn_holidays)

            aw_layout.addLayout(ah_layout)

            self.team_table = QTableWidget()
            self.team_table.cellDoubleClicked.connect(self.on_cell_double_click)
            aw_layout.addWidget(self.team_table)
            splitter.addWidget(admin_widget)
        elif self.is_admin() and not self.sync_enabled:
            sync_notice = QLabel(
                "LOCAL MODE: team attendance sync is unavailable.\n"
                "Reconnect to central PostgreSQL mode to use team overview and export."
            )
            sync_notice.setWordWrap(True)
            sync_notice.setStyleSheet(
                "color: #D9A441; background: #16323A; border: 1px solid #2C2C34; "
                "border-radius: 8px; padding: 10px; font-weight: 600;"
            )
            layout.addWidget(sync_notice)

        layout.addWidget(splitter)


    def setup_table(self, table):
        # Premium Table CSS
        table.setStyleSheet("""
            QTableWidget {
                background: #16161A;
                border: 1px solid #26262D;
                gridline-color: #1D1D22;
                font-family: "Segoe UI";
                font-size: 13px;
                selection-background-color: #26262D;
                selection-color: white;
            }
            QHeaderView::section {
                background: #26262D;
                color: #B4B1AA;
                padding: 6px;
                border: none;
                font-weight: bold;
                text-transform: uppercase;
                font-size: 11px;
            }
            QTableWidget::item { padding: 4px; }
        """)

        table.horizontalHeader().setStretchLastSection(True)
        # Edit Triggers: Personal table is ReadOnly. Team table (Admin) handles DoubleClick manually.
        table.setEditTriggers(QTableWidget.NoEditTriggers)

        # Column Resizing

        if table == self.my_table:
            table.setColumnWidth(0, 120) # Date
            table.setColumnWidth(1, 100) # In
            table.setColumnWidth(2, 100) # Out
            table.setColumnWidth(3, 100) # Hours
            table.setColumnWidth(4, 150) # Status


    def manual_punch(self, action):
        """Perform manual attendance action."""
        try:
            # Metadata for WFH
            meta = {}
            if action == 'in' and self.chk_wfh_box.isChecked():
                meta['wfh'] = True

            self.attendance.log_action(self.username, action, metadata=meta)
            self.refresh_personal_view()
            if self.is_admin() and self.sync_enabled:
                self.refresh_team_view()

            msg = "Logged IN" if action == "in" else "Logged OUT"
            desc = f"Successfully {msg} at {datetime.now().strftime('%H:%M')}"
            self._notify(desc, "success")
        except ValueError as ve:
            # Validation error (e.g., duplicate punch, time validation)
            self._notify(str(ve), "warning")
        except Exception as e:
            self._notify("Failed to punch attendance.", "error", details=f"Failed to punch: {e}")

    def edit_holidays(self):
        """
        Open the studio's holiday calendar, on the year being looked at.

        The calendar drives three things that all read as the software being
        wrong when it is out of date: which days are shaded here, whether a day
        counts as an absence, and what a leave request costs. It opens on the
        year in the picker because that is the year somebody has just noticed
        something wrong in.
        """
        from slate.gui.tabs.leave_admin import HolidayCalendarDialog

        dialog = HolidayCalendarDialog(parent=self, year=self.spin_year.value())
        dialog.exec()

        # The grid shades holidays from a cache, so it keeps showing the old
        # calendar until that is dropped. A change nobody can see having taken
        # effect gets made twice.
        self._holiday_cache = {}
        try:
            self.refresh_team_view()
            self.refresh_personal_view()
        except Exception as exc:
            logging.debug("Could not refresh after editing the holidays: %s", exc)

    def _holidays(self, year: int) -> set:
        """
        The studio's public holidays for a year, for this person's location.

        Attendance used to know about Sundays and nothing else, so a public
        holiday read as an absence and counted against the person. The list is
        the same one leave is charged against - there is only one calendar.
        """
        cached = getattr(self, "_holiday_cache", None)
        if cached is None:
            cached = self._holiday_cache = {}
        location = str((self.user_data or {}).get("location") or "").strip()
        key = (year, location.lower())
        if key not in cached:
            try:
                from slate.core.infra.leave_repository import LeaveRepository
                cached[key] = LeaveRepository().holidays(year, location or None)
            except Exception as exc:
                logging.debug("Attendance could not read the holidays: %s", exc)
                cached[key] = set()
        return cached[key]

    def _is_non_working(self, day) -> bool:
        """A day nobody was expected in: a weekly off or a public holiday."""
        return not lp.is_working_day(day, self._holidays(day.year))

    def calculate_streak(self, user_log, year, month):
        return compute_streak(
            non_working=self._is_non_working,
            user_log=user_log,
            year=year,
            month=month,
            cutoff_hour=self.attendance.LATE_CUTOFF_HOUR,
            cutoff_minute=self.attendance.LATE_CUTOFF_MINUTE,
        )

    def _calculate_hours(self, in_time: str, out_time: str = "", now_ref: datetime = None) -> float:
        return compute_hours(in_time=in_time, out_time=out_time, now_ref=now_ref)


    # __init__ calls this, so without the decorator an unreachable database
    # does not produce an empty tab - it produces no tab at all, and the
    # error surfaces as a failure to open rather than as an explanation.
    @on_database_error
    def refresh_personal_view(self):
        """Load current month logs for self."""
        now = datetime.now()
        data = self.attendance.get_full_month_data(now.year, now.month) # Ensure defaults
        user_log = data.get(self.username.lower(), {})

        # Update Status Label
        today_key = f"{now.day:02d}"
        today_entry = user_log.get(today_key, {})
        # A record can carry an empty 'out' rather than no 'out' at all, and
        # .get(key, default) only falls back when the key is missing - so an
        # empty string sailed past the checks below and the banner read
        # "Punched Out ()" with nothing between the brackets.
        t_in = (today_entry.get('in') or '').strip() or '--:--'
        t_out = (today_entry.get('out') or '').strip() or '--:--'

        status_color = "#B4B1AA"
        if t_in != '--:--' and t_out == '--:--':
            live_hours = self._calculate_hours(t_in, "", now)
            status_text = f"Working {live_hours:.1f}h (In: {t_in})"
            status_color = "#5FBF8F"
            if today_entry.get('wfh'): status_text += "  WFH"
        elif t_in != '--:--' and t_out != '--:--':
            # Check if auto-logout
            if today_entry.get('auto_logout'):
                status_text = f"Punched Out ({t_out}) - auto"
            else:
                status_text = f"Punched Out ({t_out})"
            status_color = "#D9635F"
        else:
            status_text = "Not Checked In Today"


        self.lbl_status.setText(status_text)
        self.lbl_status.setStyleSheet(f"font-size: 20px; font-weight: bold; color: {status_color};")


        # Streak
        streak = self.calculate_streak(user_log, now.year, now.month)
        self.lbl_streak.setText(f"{streak} day streak")

        # MONTHLY STATS CALCULATION
        monthly_present = 0
        monthly_late = 0
        monthly_hours = 0.0
        monthly_wfh = 0

        for day_key, entry in user_log.items():
            t_in = entry.get('in')
            if t_in:
                monthly_present += 1
                if entry.get('wfh'):
                    monthly_wfh += 1

                # Check late. Coming in after the cutoff on a day nobody was
                # expected at all is not lateness - it is somebody helping out
                # on their day off, and counting it against them is the fastest
                # way to make people stop.
                try:
                    h, m = map(int, t_in.split(':'))
                    late_hour, late_minute = lp.late_cutoff()
                    after_cutoff = h > late_hour or (h == late_hour and m > late_minute)
                    working_day = True
                    try:
                        working_day = not self._is_non_working(
                            datetime(now.year, now.month, int(day_key)).date())
                    except (ValueError, TypeError):
                        pass
                    if after_cutoff and working_day:
                        monthly_late += 1
                except (ValueError, AttributeError):
                    pass  # Invalid time format, skip late check

                # Calculate hours
                t_out = entry.get('out')
                if t_out:
                    monthly_hours += self._calculate_hours(t_in, t_out, now)
                elif day_key == today_key:
                    # Include today's running session even before punch-out.
                    monthly_hours += self._calculate_hours(t_in, "", now)

        # Update monthly stats labels
        self.lbl_monthly_present_value.setText(f"{monthly_present}")
        self.lbl_monthly_late_value.setText(f"{monthly_late}")
        self.lbl_monthly_hours_value.setText(f"{monthly_hours:.1f}")
        self.lbl_monthly_wfh_value.setText(f"{monthly_wfh}")

        # Fill Table
        self.my_table.clear()
        self.my_table.setColumnCount(6)
        self.my_table.setHorizontalHeaderLabels(["Date", "Punch In", "Punch Out", "Total Hours", "Status", "Notes"])


        days_in_month = calendar.monthrange(now.year, now.month)[1]
        self.my_table.setRowCount(days_in_month)

        for i in range(1, days_in_month + 1):
            day_key = f"{i:02d}"
            date_str = f"{now.year}-{now.month:02d}-{i:02d}"

            entry = user_log.get(day_key, {})
            t_in = entry.get('in', '')
            t_out = entry.get('out', '')
            is_wfh = entry.get('wfh', False)

            # Duration & Overtime
            duration = ""
            overtime_txt = ""
            status_lite = ""
            bg_mod = None

            if t_in:
                # Late Check
                try:
                    ih, im = map(int, t_in.split(':'))
                    cutoff_hour = self.attendance.LATE_CUTOFF_HOUR
                    cutoff_min = self.attendance.LATE_CUTOFF_MINUTE
                    if ih > cutoff_hour or (ih == cutoff_hour and im > cutoff_min):
                        status_lite = "LATE"
                        bg_mod = QColor("#3A2F19")
                except (ValueError, AttributeError):
                    pass  # Invalid time format, skip late status

                if is_wfh:
                    status_lite += " / WFH"

            if t_in and t_out:
                hours = self._calculate_hours(t_in, t_out, now)
                duration = f"{hours:.1f}h"
                standard = lp.standard_day_hours()
                if hours > standard:
                    ot = hours - standard
                    overtime_txt = f"+{ot:.1f}h OT"
                    bg_mod = QColor("#1B3A2C") # Green tint for hard work
            elif t_in and not t_out and i == now.day:
                # Live duration for today's active session.
                hours = self._calculate_hours(t_in, "", now)
                duration = f"{hours:.1f}h"
                if "WORKING" not in status_lite:
                    status_lite = f"{status_lite} | WORKING ⏱".strip(" |")
                if not bg_mod:
                    bg_mod = QColor("#3A2F19")  # Yellow tint (currently working)

            # Highlight
            row_color = QColor("#1D1D22")
            dt = datetime(now.year, now.month, i)
            # A public holiday is said out loud, the same way a weekend is.
            # Left unsaid it read as an absence, and the row looked like
            # somebody who had not turned up.
            if dt.date() in self._holidays(now.year):
                row_color = QColor("#1D1D22")
                if not t_in: status_lite = "HOLIDAY"
            elif lp.is_weekly_off(dt.date()):
                row_color = QColor("#1D1D22") # Weekend
                if not t_in: status_lite = "WEEKEND"

            if bg_mod: row_color = bg_mod

            # CURRENT DAY HIGHLIGHT (Border)
            is_today = (i == now.day and now.month == datetime.now().month and now.year == datetime.now().year)

            items = [date_str, t_in, t_out, duration, status_lite, overtime_txt]
            for c, txt in enumerate(items):
                it = QTableWidgetItem(txt)
                it.setBackground(row_color)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if is_today:
                    it.setFont(QFont("Segoe UI", 9, QFont.Bold))
                    it.setForeground(QColor("#3EA8BF")) # Cyan Text for Today
                self.my_table.setItem(i-1, c, it)



    @on_database_error
    def refresh_team_view(self):
        """Admin Grid - Now with Stats Columns (P, L, OT, WFH)."""
        year = self.spin_year.value()
        month = self.combo_month.currentIndex() + 1
        days = calendar.monthrange(year, month)[1]
        now_dt = datetime.now()

        # Update refresh timestamp
        self.lbl_last_refresh.setText(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")

        users = self.user_manager.get_all_users()
        user_ids = list(users.keys())
        self._team_row_user_ids = list(user_ids)
        data = self.attendance.get_full_month_data(year, month)

        # Headers: Name + Stats + Days
        # Stats: [Present, Late, Hours, WFH]
        files_cols = ["Total\nDays", "Late\n⚠", "Total\nHrs", "WFH\n🏠"]
        day_cols = []
        weekend_indices = []
        holidays = self._holidays(year)
        for i in range(1, days + 1):
            dt = datetime(year, month, i)
            day_str = f"{i:02d}\n{dt.strftime('%a')}"
            day_cols.append(day_str)
            # Shaded the same as a weekend, because for the purpose of reading
            # this grid they are the same thing: nobody was expected in.
            if lp.is_weekly_off(dt.date()) or dt.date() in holidays:
                weekend_indices.append(i - 1)

        total_cols = len(files_cols) + len(day_cols)

        self.team_table.clear()
        self.team_table.setColumnCount(total_cols)
        self.team_table.setHorizontalHeaderLabels(files_cols + day_cols)

        self.team_table.setRowCount(len(user_ids))
        self.team_table.setVerticalHeaderLabels([users[u].get('display_name',u) for u in user_ids])

        # Styling
        self.team_table.setStyleSheet("""
            QTableWidget { background: #16161A; border: 1px solid #26262D; gridline-color: #1D1D22; font-family: "Segoe UI"; font-size: 11px; }
            QHeaderView::section { background: #26262D; color: #B4B1AA; padding: 4px; font-weight: bold; border: 1px solid #26262D; }
        """)

        for r, uid in enumerate(user_ids):
            # CRITICAL FIX: Attendance data stored with lowercase user IDs
            # but user_manager returns original case (EMP0001 vs emp0001)
            user_log = data.get(uid.lower(), {})  # Convert to lowercase for lookup

            # Stats Accumulators
            p_cnt = 0
            l_cnt = 0
            h_sum = 0.0
            w_cnt = 0

            # 1. Fill Day Columns First (to calc stats)
            for d in range(1, days + 1):
                col_idx = len(files_cols) + (d - 1)
                day_key = f"{d:02d}"

                item = QTableWidgetItem("")
                # Weekend BG default
                bg = QColor("#1D1D22")
                if (d-1) in weekend_indices: bg = QColor("#1D1D22")

                if day_key in user_log:
                    info = user_log[day_key]
                    t_in = info.get("in","")
                    t_out = info.get("out","")
                    wfh = info.get("wfh", False)

                    lbl = ""
                    if t_in:
                        lbl = t_in
                        p_cnt += 1
                        if wfh: w_cnt += 1

                        # Late?
                        try:
                            hh, mm = map(int, t_in.split(':'))
                            cutoff_hour = self.attendance.LATE_CUTOFF_HOUR
                            cutoff_min = self.attendance.LATE_CUTOFF_MINUTE
                            if hh > cutoff_hour or (hh == cutoff_hour and mm > cutoff_min):
                                l_cnt += 1
                                lbl += " (late)"
                                bg = QColor("#3A1F1E") # Dark Red tint
                        except (ValueError, AttributeError):
                            pass  # Invalid time format

                        # Hours
                        if t_out:
                            lbl += f"\n{t_out}"

                            # Check if auto-logout
                            if info.get('auto_logout'):
                                lbl += " ⏰"  # Clock emoji for auto-logout
                                bg = QColor("#3A2F19")  # Orange tint for auto
                            else:
                                bg = QColor("#1B3A2C")  # Green tint for manual

                            h_sum += self._calculate_hours(t_in, t_out, now_dt)
                        else:
                             bg = QColor("#3A2F19") # Yellow tint (working)
                             if year == now_dt.year and month == now_dt.month and d == now_dt.day:
                                 # Count live running hours for active session.
                                 h_sum += self._calculate_hours(t_in, "", now_dt)

                    item.setText(lbl)

                item.setBackground(bg)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.team_table.setItem(r, col_idx, item)

            # 2. Fill Stats Columns
            # Present
            it_p = QTableWidgetItem(str(p_cnt)); it_p.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            it_p.setForeground(QColor("#5FBF8F")); it_p.setBackground(QColor("#1B3A2C"))
            self.team_table.setItem(r, 0, it_p)

            # Late
            it_l = QTableWidgetItem(str(l_cnt)); it_l.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if l_cnt > 0: it_l.setForeground(QColor("#D9635F")); it_l.setBackground(QColor("#3A1F1E"))
            self.team_table.setItem(r, 1, it_l)

            # Hours
            it_h = QTableWidgetItem(f"{h_sum:.1f}"); it_h.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.team_table.setItem(r, 2, it_h)

            # WFH
            it_w = QTableWidgetItem(str(w_cnt)); it_w.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if w_cnt > 0: it_w.setForeground(QColor("#3EA8BF"))
            self.team_table.setItem(r, 3, it_w)

        self.team_table.resizeRowsToContents()
        self.team_table.horizontalHeader().setDefaultSectionSize(65)
        self.team_table.setColumnWidth(0, 50)
        self.team_table.setColumnWidth(1, 40)
        self.team_table.setColumnWidth(2, 50)
        self.team_table.setColumnWidth(3, 40)

    @on_database_error
    def auto_refresh_team_view(self):
        """
        Auto-refresh Team Overview from Database.
        """
        if not self.is_admin():
            return

        # Simply refresh from DB (Postgres handle concurrency well)
        # No file check needed as we are using a centralized DB now.
        try:
            logging.debug("Auto-refreshing Team Attendance...")
            self.refresh_team_view()
        except Exception as e:
            logging.exception(f"Auto-refresh failed: {e}")



    def on_cell_double_click(self, row, col):
        """Admin Edit: Open Dialog to change In/Out times."""
        if not self.is_admin():
            return

        # 1. Check if it's a valid day column (skip Stats cols)
        # Stats cols are 0..3 (Present, Late, Hrs, WFH)
        # Day 1 starts at col 4
        if col < 4: return

        day_idx = col - 4 + 1 # 1-based day
        year = self.spin_year.value()
        month = self.combo_month.currentIndex() + 1
        days_in_month = calendar.monthrange(year, month)[1]
        if day_idx < 1 or day_idx > days_in_month:
            return

        # Do not allow editing future dates.
        target_date = datetime(year, month, day_idx).date()
        if target_date > datetime.now().date():
            self._notify("Cannot edit attendance for a future date.", "warning")
            return

        # Get User ID
        users = self.user_manager.get_all_users()
        row_user_ids = self._team_row_user_ids or list(users.keys())
        if row >= len(row_user_ids):
            return
        uid = row_user_ids[row]
        if uid not in users:
            self._notify("Selected user is not available for editing.", "warning")
            return
        u_name = users[uid].get('display_name', uid)

        # Get Current Data
        item = self.team_table.item(row, col)
        curr_text = item.text() if item else ""
        t_in = ""
        t_out = ""
        time_extract = re.compile(r'([01]?[0-9]|2[0-3]):[0-5][0-9]')

        def _extract_time(raw_value: str) -> str:
            if not raw_value:
                return ""
            match = time_extract.search(raw_value)
            return match.group(0) if match else ""

        if "\n" in curr_text:
            parts = curr_text.split("\n")
            t_in = _extract_time(parts[0])
            t_out = _extract_time(parts[1] if len(parts) > 1 else "")
        else:
            t_in = _extract_time(curr_text)

        self.show_edit_dialog(uid, u_name, year, month, day_idx, t_in, t_out)

    def show_edit_dialog(self, uid, name, year, month, day, t_in, t_out):
        d = QDialog(self, Qt.WindowCloseButtonHint)
        d.setWindowTitle("Edit Punch")
        d.setMinimumSize(300, 200)

        layout = QVBoxLayout(d)

        # Header
        lbl = QLabel(f"Editing: <b>{name}</b><br>Date: {year}-{month:02d}-{day:02d}")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)

        # Form
        c_frame = QFrame(); c_frame.setStyleSheet("background: #1D1D22; border-radius: 6px; padding: 10px;")
        fl = QFormLayout(c_frame)

        e_in = QLineEdit(t_in); e_in.setPlaceholderText("HH:MM")
        e_in.setStyleSheet("background: #26262D; color: white; border: 1px solid #2C2C34; padding: 4px;")

        e_out = QLineEdit(t_out); e_out.setPlaceholderText("HH:MM")
        e_out.setStyleSheet("background: #26262D; color: white; border: 1px solid #2C2C34; padding: 4px;")

        fl.addRow("In Time:", e_in)
        fl.addRow("Out Time:", e_out)
        layout.addWidget(c_frame)

        # Buttons
        h_btn = QHBoxLayout()
        btn_save = QPushButton("SAVE"); btn_save.setStyleSheet("background: #3EA8BF; font-weight: bold; color: black; padding: 8px;")
        btn_save.clicked.connect(d.accept)

        btn_cancel = QPushButton("CANCEL"); btn_cancel.setStyleSheet("background: #2C2C34; color: #B4B1AA; padding: 8px;")
        btn_cancel.clicked.connect(d.reject)

        h_btn.addWidget(btn_cancel)
        h_btn.addWidget(btn_save)
        layout.addLayout(h_btn)

        if d.exec() == QDialog.DialogCode.Accepted:
            new_in = e_in.text().strip()
            new_out = e_out.text().strip()

            # Time validation using regex
            time_pattern = re.compile(r'^([01]?[0-9]|2[0-3]):[0-5][0-9]$')

            # Validate In Time (if provided)
            if new_in and not time_pattern.match(new_in):
                self._notify(
                    "Invalid In Time format. Please use HH:MM (00:00 to 23:59).",
                    "error",
                    details=new_in,
                )
                return

            # Validate Out Time (if provided)
            if new_out and not time_pattern.match(new_out):
                self._notify(
                    "Invalid Out Time format. Please use HH:MM (00:00 to 23:59).",
                    "error",
                    details=new_out,
                )
                return

            success, msg = self.attendance.update_record(uid, year, month, day, new_in, new_out)
            if success:
                self.refresh_team_view()
                self.status_bar_msg("Updated Record")
            else:
                self._notify(msg, "error")

    def status_bar_msg(self, msg):
        # Helper to find parent window status bar
        try:
            self.window().statusBar().showMessage(msg, 3000)
        except (AttributeError, RuntimeError):
            pass  # Status bar not available



    def export_csv(self):
        """Export current month data for ALL users to professionally formatted Excel (async)."""
        year = self.spin_year.value()
        month = self.combo_month.currentIndex() + 1

        # Check for openpyxl before showing dialog
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self._notify(
                "openpyxl is required for Excel export. Install with: pip install openpyxl",
                "error",
            )
            return

        # File picker stays on the main thread
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Attendance",
            f"Attendance_{year}_{month:02d}.xlsx",
            "Excel Files (*.xlsx)"
        )
        if not path:
            return

        # Gather data on main thread (lightweight dict reads)
        users = self.user_manager.get_all_users()
        data = self.attendance.get_full_month_data(year, month)
        late_hour = getattr(self.attendance, 'LATE_CUTOFF_HOUR', 10)
        late_min = getattr(self.attendance, 'LATE_CUTOFF_MINUTE', 30)

        # Launch background worker
        self._cleanup_export_worker()
        self._export_worker = ExcelExportWorker(path, year, month, users, data, late_hour, late_min)
        self._export_worker.finished_export.connect(self._on_export_finished)
        self._export_worker.finished.connect(self._on_export_worker_done)
        self._export_worker.finished.connect(self._export_worker.deleteLater)
        self._export_worker.start()
        self.status_bar_msg("Exporting attendance report...")

    def _on_export_finished(self, success, message):
        """Handle export worker completion."""
        if self.sender() is not self._export_worker:
            return
        if success:
            self._notify(message, "success")
        else:
            self._notify(message, "error")
        self.status_bar_msg("Ready")

    def _on_export_worker_done(self):
        if self.sender() is self._export_worker:
            self._export_worker = None

    def _cleanup_export_worker(self, timeout_ms: int = 2000):
        worker = self._export_worker
        if worker is None:
            return
        if worker.isRunning():
            worker.requestInterruption()
            worker.wait(timeout_ms)
        try:
            worker.deleteLater()
        except RuntimeError as exc:
            logging.debug("Attendance export worker deleteLater skipped: %s", exc)
        self._export_worker = None


    def closeEvent(self, event):
        if hasattr(self, "personal_refresh_timer") and self.personal_refresh_timer.isActive():
            self.personal_refresh_timer.stop()
        if hasattr(self, "auto_refresh_timer") and self.auto_refresh_timer.isActive():
            self.auto_refresh_timer.stop()
        self._cleanup_export_worker(timeout_ms=2000)
        super().closeEvent(event)
