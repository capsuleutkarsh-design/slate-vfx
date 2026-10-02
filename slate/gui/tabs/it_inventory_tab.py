from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QMessageBox,
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox, QSpinBox, QAbstractItemView,
    QInputDialog
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from ...core.infra.app_context import AppContext
import json
import logging
from ..core.empty_state import EmptyState
from ..core.controls import page_title, gate_selection_buttons, make_button, tidy_form
from ..core.table_style import style_table, dim_cell, set_cell_status
from slate.gui.core.offline_notice import on_database_error
from slate.core.infra.gate import Gate

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
        # No stylesheet of its own: it drew every value in amber (the warning
        # colour), square fields beside a pill combo, and a large gradient
        # "Save PC" next to a small Cancel.
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        form = tidy_form(QFormLayout())
        
        self.inp_name = QLineEdit()
        self.inp_cpu = QLineEdit()
        self.inp_gpu = QLineEdit()
        self.inp_ram = QLineEdit()
        self.inp_storage = QLineEdit()
        self.inp_location = QLineEdit()
        # Examples go in the field, not in the label beside it.
        self.inp_ram.setPlaceholderText("e.g. 64 GB")
        self.inp_storage.setPlaceholderText("e.g. 2 TB NVMe")
        self.inp_location.setPlaceholderText("e.g. Comp bay 2")

        # Status is set here. It used to be impossible to say a machine was in
        # for repair or free to hand out, so every row said Active for ever.
        self.inp_status = QComboBox()
        for state in ("Active", "Available", "Repair"):
            self.inp_status.addItem(state)

        if self.edit_data:
            self.inp_name.setText(self.edit_data.get('machine_name', ''))
            self.inp_name.setReadOnly(True)
            self.inp_cpu.setText(self.edit_data.get('cpu', ''))
            self.inp_gpu.setText(self.edit_data.get('gpu', ''))
            self.inp_ram.setText(self.edit_data.get('ram', ''))
            self.inp_storage.setText(self.edit_data.get('storage', ''))
            self.inp_location.setText(self.edit_data.get('location', ''))
            current = str(self.edit_data.get('status') or '').strip().title()
            if current:
                index = self.inp_status.findText(current)
                if index < 0:
                    self.inp_status.addItem(current)
                    index = self.inp_status.count() - 1
                self.inp_status.setCurrentIndex(index)

        form.addRow("Machine Name:", self.inp_name)
        form.addRow("CPU:", self.inp_cpu)
        form.addRow("GPU:", self.inp_gpu)
        form.addRow("RAM:", self.inp_ram)
        form.addRow("Storage:", self.inp_storage)
        form.addRow("Location/Dept:", self.inp_location)
        form.addRow("Status:", self.inp_status)

        layout.addLayout(form)

        # Who has it is not typed here. Issuing a machine writes a loan record,
        # which is what offboarding reads to know what to collect back; a name
        # typed into this dialog wrote the inventory and not the ledger, so the
        # fleet view and the leaving checklist disagreed about the same machine.
        owner_note = QLabel(
            "Use Issue and Collect on the Hardware tab to say who has this "
            "machine. That writes the loan record offboarding reads."
        )
        owner_note.setWordWrap(True)
        owner_note.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 11px; background: transparent; border: none;")
        layout.addWidget(owner_note)
        
        # Enter saves. The auto-fill button used to be the dialog's default,
        # so Enter in any field went scanning the Live Ops share instead.
        if not self.edit_data:
            btn_scan = make_button("Auto-fill from Live Ops", "secondary",
                                   tooltip="Read CPU, GPU, RAM and storage from this machine's Live Ops report",
                                   on_click=self.auto_fill)
            layout.addWidget(btn_scan)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.cancel_btn = make_button("Cancel", on_click=self.reject)
        self.ok_btn = make_button("Save PC", "primary", on_click=self.accept)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.ok_btn)
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
                    
                    # Deliberately not the logged-in user. Who was sitting at a
                    # machine when it was scanned is not who it was issued to.
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
    def __init__(self, user_data=None, parent=None):
        super().__init__(parent)
        # Who is issuing a machine gets recorded on the loan, so the tab needs
        # to know who is using it.
        self.user_data = user_data or {}
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
        
        lbl = QLabel("Status:")
        lbl.setStyleSheet(f"color: {Gate.TEXT_DIM}; background: transparent; border: none;")
        controls.addWidget(lbl)
        
        self.filter_cb = QComboBox()
        self.filter_cb.addItems(["All", "Active", "Repair", "Available"])
        self.filter_cb.currentTextChanged.connect(self.load_data)
        controls.addWidget(self.filter_cb)
        controls.addStretch()
        
        controls.addWidget(make_button("Add PC", "primary", icon="plus", on_click=self.add_workstation))
        controls.addWidget(make_button("Edit Selected", on_click=self.edit_workstation))
        self.issue_btn = make_button("Issue to...", tooltip="Hand this machine to somebody, and record the loan",
                                     on_click=self.issue_selected)
        controls.addWidget(self.issue_btn)
        self.collect_btn = make_button("Collect", tooltip="Take this machine back and free it in the inventory",
                                       on_click=self.collect_selected)
        controls.addWidget(self.collect_btn)
        controls.addWidget(make_button("Delete Selected", "danger", on_click=self.delete_workstation))
        controls.addWidget(make_button("Sync from Live Ops",
                                       tooltip="Automatically import any unknown online PCs from Live Ops",
                                       on_click=self.sync_from_live_ops))
        
        main_layout.addLayout(controls)

        # Table
        self.grid = QTableWidget(0, 8)
        self.grid.setHorizontalHeaderLabels(["Machine Name", "Assigned To", "Location", "CPU", "GPU", "RAM", "Storage", "Status"])
        self.style_table(self.grid)
        self.load_data()
        # Other people's changes, without a restart (the change feed; a timer if it is missing).
        from slate.gui.components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(self, self.load_data, seconds=30, topics=("hardware_inventory", "asset_assignments"))
        
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
            assigned = row.get('display_name') or row.get('username') or row.get('assigned_to')
            self.grid.setItem(r, 0, QTableWidgetItem(str(row.get('machine_name', ''))))
            assigned_item = QTableWidgetItem(assigned or "Unassigned")
            if not assigned:
                dim_cell(assigned_item)        # a placeholder, not a name
            self.grid.setItem(r, 1, assigned_item)
            self.grid.setItem(r, 2, QTableWidgetItem(str(row.get('location', ''))))
            self.grid.setItem(r, 3, QTableWidgetItem(str(row.get('cpu', 'N/A'))))
            self.grid.setItem(r, 4, QTableWidgetItem(str(row.get('gpu', 'N/A'))))
            self.grid.setItem(r, 5, QTableWidgetItem(str(row.get('ram', 'N/A'))))
            self.grid.setItem(r, 6, QTableWidgetItem(str(row.get('storage', 'N/A'))))
            
            status_item = QTableWidgetItem(str(row.get('status', '')))
            st_text = status_item.text().strip().lower()
            if st_text == "active":
                set_cell_status(status_item, "ok", background=False)
            elif st_text == "repair":
                set_cell_status(status_item, "bad", background=False)
            elif st_text == "available":
                set_cell_status(status_item, "accent", background=False)
            else:
                dim_cell(status_item)
            self.grid.setItem(r, 7, status_item)

    def _selected_row(self):
        selected = self.grid.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        if row >= len(self.hardware_data):
            return None
        return self.hardware_data[row]

    def _service(self):
        from slate.core.domain.onboarding_service import OnboardingService
        return OnboardingService()

    def add_workstation(self):
        dialog = AddPCDialog(self, self.hub)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        mname = dialog.inp_name.text().strip()
        if not mname:
            return

        from slate.core.infra.database_manager import database_manager

        # Adding a name that already exists used to overwrite its specs and
        # report success, while quietly keeping the old owner. Say so instead.
        existing = database_manager.execute_query(
            "SELECT machine_name FROM hardware_inventory WHERE LOWER(machine_name) = LOWER(%s)",
            (mname,), fetch="one")
        if existing:
            QMessageBox.information(
                self, "Already registered",
                "%s is already in the inventory. Select it and use Edit Selected "
                "to change its specification." % mname)
            return

        query = """
            INSERT INTO hardware_inventory (machine_name, type, status, cpu, gpu, ram, storage, location)
            VALUES (%s, 'Workstation', %s, %s, %s, %s, %s, %s)
        """
        # A machine nobody has is Available, not Active. Everything arrived as
        # Active, so the fleet view never showed a single free machine and the
        # issue list had to work it out from assigned_to instead.
        database_manager.execute_query(
            query,
            (mname, dialog.inp_status.currentText() or "Available",
             dialog.inp_cpu.text().strip(), dialog.inp_gpu.text().strip(),
             dialog.inp_ram.text().strip(), dialog.inp_storage.text().strip(),
             dialog.inp_location.text().strip()),
            fetch=False)
        self.load_data()

    def edit_workstation(self):
        edit_data = self._selected_row()
        if edit_data is None:
            QMessageBox.warning(self, "Warning", "Please select a PC to edit.")
            return

        dialog = AddPCDialog(self, self.hub, edit_data=edit_data)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        mname = edit_data.get('machine_name')
        from slate.core.infra.database_manager import database_manager
        query = """
            UPDATE hardware_inventory
            SET cpu = %s, gpu = %s, ram = %s, storage = %s, location = %s, status = %s
            WHERE machine_name = %s
        """
        database_manager.execute_query(
            query,
            (dialog.inp_cpu.text().strip(), dialog.inp_gpu.text().strip(),
             dialog.inp_ram.text().strip(), dialog.inp_storage.text().strip(),
             dialog.inp_location.text().strip(),
             dialog.inp_status.currentText(), mname),
            fetch=False)
        self.load_data()

    def issue_selected(self):
        """
        Hand a machine to somebody, and write the loan that says so.

        This is the only way a machine gets an owner now. The free-text box
        that used to be in the dialog wrote hardware_inventory and nothing
        else, so the loan ledger offboarding reads never heard about it - and a
        machine issued that way was never asked for back.
        """
        row = self._selected_row()
        if row is None:
            QMessageBox.warning(self, "Warning", "Please select a PC to issue.")
            return

        machine = row.get("machine_name")
        if str(row.get("status") or "").strip().lower() == "repair":
            QMessageBox.warning(
                self, "In for repair",
                "%s is marked as being in for repair. Mark it Available first."
                % machine)
            return

        service = self._service()
        held = service.held_by_machine(machine) if hasattr(service, "held_by_machine") else []
        if held:
            QMessageBox.warning(
                self, "Already issued",
                "%s is already out with %s. Collect it back first."
                % (machine, held[0].get("user_id")))
            return

        people = [str(p.get("username")) for p in service.people() if p.get("username")]
        if not people:
            QMessageBox.warning(self, "Nobody to issue to",
                                "There are no users in the database.")
            return

        who, ok = QInputDialog.getItem(
            self, "Issue %s" % machine, "Issue this machine to:", people, 0, False)
        if not ok or not who:
            return

        by_whom = str((self.user_data or {}).get("user_id")
                      or (self.user_data or {}).get("username") or "IT")
        if service.issue_machine(machine, who, by_whom):
            QMessageBox.information(
                self, "Issued",
                "%s is now with %s. When they leave, the leaving checklist will "
                "ask for it back." % (machine, who))
        else:
            QMessageBox.warning(self, "Not issued",
                                "The loan could not be recorded, so nothing was changed.")
        self.load_data()

    def collect_selected(self):
        """Take a machine back, and close the loan."""
        row = self._selected_row()
        if row is None:
            QMessageBox.warning(self, "Warning", "Please select a PC to collect.")
            return

        machine = row.get("machine_name")
        service = self._service()
        held = service.held_by_machine(machine) if hasattr(service, "held_by_machine") else []
        if not held:
            QMessageBox.information(
                self, "Nobody has it",
                "%s is not out on loan, so there is nothing to collect." % machine)
            return

        holder = held[0].get("user_id")
        if QMessageBox.question(
            self, "Collect %s" % machine,
            "Take %s back from %s?" % (machine, holder),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return

        if service.return_machine(machine, holder):
            QMessageBox.information(self, "Collected",
                                    "%s is back and free to issue." % machine)
        else:
            QMessageBox.warning(self, "Not collected",
                                "The return could not be recorded.")
        self.load_data()

    def delete_workstation(self):
        row = self._selected_row()
        if row is None:
            QMessageBox.warning(self, "Warning", "Please select a PC to delete.")
            return

        mname = row.get('machine_name')

        # Deleting a machine somebody still has left the loan record behind, so
        # it haunted the leaving checklist as a machine that could never be
        # collected because it no longer existed.
        service = self._service()
        held = service.held_by_machine(mname) if hasattr(service, "held_by_machine") else []
        if held:
            QMessageBox.warning(
                self, "Still issued",
                "%s is out with %s. Collect it back before deleting it, or the "
                "loan record is left behind with nothing to return."
                % (mname, held[0].get("user_id")))
            return

        reply = QMessageBox.question(self, "Confirm Delete", f"Are you sure you want to delete PC '{mname}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            from slate.core.infra.database_manager import database_manager
            database_manager.execute_query("DELETE FROM hardware_inventory WHERE machine_name = %s", (mname,), fetch=False)
            self.load_data()

    def sync_from_live_ops(self):
        """
        Import machines Live Ops has seen that the inventory does not know.

        Two things this used to get wrong. It counted every row it touched as
        "added", so re-running it on a settled fleet reported forty new PCs
        every time. And it set the owner to whoever happened to be logged in
        when the scan ran, which both invented a loan nobody recorded and hid
        the machine from the issue list.
        """
        from slate.core.infra.database_manager import database_manager
        status_dir = self.hub.get_livestatus_dir()
        if not status_dir.exists():
            QMessageBox.warning(self, "Error", "Live Ops directory not found.")
            return

        added = updated = 0
        for f in status_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)

                mname = data.get("ComputerName", data.get("pc_name", f.stem))
                if not mname:
                    continue
                cpu = data.get("CPU", "")
                gpu = data.get("GPU", "")
                ram = str(data.get("RAM_GB", "")) + " GB"

                drives = data.get("Drives", [])
                storage = ""
                if drives:
                    total_gb = sum(float(d.get("Capacity_GB", 0)) for d in drives)
                    storage = f"{total_gb:.0f} GB"

                existing = database_manager.execute_query(
                    "SELECT machine_name FROM hardware_inventory "
                    "WHERE LOWER(machine_name) = LOWER(%s)", (mname,), fetch="one")

                if existing:
                    database_manager.execute_query(
                        "UPDATE hardware_inventory SET cpu = %s, gpu = %s, ram = %s, "
                        "storage = %s WHERE LOWER(machine_name) = LOWER(%s)",
                        (cpu, gpu, ram, storage, mname), fetch=False)
                    updated += 1
                else:
                    # No owner. Live Ops knows who was sitting at it, which is
                    # not the same as who it was issued to - and guessing puts a
                    # machine beyond the reach of the issue list.
                    database_manager.execute_query(
                        "INSERT INTO hardware_inventory "
                        "(machine_name, type, status, cpu, gpu, ram, storage) "
                        "VALUES (%s, 'Workstation', 'Available', %s, %s, %s, %s)",
                        (mname, cpu, gpu, ram, storage), fetch=False)
                    added += 1
            except DatabaseUnavailableError:
                raise
            except Exception as exc:
                logging.debug("Live Ops sync skipped %s: %s", f.name, exc)

        QMessageBox.information(
            self, "Sync Complete",
            "%d new machine(s) added, %d existing one(s) refreshed.\n\n"
            "New machines are left unassigned - use Issue to say who has them."
            % (added, updated))
        self.load_data()

    def style_table(self, table: QTableWidget):
        """The shared table style: data in text colour (it was all amber, the
        warning colour), readable size and padding, one selection colour."""
        style_table(table, {
            "Machine Name": "contents",
            "Assigned To": ("interactive", 160),
            "Location": ("interactive", 130),
            "CPU": "stretch",
            "GPU": "stretch",
            "RAM": "contents",
            "Storage": "contents",
            "Status": "contents",
        })
