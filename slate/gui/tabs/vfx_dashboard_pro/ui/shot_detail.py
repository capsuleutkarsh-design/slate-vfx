"""
The shot detail panel beside the grid.

What it used to do on Save, without being touched: wipe department statuses
it had no word for (RETAKE, SENT FOR REVIEW, YTS, OMIT - even 'APPROVED',
because its list said 'Approved'), give every empty department target today's
date, turn a shot type it did not list into blank and a status into WIP, add
the selected hero to the shot's list again on every save, and flash a green
"Saved!" before anything had been written. A Compositor got a fully editable
panel that was refused at the end.

Now:
- every list shows the stored value exactly, even one it does not offer
  (shot_status / shot types); dates can be "No date" (date_fields);
- Apply sends only what was changed in the panel, and it becomes a pending
  edit like any other (one Save writes it; Ctrl+Z undoes it);
- what you may change is what the grid lets you change: everything with full
  rights, your own department's rows as a lead, your own department status as
  an artist (saved at once, as in the grid);
- the result is said honestly, after the fact.
"""

import hashlib
import json
import logging
import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QIcon, QPixmap, QTextOption
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMenu, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from slate.core.domain import shot_status
from slate.core.infra.gate import Gate
from slate.core.infra.global_config import GlobalConfig
from slate.core.system.adaptation_engine import system_engine
from slate.gui.core.controls import make_button, style_button
from slate.gui.core.icons import icon as draw_icon
from slate.utils.resource_manager import ResourcePathManager

from ..models.shot_model import Shot
from .date_fields import OptionalDateField


def _section(title: str) -> QLabel:
    """A section heading: the body font, bold, a little larger - not a 9 px pill."""
    label = QLabel(title)
    label.setObjectName("detailSection")
    font = label.font()
    font.setBold(True)
    font.setPointSizeF(font.pointSizeF() * 1.1)
    label.setFont(font)
    label.setStyleSheet(f"color: {Gate.TEXT}; padding-top: 6px;")
    return label


def _select(combo: QComboBox, value):
    idx = combo.findData(value)
    if idx < 0:
        idx = combo.findText(str(value or ""), Qt.MatchFlag.MatchFixedString)
    combo.setCurrentIndex(max(0, idx))


