"""
Delivery batches management dialog.

Item 4.4: Allows production to group versions into named packages, review what
went out together, and generate client delivery notes.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QSplitter, QWidget, QFrame,
    QLineEdit, QTextEdit, QMessageBox, QListWidget, QListWidgetItem,
    QApplication, QHeaderView,
)

from slate.core.domain.deliveries import DeliveryStore, Delivery
from slate.core.domain.versions import VersionStore


class CreateDeliveryDialog(QDialog):
    """Sub-dialog to assemble and create a new delivery batch."""

    def __init__(self, project_code: str, preselected_version_ids: Optional[List[int]] = None,
                 store: Optional[DeliveryStore] = None, version_store: Optional[VersionStore] = None,
                 created_by: str = "", parent=None):
        super().__init__(parent)
        self.project_code = project_code
        self.store = store or DeliveryStore()
        self.version_store = version_store or VersionStore()
        self.created_by = created_by
        self.preselected_version_ids = set(preselected_version_ids or [])
        self.created_delivery = None

        self.setWindowTitle(f"Create Delivery Batch - {project_code}")
        self.resize(700, 520)
        self.setStyleSheet("QDialog { background-color: #0D0D0F; color: #E8E6E1; } QLabel { background: transparent; border: none; }")

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Form fields
        form_frame = QFrame()
        form_frame.setStyleSheet("background: #16161A; border: 1px solid #1D1D22; border-radius: 6px; padding: 10px;")
        form_l = QVBoxLayout(form_frame)
        form_l.setSpacing(8)

        # Name
        today_str = date.today().strftime("%Y%m%d")
        name_l = QHBoxLayout()
        name_l.addWidget(QLabel("Package Name:"))
        self.name_input = QLineEdit(f"DEL_{today_str}_01")
        name_l.addWidget(self.name_input)
        form_l.addLayout(name_l)

        # Recipient
        recip_l = QHBoxLayout()
        recip_l.addWidget(QLabel("Recipient:"))
        self.recipient_input = QLineEdit()
        self.recipient_input.setPlaceholderText("e.g. Client VFX Editor / Editorial")
        recip_l.addWidget(self.recipient_input)
        form_l.addLayout(recip_l)

        # Notes
        notes_l = QVBoxLayout()
        notes_l.addWidget(QLabel("Delivery Notes / Instructions:"))
        self.notes_input = QTextEdit()
        self.notes_input.setPlaceholderText("Notes describing the purpose of this package...")
        self.notes_input.setMaximumHeight(70)
        notes_l.addWidget(self.notes_input)
        form_l.addLayout(notes_l)

        layout.addWidget(form_frame)

        # Versions Table Selection
        layout.addWidget(QLabel("Select Versions to Include in Delivery:"))
        self.versions_table = QTableWidget(0, 5)
        self.versions_table.setHorizontalHeaderLabels(["Select", "Shot", "Version", "Dept", "Status"])
        self.versions_table.verticalHeader().setVisible(False)
        self.versions_table.setAlternatingRowColors(True)
        self.versions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.versions_table.horizontalHeader().setStretchLastSection(True)
        self.versions_table.setStyleSheet("""
            QTableWidget {
                background-color: #0D0D0F;
                color: #E8E6E1;
                gridline-color: #1D1D22;
                border: 1px solid #1D1D22;
                border-radius: 6px;
            }
            QTableWidget::item:alternate { background-color: #16161A; }
            QTableWidget::item:selected { background-color: rgba(62, 168, 191, 0.18); }
            QHeaderView::section {
                background-color: #16161A;
                color: #87857F;
                border: none;
                border-bottom: 2px solid #1D1D22;
                border-right: 1px solid rgba(255, 255, 255, 0.04);
                padding: 6px 10px;
                font-weight: 700;
                font-size: 11px;
                text-transform: uppercase;
            }
        """)
        layout.addWidget(self.versions_table)

        self._populate_versions()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("secondaryButton")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.submit_btn = QPushButton("Create Delivery")
        self.submit_btn.setObjectName("primaryButton")
        self.submit_btn.clicked.connect(self._on_submit)
        btn_layout.addWidget(self.submit_btn)

        layout.addLayout(btn_layout)

    def _populate_versions(self):
        try:
            # Query all versions for project
            db = self.version_store.db
            rows = db.execute_query(
                "SELECT * FROM tracking_versions WHERE project_code=%s ORDER BY shot_name, version_name",
                (self.project_code,), fetch="all",
            ) or []
        except Exception:
            rows = []

        self.versions_table.setRowCount(len(rows))
        for row_idx, r in enumerate(rows):
            v_id = int(r.get("id") or -1)
            shot = str(r.get("shot_name") or "")
            ver = str(r.get("version_name") or "")
            dept = str(r.get("department") or "")
            st = str(r.get("status") or "")

            item_check = QTableWidgetItem()
            item_check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            is_checked = v_id in self.preselected_version_ids
            item_check.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
            item_check.setData(Qt.ItemDataRole.UserRole, v_id)

            self.versions_table.setItem(row_idx, 0, item_check)
            self.versions_table.setItem(row_idx, 1, QTableWidgetItem(shot))
            self.versions_table.setItem(row_idx, 2, QTableWidgetItem(ver))
            self.versions_table.setItem(row_idx, 3, QTableWidgetItem(dept))
            self.versions_table.setItem(row_idx, 4, QTableWidgetItem(st))

    def _on_submit(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Package name cannot be empty.")
            return

        selected_ids = []
        for row in range(self.versions_table.rowCount()):
            item = self.versions_table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                selected_ids.append(int(item.data(Qt.ItemDataRole.UserRole)))

        if not selected_ids:
            QMessageBox.warning(self, "Validation Error", "Please select at least one version to include.")
            return

        delivery = self.store.create_delivery(
            project_code=self.project_code,
            name=name,
            version_ids=selected_ids,
            recipient=self.recipient_input.text().strip(),
            notes=self.notes_input.toPlainText().strip(),
            created_by=self.created_by,
        )

        if not delivery:
            QMessageBox.critical(self, "Error", "Failed to create delivery record in database.")
            return

        self.created_delivery = delivery
        QMessageBox.information(
            self, "Delivery Created",
            f"Delivery '{name}' created with {len(selected_ids)} version(s)."
        )
        self.accept()


class DeliveryBatchesDialog(QDialog):
    """Main dialog to inspect, copy, and manage delivery batches for a project."""

    def __init__(self, project_code: str, store: Optional[DeliveryStore] = None,
                 current_user: str = "", parent=None):
        super().__init__(parent)
        self.project_code = project_code
        self.store = store or DeliveryStore()
        self.current_user = current_user
        self.deliveries: List[Delivery] = []

        self.setWindowTitle(f"Delivery Batches - {project_code}")
        self.resize(1000, 600)
        self.setStyleSheet("QDialog { background-color: #0D0D0F; color: #E8E6E1; } QLabel { background: transparent; border: none; }")

        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Deliveries List
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        lbl_hdr = QLabel("Delivery Packages:")
        lbl_hdr.setStyleSheet("font-weight: 700; color: #E8E6E1; font-size: 13px;")
        left_layout.addWidget(lbl_hdr)

        self.delivery_list = QListWidget()
        self.delivery_list.setStyleSheet("""
            QListWidget {
                background-color: #16161A;
                border: 1px solid #1D1D22;
                border-radius: 6px;
                padding: 4px;
            }
            QListWidget::item {
                padding: 8px 12px;
                border-radius: 4px;
                color: #E8E6E1;
            }
            QListWidget::item:selected {
                background-color: rgba(62, 168, 191, 0.18);
                color: #3EA8BF;
                font-weight: bold;
            }
        """)
        self.delivery_list.currentRowChanged.connect(self._on_delivery_selected)
        left_layout.addWidget(self.delivery_list)

        left_actions = QHBoxLayout()
        left_actions.setSpacing(8)
        self.create_btn = QPushButton("+ New Delivery")
        self.create_btn.setObjectName("primaryButton")
        self.create_btn.clicked.connect(self._on_create_clicked)
        left_actions.addWidget(self.create_btn)

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setObjectName("dangerButton")
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        left_actions.addWidget(self.delete_btn)

        left_layout.addLayout(left_actions)
        splitter.addWidget(left_widget)

        # Right: Delivery Details & Items
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        # Meta info card
        self.meta_frame = QFrame()
        self.meta_frame.setStyleSheet("background: #16161A; border: 1px solid #1D1D22; border-radius: 6px; padding: 12px;")
        meta_l = QVBoxLayout(self.meta_frame)
        meta_l.setSpacing(4)

        self.title_lbl = QLabel("Select a delivery package from the left.")
        self.title_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #E8E6E1;")
        meta_l.addWidget(self.title_lbl)

        self.info_lbl = QLabel("")
        self.info_lbl.setStyleSheet("font-size: 12px; color: #87857F;")
        meta_l.addWidget(self.info_lbl)

        self.notes_lbl = QLabel("")
        self.notes_lbl.setStyleSheet("font-size: 12px; color: #E8E6E1; font-style: italic;")
        self.notes_lbl.setWordWrap(True)
        meta_l.addWidget(self.notes_lbl)

        right_layout.addWidget(self.meta_frame)

        # Items Table
        lbl_tbl = QLabel("Versions Included in Delivery:")
        lbl_tbl.setStyleSheet("font-weight: 700; color: #E8E6E1; font-size: 13px; margin-top: 4px;")
        right_layout.addWidget(lbl_tbl)
        self.items_table = QTableWidget(0, 5)
        self.items_table.setHorizontalHeaderLabels(["Shot", "Version", "Dept", "Status at Send", "Media Path"])
        self.items_table.verticalHeader().setVisible(False)
        self.items_table.setAlternatingRowColors(True)
        self.items_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.items_table.horizontalHeader().setStretchLastSection(True)
        self.items_table.setStyleSheet("""
            QTableWidget {
                background-color: #0D0D0F;
                color: #E8E6E1;
                gridline-color: #1D1D22;
                border: 1px solid #1D1D22;
                border-radius: 6px;
            }
            QTableWidget::item:alternate { background-color: #16161A; }
            QTableWidget::item:selected { background-color: rgba(62, 168, 191, 0.18); }
            QHeaderView::section {
                background-color: #16161A;
                color: #87857F;
                border: none;
                border-bottom: 2px solid #1D1D22;
                border-right: 1px solid rgba(255, 255, 255, 0.04);
                padding: 6px 10px;
                font-weight: 700;
                font-size: 11px;
                text-transform: uppercase;
            }
        """)
        right_layout.addWidget(self.items_table)

        # Right Actions
        right_actions = QHBoxLayout()
        right_actions.addStretch()

        self.copy_note_btn = QPushButton("Copy Delivery Note to Clipboard")
        self.copy_note_btn.setObjectName("secondaryButton")
        self.copy_note_btn.clicked.connect(self._on_copy_note_clicked)
        right_actions.addWidget(self.copy_note_btn)

        right_layout.addLayout(right_actions)
        splitter.addWidget(right_widget)

        splitter.setStretchFactor(0, 3)  # 30% List
        splitter.setStretchFactor(1, 7)  # 70% Details
        main_layout.addWidget(splitter)

    def refresh(self):
        self.deliveries = self.store.list_deliveries(self.project_code)
        self.delivery_list.clear()

        for d in self.deliveries:
            item = QListWidgetItem(f"{d.name} ({d.delivery_date})")
            item.setData(Qt.ItemDataRole.UserRole, d.id)
            self.delivery_list.addItem(item)

        if self.deliveries:
            self.delivery_list.setCurrentRow(0)
        else:
            self._on_delivery_selected(-1)

    def _on_delivery_selected(self, row: int):
        if row < 0 or row >= len(self.deliveries):
            self.title_lbl.setText("No delivery selected.")
            self.info_lbl.setText("")
            self.notes_lbl.setText("")
            self.items_table.setRowCount(0)
            self.delete_btn.setEnabled(False)
            self.copy_note_btn.setEnabled(False)
            return

        d_summary = self.deliveries[row]
        delivery = self.store.get_delivery(d_summary.id)
        if not delivery:
            return

        self.delete_btn.setEnabled(True)
        self.copy_note_btn.setEnabled(True)

        self.title_lbl.setText(f"{delivery.name}")
        self.info_lbl.setText(
            f"Date: {delivery.delivery_date} | Recipient: {delivery.recipient or 'Client VFX'} | "
            f"Sent by: {delivery.created_by or 'Production'}"
        )
        self.notes_lbl.setText(f"Notes: {delivery.notes}" if delivery.notes else "No notes provided.")

        self.items_table.setRowCount(len(delivery.items))
        for r_idx, item in enumerate(delivery.items):
            self.items_table.setItem(r_idx, 0, QTableWidgetItem(item.shot_name))
            self.items_table.setItem(r_idx, 1, QTableWidgetItem(item.version_name))
            self.items_table.setItem(r_idx, 2, QTableWidgetItem(item.department))
            self.items_table.setItem(r_idx, 3, QTableWidgetItem(item.status_at_delivery))
            self.items_table.setItem(r_idx, 4, QTableWidgetItem(item.media_path or "-"))

    def _on_create_clicked(self):
        dlg = CreateDeliveryDialog(
            project_code=self.project_code,
            store=self.store,
            created_by=self.current_user,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _on_delete_clicked(self):
        row = self.delivery_list.currentRow()
        if row < 0 or row >= len(self.deliveries):
            return

        d = self.deliveries[row]
        ans = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete delivery package '{d.name}'? This will not delete the versions themselves.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self.store.delete_delivery(d.id)
            self.refresh()

    def _on_copy_note_clicked(self):
        row = self.delivery_list.currentRow()
        if row < 0 or row >= len(self.deliveries):
            return

        d = self.deliveries[row]
        note = self.store.generate_delivery_note(d.id)
        clipboard = QApplication.clipboard()
        clipboard.setText(note)
        QMessageBox.information(self, "Copied", "Delivery note copied to clipboard.")
