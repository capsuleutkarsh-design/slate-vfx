from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, 
    QComboBox, QDateEdit, QPushButton, QFrame, QGridLayout
)
from PySide6.QtCore import Qt, QDate
from ut_vfx.core.infra.design_tokens import (
    ColorTokens as C,
    SpacingTokens as S,
    RadiusTokens as R,
    TypographyTokens as T,
)
from ut_vfx.core.system.adaptation_engine import system_engine
from ut_vfx.gui.widgets.styled_buttons import PrimaryButton, SecondaryButton


class BatchEditDialog(QDialog):
    """
    Dialog for batch editing multiple selected shots simultaneously.
    Provides selective opt-in checkboxes for each property.
    """

    def __init__(self, selected_count: int, all_users: list = None, parent=None):
        super().__init__(parent)
        self.selected_count = selected_count
        self.all_users = sorted(list(set(str(u).strip() for u in (all_users or []) if str(u).strip())))
        self.sp = system_engine.scale_px

        self.setWindowTitle(f"Batch Edit ({self.selected_count} Shots)")
        self.setMinimumWidth(self.sp(440, minimum=380))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(self.sp(20), self.sp(20), self.sp(20), self.sp(20))
        layout.setSpacing(self.sp(16))

        # Title & Subtitle
        header_layout = QVBoxLayout()
        header_layout.setSpacing(self.sp(4))

        title = QLabel(f"Batch Edit {self.selected_count} Shots")
        title.setStyleSheet(f"font-size: {T.SIZE_LG}px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: #E8E6E1;")
        header_layout.addWidget(title)

        subtitle = QLabel("Check the properties you want to update across all selected shots.")
        subtitle.setStyleSheet("font-size: 12px; color: #6BA4C9;")
        header_layout.addWidget(subtitle)
        layout.addLayout(header_layout)

        # Main Properties Form
        form_frame = QFrame()
        form_frame.setStyleSheet(
            f"background-color: {C.BG_SURFACE}; border: 1px solid {C.BORDER_DEFAULT}; border-radius: {R.MD}px; padding: {S.MD}px;"
        )
        grid = QGridLayout(form_frame)
        grid.setContentsMargins(self.sp(12), self.sp(12), self.sp(12), self.sp(12))
        grid.setSpacing(self.sp(12))

        # 1. Status
        self.status_cb = QCheckBox("Status:")
        self.status_cb.setStyleSheet("font-weight: 600; color: #E8E6E1;")
        self.status_combo = QComboBox()
        self.status_combo.addItems(["WIP", "APPROVED", "RETAKE", "SENT FOR REVIEW", "YTS", "READY", "OMIT"])
        self.status_combo.setEnabled(False)
        self.status_cb.toggled.connect(self.status_combo.setEnabled)
        grid.addWidget(self.status_cb, 0, 0)
        grid.addWidget(self.status_combo, 0, 1)

        # 2. Assigned Artist
        self.artist_cb = QCheckBox("Assigned Artist:")
        self.artist_cb.setStyleSheet("font-weight: 600; color: #E8E6E1;")
        self.artist_combo = QComboBox()
        self.artist_combo.addItem("Unassigned", "")
        for user in self.all_users:
            self.artist_combo.addItem(user, user)
        self.artist_combo.setEnabled(False)
        self.artist_cb.toggled.connect(self.artist_combo.setEnabled)
        grid.addWidget(self.artist_cb, 1, 0)
        grid.addWidget(self.artist_combo, 1, 1)

        # 3. Priority
        self.priority_cb = QCheckBox("Priority:")
        self.priority_cb.setStyleSheet("font-weight: 600; color: #E8E6E1;")
        self.priority_combo = QComboBox()
        self.priority_combo.addItems(["0 (Urgent)", "1 (High)", "2 (Normal)", "3 (Low)"])
        self.priority_combo.setCurrentIndex(2)
        self.priority_combo.setEnabled(False)
        self.priority_cb.toggled.connect(self.priority_combo.setEnabled)
        grid.addWidget(self.priority_cb, 2, 0)
        grid.addWidget(self.priority_combo, 2, 1)

        # 4. Shot Type
        self.type_cb = QCheckBox("Shot Type:")
        self.type_cb.setStyleSheet("font-weight: 600; color: #E8E6E1;")
        self.type_combo = QComboBox()
        self.type_combo.addItems(["Prep", "2D Comp", "2.5D Comp", "CG Comp", "AI Shot", "Roto", "DMP"])
        self.type_combo.setEnabled(False)
        self.type_cb.toggled.connect(self.type_combo.setEnabled)
        grid.addWidget(self.type_cb, 3, 0)
        grid.addWidget(self.type_combo, 3, 1)

        # 5. Target Date
        self.target_cb = QCheckBox("Target Date:")
        self.target_cb.setStyleSheet("font-weight: 600; color: #E8E6E1;")
        self.target_edit = QDateEdit()
        self.target_edit.setCalendarPopup(True)
        self.target_edit.setDisplayFormat("yyyy-MM-dd")
        self.target_edit.setDate(QDate.currentDate())
        self.target_edit.setEnabled(False)
        self.target_cb.toggled.connect(self.target_edit.setEnabled)
        grid.addWidget(self.target_cb, 4, 0)
        grid.addWidget(self.target_edit, 4, 1)

        layout.addWidget(form_frame)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(self.sp(10))
        btn_layout.addStretch()

        self.cancel_btn = SecondaryButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.apply_btn = PrimaryButton(f"Apply to {self.selected_count} Shots")
        self.apply_btn.clicked.connect(self._on_apply)
        btn_layout.addWidget(self.apply_btn)

        layout.addLayout(btn_layout)

    def _on_apply(self):
        # Verify at least one option is checked
        if not any([
            self.status_cb.isChecked(),
            self.artist_cb.isChecked(),
            self.priority_cb.isChecked(),
            self.type_cb.isChecked(),
            self.target_cb.isChecked(),
        ]):
            return
        self.accept()

    def get_updates(self) -> dict:
        """Return dictionary of fields that were selected for update."""
        updates = {}
        if self.status_cb.isChecked():
            updates["status"] = self.status_combo.currentText()
        if self.artist_cb.isChecked():
            updates["assigned_artist"] = self.artist_combo.currentData() or ""
        if self.priority_cb.isChecked():
            updates["priority"] = self.priority_combo.currentIndex()
        if self.type_cb.isChecked():
            updates["shot_type"] = self.type_combo.currentText()
        if self.target_cb.isChecked():
            updates["target"] = self.target_edit.date().toString("yyyy-MM-dd")
        return updates
