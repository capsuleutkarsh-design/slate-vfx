"""
Deliveries: production groups versions into named packages, sees what went
out together, and copies a delivery note for the client.

New delivery lists the latest Approved version of each shot and department
(with a switch to see every version, and a search), says so before a version
that is not Approved or has no media goes in, and suggests a name nobody used
today. A package that cannot be read, or a list that cannot be read, says so.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QSplitter, QWidget, QFrame,
    QLineEdit, QTextEdit, QMessageBox, QListWidget, QListWidgetItem,
    QApplication, QHeaderView,
)

from slate.core.domain.deliveries import DeliveryStore, Delivery
from slate.core.domain.versions import STATUS_APPROVED, VersionStore, department_label
from slate.gui.core.offline_notice import on_database_error
from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button
from slate.gui.core.empty_state import EmptyState
from slate.gui.core.table_style import style_table

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else keeps the fallback it already had.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""


def _date_text(value) -> str:
    from slate.core.domain.dates import format_date
    return format_date(value) or str(value or "")


def _visible_ticks(table):
    """Tick boxes that show in every theme (unticked ones were dark on dark)."""
    from slate.core.infra.gate import Gate as G
    table.setStyleSheet(
        f"QTableWidget::indicator {{ width: 14px; height: 14px; border: 1px solid {G.TEXT_2};"
        f" border-radius: 3px; background: {G.PANEL}; }}"
        f"QTableWidget::indicator:checked {{ background: {G.ACCENT}; border-color: {G.ACCENT}; }}")


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

        self.setWindowTitle(f"New delivery - {project_code}")
        self.resize(700, 520)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Form fields
        form_frame = QFrame()
        form_frame.setObjectName("deliveryForm")
        form_frame.setStyleSheet(f"QFrame#deliveryForm {{ background: {Gate.PANEL}; border: 1px solid {Gate.LINE};"
                                 f" border-radius: {Gate.RADIUS_LG}px; }}")
        form_l = QVBoxLayout(form_frame)
        form_l.setSpacing(8)

        # Name: the first number nobody used today.
        name_l = QHBoxLayout()
        name_l.addWidget(QLabel("Package name"))
        self.name_input = QLineEdit(self.store.next_free_name(self.project_code)
                                    if hasattr(self.store, "next_free_name")
                                    else f"DEL_{date.today().strftime('%Y%m%d')}_01")
        name_l.addWidget(self.name_input)
        form_l.addLayout(name_l)

        # Recipient
        recip_l = QHBoxLayout()
        recip_l.addWidget(QLabel("Recipient"))
        self.recipient_input = QLineEdit()
        self.recipient_input.setPlaceholderText("e.g. Client VFX Editor / Editorial")
        recip_l.addWidget(self.recipient_input)
        form_l.addLayout(recip_l)

        # Notes
        notes_l = QVBoxLayout()
        notes_l.addWidget(QLabel("Notes for the recipient"))
        self.notes_input = QTextEdit()
        self.notes_input.setPlaceholderText("What this package is for…")
        self.notes_input.setMaximumHeight(70)
        notes_l.addWidget(self.notes_input)
        form_l.addLayout(notes_l)

        layout.addWidget(form_frame)

        # Versions: the latest approved ones unless asked for everything.
        pick_row = QHBoxLayout()
        pick_row.addWidget(QLabel("Versions in this delivery"))
        pick_row.addStretch(1)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search shots…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._filter_rows)
        pick_row.addWidget(self.search_input)
        self.show_all = QCheckBox("Show every version")
        self.show_all.setToolTip("By default only the latest Approved version of each shot and department is listed")
        self.show_all.toggled.connect(self._populate_versions)
        pick_row.addWidget(self.show_all)
        layout.addLayout(pick_row)
        self.versions_table = QTableWidget(0, 7)
        self.versions_table.setHorizontalHeaderLabels(
            ["Include", "Reel", "Shot", "Version", "Department", "Status", "Media"])
        style_table(self.versions_table, {"Shot": "contents", "Media": "stretch"}, multi_select=False)
        _visible_ticks(self.versions_table)
        layout.addWidget(self.versions_table)
        self.versions_empty = EmptyState.over(
            self.versions_table, "No approved versions yet",
            "Tick Show every version to package something else.", glyph="")

        self._populate_versions()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = make_button("Cancel", "secondary", on_click=self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.submit_btn = make_button("Create delivery", "primary", on_click=self._on_submit)
        btn_layout.addWidget(self.submit_btn)

        layout.addLayout(btn_layout)

    @on_database_error
    def _populate_versions(self, *_):
        ticked = set(self.preselected_version_ids) | set(self._ticked_ids()) \
            if self.versions_table.rowCount() else set(self.preselected_version_ids)
        db = self.version_store.db
        rows = db.execute_query(
            "SELECT * FROM tracking_versions WHERE project_code=%s ORDER BY reel, shot_name, version_name",
            (self.project_code,), fetch="all")
        if rows is None:
            rows = []
            self.versions_empty.set_message("The versions could not be read", "Close this and try again.")
        else:
            self.versions_empty.set_message("No approved versions yet",
                                            "Tick Show every version to package something else.")
        rows = [dict(r) for r in rows]
        if not self.show_all.isChecked():
            # The newest approved version of each shot and department.
            from slate.core.domain.versions import Version
            latest = {}
            for r in rows:
                if str(r.get("status") or "") != STATUS_APPROVED:
                    continue
                key = (str(r.get("reel") or ""), str(r.get("shot_name") or ""), str(r.get("department") or ""))
                if key not in latest or Version.from_row(r).number > Version.from_row(latest[key]).number:
                    latest[key] = r
            rows = [r for r in rows if r in latest.values() or int(r.get("id") or -1) in ticked]
        self._rows = rows

        self.versions_table.setRowCount(len(rows))
        for row_idx, r in enumerate(rows):
            v_id = int(r.get("id") or -1)
            item_check = QTableWidgetItem()
            item_check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            item_check.setCheckState(Qt.CheckState.Checked if v_id in ticked else Qt.CheckState.Unchecked)
            item_check.setData(Qt.ItemDataRole.UserRole, v_id)
            self.versions_table.setItem(row_idx, 0, item_check)
            values = [r.get("reel") or "-", r.get("shot_name") or "", r.get("version_name") or "",
                      department_label(r.get("department")) or "-", r.get("status") or "-",
                      r.get("media_path") or "No media recorded"]
            for col, value in enumerate(values, start=1):
                self.versions_table.setItem(row_idx, col, QTableWidgetItem(str(value)))
        self.versions_table.resizeColumnToContents(2)
        self.versions_empty.refresh()
        self._filter_rows()

    def _ticked_ids(self):
        out = []
        for row in range(self.versions_table.rowCount()):
            item = self.versions_table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                out.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return out

    def _filter_rows(self, *_):
        needle = self.search_input.text().strip().lower()
        for row in range(self.versions_table.rowCount()):
            text = " ".join((self.versions_table.item(row, c).text() if self.versions_table.item(row, c) else "")
                            for c in (1, 2, 3)).lower()
            self.versions_table.setRowHidden(row, bool(needle) and needle not in text)

    def _on_submit(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "New delivery", "Give the package a name.")
            return

        selected_ids = self._ticked_ids()
        if not selected_ids:
            QMessageBox.warning(self, "New delivery", "Tick at least one version to include.")
            return

        by_id = {int(r.get("id") or -1): r for r in getattr(self, "_rows", [])}
        picked = [by_id[i] for i in selected_ids if i in by_id]
        unapproved = [f"{r.get('shot_name')} {r.get('version_name')} ({r.get('status') or 'no status'})"
                      for r in picked if str(r.get("status") or "") != STATUS_APPROVED]
        no_media = [f"{r.get('shot_name')} {r.get('version_name')}" for r in picked if not r.get("media_path")]
        problems = []
        if unapproved:
            problems.append("Not approved: " + ", ".join(unapproved[:6]) + (" …" if len(unapproved) > 6 else ""))
        if no_media:
            problems.append("No media recorded: " + ", ".join(no_media[:6]) + (" …" if len(no_media) > 6 else ""))
        if problems and QMessageBox.question(
                self, "New delivery", "\n\n".join(problems) + "\n\nPut them in the package anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return

        try:
            delivery = self.store.create_delivery(
                project_code=self.project_code,
                name=name,
                version_ids=selected_ids,
                recipient=self.recipient_input.text().strip(),
                notes=self.notes_input.toPlainText().strip(),
                created_by=self.created_by,
            )
        except PermissionError as exc:
            QMessageBox.warning(self, "New delivery", str(exc))
            return

        if not delivery:
            QMessageBox.warning(self, "New delivery", getattr(self.store, "last_error", "")
                                or "The delivery could not be saved to the database.")
            return

        self.created_delivery = delivery
        self.accept()


class DeliveryBatchesDialog(QDialog):
    """Main dialog to inspect, copy, and manage delivery batches for a project."""

    def __init__(self, project_code: str, store: Optional[DeliveryStore] = None,
                 current_user: str = "", parent=None, roles=None, can_manage: bool = None):
        super().__init__(parent)
        self.project_code = project_code
        # The acting person's roles go to the store, which refuses a create or
        # delete they may not make (not only the hidden buttons).
        self.store = store or DeliveryStore(roles=roles)
        if roles is not None and getattr(self.store, "roles", None) is None:
            self.store.roles = roles
        self.can_manage = (getattr(self.store, "can_manage", lambda: True)()
                           if can_manage is None else bool(can_manage))
        self.current_user = current_user
        self.deliveries: List[Delivery] = []

        self.setWindowTitle(f"Deliveries - {project_code}")
        self.resize(1000, 600)
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

        lbl_hdr = QLabel("Deliveries")
        lbl_hdr.setStyleSheet(f"font-weight: 700; color: {Gate.TEXT}; font-size: 13px;")
        left_layout.addWidget(lbl_hdr)

        self.delivery_list = QListWidget()
        self.delivery_list.currentRowChanged.connect(self._on_delivery_selected)
        left_layout.addWidget(self.delivery_list)

        left_actions = QHBoxLayout()
        left_actions.setSpacing(8)
        self.create_btn = make_button("New delivery", "primary", icon="plus", on_click=self._on_create_clicked)
        left_actions.addWidget(self.create_btn)

        self.delete_btn = make_button("Delete", "danger", on_click=self._on_delete_clicked)
        left_actions.addWidget(self.delete_btn)
        left_actions.addStretch(1)
        # Making and deleting packages is production's job.
        self.create_btn.setVisible(self.can_manage)
        self.delete_btn.setVisible(self.can_manage)

        left_layout.addLayout(left_actions)
        splitter.addWidget(left_widget)

        # Right: Delivery Details & Items
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        # Meta info card
        self.meta_frame = QFrame()
        self.meta_frame.setObjectName("deliveryMeta")
        self.meta_frame.setStyleSheet(f"QFrame#deliveryMeta {{ background: {Gate.PANEL}; border: 1px solid {Gate.LINE};"
                                      f" border-radius: {Gate.RADIUS_LG}px; }}")
        meta_l = QVBoxLayout(self.meta_frame)
        meta_l.setSpacing(4)

        self.title_lbl = QLabel("Select a delivery package from the left.")
        self.title_lbl.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {Gate.TEXT};")
        meta_l.addWidget(self.title_lbl)

        self.info_lbl = QLabel("")
        self.info_lbl.setStyleSheet(f"font-size: 12px; color: {Gate.TEXT_DIM};")
        meta_l.addWidget(self.info_lbl)

        self.notes_lbl = QLabel("")
        self.notes_lbl.setStyleSheet(f"font-size: 12px; color: {Gate.TEXT}; font-style: italic;")
        self.notes_lbl.setWordWrap(True)
        meta_l.addWidget(self.notes_lbl)

        right_layout.addWidget(self.meta_frame)

        # Items Table
        lbl_tbl = QLabel("Versions in this delivery")
        self.items_heading = lbl_tbl
        lbl_tbl.setStyleSheet(f"font-weight: 700; color: {Gate.TEXT}; font-size: 13px; margin-top: 4px;")
        right_layout.addWidget(lbl_tbl)
        self.items_table = QTableWidget(0, 6)
        self.items_table.setHorizontalHeaderLabels(["Reel", "Shot", "Version", "Department",
                                                    "Status when sent", "Media path"])
        style_table(self.items_table, {"Shot": "contents", "Status when sent": "contents",
                                       "Media path": "stretch"}, multi_select=False)
        right_layout.addWidget(self.items_table)

        # Right Actions
        right_actions = QHBoxLayout()
        right_actions.addStretch()

        self.copy_note_btn = make_button("Copy delivery note", "secondary", icon="copy",
                                         tooltip="Copy the note for this package to the clipboard",
                                         on_click=self._on_copy_note_clicked)
        right_actions.addWidget(self.copy_note_btn)

        right_layout.addLayout(right_actions)
        splitter.addWidget(right_widget)

        splitter.setStretchFactor(0, 3)  # 30% List
        splitter.setStretchFactor(1, 7)  # 70% Details
        main_layout.addWidget(splitter)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        main_layout.addWidget(close)
        self.right_widget = right_widget
        self.list_empty = EmptyState.over(
            self.delivery_list, "No deliveries yet",
            "Create one to package versions for the client." if self.can_manage
            else "Production creates delivery packages here.", glyph="package")

    def refresh(self):
        try:
            self.deliveries = self.store.list_deliveries(self.project_code)
            failed = False
        except DatabaseUnavailableError:
            raise
        except Exception:
            self.deliveries, failed = [], True
        if hasattr(self, "list_empty"):
            if failed:
                self.list_empty.set_message("The deliveries could not be read",
                                            "The database did not answer. Close this and try again.")
            else:
                self.list_empty.set_message(
                    "No deliveries yet", "Create one to package versions for the client."
                    if self.can_manage else "Production creates delivery packages here.")
        self.delivery_list.clear()

        for d in self.deliveries:
            item = QListWidgetItem(f"{d.name} ({_date_text(d.delivery_date)})")
            item.setData(Qt.ItemDataRole.UserRole, d.id)
            self.delivery_list.addItem(item)

        if self.deliveries:
            self.delivery_list.setCurrentRow(0)
        else:
            self._on_delivery_selected(-1)
        if hasattr(self, "list_empty"):
            self.list_empty.refresh()

    def _on_delivery_selected(self, row: int):
        if row < 0 or row >= len(self.deliveries):
            # Nothing chosen: no empty cards, just the list (and its empty state).
            self.right_widget.setVisible(False)
            self.items_table.setRowCount(0)
            self.delete_btn.setEnabled(False)
            self.copy_note_btn.setEnabled(False)
            return
        self.right_widget.setVisible(True)

        d_summary = self.deliveries[row]
        delivery = self.store.get_delivery(d_summary.id)
        if not delivery:
            # Never leave the previous package on screen with Delete live.
            self.title_lbl.setText(f"{d_summary.name} could not be loaded")
            self.info_lbl.setText("It may have been deleted by someone else. Close and open Deliveries again.")
            self.notes_lbl.setText("")
            self.items_table.setRowCount(0)
            self.delete_btn.setEnabled(False)
            self.copy_note_btn.setEnabled(False)
            return

        self.delete_btn.setEnabled(True)
        self.copy_note_btn.setEnabled(True)

        self.title_lbl.setText(f"{delivery.name}")
        self.info_lbl.setText(
            f"{_date_text(delivery.delivery_date)} · to {delivery.recipient or 'the client'} · "
            f"prepared by {delivery.created_by or 'production'}"
        )
        self.notes_lbl.setText(delivery.notes if delivery.notes else "No notes.")

        self.items_table.setRowCount(len(delivery.items))
        for r_idx, item in enumerate(delivery.items):
            values = [item.reel or "-", item.shot_name, item.version_name,
                      department_label(item.department) or "-", item.status_at_delivery,
                      item.media_path or "No media recorded"]
            for col, value in enumerate(values):
                self.items_table.setItem(r_idx, col, QTableWidgetItem(str(value)))

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
            self, "Delete delivery",
            f"Delete delivery package '{d.name}'? This will not delete the versions themselves.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            try:
                ok = self.store.delete_delivery(d.id)
            except PermissionError as exc:
                QMessageBox.warning(self, "Delete delivery", str(exc))
                return
            if not ok:
                QMessageBox.warning(self, "Delete delivery", f"'{d.name}' could not be deleted.")
            self.refresh()

    def _on_copy_note_clicked(self):
        row = self.delivery_list.currentRow()
        if row < 0 or row >= len(self.deliveries):
            return

        d = self.deliveries[row]
        note = self.store.generate_delivery_note(d.id)
        clipboard = QApplication.clipboard()
        clipboard.setText(note)
        self.copy_note_btn.setText("Copied")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, lambda: self.copy_note_btn.setText("Copy delivery note"))
