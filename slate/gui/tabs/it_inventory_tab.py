from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QMessageBox,
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox, QSpinBox, QAbstractItemView
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from ...core.infra.app_context import AppContext
import json
from ..core.empty_state import EmptyState
from ..core.controls import page_title, gate_selection_buttons
from slate.gui.core.offline_notice import on_database_error

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else keeps the fallback it already had.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""

class AddPCDialog(QDialog):
    def __init__(self, parent=None, hub=None, edit_data=None):
        super().__init__(parent)
        self.hub = hub
        self.edit_data = edit_data
        
        if self.edit_data:
            self.setWindowTitle("Edit PC")
        else:
            self.setWindowTitle("Add New PC")
            
        self.setMinimumWidth(420)
        self.setStyleSheet("""
            QDialog {
                background-color: #0D0D0F;
                color: #D9A441;
            }
            QLabel {
                color: #87857F;
                font-weight: 600;
                background: transparent;
                border: none;
            }
            QLineEdit {
                background-color: #16161A;
                border: 1px solid #26262D;
                border-radius: 4px;
                color: #D9A441;
                padding: 6px;
            }
            QLineEdit:focus {
                border-color: #3EA8BF;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        form = QFormLayout()
        form.setSpacing(10)
        
        self.inp_name = QLineEdit()
        self.inp_user = QLineEdit()
        self.inp_cpu = QLineEdit()
        self.inp_gpu = QLineEdit()
        self.inp_ram = QLineEdit()
        self.inp_storage = QLineEdit()
        self.inp_location = QLineEdit()
        
        if self.edit_data:
            self.inp_name.setText(self.edit_data.get('machine_name', ''))
            self.inp_name.setReadOnly(True)
            self.inp_user.setText(self.edit_data.get('assigned_to', ''))
            self.inp_cpu.setText(self.edit_data.get('cpu', ''))
            self.inp_gpu.setText(self.edit_data.get('gpu', ''))
            self.inp_ram.setText(self.edit_data.get('ram', ''))
            self.inp_storage.setText(self.edit_data.get('storage', ''))
            self.inp_location.setText(self.edit_data.get('location', ''))
            
        form.addRow("Machine Name:", self.inp_name)
        form.addRow("Assigned User:", self.inp_user)
        form.addRow("CPU:", self.inp_cpu)
        form.addRow("GPU:", self.inp_gpu)
        form.addRow("RAM (e.g. 64GB):", self.inp_ram)
        form.addRow("Storage (e.g. 2TB NVMe):", self.inp_storage)
        form.addRow("Location/Dept:", self.inp_location)
        
        layout.addLayout(form)
        
        if not self.edit_data:
            btn_scan = QPushButton("Auto-fill from Live Ops (Network)")
            btn_scan.setObjectName("secondaryButton")
            btn_scan.clicked.connect(self.auto_fill)
            layout.addWidget(btn_scan)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(self.reject)
        
        ok_btn = QPushButton("Save PC")
        ok_btn.setObjectName("primaryButton")
        ok_btn.clicked.connect(self.accept)
        
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

    def auto_fill(self):
        machine_name = self.inp_name.text().strip()
        if not machine_name:
            QMessageBox.warning(self, "Warning", "Please enter a Machine Name to scan for.")
            return
            
        if self.hub:
            status_dir = self.hub.get_livestatus_dir()
            report_path = status_dir / f"{machine_name}.json"
            if report_path.exists():
                try:
                    with open(report_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    self.inp_user.setText(data.get("user", ""))
                    self.inp_cpu.setText(data.get("CPU", ""))
                    self.inp_gpu.setText(data.get("GPU", ""))
                    self.inp_ram.setText(str(data.get("RAM_GB", "")) + " GB")
                    
                    drives = data.get("Drives", [])
                    if drives:
                        total_gb = sum(float(d.get("Capacity_GB", 0)) for d in drives)
                        self.inp_storage.setText(f"{total_gb:.0f} GB")
                        
                    QMessageBox.information(self, "Success", "Auto-filled data from Live Ops!")
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Could not read Live Ops data: {e}")
            else:
                QMessageBox.warning(self, "Not Found", "No Live Ops data found for this machine. It may be offline.")


class ItInventoryTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.app_context = AppContext()
        self.hub = self.app_context.server_hub()
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)
        
        self.build_ui(main_layout)

    def build_ui(self, main_layout):
        header_title = page_title('Hardware', 'Workstations registered to the studio')
        main_layout.addWidget(header_title)
        
        # Controls
        controls = QHBoxLayout()
        controls.setSpacing(10)
        
        lbl = QLabel("Filter by Status:")
        lbl.setStyleSheet("color: #87857F; font-weight: 600; background: transparent; border: none;")
        controls.addWidget(lbl)
        
        self.filter_cb = QComboBox()
        self.filter_cb.addItems(["All", "Active", "Repair", "Available"])
        self.filter_cb.setStyleSheet("""
            QComboBox {
                background: #16161A;
                color: #D9A441;
                border: 1px solid #26262D;
                border-radius: 4px;
                padding: 4px 10px;
                min-height: 24px;
            }
            QComboBox:focus { border-color: #3EA8BF; }
            QComboBox QAbstractItemView {
                background-color: #16161A;
                color: #E8E6E1;
                border: 1px solid #26262D;
                selection-background-color: #1D1D22;
                selection-color: #3EA8BF;
            }
        """)
        self.filter_cb.currentTextChanged.connect(self.load_data)
        controls.addWidget(self.filter_cb)
        controls.addStretch()
        
        add_btn = QPushButton("+ Add PC")
        add_btn.setObjectName("primaryButton")
        add_btn.clicked.connect(self.add_workstation)
        controls.addWidget(add_btn)
        
        edit_btn = QPushButton("Edit Selected")
        edit_btn.setObjectName("secondaryButton")
        edit_btn.clicked.connect(self.edit_workstation)
        controls.addWidget(edit_btn)
        
        delete_btn = QPushButton("Delete Selected")
        delete_btn.setObjectName("dangerButton")
        delete_btn.clicked.connect(self.delete_workstation)
        controls.addWidget(delete_btn)
        
        sync_btn = QPushButton("Sync from Live Ops")
        sync_btn.setObjectName("secondaryButton")
        sync_btn.setToolTip("Automatically import any unknown online PCs from Live Ops")
        sync_btn.clicked.connect(self.sync_from_live_ops)
        controls.addWidget(sync_btn)
        
        main_layout.addLayout(controls)

        # Table
        self.grid = QTableWidget(0, 8)
        self.grid.setHorizontalHeaderLabels(["Machine Name", "Assigned To", "Location", "CPU", "GPU", "RAM", "Storage", "Status"])
        self.style_table(self.grid)
        self.load_data()
        
        self.grid.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        main_layout.addWidget(self.grid)

        # What this table says when there is nothing in it. It used to
        # render as several hundred pixels of black, with no way to tell
        # an empty table from a broken one.
        self.empty_state = EmptyState(
            'No machines registered yet',
            'Add a workstation, or pull the current fleet from Live Ops.',
            glyph='monitor',
        )
        main_layout.addWidget(self.empty_state)
        self.empty_state.attach_to(self.grid)

        # Nothing is selected yet, so the actions that need a selection
        # start switched off rather than arguing with a dialog.
        gate_selection_buttons(self, self.grid)


    @on_database_error
    def load_data(self):
        try:
            from slate.core.infra.database_manager import database_manager
            status_filter = self.filter_cb.currentText()
            if status_filter != "All":
                query = """
                    SELECT h.id, h.machine_name, h.gpu, h.cpu, h.storage, h.ram, h.status, h.location, h.assigned_to,
                           u.display_name, u.username
                    FROM hardware_inventory h
                    LEFT JOIN ut_users u ON h.assigned_to = u.username
                    WHERE h.status = %s
                    ORDER BY h.machine_name ASC
                """
                self.hardware_data = database_manager.execute_query(query, (status_filter,)) or []
            else:
                query = """
                    SELECT h.id, h.machine_name, h.gpu, h.cpu, h.storage, h.ram, h.status, h.location, h.assigned_to,
                           u.display_name, u.username
                    FROM hardware_inventory h
                    LEFT JOIN ut_users u ON h.assigned_to = u.username
                    ORDER BY h.machine_name ASC
                """
                self.hardware_data = database_manager.execute_query(query) or []
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            self.hardware_data = []
            
        self.grid.setRowCount(len(self.hardware_data))
        for r, row in enumerate(self.hardware_data):
            assigned = row.get('display_name') or row.get('username') or row.get('assigned_to') or "Unassigned"
            self.grid.setItem(r, 0, QTableWidgetItem(str(row.get('machine_name', ''))))
            self.grid.setItem(r, 1, QTableWidgetItem(assigned))
            self.grid.setItem(r, 2, QTableWidgetItem(str(row.get('location', ''))))
            self.grid.setItem(r, 3, QTableWidgetItem(str(row.get('cpu', 'N/A'))))
            self.grid.setItem(r, 4, QTableWidgetItem(str(row.get('gpu', 'N/A'))))
            self.grid.setItem(r, 5, QTableWidgetItem(str(row.get('ram', 'N/A'))))
            self.grid.setItem(r, 6, QTableWidgetItem(str(row.get('storage', 'N/A'))))
            
            status_item = QTableWidgetItem(str(row.get('status', '')))
            st_text = status_item.text().strip().lower()
            if st_text == "active":
                status_item.setForeground(QColor("#5FBF8F"))
            elif st_text == "repair":
                status_item.setForeground(QColor("#D9635F"))
            elif st_text == "available":
                status_item.setForeground(QColor("#3EA8BF"))
            else:
                status_item.setForeground(QColor("#87857F"))
            self.grid.setItem(r, 7, status_item)

    def add_workstation(self):
        dialog = AddPCDialog(self, self.hub)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            mname = dialog.inp_name.text().strip()
            user = dialog.inp_user.text().strip()
            cpu = dialog.inp_cpu.text().strip()
            gpu = dialog.inp_gpu.text().strip()
            ram = dialog.inp_ram.text().strip()
            storage = dialog.inp_storage.text().strip()
            location = dialog.inp_location.text().strip()
            
            if not mname:
                return
                
            from slate.core.infra.database_manager import database_manager
            query = """
                INSERT INTO hardware_inventory (machine_name, type, status, assigned_to, cpu, gpu, ram, storage, location) 
                VALUES (%s, 'Workstation', 'Active', %s, %s, %s, %s, %s, %s)
                ON CONFLICT (machine_name) DO UPDATE SET
                cpu = EXCLUDED.cpu,
                gpu = EXCLUDED.gpu,
                ram = EXCLUDED.ram,
                storage = EXCLUDED.storage,
                location = EXCLUDED.location
            """
            database_manager.execute_query(query, (mname, user, cpu, gpu, ram, storage, location), fetch=False)
            self.load_data()

    def edit_workstation(self):
        selected = self.grid.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Warning", "Please select a PC to edit.")
            return
            
        row = selected[0].row()
        edit_data = self.hardware_data[row]
        
        dialog = AddPCDialog(self, self.hub, edit_data=edit_data)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            user = dialog.inp_user.text().strip()
            cpu = dialog.inp_cpu.text().strip()
            gpu = dialog.inp_gpu.text().strip()
            ram = dialog.inp_ram.text().strip()
            storage = dialog.inp_storage.text().strip()
            location = dialog.inp_location.text().strip()
            mname = edit_data.get('machine_name')
            
            from slate.core.infra.database_manager import database_manager
            query = """
                UPDATE hardware_inventory 
                SET assigned_to = %s, cpu = %s, gpu = %s, ram = %s, storage = %s, location = %s 
                WHERE machine_name = %s
            """
            database_manager.execute_query(query, (user, cpu, gpu, ram, storage, location, mname), fetch=False)
            self.load_data()

    def delete_workstation(self):
        selected = self.grid.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Warning", "Please select a PC to delete.")
            return
            
        row = selected[0].row()
        edit_data = self.hardware_data[row]
        mname = edit_data.get('machine_name')
        
        reply = QMessageBox.question(self, "Confirm Delete", f"Are you sure you want to delete PC '{mname}'?", 
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            from slate.core.infra.database_manager import database_manager
            database_manager.execute_query("DELETE FROM hardware_inventory WHERE machine_name = %s", (mname,), fetch=False)
            self.load_data()

    def sync_from_live_ops(self):
        from slate.core.infra.database_manager import database_manager
        status_dir = self.hub.get_livestatus_dir()
        if not status_dir.exists():
            QMessageBox.warning(self, "Error", "Live Ops directory not found.")
            return
            
        added_count = 0
        for f in status_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    
                mname = data.get("ComputerName", data.get("pc_name", f.stem))
                user = data.get("user", "")
                cpu = data.get("CPU", "")
                gpu = data.get("GPU", "")
                ram = str(data.get("RAM_GB", "")) + " GB"
                
                drives = data.get("Drives", [])
                storage = ""
                if drives:
                    total_gb = sum(float(d.get("Capacity_GB", 0)) for d in drives)
                    storage = f"{total_gb:.0f} GB"
                
                query = """
                    INSERT INTO hardware_inventory (machine_name, type, status, assigned_to, cpu, gpu, ram, storage) 
                    VALUES (%s, 'Workstation', 'Active', %s, %s, %s, %s, %s)
                    ON CONFLICT (machine_name) DO UPDATE SET
                    cpu = EXCLUDED.cpu,
                    gpu = EXCLUDED.gpu,
                    ram = EXCLUDED.ram,
                    storage = EXCLUDED.storage
                """
                database_manager.execute_query(query, (mname, user, cpu, gpu, ram, storage), fetch=False)
                added_count += 1
            except DatabaseUnavailableError:
                raise
            except Exception as e:
                pass
                
        QMessageBox.information(self, "Sync Complete", f"Synchronized {added_count} PCs from Live Ops network.")
        self.load_data()

    def style_table(self, table: QTableWidget):
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(34)
        table.setStyleSheet("""
            QTableWidget { 
                background-color: #16161A; 
                color: #D9A441; 
                gridline-color: #1D1D22; 
                border: 1px solid #1D1D22; 
                border-radius: 6px;
                font-size: 12px;
            }
            QTableWidget::item {
                padding: 4px 8px;
                border-bottom: 1px solid #1D1D22;
            }
            QTableWidget::item:alternate { background-color: #16161A; }
            QTableWidget::item:selected { background-color: #16323A; color: #3EA8BF; }
            QHeaderView::section { 
                background-color: #16161A; 
                color: #87857F; 
                border: 1px solid #1D1D22; 
                padding: 6px 10px; 
                font-weight: 700;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
        """)
