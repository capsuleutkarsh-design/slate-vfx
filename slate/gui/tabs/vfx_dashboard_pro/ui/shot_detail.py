from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QTextEdit, QComboBox, QPushButton, 
                             QFormLayout, QTabWidget, QDateEdit, QScrollArea, QFrame, 
                             QGroupBox, QGridLayout, QCheckBox, QSizePolicy, QDoubleSpinBox, QAbstractSpinBox, QToolButton,
                             QMessageBox, QFileDialog)
from PySide6.QtCore import Qt, QDate, Signal, QSize, QTimer
from PySide6.QtGui import QPixmap, QColor, QTextOption, QIcon
from ..models.shot_model import Shot
from pathlib import Path
import hashlib
import json
import logging
import os
import subprocess
import sys
from glob import glob
from shutil import which
from slate.core.infra.design_tokens import ColorTokens as C, SpacingTokens as S, RadiusTokens as R
from slate.core.infra.global_config import GlobalConfig
from slate.core.system.adaptation_engine import system_engine
from slate.utils.resource_manager import ResourcePathManager

class ShotDetailWidget(QWidget):
    close_requested = Signal()
    search_requested = Signal(str)
    save_requested = Signal(object) # Emits the modified Shot object
    quick_look_requested = Signal(object) # Emits Shot object to open QuickLook player
    rv_review_requested = Signal(object)  # Emits Shot object to review in OpenRV
    _DCC_CONFIG_KEYS = {
        "nuke": "nuke_path",
        "after_effects": "after_effects_path",
        "premiere": "premiere_path",
        "blender": "blender_path",
        "rv": "rv_path",
    }

    def __init__(
        self,
        shot: Shot,
        user_role: str,
        project_manager=None,
        all_shots=None,
        all_users=None,
        user_data: dict = None,
        current_project_code: str = "",
        inherit_app_theme: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.shot = shot
        self.inherit_app_theme = bool(inherit_app_theme)
        
        # MULTI-ROLE FIX: Handle both string and array
        if isinstance(user_role, list):
            self.user_role = user_role[0].lower() if user_role else "artist"
            self.user_roles = [r.lower() for r in user_role]
        else:
            self.user_role = user_role.lower() if user_role else "artist"
            self.user_roles = [user_role.lower() if user_role else "artist"]
        
        self.project_manager = project_manager
        self.all_shots = all_shots or []
        self.all_users = all_users or []
        self.user_data = user_data or {}
        shot_project_code = str(getattr(self.shot, "project_name", "") or "").strip()
        self.current_project_code = str(current_project_code or shot_project_code or "UNKNOWN")
        self.feedback_state_path = self._get_feedback_state_path()
        self.viewer_identity_keys = self._resolve_viewer_identity_keys()
        self.feedback_seen_state = self._load_feedback_seen_state()
        self._feedback_tab_order = ["Client", "Director", "Internal"]
        self._feedback_signature_map = {}
        self._sp = system_engine.scale_px
        self.init_ui()
        self.load_data()
        self.apply_permissions()
        
    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # === HEADER ===
        header_frame = QFrame()
        header_frame.setObjectName("detailHeader")
        header_layout = QVBoxLayout(header_frame)
        header_layout.setContentsMargins(self._sp(16), self._sp(12), self._sp(16), self._sp(12))
        header_layout.setSpacing(self._sp(8))
        top_row = QHBoxLayout()
        top_row.setSpacing(self._sp(12))

        # Back Button (Styled)
        back_btn = QPushButton("Back")
        back_btn.setMinimumSize(self._sp(86, minimum=72), self._sp(32, minimum=28))
        back_btn.setMaximumHeight(self._sp(34, minimum=30))
        back_btn.setObjectName("backBtn")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(self.close_requested.emit)
        top_row.addWidget(back_btn)

        # Thumbnail with click-to-preview
        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(self._sp(100, minimum=88), self._sp(56, minimum=48))
        self.thumb_label.setObjectName("thumbnail")
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.thumb_label.setToolTip("Click to Play / Preview Renders (Space)")
        self.thumb_label.mousePressEvent = lambda ev: self.quick_look_requested.emit(self.shot)
        top_row.addWidget(self.thumb_label)

        # Title & Status
        title_area = QVBoxLayout()
        title_area.setSpacing(2)
        self.title_label = QLabel(f"{self.shot.shot_name}")
        self.title_label.setObjectName("detailTitle")
        title_area.addWidget(self.title_label)

        status_row = QHBoxLayout()
        self.status_badge = QLabel(self.shot.status)
        self.status_badge.setObjectName("statusBadge")
        self._update_status_badge_style(self.shot.status)
        status_row.addWidget(self.status_badge)
        status_row.addStretch()
        title_area.addLayout(status_row)
        top_row.addLayout(title_area, 1)
        top_row.addStretch()

        # Preview Button
        preview_btn = QPushButton("▶ Preview")
        preview_btn.setObjectName("headerBtn")
        preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        preview_btn.setToolTip("Open Quick Look Player for this shot (Spacebar)")
        preview_btn.clicked.connect(lambda: self.quick_look_requested.emit(self.shot))
        top_row.addWidget(preview_btn)

        # Review in RV: the plate and each department's render, in the player
        # the studio actually reviews in.
        rv_btn = QPushButton("Review in RV")
        rv_btn.setObjectName("headerBtn")
        rv_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rv_btn.setToolTip("Open the scan or a department render in OpenRV")
        rv_btn.clicked.connect(lambda: self.rv_review_requested.emit(self.shot))
        top_row.addWidget(rv_btn)

        # Controls (Filter)
        search_btn = QPushButton("Filter")
        search_btn.setObjectName("headerBtn")
        search_btn.clicked.connect(lambda: self.search_requested.emit(self.shot.shot_name))
        top_row.addWidget(search_btn)
        header_layout.addLayout(top_row)

        # Quick folders in their own row so they do not clip in narrow layouts.
        folder_layout = QHBoxLayout()
        folder_layout.setSpacing(self._sp(6))
        folder_layout.addStretch()
        for name, key in [("Scan", "scan"), ("Roto", "roto"), ("Prep", "prep"),
                          ("Comp", "comp"), ("DMP", "dmp"), ("Output", "output")]:
            btn = QPushButton(name)
            btn.setObjectName("folderBtn")
            btn.setMinimumWidth(self._sp(62, minimum=56))
            btn.setMaximumWidth(self._sp(88, minimum=76))
            btn.setMinimumHeight(self._sp(28, minimum=24))
            btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, k=key: self.open_folder(k))
            folder_layout.addWidget(btn)
        folder_layout.addStretch()
        header_layout.addLayout(folder_layout)

        # DCC launch row (icon-only buttons)
        dcc_layout = QHBoxLayout()
        dcc_layout.setSpacing(self._sp(8))
        dcc_layout.addStretch()
        dcc_label = QLabel("Open In:")
        dcc_label.setObjectName("dccLauncherLabel")
        dcc_layout.addWidget(dcc_label)
        dcc_layout.addWidget(self._create_dcc_button("nuke", "Nuke", "nuke.png"))
        dcc_layout.addWidget(self._create_dcc_button("natron", "Natron", "natron.png"))
        dcc_layout.addWidget(self._create_dcc_button("silhouette", "Silhouette", "silhouette.png"))
        dcc_layout.addWidget(self._create_dcc_button("after_effects", "After Effects", "after_effects.png"))
        dcc_layout.addWidget(self._create_dcc_button("premiere", "Premiere Pro", "premiere.png"))
        dcc_layout.addWidget(self._create_dcc_button("blender", "Blender", "blender.png"))
        dcc_layout.addWidget(self._create_dcc_button("rv", "RV", "rv.png"))
        dcc_layout.addStretch()
        header_layout.addLayout(dcc_layout)

        main_layout.addWidget(header_frame)
        
        # === SCROLL AREA ===
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("detailScroll")
        if not self.inherit_app_theme:
            scroll.setStyleSheet("""
                QScrollArea { border: none; background: transparent; }
                QWidget#detailContent { background: transparent; }
                /* Compact GroupBox */
                QGroupBox {
                    background-color: #26262D;
                    border: 1px solid #2C2C34;
                    border-radius: 4px;
                    margin-top: 0.6em;
                    padding-top: 4px;
                    padding-bottom: 4px;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    subcontrol-position: top left;
                    left: 10px;
                    padding: 0 5px;
                    color: #3EA8BF;
                    font-weight: bold;
                    font-size: 9pt;
                    background-color: transparent;
                }
                QLabel { color: #E8E6E1; font-size: 9pt; }
                QLineEdit, QTextEdit, QComboBox, QDateEdit {
                    background-color: #1D1D22;
                    border: 1px solid #26262D;
                    border-radius: 3px;
                    padding: 1px 4px;
                    color: #E8E6E1;
                    font-size: 8pt;
                    min-height: 18px;
                }
                QComboBox::drop-down { border: none; width: 16px; }
                QCalendarWidget QWidget { background-color: #1D1D22; color: white; }
                QCalendarWidget QToolButton { color: white; icon-size: 18px; }
                QCalendarWidget QMenu { background-color: #26262D; color: white; }
                QCalendarWidget QSpinBox { background-color: #26262D; color: white; }
                QCalendarWidget QAbstractItemView:enabled {
                    color: #E8E6E1;
                    background-color: #1D1D22;
                    selection-background-color: #3EA8BF;
                    selection-color: white;
                }
            """)
        else:
            # Keep detail panel compact even when inheriting the main app theme.
            scroll.setStyleSheet("""
                QScrollArea#detailScroll { border: none; background: transparent; }
                QWidget#detailContent { background: transparent; }
                QWidget#detailContent QGroupBox {
                    margin-top: 0.8em;
                    padding-top: 6px;
                    padding-bottom: 6px;
                }
                QWidget#detailContent QGroupBox::title {
                    padding: 2px 6px;
                    font-size: 10pt;
                }
                QWidget#detailContent QLineEdit,
                QWidget#detailContent QTextEdit,
                QWidget#detailContent QComboBox,
                QWidget#detailContent QDateEdit,
                QWidget#detailContent QDoubleSpinBox {
                    border-radius: 10px;
                    padding: 4px 8px;
                    min-height: 24px;
                }
            """)
        
        content_widget = QWidget()
        content_widget.setObjectName("detailContent")
        
        # Main Content Layout (GRID)
        content_layout = QGridLayout(content_widget)
        content_layout.setSpacing(self._sp(20))
        content_layout.setContentsMargins(self._sp(20), self._sp(20), self._sp(20), self._sp(20))
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.content_layout = content_layout
        
        # --- TOP ROW ---
        self.version_group = self._init_version_section()
        self.sow_group = self._init_sow_section()
        
        # --- MAIN WORK ROW ---
        self.attr_group = self._init_attributes_section()
        self.dept_group = self._init_departments_section()
        
        # --- FEEDBACK SECTION (Full Width) ---
        self.feedback_group = self._init_feedback_section()

        # --- VERSION HISTORY (what was sent, when, and what came back) ---
        self.versions_group = self._init_versions_section()

        # --- SECONDARY INFO (Below feedback) ---
        self.dates_group = self._init_dates_section()
        self.hero_group = self._init_hero_section()
        self._is_single_column_layout = None
        self._rebuild_content_layout(force=True)
        
        scroll.setWidget(content_widget)
        main_layout.addWidget(scroll)
        
        # === FOOTER (Save Button Only) ===
        footer = QFrame()
        footer.setObjectName("detailFooter")
        if not self.inherit_app_theme:
            footer.setStyleSheet(
                f"QFrame#detailFooter {{ background-color: {C.BG_ELEVATED}; border-top: 1px solid {C.BORDER_DEFAULT}; padding: {S.MD}px; }}"
            )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        
        footer_layout.addStretch()
        
        self.save_btn = QPushButton("Save Changes")
        self.save_btn.setObjectName("saveBtn")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setMinimumHeight(self._sp(40, minimum=34)) # Taller for easier clicking
        if not self.inherit_app_theme:
            self.save_btn.setStyleSheet("""
                QPushButton {
                    background-color: #3EA8BF;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 0 24px;
                    font-weight: bold;
                    font-size: 11pt;
                }
                QPushButton:hover { background-color: #3EA8BF; }
                QPushButton:pressed { background-color: #3EA8BF; }
                QPushButton:disabled { background-color: #2C2C34; color: #87857F; }
            """)
        self.save_btn.clicked.connect(self.save_data)
        footer_layout.addWidget(self.save_btn)
        
        main_layout.addWidget(footer)

    def _update_status_badge_style(self, status):
        status_color = {"APPROVED": "#5FBF8F", "WIP": "#3EA8BF", "RETAKE": "#D9A441", "READY": "#1B3A2C"}.get(status.upper(), "#2C2C34")
        self.status_badge.setStyleSheet(f"background-color: {status_color}; color: white; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 9pt;")

    def _init_version_section(self):
        group = QGroupBox("Version Info")
        layout = QGridLayout(group)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop) # Fix vertical spread
        layout.setColumnStretch(1, 1)
        
        layout.addWidget(QLabel("Previous:"), 0, 0)
        self.prev_version_label = QLabel(self.shot.prev_version or "-")
        self.prev_version_label.setObjectName("versionLabelPrev")
        layout.addWidget(self.prev_version_label, 0, 1)
        
        layout.addWidget(QLabel("Current:"), 1, 0)
        self.curr_version_edit = QLineEdit(self.shot.curr_version or "")
        self.curr_version_edit.setPlaceholderText("v001")
        layout.addWidget(self.curr_version_edit, 1, 1)
        
        return group

    def _init_attributes_section(self):
        group = QGroupBox("Attributes")
        layout = QFormLayout(group)
        layout.setSpacing(16)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        
        self.type_combo = QComboBox()
        self.type_combo.addItems(["", "Prep", "2D Comp", "2.5D Comp", "CG Comp", "AI Shot"])
        layout.addRow("Type:", self.type_combo)
        
        self.priority_combo = QComboBox()
        self.priority_combo.addItems(["0", "1", "2", "3"])
        layout.addRow("Priority:", self.priority_combo)
        
        self.status_combo = QComboBox()
        self.status_combo.addItems(["WIP", "APPROVED", "RETAKE", "SENT FOR REVIEW", "YTS", "OMIT", "READY"])
        self.status_combo.currentTextChanged.connect(lambda t: self._update_status_badge_style(t))
        layout.addRow("Status:", self.status_combo)
        
        self.frames_edit = QLineEdit()
        self.frames_edit.setPlaceholderText("0")
        layout.addRow("Frames:", self.frames_edit)
        
        return group

    def _init_versions_section(self):
        """Submission history for this shot."""
        from .versions_panel import VersionsPanel

        panel = VersionsPanel(
            project_code=self.current_project_code,
            shot_name=getattr(self.shot, "shot_name", ""),
            can_edit=self._can_edit_shot(),
            artists=list(self.all_users or []),
            user_name=str((self.user_data or {}).get("display_name", "")),
            parent=self,
        )
        return panel

    def _can_edit_shot(self) -> bool:
        from slate.core.domain.access import can_edit_dashboard
        roles = getattr(self, "user_roles", None) or [getattr(self, "user_role", "")]
        return can_edit_dashboard(roles)

    def _init_dates_section(self):
        group = QGroupBox("Submission Dates")
        layout = QFormLayout(group)
        
        self.mov_date_label = QLabel("-")
        self.mov_date_label.setObjectName("dateLabel")
        layout.addRow("MOV:", self.mov_date_label)
        
        self.exr_date_label = QLabel("-")
        self.exr_date_label.setObjectName("dateLabel")
        layout.addRow("EXR:", self.exr_date_label)
        
        return group

    def _init_hero_section(self):
        group = QGroupBox("Hero / Similar")
        layout = QVBoxLayout(group)
        
        self.hero_checkbox = QCheckBox("Is Hero Shot")
        self.hero_checkbox.setObjectName("heroCheck")
        layout.addWidget(self.hero_checkbox)
        
        layout.addWidget(QLabel("Linked Hero:"))
        self.similar_combo = QComboBox()
        self.similar_combo.setPlaceholderText("Select hero shot...")
        layout.addWidget(self.similar_combo)
        
        return group

    def _init_sow_section(self):
        group = QGroupBox("Scope of Work")
        layout = QVBoxLayout(group)
        self.sow_edit = QTextEdit()
        self.sow_edit.setMinimumHeight(self._sp(130, minimum=110))
        self.sow_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.sow_edit.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.sow_edit.setAcceptRichText(False)
        self.sow_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.sow_edit.setPlaceholderText("Enter scope of work...")
        self.sow_edit.textChanged.connect(self._refresh_text_heights)
        layout.addWidget(self.sow_edit)
        return group

    def _init_departments_section(self):
        group = QGroupBox("Departments")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QGridLayout(group)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop) # Fix vertical spread
        layout.setSpacing(self._sp(6))
        layout.setContentsMargins(self._sp(4), self._sp(4), self._sp(4), self._sp(4)) # Minimal margins
        # Balance spacing so artist and status stay readable with long names.
        layout.setColumnStretch(1, 5)
        layout.setColumnStretch(2, 3)
        layout.setColumnMinimumWidth(3, self._sp(70, minimum=60))
        layout.setColumnMinimumWidth(4, self._sp(96, minimum=84))
        
        headers = ["Dept", "Artist", "Status", "Bid", "Target"]
        for col, text in enumerate(headers):
            lbl = QLabel(text)
            lbl.setObjectName("gridHeader")
            layout.addWidget(lbl, 0, col)
            
        self.depts = {}
        # Rows come from slate/data/departments.json - adding a department
        # there adds a row here, with no code change.
        from slate.core.domain.departments import load_departments

        for row_idx, department in enumerate(load_departments(), start=1):
            dept = self.shot.dept(department.key)
            self._create_dept_grid_row(
                layout, row_idx, department.name, department.key, dept
            )
        
        return group

    def _create_dept_grid_row(self, layout, row, name, key, dept):
        layout.addWidget(QLabel(name), row, 0)
        layout.setRowMinimumHeight(row, self._sp(30, minimum=26))
        
        # Artist Dropdown (Replaces QLineEdit)
        artist_combo = QComboBox()
        artist_combo.setEditable(True) # Allow custom if needed, or quick search
        artist_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        artist_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        artist_combo.setMinimumContentsLength(10)
        artist_combo.setMinimumWidth(self._sp(150, minimum=130))
        artist_combo.addItem("") # Empty option
        if self.all_users: # Populate with users
            artist_combo.addItems(self.all_users)
        if artist_combo.view():
            artist_combo.view().setMinimumWidth(self._sp(220, minimum=180))
            
        # Set current artist
        if dept.artist:
            artist_combo.setCurrentText(dept.artist)
        artist_combo.setToolTip(artist_combo.currentText() or "No artist assigned")
        artist_combo.currentTextChanged.connect(
            lambda text, combo=artist_combo: combo.setToolTip(text or "No artist assigned")
        )
            
        layout.addWidget(artist_combo, row, 1)
        
        status_combo = QComboBox()
        status_combo.addItems(["", "WIP", "Done", "Approved", "Pending"])
        status_combo.setMinimumWidth(self._sp(96, minimum=84))
        if dept.status:
            status_combo.setCurrentText(dept.status)
        layout.addWidget(status_combo, row, 2)
        
        bid_spin = QDoubleSpinBox()
        bid_spin.setRange(0.0, 9999.9)
        bid_spin.setDecimals(1)
        bid_spin.setSingleStep(0.5)
        bid_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        bid_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bid_spin.setSpecialValueText("-")
        bid_spin.setMinimumWidth(self._sp(64, minimum=56))
        bid_spin.setMaximumWidth(self._sp(78, minimum=66))
        try:
            bid_spin.setValue(float(dept.bid_days or 0.0))
        except Exception:
            bid_spin.setValue(0.0)
        bid_spin.setToolTip("Bid days")
        layout.addWidget(bid_spin, row, 3)
        
        target_edit = QDateEdit()
        target_edit.setCalendarPopup(True)
        target_edit.setDisplayFormat("yyyy-MM-dd")
        target_edit.setMinimumWidth(self._sp(90, minimum=82))
        target_edit.setMaximumWidth(self._sp(104, minimum=90))
        if dept.target:
            try:
                qdate = QDate.fromString(dept.target, "yyyy-MM-dd")
                if qdate.isValid():
                    target_edit.setDate(qdate)
                else:
                    target_edit.setDate(QDate.currentDate())
            except Exception:
                target_edit.setDate(QDate.currentDate())
        else:
            target_edit.setDate(QDate.currentDate())
        layout.addWidget(target_edit, row, 4)
        
        self.depts[key] = {
            "artist_combo": artist_combo, # Renamed to standard
            "status_combo": status_combo,
            "bid_spin": bid_spin,
            "target_edit": target_edit
        }

    def _init_feedback_section(self):
        group = QGroupBox("Feedback History")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(group)
        layout.setSpacing(6)
        layout.setContentsMargins(10, 20, 10, 10)

        self.feedback_tabs = QTabWidget()
        self.feedback_tabs.setDocumentMode(True)

        def create_log_editor(placeholder):
            editor = QTextEdit()
            editor.setStyleSheet(
                f"background: {C.BG_SURFACE}; color: {C.TEXT_SECONDARY}; border: none;"
            )
            editor.setReadOnly(True)
            editor.setMinimumHeight(self._sp(120, minimum=100))
            editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            editor.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            editor.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            editor.setPlaceholderText(placeholder)
            return editor

        client_page = QWidget()
        client_layout = QVBoxLayout(client_page)
        client_layout.setContentsMargins(0, 0, 0, 0)
        self.client_log = create_log_editor("No client feedback yet.")
        client_layout.addWidget(self.client_log)
        self.feedback_tabs.addTab(client_page, "Client")

        director_page = QWidget()
        director_layout = QVBoxLayout(director_page)
        director_layout.setContentsMargins(0, 0, 0, 0)
        self.director_log = create_log_editor("No director feedback yet.")
        director_layout.addWidget(self.director_log)
        self.feedback_tabs.addTab(director_page, "Director")

        internal_page = QWidget()
        internal_layout = QVBoxLayout(internal_page)
        internal_layout.setContentsMargins(0, 0, 0, 0)
        self.internal_log = create_log_editor("No internal feedback yet.")
        internal_layout.addWidget(self.internal_log)
        self.feedback_tabs.addTab(internal_page, "Internal")

        self.feedback_tabs.currentChanged.connect(self._on_feedback_tab_changed)
        layout.addWidget(self.feedback_tabs)
        return group
        
    def load_data(self):
        self.sow_edit.setText(self.shot.sow)
        self.type_combo.setCurrentText(self.shot.shot_type or "")
        self.priority_combo.setCurrentText(str(self.shot.priority))
        self.status_combo.setCurrentText(self.shot.status)
        self.frames_edit.setText(str(int(self.shot.edit_frames)) if self.shot.edit_frames else "")
        
        self.mov_date_label.setText(self.shot.mov_submission or "-")
        self.exr_date_label.setText(self.shot.exr_submission or "-")
        
        self.hero_checkbox.setChecked(self.shot.is_hero)
        
        self.similar_combo.clear()
        self.similar_combo.addItem("")
        for s in self.all_shots:
            if s.is_hero and s.shot_name != self.shot.shot_name:
                self.similar_combo.addItem(s.shot_name)
        
        def format_log(entries):
            return "\n".join([f"[{e.date}] {e.text}" if hasattr(e, "date") and e.date else f"{e.text}" for e in entries])

        self.internal_log.setText(format_log(self.shot.feedback_internal) if self.shot.feedback_internal else "")
        self.client_log.setText(format_log(self.shot.feedback_client) if self.shot.feedback_client else "")
        self.director_log.setText(format_log(self.shot.feedback_director) if self.shot.feedback_director else "")
        self.title_label.setToolTip(self.shot.shot_name or "")
        self._feedback_signature_map = self._collect_feedback_signatures()
        self._update_feedback_tab_badges()
        self._mark_feedback_tab_seen(self.feedback_tabs.currentIndex())
        self._refresh_text_heights()
        
        # Try to load path
        if self.shot.thumbnail_path:
            # Check for placeholder names in path
            path_str = str(self.shot.thumbnail_path).lower()
            if "placeholder_yellow" in path_str:
                self.thumb_label.setPixmap(self._create_solid_pixmap("#D9A441"))
                return
            elif "placeholder_red" in path_str:
                self.thumb_label.setPixmap(self._create_solid_pixmap("#D9635F"))
                return

            resolved_thumb = self._resolve_thumbnail_path(self.shot.thumbnail_path)
            if resolved_thumb and os.path.exists(resolved_thumb):
                pixmap = QPixmap(resolved_thumb)
                if not pixmap.isNull():
                    self.thumb_label.setPixmap(pixmap.scaled(100, 56, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                else:
                    self.thumb_label.setText("Invalid Img")
            else:
                self.thumb_label.setText("Missing File")
        else:
            self.thumb_label.setText("No Thumb")

    @staticmethod
    def _resolve_thumbnail_path(path_value: str) -> str:
        raw = str(path_value or "").strip()
        if not raw:
            return ""
        if "$SERVER" in raw:
            return GlobalConfig.resolve_path(raw)
        return raw

    def _create_solid_pixmap(self, color_hex):
        """Helper to create color block"""
        pix = QPixmap(100, 56)
        pix.fill(QColor(color_hex))
        return pix

    def _fit_text_edit_height(self, text_edit: QTextEdit, min_height: int, max_height: int):
        """Auto-size editor height so users can read more without inner scrolling."""
        if not text_edit:
            return
        try:
            doc = text_edit.document()
            doc.setTextWidth(max(1, text_edit.viewport().width()))
            doc.adjustSize()
            content_height = int(doc.size().height()) + 12
            target_height = max(min_height, min(max_height, content_height))
            text_edit.setFixedHeight(target_height)
        except Exception:
            text_edit.setFixedHeight(min_height)

    def _refresh_text_heights(self):
        self._fit_text_edit_height(self.sow_edit, 120, 220)
        self._fit_text_edit_height(self.client_log, 110, 220)
        self._fit_text_edit_height(self.director_log, 110, 220)
        self._fit_text_edit_height(self.internal_log, 110, 220)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rebuild_content_layout()
        self._refresh_text_heights()

    def _rebuild_content_layout(self, force: bool = False):
        """
        Responsive layout for shot detail:
        - Narrow widths: single-column stack (prevents horizontal scroll)
        - Wide widths: two-column dashboard layout
        """
        if not hasattr(self, "content_layout") or self.content_layout is None:
            return

        is_single = self.width() < self._sp(980, minimum=860)
        if not force and is_single == self._is_single_column_layout:
            return
        self._is_single_column_layout = is_single

        # Clear previous placements
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item and item.widget():
                item.widget().setParent(None)

        if is_single:
            self.content_layout.setColumnStretch(0, 1)
            self.content_layout.setColumnStretch(1, 0)
            self.content_layout.addWidget(self.version_group, 0, 0)
            self.content_layout.addWidget(self.sow_group, 1, 0)
            self.content_layout.addWidget(self.attr_group, 2, 0)
            self.content_layout.addWidget(self.dept_group, 3, 0)
            self.content_layout.addWidget(self.feedback_group, 4, 0)
            self.content_layout.addWidget(self.versions_group, 5, 0)
            self.content_layout.addWidget(self.dates_group, 6, 0)
            self.content_layout.addWidget(self.hero_group, 7, 0)
        else:
            self.content_layout.setColumnStretch(0, 4)
            self.content_layout.setColumnStretch(1, 6)
            self.content_layout.addWidget(self.version_group, 0, 0)
            self.content_layout.addWidget(self.sow_group, 0, 1)
            self.content_layout.addWidget(self.attr_group, 1, 0)
            self.content_layout.addWidget(self.dept_group, 1, 1)
            self.content_layout.addWidget(self.feedback_group, 2, 0, 1, 2)
            self.content_layout.addWidget(self.versions_group, 3, 0, 1, 2)
            self.content_layout.addWidget(self.dates_group, 4, 0)
            self.content_layout.addWidget(self.hero_group, 4, 1)
    
    def save_data(self):
        self.shot.sow = self.sow_edit.toPlainText()
        self.shot.shot_type = self.type_combo.currentText()
        try:
            self.shot.priority = int(self.priority_combo.currentText())
        except (TypeError, ValueError):
            self.shot.priority = 3
        self.shot.status = self.status_combo.currentText()
        try:
            self.shot.edit_frames = float(self.frames_edit.text())
        except (TypeError, ValueError) as exc:
            logging.debug("Invalid edit_frames value for %s: %s", self.shot.shot_name, exc)
            
        self.shot.curr_version = self.curr_version_edit.text()
        self.shot.is_hero = self.hero_checkbox.isChecked()
        
        similar = self.similar_combo.currentText()
        if similar and similar not in self.shot.similar_to:
            self.shot.similar_to.append(similar)
        
        for key, widgets in self.depts.items():
            dept = self.shot.dept(key)
            dept.artist = widgets["artist_combo"].currentText() # Get from Combo
            dept.status = widgets["status_combo"].currentText()
            try:
                dept.bid_days = float(widgets["bid_spin"].value())
            except (TypeError, ValueError):
                dept.bid_days = 0.0
            dept.target = widgets["target_edit"].date().toString("yyyy-MM-dd")
        
        self.shot._modified = True
        self.save_requested.emit(self.shot)
        
        # Reset button state for visual feedback
        original_text = self.save_btn.text()
        original_style = self.save_btn.styleSheet()
        self.save_btn.setText("Saved!")
        self.save_btn.setStyleSheet("background-color: #5FBF8F; color: white; border: none; border-radius: 4px; padding: 0 24px; font-weight: bold; font-size: 11pt;")
        QTimer.singleShot(1500, lambda: self.save_btn.setText(original_text))
        QTimer.singleShot(1500, lambda: self.save_btn.setStyleSheet(original_style))
        
    def apply_permissions(self):
        # Multi-role check: restrict if user ONLY has artist role (case-insensitive)
        is_artist_only = all(r.lower() == "artist" for r in self.user_roles)

        if is_artist_only:
            self.status_combo.setEnabled(False)
            self.frames_edit.setReadOnly(True)
            self.type_combo.setEnabled(False)
            self.priority_combo.setEnabled(False)
            self.hero_checkbox.setEnabled(False)
            self.sow_edit.setReadOnly(True)
            self.save_btn.setEnabled(False)
            self.curr_version_edit.setReadOnly(True)
            for widgets in self.depts.values():
                widgets["artist_combo"].setEnabled(False) # Disable Combo
                widgets["status_combo"].setEnabled(False)
                widgets["bid_spin"].setReadOnly(True)
                widgets["target_edit"].setReadOnly(True)

    def _get_feedback_state_path(self) -> Path:
        """Per-user local state for feedback-read badges."""
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / "AppData" / "Local")) / "Slate"
        if os.name != "nt" and not base.exists():
            base = Path.home() / ".slate"
        base.mkdir(parents=True, exist_ok=True)
        return base / "feedback_seen_state.json"

    @staticmethod
    def _normalize_identity(value) -> str:
        if value is None:
            return ""
        text = str(value).strip().lower()
        return " ".join(text.split()) if text else ""

    def _resolve_viewer_identity_keys(self) -> set:
        keys = set()
        if isinstance(self.user_data, dict):
            for key in ("user_id", "username", "display_name"):
                norm = self._normalize_identity(self.user_data.get(key))
                if norm:
                    keys.add(norm)
        if not keys:
            keys.add("unknown_user")
        return keys

    def _get_assigned_artist_identity(self) -> str:
        direct = self._normalize_identity(getattr(self.shot, "assigned_artist", ""))
        if direct:
            return direct
        comp_artist = self._normalize_identity(getattr(self.shot.dept("comp"), "artist", ""))
        return comp_artist

    def _feedback_owner_bucket_key(self) -> str:
        owner = self._get_assigned_artist_identity()
        return owner if owner else "__unassigned__"

    def _viewer_can_ack_feedback(self) -> bool:
        owner = self._get_assigned_artist_identity()
        if not owner:
            return True
        return owner in self.viewer_identity_keys

    def _load_feedback_seen_state(self) -> dict:
        try:
            if self.feedback_state_path.exists():
                with open(self.feedback_state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, dict) else {}
        except Exception as exc:
            logging.debug("Failed to load feedback seen state from %s: %s", self.feedback_state_path, exc)
        return {}

    def _save_feedback_seen_state(self):
        try:
            with open(self.feedback_state_path, "w", encoding="utf-8") as f:
                json.dump(self.feedback_seen_state, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            logging.debug("Failed to persist feedback seen state to %s: %s", self.feedback_state_path, exc)

    def _feedback_shot_key(self) -> str:
        return f"{self.current_project_code}:{self.shot.shot_name}"

    @staticmethod
    def _feedback_signature(entries) -> str:
        if not entries:
            return ""
        chunks = []
        for entry in entries:
            date = getattr(entry, "date", "")
            source = getattr(entry, "source", "")
            text = getattr(entry, "text", str(entry))
            logged_by = getattr(entry, "logged_by", "")
            chunks.append(f"{date}|{source}|{logged_by}|{text}")
        payload = "\n".join(chunks)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _collect_feedback_signatures(self) -> dict:
        return {
            "Client": self._feedback_signature(self.shot.feedback_client),
            "Director": self._feedback_signature(self.shot.feedback_director),
            "Internal": self._feedback_signature(self.shot.feedback_internal),
        }

    def _seen_feedback_for_current_shot(self) -> dict:
        owner_bucket = self.feedback_seen_state.setdefault(self._feedback_owner_bucket_key(), {})
        return owner_bucket.setdefault(self._feedback_shot_key(), {})

    def _set_tab_label_with_badge(self, tab_index: int, base_label: str, unread: bool):
        badge_label = f"{base_label} •" if unread else base_label
        self.feedback_tabs.setTabText(tab_index, badge_label)

    def _update_feedback_tab_badges(self):
        seen = self._seen_feedback_for_current_shot()
        for idx, tab_name in enumerate(self._feedback_tab_order):
            sig = self._feedback_signature_map.get(tab_name, "")
            has_content = bool(sig)
            unread = has_content and sig != seen.get(tab_name, "")
            self._set_tab_label_with_badge(idx, tab_name, unread)

    def _mark_feedback_tab_seen(self, tab_index: int):
        if tab_index < 0 or tab_index >= len(self._feedback_tab_order):
            return
        if not self._viewer_can_ack_feedback():
            # Badge persists until assigned artist account views the feedback tab.
            return
        tab_name = self._feedback_tab_order[tab_index]
        sig = self._feedback_signature_map.get(tab_name, "")
        seen = self._seen_feedback_for_current_shot()
        if sig:
            seen[tab_name] = sig
        elif tab_name in seen:
            seen.pop(tab_name, None)
        self._save_feedback_seen_state()
        self._update_feedback_tab_badges()

    def _on_feedback_tab_changed(self, tab_index: int):
        self._mark_feedback_tab_seen(tab_index)
                
    def open_folder(self, folder_type):
        if not self.project_manager:
            return
        path = self.project_manager.get_folder_path(
            self.current_project_code, folder_type, self.shot.reel_episode, self.shot.shot_name
        )
        if path and os.path.exists(path):
            if sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])

    def _create_dcc_button(self, app_key: str, app_label: str, icon_name: str) -> QToolButton:
        btn = QToolButton(self)
        btn.setObjectName("dccLauncherBtn")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(f"Open shot file in {app_label}")
        btn.setFixedSize(self._sp(34, minimum=30), self._sp(34, minimum=30))
        btn.setIconSize(QSize(self._sp(20, minimum=18), self._sp(20, minimum=18)))
        btn.setStyleSheet(
            f"""
            QToolButton#dccLauncherBtn {{
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: {R.SM}px;
                background-color: {C.BG_ELEVATED};
                padding: 2px;
            }}
            QToolButton#dccLauncherBtn:hover {{
                border: 1px solid {C.ACCENT_PRIMARY};
                background-color: {C.BG_HOVER};
            }}
            QToolButton#dccLauncherBtn:pressed {{
                background-color: {C.BG_SURFACE};
            }}
            """
        )

        icon_path = ResourcePathManager.get_resource_path(f"resources/logos/{icon_name}")
        if icon_path.exists():
            btn.setIcon(QIcon(str(icon_path)))
        else:
            btn.setText(app_label[:1].upper())
        btn.clicked.connect(lambda: self._launch_dcc_for_shot(app_key, app_label))
        return btn

    def _launch_dcc_for_shot(self, app_key: str, app_label: str):
        if app_key in ["nuke", "blender", "natron", "silhouette"]:
            from slate.core.dcc_launcher import DCCLauncher
            launcher = DCCLauncher(self)
            launcher.launch(app_key, self.shot.id)
            return

        target_file = self._resolve_or_prompt_dcc_target_file(app_key)
        if not target_file:
            return

        executable = self._resolve_dcc_executable(app_key)
        if executable:
            try:
                subprocess.Popen([executable, str(target_file)], cwd=str(target_file.parent))
                return
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Launch Failed",
                    f"Could not open {app_label}.\n\nError:\n{exc}",
                )
                return

        key = self._DCC_CONFIG_KEYS.get(app_key, f"{app_key}_path")
        QMessageBox.information(
            self,
            "Configure App Path",
            (
                f"{app_label} executable was not found automatically.\n\n"
                f"Please set '{key}' in your config and retry.\n\n"
                f"Selected file:\n{target_file}"
            ),
        )

    def _resolve_or_prompt_dcc_target_file(self, app_key: str):
        exts = {
            "nuke": [".nk", ".nknc"],
            "after_effects": [".aep", ".aepx"],
            "premiere": [".prproj"],
            "blender": [".blend"],
        }.get(app_key, [])
        if not exts:
            return None

        candidates = []
        for base in self._candidate_shot_roots():
            if not base.exists():
                continue
            for ext in exts:
                try:
                    candidates.extend(base.rglob(f"*{ext}"))
                except Exception:
                    continue
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime)

        start_dir = ""
        roots = self._candidate_shot_roots()
        for root in roots:
            if root.exists():
                start_dir = str(root)
                break
        if not start_dir:
            start_dir = str(Path.home())

        filter_map = {
            "nuke": "Nuke Script (*.nk *.nknc)",
            "after_effects": "After Effects Project (*.aep *.aepx)",
            "premiere": "Premiere Project (*.prproj)",
            "blender": "Blender File (*.blend)",
        }
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Select File to Open",
            start_dir,
            filter_map.get(app_key, "All Files (*.*)"),
        )
        return Path(selected) if selected else None

    def _candidate_shot_roots(self):
        roots = []
        if self.project_manager:
            for key in ("comp", "output", "prep", "scan"):
                try:
                    path = self.project_manager.get_folder_path(
                        self.current_project_code,
                        key,
                        self.shot.reel_episode,
                        self.shot.shot_name,
                    )
                    if path:
                        roots.append(Path(path))
                except Exception:
                    continue

        folder_paths = getattr(self.shot, "folder_paths", {}) or {}
        if isinstance(folder_paths, dict):
            for value in folder_paths.values():
                p = Path(str(value or "").strip())
                if str(p):
                    roots.append(p)

        unique = []
        seen = set()
        for root in roots:
            norm = str(root).strip().lower()
            if norm and norm not in seen:
                seen.add(norm)
                unique.append(root)
        return unique

    def _resolve_dcc_executable(self, app_key: str):
        config_key = self._DCC_CONFIG_KEYS.get(app_key, f"{app_key}_path")
        configured = str(GlobalConfig.get(config_key, "") or "").strip()
        if configured and Path(configured).exists():
            return configured

        env_key_map = {
            "nuke": "Slate_NUKE_PATH",
            "after_effects": "Slate_AFTER_EFFECTS_PATH",
            "premiere": "Slate_PREMIERE_PATH",
            "blender": "Slate_BLENDER_PATH",
        }
        env_value = os.getenv(env_key_map.get(app_key, ""), "").strip()
        if env_value and Path(env_value).exists():
            return env_value

        prog_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
        win_globs = {
            "nuke": [
                rf"{prog_files}\Nuke*\Nuke*.exe",
                rf"{prog_files}\Foundry\Nuke*\Nuke*.exe",
            ],
            "after_effects": [
                rf"{prog_files}\Adobe\Adobe After Effects*\Support Files\AfterFX.exe",
            ],
            "premiere": [
                rf"{prog_files}\Adobe\Adobe Premiere Pro*\Adobe Premiere Pro.exe",
            ],
            "blender": [
                rf"{prog_files}\Blender Foundation\Blender*\blender.exe",
            ],
        }
        if sys.platform == "win32":
            matches = []
            for pattern in win_globs.get(app_key, []):
                matches.extend(glob(pattern))
            if matches:
                matches.sort(reverse=True)
                return matches[0]

        path_names = {
            "nuke": ["Nuke16.0.exe", "Nuke15.1.exe", "Nuke15.0.exe", "Nuke14.0.exe", "nuke.exe"],
            "after_effects": ["AfterFX.exe"],
            "premiere": ["Adobe Premiere Pro.exe", "premiere.exe"],
            "blender": ["blender.exe"],
        }
        for name in path_names.get(app_key, []):
            resolved = which(name)
            if resolved:
                return resolved
        return None