class ShotDetailWidget(QWidget):
    close_requested = Signal()
    search_requested = Signal(str)            # kept for older callers
    show_only_requested = Signal(object)      # the grid shows only this shot
    apply_requested = Signal(object, dict)    # shot, {field path: new value}
    save_requested = Signal(object)           # kept for older callers
    quick_look_requested = Signal(object)
    rv_review_requested = Signal(object)
    history_requested = Signal(object)

    def __init__(
        self,
        shot: Shot,
        user_role,
        project_manager=None,
        all_shots=None,
        all_users=None,
        user_data: dict = None,
        current_project_code: str = "",
        inherit_app_theme: bool = False,
        parent=None,
        department_scope=None,
        user_identities=None,
        allowed_statuses=None,
    ):
        super().__init__(parent)
        self.shot = shot
        self.inherit_app_theme = bool(inherit_app_theme)
        if isinstance(user_role, (list, tuple)):
            self.user_roles = [str(r).lower() for r in user_role] or ["artist"]
        else:
            self.user_roles = [str(user_role or "artist").lower()]
        self.user_role = self.user_roles[0]
        self.project_manager = project_manager
        self.all_shots = all_shots or []
        self.all_users = list(all_users or [])
        self.user_data = user_data or {}
        self.department_scope = department_scope
        self.user_identities = {str(i).strip().lower() for i in (user_identities or []) if str(i).strip()}
        self.allowed_statuses = list(allowed_statuses) if allowed_statuses else list(shot_status.WORKFLOW)
        shot_project_code = str(getattr(self.shot, "project_name", "") or "").strip()
        self.current_project_code = str(current_project_code or shot_project_code or "UNKNOWN")
        self.feedback_state_path = self._get_feedback_state_path()
        self.viewer_identity_keys = self._resolve_viewer_identity_keys()
        self.feedback_seen_state = self._load_feedback_seen_state()
        self._feedback_tab_order = ["Client", "Director", "Internal"]
        self._feedback_signature_map = {}
        self._sp = system_engine.scale_px
        self._loaded = {}
        self._loading = False
        self.init_ui()
        self.load_data()
        self.apply_permissions()

    # ------------------------------------------------------------ rights
    def _can_edit_shot(self) -> bool:
        """Full edit rights on the shot itself (not a lead, not an artist)."""
        from slate.core.domain.access import can_edit_dashboard
        return can_edit_dashboard(self.user_roles) and self.department_scope is None

    def _can_edit_department(self, key) -> bool:
        from slate.core.domain.access import can_edit_dashboard
        if not can_edit_dashboard(self.user_roles):
            return False
        return self.department_scope is None or key in self.department_scope

    def _owns_department(self, key) -> bool:
        from slate.core.domain.access import can_edit_own_status
        artist = str(self.shot.dept(key).artist or "").strip().lower()
        return bool(artist) and artist in self.user_identities and can_edit_own_status(self.user_roles)

    # ------------------------------------------------------------ building
    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # === HEADER (stays put while the details scroll) ===
        header_frame = QFrame()
        header_frame.setObjectName("detailHeader")
        header_frame.setStyleSheet(
            f"QFrame#detailHeader {{ border-bottom: 1px solid {Gate.LINE}; }}")
        header_layout = QVBoxLayout(header_frame)
        header_layout.setContentsMargins(self._sp(14), self._sp(10), self._sp(14), self._sp(10))
        header_layout.setSpacing(self._sp(8))

        top_row = QHBoxLayout()
        top_row.setSpacing(self._sp(10))
        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(self._sp(112, minimum=96), self._sp(63, minimum=54))
        self.thumb_label.setObjectName("thumbnail")
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.thumb_label.setToolTip("Preview the shot (Space)")
        self.thumb_label.setStyleSheet(
            f"QLabel#thumbnail {{ background: {Gate.RAISED}; border: 1px solid {Gate.LINE};"
            f" border-radius: {Gate.RADIUS_MD}px; color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_XS}px; }}")
        self.thumb_label.mousePressEvent = lambda ev: self.quick_look_requested.emit(self.shot)
        top_row.addWidget(self.thumb_label)

        title_area = QVBoxLayout()
        title_area.setSpacing(4)
        self.title_label = QLabel(self.shot.shot_name)
        self.title_label.setObjectName("detailTitle")
        title_font = QFont(self.title_label.font())
        title_font.setBold(True)
        title_font.setPointSizeF(title_font.pointSizeF() * 1.45)
        self.title_label.setFont(title_font)
        self.title_label.setStyleSheet(f"color: {Gate.TEXT};")
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title_area.addWidget(self.title_label)
        self.reel_label = QLabel(self.shot.reel_episode or "No reel")
        self.reel_label.setStyleSheet(f"color: {Gate.TEXT_2};")
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(self.reel_label)
        self.status_badge = QLabel()
        self.status_badge.setObjectName("statusBadge")
        status_row.addWidget(self.status_badge)
        status_row.addStretch()
        title_area.addLayout(status_row)
        top_row.addLayout(title_area, 1)

        self.close_btn = QToolButton()
        self.close_btn.setObjectName("backBtn")
        self.close_btn.setIcon(draw_icon("close", Gate.TEXT_2, 18))
        self.close_btn.setToolTip("Close the panel (Esc)")
        self.close_btn.setAutoRaise(True)
        self.close_btn.clicked.connect(self.close_requested.emit)
        top_row.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        header_layout.addLayout(top_row)

        # Actions: one row that never squeezes - the rest is in menus.
        actions = QHBoxLayout()
        actions.setSpacing(self._sp(6))
        self.preview_btn = make_button("Preview", "secondary", icon="play",
                                       tooltip="Play the latest render in Quick Look (Space)",
                                       on_click=lambda: self.quick_look_requested.emit(self.shot))
        actions.addWidget(self.preview_btn)
        self.rv_btn = make_button("Review in RV", "secondary",
                                  tooltip="Open the scan or a department render in OpenRV",
                                  on_click=lambda: self.rv_review_requested.emit(self.shot))
        actions.addWidget(self.rv_btn)

        self.more_btn = make_button("More", "secondary")
        more = QMenu(self.more_btn)
        more.addAction("Show only this shot in the grid", lambda: self.show_only_requested.emit(self.shot))
        more.addAction("View history", lambda: self.history_requested.emit(self.shot))
        more.addSeparator()
        self.folder_menu = more.addMenu("Open folder")
        self.folder_menu.aboutToShow.connect(self._fill_folder_menu)
        self.dcc_menu = more.addMenu("Open in")
        self.dcc_menu.aboutToShow.connect(self._fill_dcc_menu)
        self.more_btn.setMenu(more)
        style_button(self.more_btn, "secondary")
        actions.addWidget(self.more_btn)
        actions.addStretch(1)
        header_layout.addLayout(actions)
        main_layout.addWidget(header_frame)

        # === DETAILS (scroll) ===
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("detailScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content_widget = QWidget()
        content_widget.setObjectName("detailContent")
        content = QVBoxLayout(content_widget)
        content.setContentsMargins(self._sp(14), self._sp(8), self._sp(14), self._sp(14))
        content.setSpacing(self._sp(6))
        self.content_layout = content

        content.addWidget(_section("Shot"))
        content.addLayout(self._init_attributes_section())
        content.addWidget(_section("Scope of work"))
        self.sow_edit = QTextEdit()
        self.sow_edit.setAcceptRichText(False)
        self.sow_edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.sow_edit.setPlaceholderText("What the client asked for on this shot")
        self.sow_edit.textChanged.connect(self._refresh_text_heights)
        self.sow_edit.textChanged.connect(self._changed)
        content.addWidget(self.sow_edit)

        content.addWidget(_section("Departments"))
        self.dept_group = self._init_departments_section()
        content.addWidget(self.dept_group)

        content.addWidget(_section("Feedback"))
        self.feedback_group = self._init_feedback_section()
        content.addWidget(self.feedback_group)

        content.addWidget(_section("Versions"))
        self.versions_group = self._init_versions_section()
        content.addWidget(self.versions_group)

        content.addWidget(_section("Submission dates"))
        self.dates_group = self._init_dates_section()
        content.addWidget(self.dates_group)
        content.addStretch(1)

        scroll.setWidget(content_widget)
        main_layout.addWidget(scroll, 1)

        # === FOOTER (outside the scroll, so it never sits on the content) ===
        footer = QFrame()
        footer.setObjectName("detailFooter")
        footer.setStyleSheet(f"QFrame#detailFooter {{ border-top: 1px solid {Gate.LINE}; }}")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(self._sp(14), self._sp(8), self._sp(14), self._sp(8))
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setStyleSheet(f"color: {Gate.TEXT_2};")
        footer_layout.addWidget(self.result_label, 1)
        self.revert_btn = make_button("Revert", "secondary", tooltip="Put the panel back to how the shot is",
                                      on_click=self.load_data)
        footer_layout.addWidget(self.revert_btn)
        self.save_btn = make_button("Apply", "primary", on_click=self.apply_changes)
        self.save_btn.setObjectName("saveBtn")
        footer_layout.addWidget(self.save_btn)
        main_layout.addWidget(footer)

    def _init_attributes_section(self):
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(self._sp(12))
        form.setVerticalSpacing(self._sp(6))

        self.status_combo = QComboBox()
        self.status_combo.currentIndexChanged.connect(self._changed)
        self.status_combo.currentIndexChanged.connect(lambda *_: self._update_status_badge_style(
            self.status_combo.currentData() or ""))
        form.addRow("Status", self.status_combo)

        self.type_combo = QComboBox()
        self.type_combo.currentIndexChanged.connect(self._changed)
        form.addRow("Type", self.type_combo)

        self.priority_combo = QComboBox()
        for value, label in shot_status.priorities():
            self.priority_combo.addItem(label, value)
        self.priority_combo.currentIndexChanged.connect(self._changed)
        form.addRow("Priority", self.priority_combo)

        self.frames_edit = QSpinBox()
        self.frames_edit.setRange(0, 1_000_000)
        self.frames_edit.setGroupSeparatorShown(True)
        self.frames_edit.setSpecialValueText("Not set")
        self.frames_edit.valueChanged.connect(self._changed)
        form.addRow("Frames", self.frames_edit)

        self.curr_version_edit = QLineEdit()
        self.curr_version_edit.setPlaceholderText("v001")
        self.curr_version_edit.textChanged.connect(self._changed)
        self.prev_version_label = QLabel()
        self.prev_version_label.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        version_row = QHBoxLayout()
        version_row.addWidget(self.curr_version_edit, 1)
        version_row.addWidget(self.prev_version_label)
        form.addRow("Version", version_row)

        self.target_edit = OptionalDateField()
        self.target_edit.value_changed.connect(self._changed)
        form.addRow("Target", self.target_edit)

        self.hero_checkbox = QCheckBox("This is a hero shot")
        self.hero_checkbox.setObjectName("heroCheck")
        self.hero_checkbox.toggled.connect(self._changed)
        form.addRow("Hero", self.hero_checkbox)

        # Linked heroes: a list you can add to and remove from. The old combo
        # appended its choice on every save and could never take one away.
        hero_box = QVBoxLayout()
        hero_box.setSpacing(4)
        self.similar_list = QListWidget()
        self.similar_list.setMaximumHeight(self._sp(70, minimum=60))
        hero_box.addWidget(self.similar_list)
        hero_row = QHBoxLayout()
        self.similar_combo = QComboBox()
        self.similar_combo.setPlaceholderText("Pick a hero shot…")
        hero_row.addWidget(self.similar_combo, 1)
        self.similar_add_btn = make_button("Link", "secondary", on_click=self._add_similar)
        hero_row.addWidget(self.similar_add_btn)
        self.similar_remove_btn = make_button("Unlink", "secondary", on_click=self._remove_similar)
        hero_row.addWidget(self.similar_remove_btn)
        hero_box.addLayout(hero_row)
        form.addRow("Linked to", hero_box)
        return form

    def _init_departments_section(self):
        group = QWidget()
        group.setObjectName("deptGrid")
        layout = QGridLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(self._sp(6))
        layout.setVerticalSpacing(self._sp(4))
        layout.setColumnStretch(1, 4)
        layout.setColumnStretch(2, 3)
        for col, text in enumerate(["Department", "Artist", "Status", "Bid", "Actual", "Target"]):
            lbl = QLabel(text)
            lbl.setObjectName("gridHeader")
            lbl.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: {Gate.SIZE_XS}px; font-weight: 600;")
            layout.addWidget(lbl, 0, col)

        self.depts = {}
        from slate.core.domain.departments import load_departments
        for row_idx, department in enumerate(load_departments(), start=1):
            self._create_dept_grid_row(layout, row_idx, department.name, department.key)
        return group

    def _create_dept_grid_row(self, layout, row, name, key):
        label = QLabel(name)
        layout.addWidget(label, row, 0)

        artist_combo = QComboBox()
        artist_combo.setEditable(True)
        artist_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        artist_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        artist_combo.setMinimumContentsLength(8)
        artist_combo.currentTextChanged.connect(self._changed)
        layout.addWidget(artist_combo, row, 1)

        status_combo = QComboBox()
        status_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        status_combo.setMinimumContentsLength(7)
        status_combo.currentIndexChanged.connect(self._changed)
        layout.addWidget(status_combo, row, 2)

        bid_spin = QDoubleSpinBox()
        bid_spin.setRange(0.0, 9999.9)
        bid_spin.setDecimals(1)
        bid_spin.setSingleStep(0.5)
        bid_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        bid_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        bid_spin.setSpecialValueText("-")
        bid_spin.setToolTip("Bid days")
        bid_spin.setMaximumWidth(self._sp(64, minimum=56))
        bid_spin.valueChanged.connect(self._changed)
        layout.addWidget(bid_spin, row, 3)

        # Days actually spent, for comparing with the bid (Bidding tracking).
        actual_spin = QDoubleSpinBox()
        actual_spin.setRange(0.0, 9999.9)
        actual_spin.setDecimals(1)
        actual_spin.setSingleStep(0.5)
        actual_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        actual_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        actual_spin.setSpecialValueText("-")
        actual_spin.setToolTip("Days actually spent")
        actual_spin.setMaximumWidth(self._sp(64, minimum=56))
        actual_spin.valueChanged.connect(self._changed)
        layout.addWidget(actual_spin, row, 4)

        target_edit = OptionalDateField()
        target_edit.value_changed.connect(self._changed)
        layout.addWidget(target_edit, row, 5)

        self.depts[key] = {
            "label": label,
            "artist_combo": artist_combo,
            "status_combo": status_combo,
            "bid_spin": bid_spin,
            "actual_spin": actual_spin,
            "target_edit": target_edit,
        }

    def _init_feedback_section(self):
        self.feedback_tabs = QTabWidget()
        self.feedback_tabs.setDocumentMode(True)

        def create_log_editor(placeholder):
            editor = QTextEdit()
            editor.setReadOnly(True)
            editor.setMinimumHeight(self._sp(90, minimum=80))
            editor.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            editor.setPlaceholderText(placeholder)
            return editor

        self.client_log = create_log_editor("No client feedback yet.")
        self.director_log = create_log_editor("No director feedback yet.")
        self.internal_log = create_log_editor("No internal feedback yet.")
        self.feedback_tabs.addTab(self.client_log, "Client")
        self.feedback_tabs.addTab(self.director_log, "Director")
        self.feedback_tabs.addTab(self.internal_log, "Internal")
        self.feedback_tabs.currentChanged.connect(self._on_feedback_tab_changed)
        return self.feedback_tabs

    def _init_versions_section(self):
        from .versions_panel import VersionsPanel
        from slate.core.domain.access import can_edit_dashboard
        return VersionsPanel(
            project_code=self.current_project_code,
            shot_name=getattr(self.shot, "shot_name", ""),
            can_edit=can_edit_dashboard(self.user_roles),
            artists=list(self.all_users or []),
            user_name=str((self.user_data or {}).get("display_name", "")),
            parent=self,
        )

    def _init_dates_section(self):
        box = QWidget()
        form = QFormLayout(box)
        form.setContentsMargins(0, 0, 0, 0)
        self.mov_date_label = QLabel("-")
        self.exr_date_label = QLabel("-")
        form.addRow("MOV", self.mov_date_label)
        form.addRow("EXR", self.exr_date_label)
        return box

    # ------------------------------------------------------------ data
    def load_data(self):
        """Fill every field from the shot, exactly as stored."""
        from slate.core.domain.dates import format_date
        self._loading = True
        try:
            shot = self.shot
            self.title_label.setText(shot.shot_name)
            self.title_label.setToolTip(f"{shot.shot_name} ({shot.reel_episode})" if shot.reel_episode
                                        else shot.shot_name)
            self.reel_label.setText(shot.reel_episode or "No reel")

            self.status_combo.clear()
            for value in shot_status.choices_with(shot.status, include_blank=True):
                self.status_combo.addItem(shot_status.label(value), value)
            idx = shot_status.match_index([self.status_combo.itemData(i)
                                           for i in range(self.status_combo.count())], shot.status)
            self.status_combo.setCurrentIndex(max(0, idx))
            self._update_status_badge_style(shot.status)

            self.type_combo.clear()
            for value in shot_status.shot_types_with(shot.shot_type, include_blank=True):
                self.type_combo.addItem(value or "(none)", value)
            _select(self.type_combo, shot.shot_type or "")

            prio = self.priority_combo.findData(shot.priority)
            if prio < 0:
                self.priority_combo.addItem(str(shot.priority), shot.priority)
                prio = self.priority_combo.count() - 1
            self.priority_combo.setCurrentIndex(prio)

            try:
                self.frames_edit.setValue(int(float(shot.edit_frames or 0)))
            except (TypeError, ValueError):
                self.frames_edit.setValue(0)
            self.curr_version_edit.setText(shot.curr_version or "")
            self.prev_version_label.setText(f"Previous: {shot.prev_version}" if shot.prev_version else "")
            self.target_edit.set_value(shot.target)
            self.sow_edit.setPlainText(shot.sow or "")
            self.hero_checkbox.setChecked(bool(shot.is_hero))

            self.similar_list.clear()
            for name in shot.similar_to or []:
                self.similar_list.addItem(str(name))
            self.similar_combo.clear()
            for s in self.all_shots:
                if s.is_hero and s.shot_name != shot.shot_name:
                    self.similar_combo.addItem(s.shot_name)
            self.similar_combo.setCurrentIndex(-1)

            for key, widgets in self.depts.items():
                dept = shot.dept(key)
                combo = widgets["artist_combo"]
                combo.clear()
                combo.addItem("", "")
                names = list(self.all_users)
                if dept.artist and dept.artist.lower() not in {n.lower() for n in names}:
                    names.append(dept.artist)
                for n in names:
                    combo.addItem(n, n)
                combo.setCurrentText(dept.artist or "")
                combo.setToolTip(dept.artist or "Nobody assigned")

                status_combo = widgets["status_combo"]
                status_combo.clear()
                # An artist on their own row picks from the states of their own
                # work; the stored value is always kept on the list.
                own_only = self._owns_department(key) and not self._can_edit_department(key)
                choices = shot_status.choices_with(
                    dept.status, include_blank=True,
                    allowed=self.allowed_statuses if own_only else None)
                for value in choices:
                    status_combo.addItem(value or "-", value)
                    if value:
                        status_combo.setItemData(status_combo.count() - 1,
                                                 shot_status.describe(value), Qt.ItemDataRole.ToolTipRole)
                idx = shot_status.match_index([status_combo.itemData(i)
                                               for i in range(status_combo.count())], dept.status)
                status_combo.setCurrentIndex(max(0, idx))
                try:
                    widgets["bid_spin"].setValue(float(dept.bid_days or 0.0))
                except (TypeError, ValueError):
                    widgets["bid_spin"].setValue(0.0)
                try:
                    widgets["actual_spin"].setValue(float(getattr(dept, "actual_days", 0.0) or 0.0))
                except (TypeError, ValueError):
                    widgets["actual_spin"].setValue(0.0)
                widgets["target_edit"].set_value(dept.target or dept.eta)

            self.mov_date_label.setText(format_date(shot.mov_submission) or "-")
            self.exr_date_label.setText(format_date(shot.exr_submission) or "-")

            def format_log(entries):
                return "\n".join([f"[{format_date(e.date) or e.date}] {e.text}" if getattr(e, "date", "")
                                  else f"{e.text}" for e in entries])

            self.internal_log.setPlainText(format_log(shot.feedback_internal) if shot.feedback_internal else "")
            self.client_log.setPlainText(format_log(shot.feedback_client) if shot.feedback_client else "")
            self.director_log.setPlainText(format_log(shot.feedback_director) if shot.feedback_director else "")
            self._feedback_signature_map = self._collect_feedback_signatures()
            self._update_feedback_tab_badges()
            self._mark_feedback_tab_seen(self.feedback_tabs.currentIndex())
        finally:
            self._loading = False
        self._loaded = self._values()
        self._refresh_text_heights()
        self._changed()

    def reload_from_shot(self):
        """The shot changed under the panel (an undo, a board drop): show it, if nothing is half-typed."""
        if not self.has_unapplied_changes():
            self.load_data()

    def _values(self) -> dict:
        """Every editable value, as field paths (the same paths the model stages)."""
        values = {
            "status": self.status_combo.currentData() if self.status_combo.currentIndex() >= 0 else self.shot.status,
            "shot_type": self.type_combo.currentData() if self.type_combo.currentIndex() >= 0 else self.shot.shot_type,
            "priority": self.priority_combo.currentData(),
            "edit_frames": float(self.frames_edit.value()),
            "curr_version": self.curr_version_edit.text().strip(),
            "target": self.target_edit.value(),
            "sow": self.sow_edit.toPlainText(),
            "is_hero": self.hero_checkbox.isChecked(),
            "similar_to": [self.similar_list.item(i).text() for i in range(self.similar_list.count())],
        }
        for key, w in self.depts.items():
            values[f"departments.{key}.artist"] = w["artist_combo"].currentText().strip()
            status_combo = w["status_combo"]
            values[f"departments.{key}.status"] = (status_combo.currentData()
                                                   if status_combo.currentIndex() >= 0 else "")
            values[f"departments.{key}.bid_days"] = float(w["bid_spin"].value())
            values[f"departments.{key}.actual_days"] = float(w["actual_spin"].value())
            values[f"departments.{key}.target"] = w["target_edit"].value()
        return values

    def collect_changes(self) -> dict:
        """Only what was changed in the panel - nothing else is ever written back."""
        now = self._values()
        changes = {}
        for path, value in now.items():
            before = self._loaded.get(path)
            if path == "edit_frames":
                if float(before or 0) != float(value or 0):
                    changes[path] = value
                continue
            if (before if before is not None else "") != (value if value is not None else ""):
                changes[path] = value
        # A target date shown as text it could not read ("TBD") is unchanged
        # until a date is picked; the value() above already keeps it.
        return changes

    def has_unapplied_changes(self) -> bool:
        return bool(self._loaded) and bool(self.collect_changes())

    def _changed(self, *args):
        if self._loading or not hasattr(self, "save_btn"):
            return
        pending = self.has_unapplied_changes()
        self.save_btn.setEnabled(pending and self.save_btn.isVisible())
        self.revert_btn.setEnabled(pending)
        if pending:
            self.result_label.setText("Changes in this panel are not applied yet.")
            self.result_label.setStyleSheet(f"color: {Gate.WARN};")
        elif self.result_label.text().startswith("Changes in this panel"):
            self.result_label.setText("")

    def apply_changes(self):
        changes = self.collect_changes()
        if not changes:
            self.show_apply_result(True, "Nothing changed.")
            return
        self.apply_requested.emit(self.shot, changes)

    # Older callers.
    def save_data(self):
        self.apply_changes()

    def show_apply_result(self, ok: bool, message: str):
        """Said after the dashboard has actually done it - never before."""
        if ok:
            self.load_data()
        self.result_label.setText(message)
        self.result_label.setStyleSheet(f"color: {Gate.OK if ok else Gate.BAD};")

    def _add_similar(self):
        name = self.similar_combo.currentText().strip()
        if not name:
            return
        existing = {self.similar_list.item(i).text() for i in range(self.similar_list.count())}
        if name not in existing:
            self.similar_list.addItem(name)
            self._changed()

    def _remove_similar(self):
        for item in self.similar_list.selectedItems():
            self.similar_list.takeItem(self.similar_list.row(item))
        self._changed()

    def _update_status_badge_style(self, status):
        text = shot_status.label(status)
        colour = Gate.status_color(status) if status else Gate.TEXT_DIM
        self.status_badge.setText(text)
        self.status_badge.setToolTip(shot_status.describe(status))
        self.status_badge.setStyleSheet(
            f"background-color: {Gate.tint(colour, 0.14)}; color: {colour};"
            f" border: 1px solid {Gate.tint(colour, 0.4)}; padding: 1px 8px;"
            f" border-radius: {Gate.RADIUS_SM}px; font-weight: 600; font-size: {Gate.SIZE_XS}px;")

    def set_thumbnail(self, pixmap, text: str = "No preview"):
        """A picture of the shot, or a neutral box saying there is none (never a red block)."""
        if pixmap is not None and not pixmap.isNull():
            self.thumb_label.setText("")
            self.thumb_label.setPixmap(pixmap.scaled(
                self.thumb_label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self.thumb_label.setPixmap(QPixmap())
            self.thumb_label.setText(text)

    def apply_permissions(self):
        """The same rules as the grid."""
        shot_editable = self._can_edit_shot()
        for widget in (self.status_combo, self.type_combo, self.priority_combo, self.frames_edit,
                       self.hero_checkbox, self.similar_combo, self.similar_add_btn,
                       self.similar_remove_btn):
            widget.setEnabled(shot_editable)
        self.curr_version_edit.setReadOnly(not shot_editable)
        self.sow_edit.setReadOnly(not shot_editable)
        self.target_edit.setReadOnly(not shot_editable)
        self.target_edit.setEnabled(shot_editable)

        any_editable = shot_editable
        for key, w in self.depts.items():
            full = self._can_edit_department(key)
            own = (not full) and self._owns_department(key)
            w["artist_combo"].setEnabled(full)
            w["bid_spin"].setReadOnly(not full)
            w["bid_spin"].setEnabled(full)
            w["actual_spin"].setReadOnly(not full)
            w["actual_spin"].setEnabled(full)
            w["target_edit"].setReadOnly(not full)
            w["target_edit"].setEnabled(full)
            w["status_combo"].setEnabled(full or own)
            if own:
                w["label"].setStyleSheet(f"color: {Gate.ACCENT}; font-weight: 600;")
                w["label"].setToolTip("Your department - you can set its status.")
            elif full and self.department_scope is not None:
                w["label"].setStyleSheet(f"color: {Gate.ACCENT}; font-weight: 600;")
            any_editable = any_editable or full or own

        from slate.core.domain.access import can_edit_dashboard
        self.save_btn.setVisible(any_editable)
        self.revert_btn.setVisible(any_editable)
        self.save_btn.setText("Apply" if can_edit_dashboard(self.user_roles) else "Save my status")
        self.save_btn.setToolTip(
            "Put these changes on the grid as pending edits - Save writes them (Ctrl+Z undoes them)"
            if can_edit_dashboard(self.user_roles) else "Saves your department's status straight away")
        if not any_editable:
            self.result_label.setText("Read only - you can see this shot but not change it.")
            self.result_label.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        self._changed()

    # ------------------------------------------------------------ sizing
    def _fit_text_edit_height(self, text_edit: QTextEdit, min_height: int, max_height: int):
        try:
            doc = text_edit.document()
            doc.setTextWidth(max(1, text_edit.viewport().width()))
            target = max(min_height, min(max_height, int(doc.size().height()) + 12))
            text_edit.setFixedHeight(target)
        except Exception:
            text_edit.setFixedHeight(min_height)

    def _refresh_text_heights(self):
        # Two lines when empty, growing with the text - not a 120 px empty box.
        line = self.sow_edit.fontMetrics().lineSpacing()
        self._fit_text_edit_height(self.sow_edit, line * 2 + 14, 220)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_text_heights()

    # ------------------------------------------------------------ folders and apps
    def _fill_folder_menu(self):
        from slate.core.domain.departments import load_departments
        self.folder_menu.clear()
        for name, key in [("Scan", "scan")] + [(d.name, d.key) for d in load_departments()] + [("Output", "output")]:
            self.folder_menu.addAction(name, lambda k=key: self.open_folder(k))

    def _fill_dcc_menu(self):
        from slate.core.dcc_launcher import dashboard_apps
        self.dcc_menu.clear()
        for app_key, label in dashboard_apps():
            action = self.dcc_menu.addAction(label, lambda k=app_key, l=label: self._launch_dcc_for_shot(k, l))
            icon = self._app_icon(app_key)
            if icon is not None:
                action.setIcon(icon)

    @staticmethod
    def _app_icon(app_key):
        """The app's logo, when it is a square picture (RV's file is a wide wordmark)."""
        path = ResourcePathManager.get_resource_path(f"resources/logos/{app_key}.png")
        if not path.exists():
            return None
        pixmap = QPixmap(str(path))
        if pixmap.isNull() or not (0.6 <= pixmap.width() / max(1, pixmap.height()) <= 1.6):
            return None
        return QIcon(pixmap)

    def open_folder(self, folder_type):
        if not self.project_manager:
            return
        path = self.project_manager.get_folder_path(
            self.current_project_code, folder_type, self.shot.reel_episode, self.shot.shot_name)
        if path and os.path.exists(path):
            self.project_manager.open_folder(path)
        else:
            self.result_label.setText(f"There is no {folder_type} folder for this shot yet.")
            self.result_label.setStyleSheet(f"color: {Gate.WARN};")

    def _launch_dcc_for_shot(self, app_key: str, app_label: str):
        if app_key == "rv":
            self.rv_review_requested.emit(self.shot)
            return
        if app_key in ("after_effects", "premiere"):
            target_file = self._resolve_or_prompt_dcc_target_file(app_key)
            if not target_file:
                return
        else:
            target_file = self._find_dcc_target_file(app_key)
        from slate.core.dcc_launcher import DCCLauncher
        DCCLauncher(self).launch(app_key, self.shot.id, file_path=target_file)

    _DCC_FILE_TYPES = {
        "nuke": [".nk", ".nknc"],
        "after_effects": [".aep", ".aepx"],
        "premiere": [".prproj"],
        "blender": [".blend"],
    }

    def _find_dcc_target_file(self, app_key: str):
        exts = self._DCC_FILE_TYPES.get(app_key, [])
        candidates = []
        for base in self._candidate_shot_roots():
            if not base.exists():
                continue
            for ext in exts:
                try:
                    candidates.extend(base.rglob(f"*{ext}"))
                except Exception:
                    continue

        def _mtime(p):
            try:
                return p.stat().st_mtime
            except OSError:
                return 0
        return max(candidates, key=_mtime) if candidates else None

    def _resolve_or_prompt_dcc_target_file(self, app_key: str):
        if not self._DCC_FILE_TYPES.get(app_key):
            return None
        found = self._find_dcc_target_file(app_key)
        if found:
            return found
        start_dir = next((str(r) for r in self._candidate_shot_roots() if r.exists()), str(Path.home()))
        filter_map = {
            "nuke": "Nuke script (*.nk *.nknc)",
            "after_effects": "After Effects project (*.aep *.aepx)",
            "premiere": "Premiere project (*.prproj)",
            "blender": "Blender file (*.blend)",
        }
        selected, _ = QFileDialog.getOpenFileName(self, "Choose the file to open", start_dir,
                                                  filter_map.get(app_key, "All files (*.*)"))
        return Path(selected) if selected else None

    def _candidate_shot_roots(self):
        roots = []
        if self.project_manager:
            for key in ("comp", "output", "prep", "scan"):
                try:
                    path = self.project_manager.get_folder_path(
                        self.current_project_code, key, self.shot.reel_episode, self.shot.shot_name)
                    if path:
                        roots.append(Path(path))
                except Exception:
                    continue
        folder_paths = getattr(self.shot, "folder_paths", {}) or {}
        if isinstance(folder_paths, dict):
            for value in folder_paths.values():
                text = str(value or "").strip()
                if text:
                    roots.append(Path(text))
        unique, seen = [], set()
        for root in roots:
            norm = str(root).strip().lower()
            if norm and norm not in seen:
                seen.add(norm)
                unique.append(root)
        return unique

    # ------------------------------------------------------------ feedback badges
    def _get_feedback_state_path(self) -> Path:
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
        return keys or {"unknown_user"}

    def _get_assigned_artist_identity(self) -> str:
        direct = self._normalize_identity(getattr(self.shot, "assigned_artist", ""))
        if direct:
            return direct
        return self._normalize_identity(getattr(self.shot.dept("comp"), "artist", ""))

    def _feedback_owner_bucket_key(self) -> str:
        return self._get_assigned_artist_identity() or "__unassigned__"

    def _viewer_can_ack_feedback(self) -> bool:
        owner = self._get_assigned_artist_identity()
        return (not owner) or owner in self.viewer_identity_keys

    def _load_feedback_seen_state(self) -> dict:
        try:
            if self.feedback_state_path.exists():
                with open(self.feedback_state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, dict) else {}
        except Exception as exc:
            logging.debug("Failed to load feedback seen state: %s", exc)
        return {}

    def _save_feedback_seen_state(self):
        try:
            with open(self.feedback_state_path, "w", encoding="utf-8") as f:
                json.dump(self.feedback_seen_state, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            logging.debug("Failed to persist feedback seen state: %s", exc)

    def _feedback_shot_key(self) -> str:
        return f"{self.current_project_code}:{self.shot.reel_episode}:{self.shot.shot_name}"

    @staticmethod
    def _feedback_signature(entries) -> str:
        if not entries:
            return ""
        chunks = [f"{getattr(e, 'date', '')}|{getattr(e, 'source', '')}|{getattr(e, 'logged_by', '')}|"
                  f"{getattr(e, 'text', str(e))}" for e in entries]
        return hashlib.sha1("\n".join(chunks).encode("utf-8")).hexdigest()

    def _collect_feedback_signatures(self) -> dict:
        return {
            "Client": self._feedback_signature(self.shot.feedback_client),
            "Director": self._feedback_signature(self.shot.feedback_director),
            "Internal": self._feedback_signature(self.shot.feedback_internal),
        }

    def _seen_feedback_for_current_shot(self) -> dict:
        owner_bucket = self.feedback_seen_state.setdefault(self._feedback_owner_bucket_key(), {})
        return owner_bucket.setdefault(self._feedback_shot_key(), {})

    def _update_feedback_tab_badges(self):
        seen = self._seen_feedback_for_current_shot()
        for idx, tab_name in enumerate(self._feedback_tab_order):
            sig = self._feedback_signature_map.get(tab_name, "")
            unread = bool(sig) and sig != seen.get(tab_name, "")
            self.feedback_tabs.setTabText(idx, f"{tab_name} •" if unread else tab_name)

    def _mark_feedback_tab_seen(self, tab_index: int):
        if tab_index < 0 or tab_index >= len(self._feedback_tab_order):
            return
        if not self._viewer_can_ack_feedback():
            return
        tab_name = self._feedback_tab_order[tab_index]
        sig = self._feedback_signature_map.get(tab_name, "")
        seen = self._seen_feedback_for_current_shot()
        if sig:
            seen[tab_name] = sig
        else:
            seen.pop(tab_name, None)
        self._save_feedback_seen_state()
        self._update_feedback_tab_badges()

    def _on_feedback_tab_changed(self, tab_index: int):
        self._mark_feedback_tab_seen(tab_index)
