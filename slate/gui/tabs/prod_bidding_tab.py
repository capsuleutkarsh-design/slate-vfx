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
from slate.gui.core.data_display import money_item, select_row_by_id
from slate.core.domain import money
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
        layout = tidy_form(QFormLayout(self))
        
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

    def build_ui(self, main_layout):
        main_layout.addWidget(page_title("Bidding", "Bids, their estimates and whether they were won"))

        # Summary on one compact line, so the table keeps the height at 1366x768.
        strip = StatStrip(compact=True)
        self.lbl_total = strip.add("Total bids", "0", tone="accent")
        self.lbl_value = strip.add("Pipeline value", "0", tone="ok")
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
        # Kept per currency - rupees and dollars do not add up to anything -
        # and shown short (3.8 Cr, 380.3M) with the exact figures in the
        # tooltip. It used to be whole dollars while the table showed cents.
        totals = money.sum_by_currency(
            (row.get('estimated_budget') or 0, row.get('currency') or 'USD') for row in bids
            if str(row.get('status') or '') in ('Draft', 'Approved'))
        approved = sum(1 for row in bids if row.get('status') == 'Approved')

        self.lbl_total.set_value(total)
        self.lbl_value.set_value(money.format_totals(totals, compact=True))
        self.lbl_value.setToolTip(money.format_totals(totals))
        self.lbl_approved.set_value(approved)

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
            self.grid.setItem(r, 0, make_item(str(bid_id or ''), sort_value=bid_id, key=bid_id))
            self.grid.setItem(r, 1, make_item(str(row.get('project_code', ''))))
            self.grid.setItem(r, 2, make_item(str(row.get('shot_count', 0)),
                                              sort_value=row.get('shot_count', 0) or 0))
            self.grid.setItem(r, 3, make_item(str(row.get('complexity', ''))))
            self.grid.setItem(r, 4, make_item(f"{days:.1f} d", sort_value=days))
            self.grid.setItem(r, 5, make_item(f"{margin:.0f}%", sort_value=margin))
            # Bids made before currencies were recorded were in dollars.
            code = row.get('currency') or 'USD'
            self.grid.setItem(r, 6, money_item(row.get('estimated_cost') or 0, code))
            self.grid.setItem(r, 7, money_item(row.get('estimated_budget') or 0, code))

            status_item = make_item(str(row.get('status', '')))
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
        # By bid id, never by row number.
        bid_ids = [k for k in selected_keys(self.grid) if k is not None]
        if not bid_ids:
            QMessageBox.warning(self, "%s bid" % ("Approve" if new_status == "Approved" else "Reject"),
                                "Select the bid first.")
            return

        failed = []
        for bid in bid_ids:
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
