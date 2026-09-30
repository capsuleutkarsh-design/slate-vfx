from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QComboBox, QDialog, QFormLayout, QLineEdit,
    QAbstractItemView
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from ..core.empty_state import EmptyState
from ..core.controls import page_title, gate_selection_buttons, make_button, tidy_form
from ..core.stat_card import StatStrip
from ..core.table_style import style_table, set_cell_status, dim_cell
from slate.gui.core.offline_notice import on_database_error
from slate.core.infra.gate import Gate

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else keeps the fallback it already had.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""

class AddDeploymentDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Record a deployment")
        self.setMinimumWidth(440)
        layout = tidy_form(QFormLayout(self))
        layout.setContentsMargins(16, 16, 16, 16)
        
        self.pkg_input = QLineEdit()
        self.target_input = QLineEdit()

        layout.addRow("Package Name:", self.pkg_input)
        layout.addRow("Target Machine:", self.target_input)
        
        # Enter records the deployment; Cancel is never the default.
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.cancel_btn = make_button("Cancel", on_click=self.reject)
        self.save_btn = make_button("Record", "primary", on_click=self.accept)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.save_btn)
        layout.addRow(btn_layout)


class ItDeploymentTab(QWidget):
    """
    A record of software installed on workstations.

    It does not install anything. It never did - the tab was called Deployment
    and its button said "Deploy New Package", which reads as a promise that
    something is pushed to the machine named in the row. Nothing is: this is a
    log somebody fills in, and calling it one is the difference between a
    useful record and a feature that appears broken.

    If it is ever made real, the channel already exists: ServerHub.post_command
    is how the admin panel tells workstations to clear their caches, and a
    deployment is the same shape of instruction.
    """

    def __init__(self, user_data=None, parent=None):
        super().__init__(parent)
        self.user_data = user_data or {}
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)
        
        self.build_ui(main_layout)

    def build_ui(self, main_layout):
        header_title = page_title(
            'Deployment log',
            'A record of what was installed where. Slate does not push it.')
        main_layout.addWidget(header_title)
        
        # Summary. The figures were set with QFont, which the old global
        # stylesheet overruled, so "45" came out the size of its label.
        strip = StatStrip()
        self.lbl_total = strip.add("Total deployments", "0", tone="accent")
        self.lbl_success = strip.add("Success rate", "-", tone="ok")
        self.lbl_pending = strip.add("Pending", "0", tone="warn")
        main_layout.addWidget(strip)
        
        controls = QHBoxLayout()
        controls.setSpacing(10)
        
        lbl = QLabel("Status:")
        lbl.setStyleSheet(f"color: {Gate.TEXT_DIM}; background: transparent; border: none;")
        controls.addWidget(lbl)
        
        self.filter_cb = QComboBox()
        self.filter_cb.addItems(["All", "Pending", "Success", "Failed"])
        self.filter_cb.currentTextChanged.connect(self.load_data)
        controls.addWidget(self.filter_cb)
        
        controls.addWidget(make_button("Record deployment", "primary", icon="plus",
                                       on_click=self.add_deployment))
        controls.addWidget(make_button("Mark Success", on_click=lambda: self.update_status("Success")))
        controls.addWidget(make_button("Mark Failed", "danger", on_click=lambda: self.update_status("Failed")))
        
        controls.addStretch()
        main_layout.addLayout(controls)

        self.grid = QTableWidget(0, 6)
        self.grid.setHorizontalHeaderLabels(["ID", "Package Name", "Target Machine", "Deployed By", "Status", "Deployed At"])
        self.style_table(self.grid)
        self.load_data()
        
        self.grid.hideColumn(0) # Hide ID
        main_layout.addWidget(self.grid)

        # What this table says when there is nothing in it. It used to
        # render as several hundred pixels of black, with no way to tell
        # an empty table from a broken one.
        self.empty_state = EmptyState(
            'Nothing deployed yet',
            'Software pushed to workstations will be listed here.',
            glyph='package',
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
                query = "SELECT * FROM it_deployments WHERE status = %s ORDER BY id DESC"
                deps = database_manager.execute_query(query, (status_filter,)) or []
            else:
                query = "SELECT * FROM it_deployments ORDER BY id DESC"
                deps = database_manager.execute_query(query) or []
            
            all_deps = database_manager.execute_query("SELECT status FROM it_deployments") or []
        except DatabaseUnavailableError:
            raise
        except:
            deps = []
            all_deps = []
            
        self.grid.setRowCount(len(deps))
        
        total = len(all_deps)
        successes = sum(1 for d in all_deps if d.get('status') == 'Success')
        failures = sum(1 for d in all_deps if d.get('status') == 'Failed')
        pending = sum(1 for d in all_deps if d.get('status') == 'Pending')

        self.lbl_total.set_value(total)
        self.lbl_pending.set_value(pending)
        # Out of the ones that finished. Dividing by every row counted anything
        # still pending as a failure, so the rate fell every time somebody
        # recorded a job they had not done yet.
        finished = successes + failures
        if finished > 0:
            self.lbl_success.set_value(f"{int((successes / finished) * 100)}%")
        else:
            self.lbl_success.set_value("-")

        for r, row in enumerate(deps):
            self.grid.setItem(r, 0, QTableWidgetItem(str(row.get('id', ''))))
            self.grid.setItem(r, 1, QTableWidgetItem(str(row.get('package_name', ''))))
            self.grid.setItem(r, 2, QTableWidgetItem(str(row.get('target_machine', ''))))
            self.grid.setItem(r, 3, QTableWidgetItem(str(row.get('deployed_by', ''))))
            
            status_item = QTableWidgetItem(str(row.get('status', '')))
            st_text = status_item.text().strip().lower()
            if st_text == "success":
                set_cell_status(status_item, "ok", background=False)
            elif st_text == "failed":
                set_cell_status(status_item, "bad", background=False)
            elif st_text == "pending":
                set_cell_status(status_item, "warn", background=False)
            else:
                dim_cell(status_item)
            self.grid.setItem(r, 4, status_item)
            
            self.grid.setItem(r, 5, QTableWidgetItem(str(row.get('deployed_at', ''))))

    def add_deployment(self):
        dialog = AddDeploymentDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            pkg = dialog.pkg_input.text().strip()
            tgt = dialog.target_input.text().strip()
            
            if not pkg or not tgt:
                QMessageBox.warning(self, "Error", "Package Name and Target Machine are required.")
                return
                
            from slate.core.infra.database_manager import database_manager
            current_user = self.user_data.get('username', 'admin')
            query = "INSERT INTO it_deployments (package_name, target_machine, deployed_by, status) VALUES (%s, %s, %s, 'Pending')"
            if database_manager.execute_query(query, (pkg, tgt, current_user), fetch=False):
                QMessageBox.information(self, "Success", "Deployment task created.")
                self.load_data()

    def update_status(self, new_status):
        selected_rows = set(item.row() for item in self.grid.selectedItems())
        if not selected_rows:
            QMessageBox.warning(self, "Selection Empty", "Please select a deployment to update.")
            return
            
        from slate.core.infra.database_manager import database_manager
        for r in selected_rows:
            did = self.grid.item(r, 0).text()
            query = "UPDATE it_deployments SET status = %s WHERE id = %s"
            database_manager.execute_query(query, (new_status, int(did)), fetch=False)
        self.load_data()
            
    def style_table(self, table: QTableWidget):
        """The shared table style (it was amber text, ALL-CAPS headers)."""
        style_table(table, {
            "Package Name": "stretch",
            "Target Machine": "contents",
            "Deployed By": "contents",
            "Status": "contents",
            "Deployed At": "contents",
        })
