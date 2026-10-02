from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDialog, QFormLayout, QComboBox, QDoubleSpinBox, QSpinBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from slate.core.infra.database_manager import database_manager
from slate.gui.core.offline_notice import on_database_error
from slate.gui.components.table_tools import (
    KeepSelection, TableToolbar, make_item, selected_keys, setup_table,
)

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
        self.setStyleSheet("background-color: #1D1D22; color: white;")
        layout = QFormLayout(self)
        
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
        save_btn = QPushButton("Save Bid")
        save_btn.setStyleSheet("background-color: #3EA8BF; font-weight: bold; padding: 5px;")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("background-color: #87857F; font-weight: bold; padding: 5px;")
        cancel_btn.clicked.connect(self.reject)
        
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
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
    def __init__(self, parent=None, user_data=None):
        super().__init__(parent)
        # Who is looking, so the tab can ask access.can(self.user_roles, ...)
        # (schedule_write / approve_bid). It was built without any user at all.
        self.user_data = dict(user_data or {})
        roles = self.user_data.get("roles") or self.user_data.get("role") or []
        self.user_roles = [roles] if isinstance(roles, str) else list(roles)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        self.build_ui(main_layout)

    def create_stat_card(self, title, value, color):
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: #16161A;
                border: 1px solid #1D1D22;
                border-left: 4px solid {color};
                border-radius: 6px;
            }}
        """)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)
        
        t_label = QLabel(title)
        t_label.setFont(QFont("Inter", 10))
        t_label.setStyleSheet("color: #87857F; font-size: 11px; font-weight: 600; text-transform: uppercase; background: transparent; border: none;")
        
        v_label = QLabel(str(value))
        v_label.setFont(QFont("Inter", 20, QFont.Weight.Bold))
        v_label.setStyleSheet("color: #E8E6E1; background: transparent; border: none;")
        
        lay.addWidget(t_label)
        lay.addWidget(v_label)
        return card, v_label

    def build_ui(self, main_layout):
        header_title = QLabel("Production Bidding")
        header_title.setFont(QFont("Inter", 16, QFont.Weight.Bold))
        header_title.setStyleSheet("color: #E8E6E1; margin-bottom: 2px;")
        main_layout.addWidget(header_title)
        
        # Summary Cards
        cards_lay = QHBoxLayout()
        cards_lay.setSpacing(12)
        card1, self.lbl_total = self.create_stat_card("Total Bids", "0", "#3EA8BF")
        card2, self.lbl_value = self.create_stat_card("Total Pipeline Value", "$0", "#5FBF8F")
        card3, self.lbl_approved = self.create_stat_card("Approved", "0", "#3EA8BF")
        cards_lay.addWidget(card1)
        cards_lay.addWidget(card2)
        cards_lay.addWidget(card3)
        main_layout.addLayout(cards_lay)
        
        controls = QHBoxLayout()
        controls.setSpacing(10)
        add_btn = QPushButton("+ Create New Bid")
        add_btn.setObjectName("primaryButton")
        add_btn.clicked.connect(self.add_bid)
        controls.addWidget(add_btn)
        
        approve_btn = QPushButton("Approve Bid")
        approve_btn.setObjectName("secondaryButton")
        approve_btn.clicked.connect(lambda: self.update_status("Approved"))
        controls.addWidget(approve_btn)

        reject_btn = QPushButton("Reject Bid")
        reject_btn.setObjectName("secondaryButton")
        reject_btn.clicked.connect(lambda: self.update_status("Rejected"))
        controls.addWidget(reject_btn)

        edit_btn = QPushButton("Edit Bid")
        edit_btn.setObjectName("secondaryButton")
        edit_btn.clicked.connect(self.edit_bid)
        controls.addWidget(edit_btn)

        del_btn = QPushButton("Delete Bid")
        del_btn.setObjectName("dangerButton")
        del_btn.clicked.connect(self.delete_bid)
        controls.addWidget(del_btn)

        controls.addStretch()
        main_layout.addLayout(controls)

        self.grid = QTableWidget(0, 9)
        self.grid.setHorizontalHeaderLabels(["ID", "Project Code", "Shots", "Complexity", "Est. Days", "Margin", "Est. Cost", "Final Budget", "Status"])
        self.style_table(self.grid)
        # Read-only (typing into Final Budget saved nothing); double-click
        # edits the bid; headers sort money and days by value.
        setup_table(self.grid)
        self.grid.doubleClicked.connect(lambda _index: self.edit_bid())

        self.toolbar = TableToolbar(self.grid, placeholder="Search project, complexity or status…",
                                    columns=(1, 3, 8), on_refresh=self.load_data)
        self.project_filter = self.toolbar.add_filter("Project", [("All projects", "")], column=1)
        self.status_filter = self.toolbar.add_filter(
            "Status", [("All statuses", ""), ("Draft", "Draft"), ("Approved", "Approved"),
                       ("Rejected", "Rejected")], column=8)
        main_layout.addWidget(self.toolbar)
        # Other people's bids appear without a restart.
        from slate.gui.components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(self, self.load_data, seconds=30,
                                         topics=("prod_bidding",))
        
        self.grid.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.grid.hideColumn(0) # Hide ID
        main_layout.addWidget(self.grid)
        # First read only now that the table is in the layout: a notice for a
        # failed read takes the table's place, and with no layout yet it
        # floated as a window of its own while the empty state said
        # there was nothing here.
        self.load_data()

    @on_database_error
    def load_data(self):
        try:
            query = "SELECT * FROM prod_bidding ORDER BY id DESC"
            bids = database_manager.execute_query(query) or []
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            # A failed read is not an empty pipeline.
            import logging
            from slate.gui.components.state_notice import show_load_error
            logging.exception("Bids could not be read")
            show_load_error(self, e, retry=self.load_data, what="the bids")
            return

        combo = self.project_filter
        keep = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("All projects", "")
        for code in sorted({str(b.get('project_code') or '') for b in bids if b.get('project_code')}):
            combo.addItem(code, code)
        index = combo.findData(keep)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

        total = len(bids)
        # Pipeline value is work that might still happen. Rejected bids were
        # counted in it, so the figure grew every time the studio lost a job.
        total_val = sum(row.get('estimated_budget', 0) or 0 for row in bids
                        if str(row.get('status') or '') in ('Draft', 'Approved'))
        approved = sum(1 for row in bids if row.get('status') == 'Approved')
        
        self.lbl_total.setText(str(total))
        self.lbl_value.setText(f"${total_val:,.0f}")
        self.lbl_approved.setText(str(approved))

        # The selection follows the bid (by id), not the row number: after a
        # delete the highlighted rows used to be other bids.
        with KeepSelection(self.grid):
            self._fill(bids)

    def _fill(self, bids):
        self.grid.setRowCount(len(bids))
        for r, row in enumerate(bids):
            bid_id = row.get('id')
            days = row.get('estimated_days', 0) or 0
            margin = row.get('target_margin', 0) or 0
            cost = row.get('estimated_cost', 0) or 0
            budget = row.get('estimated_budget', 0) or 0
            self.grid.setItem(r, 0, make_item(str(bid_id or ''), sort_value=bid_id, key=bid_id))
            self.grid.setItem(r, 1, make_item(str(row.get('project_code', ''))))
            self.grid.setItem(r, 2, make_item(str(row.get('shot_count', 0)),
                                              sort_value=row.get('shot_count', 0) or 0))
            self.grid.setItem(r, 3, make_item(str(row.get('complexity', ''))))
            self.grid.setItem(r, 4, make_item(f"{days:.1f} d", sort_value=days))
            self.grid.setItem(r, 5, make_item(f"{margin:.0f}%", sort_value=margin))
            self.grid.setItem(r, 6, make_item(f"${cost:,.2f}", sort_value=cost))
            self.grid.setItem(r, 7, make_item(f"${budget:,.2f}", sort_value=budget))

            status_item = make_item(str(row.get('status', '')))
            if status_item.text() == "Approved":
                status_item.setForeground(QColor("green"))
            elif status_item.text() == "Rejected":
                status_item.setForeground(QColor("red"))
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
        keys = [k for k in selected_keys(self.grid) if k is not None]
        return int(keys[0]) if keys else None

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
        # By bid id, never by row number.
        bid_ids = [k for k in selected_keys(self.grid) if k is not None]
        if not bid_ids:
            QMessageBox.warning(self, "%s bid" % ("Approve" if new_status == "Approved" else "Reject"),
                                "Select the bid first.")
            return

        for bid in bid_ids:
            query = "UPDATE prod_bidding SET status = %s WHERE id = %s"
            database_manager.execute_query(query, (new_status, int(bid)), fetch=False)
        self.load_data()
            
    def style_table(self, table: QTableWidget):
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setDefaultSectionSize(34)
        table.setStyleSheet("""
            QTableWidget { 
                background-color: #0D0D0F; 
                color: #E8E6E1; 
                gridline-color: #1D1D22; 
                border: 1px solid #1D1D22; 
                border-radius: 6px;
                font-size: 12px; 
            }
            QTableWidget::item:alternate { background-color: #16161A; }
            QTableWidget::item:selected { background-color: rgba(62, 168, 191, 0.18); color: white; }
            QHeaderView::section { 
                background-color: #16161A; 
                color: #87857F; 
                border: none;
                border-bottom: 2px solid #1D1D22; 
                border-right: 1px solid rgba(255, 255, 255, 0.04);
                padding: 8px 10px; 
                font-weight: 700;
                font-size: 11px;
                text-transform: uppercase;
            }
        """)
