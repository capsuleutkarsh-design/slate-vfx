from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDialog, QFormLayout, QComboBox, QDoubleSpinBox, QSpinBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from slate.core.infra.database_manager import database_manager
from slate.gui.core.offline_notice import on_database_error
from slate.gui.core.data_display import money_item, select_row_by_id
from slate.core.domain import money

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

        # Each bid has its own currency: the studio's (rupees unless changed
        # in the studio settings) or the foreign client's. Every amount in the
        # dialog is shown in it. A dollar sign used to be written into the
        # widgets, with digits grouped by the Windows locale - Indian here,
        # western in the table next to it.
        self.currency_input = QComboBox()
        for code, cur in money.CURRENCIES.items():
            self.currency_input.addItem(f"{cur.symbol}  {code} - {cur.name}", code)
        index = self.currency_input.findData(money.studio_currency())
        self.currency_input.setCurrentIndex(max(index, 0))

        self.cost_input = QDoubleSpinBox()
        self.cost_input.setRange(0, 100000000)
        self.cost_input.setDecimals(2)
        # No locale grouping in an editable box - the formatted figure is
        # shown by the budget line below instead.
        self.cost_input.setGroupSeparatorShown(False)
        self._rate_touched = False
        self.cost_input.valueChanged.connect(self.update_budget)
        self.cost_input.editingFinished.connect(self._rate_edited)

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

        # Kept (the tab reads it) but not shown: the figure is shown formatted
        # in its currency by budget_label.
        self.budget_input = QDoubleSpinBox()
        self.budget_input.setRange(0, 100000000000)
        self.budget_input.setDecimals(2)
        self.budget_input.setReadOnly(True)
        self.budget_input.setVisible(False)
        self.budget_label = QLabel("")
        self.budget_label.setStyleSheet("font-weight: bold;")

        self._apply_currency(set_rate=True)
        self.currency_input.currentIndexChanged.connect(lambda *_: self._apply_currency())

        layout.addRow("Project Code:", self.proj_input)
        layout.addRow("Currency:", self.currency_input)
        layout.addRow("Total Shots (Auto):", self.shot_count_input)
        layout.addRow("Average Complexity:", self.complexity_input)
        layout.addRow("Artist Day Rate:", self.cost_input)
        layout.addRow("Target Margin:", self.margin_input)
        layout.addRow("Estimated Artist Days:", self.days_input)
        layout.addRow("Final Estimated Budget:", self.budget_label)
        
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

    def currency(self) -> str:
        return self.currency_input.currentData() or money.studio_currency()

    def set_currency(self, code) -> None:
        index = self.currency_input.findData(money.normalise_code(code, "USD"))
        if index >= 0:
            self.currency_input.setCurrentIndex(index)

    def _rate_edited(self):
        self._rate_touched = True

    def _apply_currency(self, set_rate: bool = False):
        """Show the day rate in the bid's currency, and the studio's rate for it."""
        code = self.currency()
        self.cost_input.setPrefix(money.currency(code).symbol + " ")
        if set_rate or not self._rate_touched:
            rate = money.day_rate(code)
            if rate is None and code == "USD":
                from slate.core.domain.bidding import day_rate as _day_rate
                rate = _day_rate()
            if rate is not None:
                self.cost_input.setValue(float(rate))
        self.update_budget()

    def update_budget(self):
        # The days-per-shot figures used to be three literals here, so a studio
        # whose comp runs heavier than the default had no way to say so.
        from slate.core.domain.bidding import estimate

        if not hasattr(self, "budget_label"):
            return      # still being built
        result = estimate(self.shot_count_input.value(),
                          self.complexity_input.currentText(),
                          rate=self.cost_input.value(),
                          margin=self.margin_input.value())

        self.days_input.setValue(result["days"])
        self.budget_input.setValue(result["price"])
        self.budget_label.setText(money.format_money(result["price"], self.currency()))

class ProdBiddingTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
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
        self.load_data()
        
        self.grid.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
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
        # Kept per currency - rupees and dollars do not add up to anything -
        # and shown short (3.8 Cr, 380.3M) with the exact figures in the
        # tooltip. It used to be whole dollars while the table showed cents.
        totals = money.sum_by_currency(
            (row.get('estimated_budget') or 0, row.get('currency') or 'USD') for row in bids
            if str(row.get('status') or '') in ('Draft', 'Approved'))
        approved = sum(1 for row in bids if row.get('status') == 'Approved')

        self.lbl_total.setText(str(total))
        self.lbl_value.setText(money.format_totals(totals, compact=True))
        self.lbl_value.setToolTip(money.format_totals(totals))
        self.lbl_approved.setText(str(approved))

        for r, row in enumerate(bids):
            self.grid.setItem(r, 0, QTableWidgetItem(str(row.get('id', ''))))
            self.grid.setItem(r, 1, QTableWidgetItem(str(row.get('project_code', ''))))
            self.grid.setItem(r, 2, QTableWidgetItem(str(row.get('shot_count', 0))))
            self.grid.setItem(r, 3, QTableWidgetItem(str(row.get('complexity', ''))))
            self.grid.setItem(r, 4, QTableWidgetItem(f"{row.get('estimated_days', 0):.1f} d"))
            self.grid.setItem(r, 5, QTableWidgetItem(f"{row.get('target_margin', 0):.0f}%"))
            # Bids made before currencies were recorded were in dollars.
            code = row.get('currency') or 'USD'
            self.grid.setItem(r, 6, money_item(row.get('estimated_cost') or 0, code))
            self.grid.setItem(r, 7, money_item(row.get('estimated_budget') or 0, code))
            
            status_item = QTableWidgetItem(str(row.get('status', '')))
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
            (project_code, project_name, shot_count, complexity, estimated_days, target_margin, estimated_cost, estimated_budget, status, currency)
            VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, 'Draft', %s)
            RETURNING id
            """
            params = (proj, proj, shots, comp, days, margin, cost, budget, dialog.currency())
            # execute_query(..., fetch=False) returned None even when the bid
            # was written, so neither the message nor the reload ran and people
            # saved again, making duplicates. Checked, reloaded, selected.
            result = database_manager.execute_update(query, params)
            self.load_data()
            if not result:
                QMessageBox.warning(self, "Not saved", "The bid was not saved:\n\n%s"
                                    % (result.error or "the database refused it"))
                return
            if result.last_id is not None:
                select_row_by_id(self.grid, result.last_id)
            QMessageBox.information(self, "Draft bid created", f"Draft bid created for {proj}.")

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
            dialog.set_currency(row.get("currency") or "USD")
            try:
                dialog.margin_input.setValue(float(row.get("target_margin") or 0))
            except (TypeError, ValueError):
                pass

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        days = dialog.days_input.value()
        result = database_manager.execute_update(
            "UPDATE prod_bidding SET complexity = %s, shot_count = %s, "
            "estimated_days = %s, target_margin = %s, estimated_cost = %s, "
            "estimated_budget = %s, currency = %s WHERE id = %s",
            (dialog.complexity_input.currentText(), dialog.shot_count_input.value(),
             days, dialog.margin_input.value(), days * dialog.cost_input.value(),
             dialog.budget_input.value(), dialog.currency(), bid_id))
        self.load_data()
        select_row_by_id(self.grid, bid_id)
        if not result.changed:
            QMessageBox.warning(self, "Not saved", "The bid was not changed:\n\n%s"
                                % (result.error or "it no longer exists"))

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
        result = database_manager.execute_update(
            "DELETE FROM prod_bidding WHERE id = %s", (bid_id,))
        self.load_data()
        if not result:
            QMessageBox.warning(self, "Not deleted", "The bid was not deleted:\n\n%s"
                                % (result.error or "the database refused it"))

    def update_status(self, new_status):
        selected_rows = set(item.row() for item in self.grid.selectedItems())
        if not selected_rows:
            QMessageBox.warning(self, "Selection Empty", "Please select a bid to update.")
            return
            
        failed = []
        for r in selected_rows:
            item = self.grid.item(r, 0)
            if not item: continue
            bid = item.text()
            query = "UPDATE prod_bidding SET status = %s WHERE id = %s"
            result = database_manager.execute_update(query, (new_status, int(bid)))
            if not result.changed:
                failed.append(result.error or "that bid no longer exists")
        self.load_data()
        if failed:
            QMessageBox.warning(self, "Not all updated",
                                "%d bid(s) were not marked %s:\n\n%s"
                                % (len(failed), new_status, failed[0]))
            
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
