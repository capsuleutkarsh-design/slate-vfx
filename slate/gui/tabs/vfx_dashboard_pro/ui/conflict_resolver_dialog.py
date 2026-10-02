"""
Conflict Resolution Dialog for Optimistic Concurrency Control (OCC).
Displays concurrent modification conflicts and gives user choice between
reloading latest database state, reviewing diffs, or force-saving (supervisors).
"""

from typing import List, Optional
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit
)
from slate.core.infra.gate import Gate


class ConflictResolverDialog(QDialog):
    """
    Presents conflicting shot modifications when OCC detects concurrent edits.
    """
    def __init__(self, conflicts_summary: str, details: Optional[str] = None, can_force: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Data Conflict Detected — Optimistic Concurrency Control")
        self.resize(650, 400)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {Gate.ACCENT_SURFACE}; color: {Gate.ACCENT}; }}
            QLabel {{ color: {Gate.ACCENT}; }}
            QTextEdit {{ background-color: {Gate.ACCENT_SURFACE}; color: {Gate.BAD}; border: 1px solid {Gate.INFO}; border-radius: 6px; }}
            QPushButton {{ border-radius: 6px; padding: 8px 16px; font-weight: bold; }}
        """)

        self.action_selected = "cancel"
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # Header with warning icon
        hdr_layout = QHBoxLayout()
        icon_lbl = QLabel("⚠️")
        icon_lbl.setFont(QFont("Segoe UI Emoji", 24))
        hdr_layout.addWidget(icon_lbl)

        title_lbl = QLabel("Concurrent Modifications Detected")
        title_lbl.setFont(QFont("Inter", 14, QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color: {Gate.WARN};")
        hdr_layout.addWidget(title_lbl)
        hdr_layout.addStretch()
        layout.addLayout(hdr_layout)

        desc_lbl = QLabel(
            "Another artist or supervisor has updated the database since you loaded these shots.\n"
            "To prevent silently overwriting their work, your save was paused."
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"color: {Gate.INFO}; font-size: 13px;")
        layout.addWidget(desc_lbl)

        # Conflict Details Box
        detail_box = QTextEdit()
        detail_box.setReadOnly(True)
        content = f"Conflicts Summary:\n{conflicts_summary}"
        if details:
            content += f"\n\nDetails:\n{details}"
        detail_box.setPlainText(content)
        layout.addWidget(detail_box)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.btn_refresh = QPushButton("Reload Latest from Database (Recommended)")
        self.btn_refresh.setStyleSheet(f"background-color: {Gate.ACCENT}; color: {Gate.ACCENT_SURFACE};")
        self.btn_refresh.clicked.connect(self._on_reload)
        btn_layout.addWidget(self.btn_refresh)

        if can_force:
            self.btn_force = QPushButton("Force Overwrite")
            self.btn_force.setStyleSheet(f"background-color: {Gate.BAD}; color: {Gate.ACCENT_SURFACE};")
            self.btn_force.setToolTip("Overwrite database records with your local version")
            self.btn_force.clicked.connect(self._on_force)
            btn_layout.addWidget(self.btn_force)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet(f"background-color: {Gate.INFO}; color: {Gate.ACCENT};")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        layout.addLayout(btn_layout)

    def _on_reload(self):
        self.action_selected = "reload"
        self.accept()

    def _on_force(self):
        self.action_selected = "force"
        self.accept()
