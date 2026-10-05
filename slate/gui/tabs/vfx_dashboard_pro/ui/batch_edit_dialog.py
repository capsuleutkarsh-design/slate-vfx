from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, 
    QComboBox, QDateEdit, QPushButton, QFrame, QGridLayout
)
from PySide6.QtCore import Qt, QDate
from slate.core.infra.design_tokens import (
    ColorTokens as C,
    SpacingTokens as S,
    RadiusTokens as R,
    TypographyTokens as T,
)
from slate.core.system.adaptation_engine import system_engine
from slate.gui.widgets.styled_buttons import PrimaryButton, SecondaryButton
from slate.core.infra.gate import Gate


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

        self.setWindowTitle(f"Batch edit - {self.selected_count} shots")
        self.setMinimumWidth(self.sp(440, minimum=380))
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(self.sp(20), self.sp(20), self.sp(20), self.sp(20))
        layout.setSpacing(self.sp(16))

        # Title & Subtitle
        header_layout = QVBoxLayout()
        header_layout.setSpacing(self.sp(4))

        title = QLabel(f"Change {self.selected_count} shots")
        title.setStyleSheet(f"font-size: {T.SIZE_LG}px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: {Gate.TEXT};")
        header_layout.addWidget(title)

        subtitle = QLabel("Tick what to change. It becomes a pending edit on every selected shot - "
                          "Save writes it, Ctrl+Z takes it back.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"font-size: 12px; color: {Gate.TEXT_2};")
        header_layout.addWidget(subtitle)
        layout.addLayout(header_layout)

        # Main Properties Form
        form_frame = QFrame()
        form_frame.setObjectName("formFrame")
        # Scoped to the frame: a selector-less sheet boxed every label in it.
        form_frame.setStyleSheet(
            f"QFrame#formFrame {{ background-color: {Gate.PANEL}; border: 1px solid {Gate.LINE};"
            f" border-radius: {Gate.RADIUS_LG}px; }}"
        )
        grid = QGridLayout(form_frame)
        grid.setContentsMargins(self.sp(12), self.sp(12), self.sp(12), self.sp(12))
        grid.setSpacing(self.sp(12))

        # 1. Status
        self.status_cb = QCheckBox("Status")
        self.status_cb.setStyleSheet("font-weight: 600;")
        self.status_combo = QComboBox()
        from slate.core.domain import shot_status
        self.status_combo.addItems(list(shot_status.WORKFLOW))
        self.status_combo.setEnabled(False)
        self.status_cb.toggled.connect(self.status_combo.setEnabled)
        grid.addWidget(self.status_cb, 0, 0)
        grid.addWidget(self.status_combo, 0, 1)

        # 2. Assigned Artist
        self.artist_cb = QCheckBox("Artist")
        self.artist_cb.setStyleSheet("font-weight: 600;")
        self.artist_combo = QComboBox()
        self.artist_combo.addItem("Unassigned", "")
        for user in self.all_users:
            self.artist_combo.addItem(user, user)
        self.artist_combo.setEnabled(False)
        self.artist_cb.toggled.connect(self.artist_combo.setEnabled)
        grid.addWidget(self.artist_cb, 1, 0)
        grid.addWidget(self.artist_combo, 1, 1)

        # 3. Priority
        self.priority_cb = QCheckBox("Priority")
        self.priority_cb.setStyleSheet("font-weight: 600;")
        self.priority_combo = QComboBox()
        for value, label in shot_status.priorities():
            self.priority_combo.addItem(label, value)
        self.priority_combo.setCurrentIndex(max(0, self.priority_combo.findData(2)))
        self.priority_combo.setEnabled(False)
        self.priority_cb.toggled.connect(self.priority_combo.setEnabled)
        grid.addWidget(self.priority_cb, 2, 0)
        grid.addWidget(self.priority_combo, 2, 1)

        # 4. Shot Type
        self.type_cb = QCheckBox("Type")
        self.type_cb.setStyleSheet("font-weight: 600;")
        self.type_combo = QComboBox()
        # The studio's shot types - the same list as the grid and the panel.
        self.type_combo.addItems(shot_status.shot_types())
        self.type_combo.setEnabled(False)
        self.type_cb.toggled.connect(self.type_combo.setEnabled)
        grid.addWidget(self.type_cb, 3, 0)
        grid.addWidget(self.type_combo, 3, 1)

        # 5. Target Date
        self.target_cb = QCheckBox("Target")
        self.target_cb.setStyleSheet("font-weight: 600;")
        # "No date" clears the target on every shot; a date sets it.
        from .date_fields import OptionalDateField
        self.target_edit = OptionalDateField()
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

        self.apply_btn = PrimaryButton(f"Apply to {self.selected_count} shots")
        self.apply_btn.clicked.connect(self._on_apply)
        btn_layout.addWidget(self.apply_btn)

        layout.addLayout(btn_layout)
        # Nothing ticked, nothing to apply: the button says so by being off.
        self.apply_btn.setEnabled(False)
        self.apply_btn.setToolTip("Tick at least one field to change")
        for box in (self.status_cb, self.artist_cb, self.priority_cb, self.type_cb, self.target_cb):
            box.toggled.connect(self._sync_apply)

    def _sync_apply(self, *_):
        ticked = any(b.isChecked() for b in (self.status_cb, self.artist_cb, self.priority_cb,
                                              self.type_cb, self.target_cb))
        self.apply_btn.setEnabled(ticked)
        self.apply_btn.setToolTip("" if ticked else "Tick at least one field to change")

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
            updates["priority"] = self.priority_combo.currentData()
        if self.type_cb.isChecked():
            updates["shot_type"] = self.type_combo.currentText()
        if self.target_cb.isChecked():
            updates["target"] = self.target_edit.value()
        return updates
