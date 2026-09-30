from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDialog, QFormLayout, QComboBox, QDoubleSpinBox, QSpinBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from slate.core.infra.database_manager import database_manager
from slate.gui.core.offline_notice import on_database_error
from slate.gui.core.controls import make_button, page_title, tidy_form
from slate.gui.core.stat_card import StatStrip
from slate.gui.core.table_style import style_table, set_cell_status
from slate.core.infra.gate import Gate

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else keeps the fallback it already had.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""

class AddBidDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Project Bid")
        layout = tidy_form(QFormLayout(self))
        
        self.proj_input = QComboBox()
        self.populate_projects()
        
        self.shot_count_input = QSpinBox()
        self.shot_count_input.setRange(0, 100000)
        self.shot_count_input.setReadOnly(True) # Auto-calculated from DB
        
        self.cost_input = QDoubleSpinBox()
        self.cost_input.setRange(0, 1000000)
        self.cost_input.setPrefix("$ ")
        from slate.core.domain.bidding import day_rate as _day_rate
        self.cost_input.setValue(_day_rate())
        self.cost_input.setGroupSeparatorShown(True)
        self.cost_input.valueChanged.connect(self.update_budget)

        from slate.core.domain.bidding import COMPLEXITIES, day_rate
        self.complexity_input = QComboBox()
        self.complexity_input.addItems(list(COMPLEXITIES))
        self.complexity_input.setCurrentText("Medium")
        self.complexity_input.currentTextChanged.connect(self.update_budget)

        self.margin_input = QDoubleSpinBox()
        self.margin_input.setRange(0, 100)
        self.margin_input.setSuffix(" %")
        self.margin_input.setValue(20.0)
        self.margin_input.valueChanged.connect(self.update_budget)

        self.days_input = QDoubleSpinBox()
        self.days_input.setRange(0, 1000000)
        self.days_input.setSuffix(" days")
        self.days_input.setReadOnly(True)

        self.budget_input = QDoubleSpinBox()
        self.budget_input.setRange(0, 1000000000)
        self.budget_input.setPrefix("$ ")
        self.budget_input.setGroupSeparatorShown(True)
        self.budget_input.setReadOnly(True)

        layout.addRow("Project Code:", self.proj_input)
        layout.addRow("Total Shots (Auto):", self.shot_count_input)
        layout.addRow("Average Complexity:", self.complexity_input)
        layout.addRow("Artist Day Rate:", self.cost_input)
        layout.addRow("Target Margin:", self.margin_input)
        layout.addRow("Estimated Artist Days:", self.days_input)
        layout.addRow("Final Estimated Budget:", self.budget_input)
        
        self.proj_input.currentTextChanged.connect(self.on_project_changed)
        if self.proj_input.count() > 0:
            self.on_project_changed(self.proj_input.currentText())

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(make_button("Cancel", on_click=self.reject))
        btn_layout.addWidget(make_button("Save Bid", "primary", on_click=self.accept))
        layout.addRow(btn_layout)

    @on_database_error
    def populate_projects(self):
        query = "SELECT code FROM tracking_projects WHERE active=1 ORDER BY code"
        try:
            projects = database_manager.execute_query(query) or []
            for p in projects:
                self.proj_input.addItem(p.get('code', ''))
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            print(f"Error loading projects: {e}")

    @on_database_error
    def on_project_changed(self, proj_code):
        if not proj_code:
            return
        query = "SELECT COUNT(id) as count FROM tracking_shots WHERE project_code = %s"
        try:
            res = database_manager.execute_query(query, (proj_code,))
            count = res[0]['count'] if res else 0
            self.shot_count_input.setValue(count)
            self.update_budget()
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            print(f"Error fetching shots: {e}")

    def update_budget(self):
        # The days-per-shot figures used to be three literals here, so a studio
        # whose comp runs heavier than the default had no way to say so.
        from slate.core.domain.bidding import estimate

        result = estimate(self.shot_count_input.value(),
                          self.complexity_input.currentText(),
                          rate=self.cost_input.value(),
                          margin=self.margin_input.value())

        self.days_input.setValue(result["days"])
        self.budget_input.setValue(result["price"])

class ProdBiddingTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        self.build_ui(main_layout)

    def build_ui(self, main_layout):
        main_layout.addWidget(page_title("Bidding", "Bids, their estimates and whether they were won"))

        # Summary on one compact line, so the table keeps the height at 1366x768.
        strip = StatStrip(compact=True)
        self.lbl_total = strip.add("Total bids", "0", tone="accent")
        self.lbl_value = strip.add("Pipeline value", "$0", tone="ok")
        self.lbl_approved = strip.add("Approved", "0", tone="accent")
        main_layout.addWidget(strip)

        controls = QHBoxLayout()
        controls.setSpacing(Gate.SPACE_2)
        controls.addWidget(make_button("Create Bid", "primary", icon="plus", on_click=self.add_bid))
        controls.addSpacing(Gate.SPACE_2)
        # Approve and Reject are one decision, so they sit together.
        controls.addWidget(make_button("Approve Bid", on_click=lambda: self.update_status("Approved")))
        controls.addWidget(make_button("Reject Bid", on_click=lambda: self.update_status("Rejected")))
        controls.addSpacing(Gate.SPACE_2)
        controls.addWidget(make_button("Edit Bid", on_click=self.edit_bid))
        controls.addWidget(make_button("Delete Bid", "danger", on_click=self.delete_bid))
        controls.addStretch()
        main_layout.addLayout(controls)

        self.grid = QTableWidget(0, 9)
        self.grid.setHorizontalHeaderLabels(["ID", "Project Code", "Shots", "Complexity", "Est. Days", "Margin", "Est. Cost", "Final Budget", "Status"])
        self.style_table(self.grid)
        self.load_data()
        
        self.grid.hideColumn(0) # Hide ID
        main_layout.addWidget(self.grid)

    @on_database_error
    def load_data(self):
        try:
            query = "SELECT * FROM prod_bidding ORDER BY id DESC"
            bids = database_manager.execute_query(query) or []
        except DatabaseUnavailableError:
            raise
        except:
            bids = []
            
        self.grid.setRowCount(len(bids))
        
        total = len(bids)
        # Pipeline value is work that might still happen. Rejected bids were
        # counted in it, so the figure grew every time the studio lost a job.
        total_val = sum(row.get('estimated_budget', 0) or 0 for row in bids
                        if str(row.get('status') or '') in ('Draft', 'Approved'))
        approved = sum(1 for row in bids if row.get('status') == 'Approved')
        
        self.lbl_total.set_value(total)
        self.lbl_value.set_value(f"${total_val:,.0f}")
        self.lbl_approved.set_value(approved)

        for r, row in enumerate(bids):
            self.grid.setItem(r, 0, QTableWidgetItem(str(row.get('id', ''))))
            self.grid.setItem(r, 1, QTableWidgetItem(str(row.get('project_code', ''))))
            self.grid.setItem(r, 2, QTableWidgetItem(str(row.get('shot_count', 0))))
            self.grid.setItem(r, 3, QTableWidgetItem(str(row.get('complexity', ''))))
            self.grid.setItem(r, 4, QTableWidgetItem(f"{row.get('estimated_days', 0):.1f} d"))
            self.grid.setItem(r, 5, QTableWidgetItem(f"{row.get('target_margin', 0):.0f}%"))
            self.grid.setItem(r, 6, QTableWidgetItem(f"${row.get('estimated_cost', 0):,.2f}"))
            self.grid.setItem(r, 7, QTableWidgetItem(f"${row.get('estimated_budget', 0):,.2f}"))
            
            status_item = QTableWidgetItem(str(row.get('status', '')))
            if status_item.text() == "Approved":
                set_cell_status(status_item, "ok", background=False)
            elif status_item.text() == "Rejected":
                set_cell_status(status_item, "bad", background=False)
            self.grid.setItem(r, 8, status_item)

    def add_bid(self):
        dialog = AddBidDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # No quote doubling: the insert below is parameterised.
            proj = dialog.proj_input.currentText().strip()
            shots = dialog.shot_count_input.value()
            comp = dialog.complexity_input.currentText()
            margin = dialog.margin_input.value()
            days = dialog.days_input.value()
            
            # Recompute cost based on days and rate
            cost = days * dialog.cost_input.value()
            budget = dialog.budget_input.value()
            
            if not proj:
                QMessageBox.warning(self, "Error", "Project Code is required.")
                return
                
            query = """
            INSERT INTO prod_bidding 
            (project_code, project_name, shot_count, complexity, estimated_days, target_margin, estimated_cost, estimated_budget, status) 
            VALUES 
            (%s, %s, %s, %s, %s, %s, %s, %s, 'Draft')
            """
            params = (proj, proj, shots, comp, days, margin, cost, budget)
            if database_manager.execute_query(query, params, fetch=False):
                QMessageBox.information(self, "Success", "Added new draft bid.")
                self.load_data()

    def _selected_bid_id(self):
        rows = sorted({item.row() for item in self.grid.selectedItems()})
        if not rows:
            return None
        item = self.grid.item(rows[0], 0)
        if not item or not item.text():
            return None
        return int(item.text())

    def edit_bid(self):
        """
        Change a bid. There was no way to - a typo meant a second bid for the
        same project and two rows in the pipeline value.
        """
        bid_id = self._selected_bid_id()
        if bid_id is None:
            QMessageBox.warning(self, "Selection Empty", "Please select a bid to edit.")
            return

        dialog = AddBidDialog(self)
        row = database_manager.execute_query(
            "SELECT * FROM prod_bidding WHERE id = %s", (bid_id,), fetch="one")
        if row:
            row = dict(row)
            index = dialog.proj_input.findText(str(row.get("project_code") or ""))
            if index >= 0:
                dialog.proj_input.setCurrentIndex(index)
            dialog.complexity_input.setCurrentText(str(row.get("complexity") or "Medium"))
            try:
                dialog.margin_input.setValue(float(row.get("target_margin") or 0))
            except (TypeError, ValueError):
                pass

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        days = dialog.days_input.value()
        database_manager.execute_query(
            "UPDATE prod_bidding SET complexity = %s, shot_count = %s, "
            "estimated_days = %s, target_margin = %s, estimated_cost = %s, "
            "estimated_budget = %s WHERE id = %s",
            (dialog.complexity_input.currentText(), dialog.shot_count_input.value(),
             days, dialog.margin_input.value(), days * dialog.cost_input.value(),
             dialog.budget_input.value(), bid_id), fetch=False)
        self.load_data()

    def delete_bid(self):
        bid_id = self._selected_bid_id()
        if bid_id is None:
            QMessageBox.warning(self, "Selection Empty", "Please select a bid to delete.")
            return
        if QMessageBox.question(
            self, "Delete bid",
            "Delete this bid? It disappears from the pipeline value as well.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        database_manager.execute_query(
            "DELETE FROM prod_bidding WHERE id = %s", (bid_id,), fetch=False)
        self.load_data()

    def update_status(self, new_status):
        selected_rows = set(item.row() for item in self.grid.selectedItems())
        if not selected_rows:
            QMessageBox.warning(self, "Selection Empty", "Please select a bid to update.")
            return
            
        for r in selected_rows:
            item = self.grid.item(r, 0)
            if not item: continue
            bid = item.text()
            query = "UPDATE prod_bidding SET status = %s WHERE id = %s"
            database_manager.execute_query(query, (new_status, int(bid)), fetch=False)
        self.load_data()
            
    def style_table(self, table: QTableWidget):
        """The shared table setup: the project takes the spare width, counts
        and money are as wide as their content and right-aligned."""
        style_table(table, {
            "Project Code": "stretch",
            "Shots": "numeric",
            "Complexity": "contents",
            "Est. Days": "numeric",
            "Margin": "numeric",
            "Est. Cost": "numeric",
            "Final Budget": "numeric",
            "Status": "contents",
        })
