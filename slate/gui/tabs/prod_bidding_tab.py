"""
Bidding: what each job is priced at, its revisions, and how won work tracks.

It used to be one dialog with one complexity and one day rate per project, a
table you could type into without saving, and Approve / Reject / Edit / Delete
buttons that answered with pop-ups. Editing re-priced a bid at the default
rate (and an approved one to the tracker's current shot count), Delete removed
the first of the selected bids for good, the pipeline added every draft of
every project and the sidebar promised cost tracking that did not exist.

The rules are in core/domain/bidding.py, the SQL in
core/infra/bid_repository.py. This file is the screen:

    New bid / Edit…        bid_editor_dialog.py: line items, currency, margin,
                           discount, tax (GST on rupee bids), live totals
    New revision           a sent or decided bid is changed by revising it;
                           Compare revisions shows what moved
    Mark sent / Won / Lost Won and Lost need the 'Approve bids' ability and are
                           never yours to give on your own bid (Admin and
                           Developer excepted); Reopen puts a bid back to draft
    Archive / Restore      bids are kept, never lost; only an Admin can delete a
                           plain draft for good
    Tracking               below the table for a won bid: bid vs planned vs
                           delivered vs actual days and money, per department
    Export                 the list as CSV/Excel, a bid as a PDF for the client
    Create shots           a won bid's shots onto the dashboard
"""

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QMenu, QSplitter,
    QTableWidget, QVBoxLayout, QWidget,
)

from slate.core.domain import bidding as DB
from slate.core.domain import money
from slate.core.infra.gate import Gate
from slate.gui.core.offline_notice import on_database_error
from slate.gui.core.controls import make_button, page_title, read_only_chip
from slate.gui.core.empty_state import EmptyState
from slate.gui.core.stat_card import StatStrip
from slate.gui.core.table_style import KeepColourDelegate, dim_cell, set_cell_status, style_table
from slate.gui.core.data_display import date_item, money_item
from slate.gui.components.table_tools import (
    KEY_ROLE, KeepSelection, TableToolbar, make_item, select_keys, selected_keys, setup_table,
)

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else is reported as a failed read.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""

logger = logging.getLogger(__name__)

(C_ID, C_PROJECT, C_CLIENT, C_REV, C_LINES, C_SHOTS, C_DAYS, C_COST, C_PRICE, C_TOTAL,
 C_STATUS, C_CURRENCY, C_CREATED, C_BY) = range(14)
# C_PRICE holds the stored price after discount: the Subtotal, as the editor
# and the PDF call it (Price is the figure before the discount).
HEADERS = ["ID", DB.WORDS["project"], DB.WORDS["client"], DB.WORDS["revision"], "Lines",
           DB.WORDS["shots"], DB.WORDS["days"], DB.WORDS["cost"], DB.WORDS["taxable"],
           DB.WORDS["total"], DB.WORDS["status"], "Currency", "Created", "Created by"]


def export_bid_pdf(parent, bid, lines, totals, path: str = None):
    """
    Print one bid to PDF for the client (cost and margin left out). Returns
    the path written, or None. The file dialog starts in Documents.
    """
    from PySide6.QtGui import QPageSize, QPdfWriter, QTextDocument
    from PySide6.QtWidgets import QFileDialog
    from slate.core.domain.bid_export import bid_document_html
    from slate.gui.components.feedback import toast
    if path is None:
        default = str(Path.home() / "Documents" / f"Bid_{bid.project_code}_v{bid.revision}.pdf")
        path, _ = QFileDialog.getSaveFileName(parent, "Export bid as PDF", default, "PDF (*.pdf)")
        if not path:
            return None
    try:
        from slate.core.infra.studio_settings import get_setting
        studio = str(get_setting("studio_name", "") or "")
    except Exception:
        studio = ""
    document = QTextDocument()
    document.setHtml(bid_document_html(bid, lines, totals, studio=studio))
    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setResolution(150)
    document.print_(writer)
    del writer
    if Path(path).is_file() and Path(path).stat().st_size > 0:
        toast(parent, f"Saved {Path(path).name}.", "success")
        return path
    toast(parent, f"Could not write {path}.", "error")
    return None


class _FillSplitter(QSplitter):
    """
    Asks only for its minimum height. The page title wraps, so the page frame
    sizes the page by its preferred height - and the table's preferred height
    pushed the page past a 1280x720 screen into a scroll bar. The splitter
    takes whatever room is left instead.
    """

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setHeight(self.minimumSizeHint().height())
        return hint


class ProdBiddingTab(QWidget):
    def __init__(self, parent=None, user_data=None, repo=None):
        super().__init__(parent)
        # Who is looking: Won/Lost need approve_bid, and nobody decides their own bid.
        self.user_data = dict(user_data or {})
        roles = self.user_data.get("roles") or self.user_data.get("role") or []
        self.user_roles = [roles] if isinstance(roles, str) else list(roles)
        self.username = str(self.user_data.get("username") or "")
        from slate.core.domain import access
        self.can_approve = access.can(self.user_roles, "approve_bid")
        self.is_superuser = access.is_superuser(self.user_roles)
        # Without 'Edit bids' the tab is read-only, like Scheduling without its ability.
        self.can_write = access.can(self.user_roles, "bid_write")
        # Bidding settings: margins and complexities for whoever approves bids;
        # day rates, GST and the studio name for Studio settings, as Help says.
        self.can_change_settings = self.is_superuser or self.can_approve
        self.can_edit_rates = self.is_superuser or access.can(self.user_roles, "studio_settings")
        if repo is None:
            from slate.core.infra.bid_repository import BidRepository
            repo = BidRepository(roles=self.user_roles, username=self.username)
        self.repo = repo
        self.bids = []
        self.by_id = {}

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        self.build_ui(main_layout)

    # ------------------------------------------------------------------ layout
    def build_ui(self, main_layout):
        main_layout.addWidget(page_title("Bidding", "What each job is priced at, its revisions, and "
                                                    "how won work is tracking"))

        strip = StatStrip(compact=True)
        self.lbl_total = strip.add("Bids", "0", tone="accent",
                                   tooltip="Bids shown (latest revision of each)")
        self.lbl_value = strip.add("Open pipeline", "0", tone="info",
                                   tooltip="Draft and sent bids - the newest per project")
        self.lbl_approved = strip.add("Won", "0", tone="ok")
        main_layout.addWidget(strip)

        controls = QHBoxLayout()
        controls.setSpacing(Gate.SPACE_2)
        self.new_button = make_button("New bid", "primary", icon="plus", on_click=self.add_bid)
        self.edit_button = make_button("Edit…", icon="edit", on_click=self.edit_bid,
                                       tooltip="Open the selected bid (or double-click it)")
        self.revise_button = make_button("New revision", icon="copy", on_click=self.revise_bid,
                                         tooltip="Copy the bid into a new draft revision; this one is kept")
        self.sent_button = make_button("Mark sent", icon="send", on_click=lambda: self.update_status(DB.SENT))
        self.won_button = make_button("Won", icon="check", on_click=lambda: self.update_status(DB.WON))
        self.lost_button = make_button("Lost", icon="x-circle", on_click=lambda: self.update_status(DB.LOST))
        self.archive_button = make_button("Archive…", icon="archive", on_click=self.archive_bids)
        self.restore_button = make_button("Restore", icon="undo", on_click=self.restore_bids)
        self.more_button = make_button("More", "secondary", icon="chevron-down",
                                       tooltip="Reopen, compare, duplicate, export, create shots, settings")
        self.more_menu = QMenu(self.more_button)
        self.more_button.setMenu(self.more_menu)
        self.more_menu.aboutToShow.connect(self._fill_more_menu)
        for w in (self.new_button, self.edit_button, self.revise_button):
            controls.addWidget(w)
        controls.addSpacing(Gate.SPACE_2)
        # The decision buttons, together - and only for those who may decide.
        for w in (self.sent_button, self.won_button, self.lost_button):
            controls.addWidget(w)
        self.won_button.setVisible(self.can_approve or self.is_superuser)
        self.lost_button.setVisible(self.can_approve or self.is_superuser)
        controls.addSpacing(Gate.SPACE_2)
        controls.addWidget(self.archive_button)
        controls.addWidget(self.restore_button)
        controls.addWidget(self.more_button)
        for w in (self.new_button, self.revise_button, self.sent_button, self.archive_button):
            w.setVisible(self.can_write)
        self.read_only_badge = read_only_chip("You can look at bids. Making or changing them needs "
                                              "the 'Edit bids' ability on your role.")
        self.read_only_badge.setVisible(not self.can_write)
        controls.insertWidget(0, self.read_only_badge)
        controls.addStretch()
        self.revisions_box = QCheckBox("All revisions")
        self.revisions_box.setToolTip("Show superseded revisions too, not only the latest")
        self.archived_box = QCheckBox("Archived")
        self.archived_box.setToolTip("Show archived bids too")
        for box in (self.revisions_box, self.archived_box):
            box.toggled.connect(lambda *_: self.load_data())
            controls.addWidget(box)
        main_layout.addLayout(controls)

        self.grid = QTableWidget(0, len(HEADERS))
        self.grid.setHorizontalHeaderLabels(HEADERS)
        self.style_table(self.grid)
        # Read-only (typing into a price saved nothing); double-click opens the bid.
        setup_table(self.grid)
        self.grid.setWordWrap(False)
        self.grid.doubleClicked.connect(lambda _index: self.edit_bid())
        self.grid.itemSelectionChanged.connect(self._selection_changed)

        self.toolbar = TableToolbar(self.grid, placeholder="Search project, client or person…",
                                    columns=(C_PROJECT, C_CLIENT, C_STATUS, C_BY), on_refresh=self.load_data,
                                    noun="bid")
        self.project_filter = self.toolbar.add_filter("Project", [("All projects", "")], column=C_PROJECT)
        self.status_filter = self.toolbar.add_filter(
            "Status", [("All statuses", "")] + [(DB.status_label(s), DB.status_label(s))
                                               for s in DB.STATUSES], column=C_STATUS,
            match=lambda cell, value: cell.casefold().startswith(str(value).casefold()))
        self.currency_filter = self.toolbar.add_filter(
            "Currency", [("All currencies", "")] + [(f"{c.symbol} {code}", code)
                                                   for code, c in money.CURRENCIES.items()],
            column=C_CURRENCY)
        for combo, width in ((self.project_filter, 22), (self.status_filter, 12),
                             (self.currency_filter, 12)):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(width)
            combo.view().setMinimumWidth(240)
        self.toolbar.filter.counted.connect(self._filtered)
        main_layout.addWidget(self.toolbar)

        from slate.gui.components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(self, self.load_data, seconds=30,
                                         topics=("prod_bidding", "prod_bid_lines"))

        self.grid.hideColumn(C_ID)
        self.grid.hideColumn(C_CURRENCY)
        from slate.gui.tabs.bid_tracking_view import BidTrackingView
        self.tracking = BidTrackingView(self)
        # Small minimums, so the page fits a 1280x720 screen without scrolling.
        self.grid.setMinimumHeight(110)
        # The tracking table keeps room for its header and at least two rows:
        # at 1280x720 it was squeezed to the header alone.
        self.tracking.table.setMinimumHeight(Gate.ROW_HEIGHT * 3 + 8)
        self.tracking.setMinimumHeight(Gate.ROW_HEIGHT * 3 + 8 + 48)
        split = _FillSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.grid)
        split.addWidget(self.tracking)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setChildrenCollapsible(False)
        self.splitter = split
        main_layout.addWidget(split, 1)

        self.empty = EmptyState.over(
            self.grid, "No bids yet",
            "Create a bid for a project to see its price and the pipeline here.",
            primary=("New bid", self.add_bid) if self.can_write else None, glyph="money")
        self._sync_buttons()
        # First read only now that the table is in the layout.
        self.load_data()

    def style_table(self, table: QTableWidget):
        """The project takes the spare width; counts and money right-aligned to their content."""
        style_table(table, {
            DB.WORDS["project"]: "stretch",
            DB.WORDS["client"]: ("interactive", 160),
            DB.WORDS["revision"]: "contents",
            "Lines": "numeric",
            DB.WORDS["shots"]: "numeric",
            DB.WORDS["days"]: "numeric",
            DB.WORDS["cost"]: "numeric",
            DB.WORDS["taxable"]: "numeric",
            DB.WORDS["total"]: "numeric",
            DB.WORDS["status"]: "contents",
            "Created": "contents",
            "Created by": ("interactive", 140),
        })
        # A selected row keeps its status colour.
        table.setItemDelegate(KeepColourDelegate(table))

    # ------------------------------------------------------------------ data
    @on_database_error
    def load_data(self, *_):
        try:
            bids = self.repo.list(include_archived=self.archived_box.isChecked(),
                                  all_revisions=self.revisions_box.isChecked())
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            # A failed read is not an empty pipeline.
            from slate.gui.components.state_notice import show_load_error
            logger.exception("Bids could not be read")
            show_load_error(self, e, retry=self.load_data, what="the bids")
            return
        from slate.gui.components.state_notice import clear_state
        clear_state(self)
        self.bids = bids
        self.by_id = {b.id: b for b in bids}
        self._names = self._display_names({b.created_by for b in bids if b.created_by})

        combo = self.project_filter
        keep = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("All projects", "")
        # The filter matches the Project cell's text. Bids from before project
        # codes were stored show their name there (or a dash), so each gets an
        # entry of its own - they used to fold into one whose value was "",
        # which is "All projects".
        projects, legacy = {}, set()
        for b in bids:
            if b.project_code:
                projects.setdefault(b.project_code, b.project_name)
            else:
                legacy.add(b.project_name or "—")
        for code in sorted(projects, key=str.casefold):
            name = projects[code]
            combo.addItem(f"{code} – {name}" if name and name.casefold() != code.casefold() else code, code)
        for name in sorted(legacy, key=str.casefold):
            combo.addItem("(no project)" if name == "—" else f"{name} (no project code)", name)
        index = combo.findData(keep)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

        # The selection follows the bid (by id), not the row number.
        with KeepSelection(self.grid):
            self._fill(bids)
        self.toolbar.filter.apply()
        self.empty.refresh()
        self._update_cards()
        self._selection_changed()

    @staticmethod
    def _display_names(usernames):
        try:
            from slate.core.domain.people import display_names
            return {u: (n or u) for u, n in display_names(usernames).items()}
        except DatabaseUnavailableError:
            raise
        except Exception:
            return {u: u for u in usernames}

    def _update_cards(self):
        shown = self.visible_bids()
        p = DB.pipeline(b.as_dict() for b in shown)
        self.lbl_total.set_value(len(shown))
        # Nothing open / nothing won is a dash, not '₹0' while filtering dollars.
        self.lbl_value.set_value(money.format_totals(p.open_totals, compact=True)
                                 if p.open_count else "—")
        self.lbl_value.setToolTip(f"{p.open_count} open "
                                  + ("bid" if p.open_count == 1 else "bids")
                                  + f" (the newest per project): {money.format_totals(p.open_totals)}")
        self.lbl_approved.set_value(money.format_totals(p.won_totals, compact=True)
                                    if p.won_count else "—")
        self.lbl_approved.setToolTip(f"{p.won_count} won: {money.format_totals(p.won_totals)}")

    def visible_bids(self):
        out = []
        for row in range(self.grid.rowCount()):
            if not self.grid.isRowHidden(row):
                bid = self._bid_of_row(row)
                if bid is not None:
                    out.append(bid)
        return out

    def _bid_of_row(self, row):
        item = self.grid.item(row, C_ID)
        return self.by_id.get(item.data(KEY_ROLE)) if item else None

    def _fill(self, bids):
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        self.grid.setRowCount(0)
        self.grid.setRowCount(len(bids))
        for r, b in enumerate(bids):
            # Build every cell of the row from the record: a bad value shows a
            # dash rather than stopping the table half way with stale rows.
            self.grid.setItem(r, C_ID, make_item(str(b.id), sort_value=b.id, key=b.id))
            # A bid from before project codes were stored has only its name.
            project = make_item(b.project_code or b.project_name or "—",
                                tooltip=(f"{b.project_code} – {b.project_name}"
                                         if b.project_code and b.project_name else
                                         (b.project_code or b.project_name)))
            self.grid.setItem(r, C_PROJECT, project)
            client = make_item(b.client_name or "—", tooltip=b.client_name)
            if not b.client_name:
                dim_cell(client)
            self.grid.setItem(r, C_CLIENT, client)
            self.grid.setItem(r, C_REV, make_item(f"v{b.revision}", sort_value=b.revision))
            lines = make_item(str(b.line_count) if b.has_lines else "—",
                              sort_value=b.line_count, align=right,
                              tooltip="" if b.has_lines else "Made before line items: one line")
            if not b.has_lines:
                dim_cell(lines)
            self.grid.setItem(r, C_LINES, lines)
            self.grid.setItem(r, C_SHOTS, make_item(f"{b.shot_count:,}", sort_value=b.shot_count, align=right))
            self.grid.setItem(r, C_DAYS, make_item(DB.fmt_days(b.estimated_days),
                                                   sort_value=b.estimated_days, align=right))
            self.grid.setItem(r, C_COST, money_item(b.estimated_cost, b.currency))
            self.grid.setItem(r, C_PRICE, money_item(b.estimated_budget, b.currency))
            total = money_item(b.total_amount, b.currency)
            if b.tax_amount:
                total.setToolTip(f"Includes {b.tax_label or 'tax'} {DB.fmt_percent(b.tax)}: "
                                 f"{money.format_money(b.tax_amount, b.currency)}")
            self.grid.setItem(r, C_TOTAL, total)
            status_text = DB.status_label(b.status) + (" (archived)" if b.archived else "")
            if b.status in DB.DECIDED and not b.latest:
                # Won work being revised: still won and counted until v2 is decided.
                status_text += f" – v{b.newest_revision} in progress"
            status = make_item(status_text)
            set_cell_status(status, "idle" if b.archived else DB.status_tone(b.status), background=False)
            if b.decided_by:
                status.setToolTip(f"{DB.status_label(b.status)} - decided by "
                                  f"{self._names.get(b.decided_by, b.decided_by)}")
            self.grid.setItem(r, C_STATUS, status)
            self.grid.setItem(r, C_CURRENCY, make_item(b.currency))
            self.grid.setItem(r, C_CREATED, date_item(b.created_at))
            by = make_item(self._names.get(b.created_by, b.created_by) if b.created_by else "—",
                           tooltip=b.created_by)
            if not b.created_by:
                dim_cell(by)
            self.grid.setItem(r, C_BY, by)
            if b.archived or b.status == DB.SUPERSEDED:
                for c in (C_PROJECT, C_REV, C_SHOTS, C_DAYS, C_COST, C_PRICE, C_TOTAL):
                    item = self.grid.item(r, c)
                    if item is not None:
                        dim_cell(item)

    def _filtered(self, visible, total):
        if not hasattr(self, "empty"):
            return
        narrowed = bool(total) and visible == 0
        self.empty.set_filtered(narrowed, on_clear=self.toolbar.filter.clear, noun="bids")
        if narrowed:
            self.empty.setVisible(True)
            self.empty.raise_()
        else:
            self.empty.refresh()
        self._update_cards()

    # ------------------------------------------------------------------ selection
    def _selected(self):
        return [self.by_id[int(k)] for k in selected_keys(self.grid)
                if k is not None and int(k) in self.by_id]

    def _selection_changed(self):
        self._sync_buttons()
        chosen = self._selected()
        if len(chosen) == 1 and chosen[0].status == DB.WON:
            self._show_tracking(chosen[0])
        elif len(chosen) == 1:
            self.tracking.show_nothing(f"{chosen[0].title} is {DB.status_label(chosen[0].status).lower()}"
                                       " - tracking starts once a bid is won.")
        else:
            self.tracking.show_nothing()

    @on_database_error
    def _show_tracking(self, bid):
        try:
            tracking = self.repo.tracking(bid)
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.exception("Tracking not read")
            self.tracking.show_nothing(f"The dashboard figures could not be read: {exc}")
            return
        self.tracking.show_tracking(bid, tracking)
        # Give the tracking half the room when there is something to track.
        total = sum(self.splitter.sizes())
        if total > 0 and self.splitter.sizes()[1] < total * 0.45:
            self.splitter.setSizes([int(total * 0.5), total - int(total * 0.5)])

    def _sync_buttons(self):
        chosen = self._selected()
        one = len(chosen) == 1
        live = [b for b in chosen if not b.archived]
        self.edit_button.setEnabled(one)
        self.edit_button.setText("Edit…" if not one or (chosen[0].editable and self.can_write) else "Open…")
        # Revising or archiving a won/lost bid takes the decision away, so it
        # needs what deciding needs (approve_bid, never your own bid).
        refusals = {b.id: self.repo.decided_refusal(b, self.username) for b in chosen}
        revise_why = ""
        if one:
            b = chosen[0]
            revise_why = (refusals[b.id]
                          or ("A draft is changed in place - use Edit." if b.status == DB.DRAFT else "")
                          or ("Archived - restore it first." if b.archived else "")
                          or (f"v{b.newest_revision} is the newest revision." if not b.latest else "")
                          or ("A newer revision exists." if b.status == DB.SUPERSEDED else ""))
        self.revise_button.setEnabled(one and not revise_why)
        self.revise_button.setToolTip(revise_why or "Copy the bid into a new draft revision; this one is kept")
        self.sent_button.setEnabled(bool(live) and all(DB.can_change(b.status, DB.SENT) for b in live))
        # Won and Lost say why not (your own bid) instead of asking and then refusing.
        for button, status in ((self.won_button, DB.WON), (self.lost_button, DB.LOST)):
            why = next((self.repo.decision_refusal(b, status) for b in live
                        if self.repo.decision_refusal(b, status)), "")
            button.setEnabled(bool(live) and not why and all(DB.can_change(b.status, status) and b.latest
                                                            for b in live))
            button.setToolTip(why)
        self.archive_button.setEnabled(bool(live) and not any(refusals[b.id] for b in live))
        blocked = next((refusals[b.id] for b in live if refusals[b.id]), "")
        self.archive_button.setToolTip(blocked)
        self.archive_button.setVisible(self.can_write and (not chosen or bool(live)))
        archived = [b for b in chosen if b.archived]
        self.restore_button.setVisible(self.can_write and bool(archived))
        self.restore_button.setEnabled(bool(archived) and not any(refusals[b.id] for b in archived))

    def _fill_more_menu(self):
        menu = self.more_menu
        menu.clear()
        chosen = self._selected()
        one = chosen[0] if len(chosen) == 1 else None
        reopen = menu.addAction("Reopen as draft", lambda: self.update_status(DB.DRAFT))
        reopen.setEnabled(bool(chosen) and all(DB.can_change(b.status, DB.DRAFT) and not b.archived
                                                and b.latest for b in chosen)
                          and (self.can_write or self.can_approve))
        menu.addSeparator()
        compare = menu.addAction("Compare revisions…", self.compare_revisions)
        compare.setEnabled(one is not None and (one.revision > 1 or one.status == DB.SUPERSEDED
                                                or not one.latest))
        menu.addAction("Duplicate to project…", self.duplicate_bid).setEnabled(
            one is not None and self.can_write)
        menu.addAction("Export bid as PDF…", self.export_pdf).setEnabled(one is not None)
        menu.addAction("Export list…", self.export_list).setEnabled(self.grid.rowCount() > 0)
        shots = menu.addAction("Create shots on the dashboard…", self.create_shots)
        shots.setEnabled(one is not None and one.status == DB.WON and not one.archived
                         and self.repo.can_create_shots())
        shots.setToolTip("Adds the shots this won bid names to the VFX Dashboard, with their bid days")
        if self.is_superuser:
            menu.addSeparator()
            delete = menu.addAction("Delete draft permanently…", self.delete_draft)
            delete.setEnabled(one is not None and one.status == DB.DRAFT and one.revision == 1)
        if self.can_change_settings:
            menu.addSeparator()
            menu.addAction("Bidding settings…", self.open_settings)

    # ------------------------------------------------------------------ actions
    def _projects(self):
        try:
            return self.repo.projects()
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.exception("Projects not read")
            from slate.gui.components.feedback import warn
            warn(self, "New bid", f"The project list could not be read: {exc}")
            return None

    @on_database_error
    def add_bid(self, *_):
        from slate.gui.tabs.bid_editor_dialog import BidEditorDialog
        projects = self._projects()
        if projects is None:
            return
        if not projects:
            from slate.gui.components.feedback import inform
            inform(self, "New bid", "There are no active projects yet.",
                   "Create the project on the VFX Dashboard first, then bid it here.")
            return
        dialog = BidEditorDialog(self, repo=self.repo, projects=projects, username=self.username,
                                 default_project=self.project_filter.currentData() or "")
        from slate.gui.components.screen_fit import fit_to_screen
        fit_to_screen(dialog, 1180, 760)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.saved_id is None:
            return
        self.load_data()
        select_keys(self.grid, [dialog.saved_id])
        from slate.gui.components.feedback import toast
        toast(self, f"Draft bid created for {dialog.project_code()}.", "success")

    @on_database_error
    def edit_bid(self, *_):
        chosen = self._selected()
        if len(chosen) != 1:
            return
        bid = self.repo.get(chosen[0].id)
        if bid is None:
            self.load_data()
            return
        from slate.gui.tabs.bid_editor_dialog import BidEditorDialog
        from slate.gui.components.screen_fit import fit_to_screen
        dialog = BidEditorDialog(self, repo=self.repo, bid=bid, lines=self.repo.lines(bid.id),
                                 username=self.username, read_only=not self.can_write)
        fit_to_screen(dialog, 1180, 760)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.load_data()
        from slate.gui.components.feedback import toast
        if dialog.revision_id is not None:
            # As from the toolbar's New revision: the new draft opens to be changed.
            select_keys(self.grid, [dialog.revision_id])
            toast(self, f"{bid.project_code} v{bid.revision + 1} is a new draft; "
                        f"v{bid.revision} is kept as it was.", "success")
            self.edit_bid()
        else:
            select_keys(self.grid, [bid.id])
            toast(self, f"Saved {bid.title}.", "success")

    @on_database_error
    def revise_bid(self, *_):
        chosen = self._selected()
        if len(chosen) != 1:
            return
        bid = chosen[0]
        from slate.gui.components.feedback import toast, warn
        try:
            new_id = self.repo.revise(bid.id, by=self.username)
        except DatabaseUnavailableError:
            raise
        except (DB.BidError, PermissionError) as exc:
            warn(self, "New revision", str(exc))
            return
        self.load_data()
        select_keys(self.grid, [new_id])
        toast(self, f"{bid.project_code} v{bid.revision + 1} is a new draft; v{bid.revision} is kept.",
              "success")
        self.edit_bid()

    @on_database_error
    def update_status(self, new_status, *_):
        # By bid id, never by row number.
        chosen = [b for b in self._selected() if not b.archived]
        if not chosen:
            return
        from slate.gui.components.feedback import confirm, toast, warn
        word = DB.status_label(new_status)
        names = ", ".join(b.title for b in chosen[:5]) + (" …" if len(chosen) > 5 else "")
        decided = [b for b in chosen if b.status in DB.DECIDED and b.status != new_status]
        if new_status in DB.DECIDED or decided:
            question = f"Mark {len(chosen)} bid{'s' if len(chosen) != 1 else ''} as {word}: {names}?"
            if decided:
                question = (f"Change the decision on {', '.join(b.title for b in decided[:5])} "
                            f"({', '.join(DB.status_label(b.status) for b in decided[:5])}) to {word}?")
            if not confirm(self, f"Mark as {word}", question,
                           yes_label=("Change decision" if decided else f"Mark {word}")):
                return
        # Every revision of these bids as it is now, for Undo (a decision
        # also supersedes the revision it replaces).
        before = {r.id: r for b in chosen for r in self.repo.revisions(b.bid_group or b.id)}
        try:
            previous = self.repo.set_status([b.id for b in chosen], new_status, by=self.username)
        except DatabaseUnavailableError:
            raise
        except (DB.BidError, PermissionError) as exc:
            warn(self, f"Mark as {word}", str(exc))
            self.load_data()
            return
        except Exception as exc:
            logger.exception("Bid status not changed")
            warn(self, f"Mark as {word}", f"No bid was changed: {exc}")
            self.load_data()
            return
        changed = sum(1 for old in previous.values() if old != DB.normalise_status(new_status))
        self.load_data()
        if not changed:
            toast(self, f"Nothing changed - already {word}.", "info")
            return

        def undo():
            try:
                kept = self.repo.undo_status(list(before.values()), new_status, by=self.username)
            except DatabaseUnavailableError:
                raise
            except Exception as exc:
                warn(self, f"Undo {word}", f"The bids could not be put back: {exc}")
                self.load_data()
                return
            self.load_data()
            toast(self, f"Marked {word} undone." + (f" {', '.join(kept[:3])} changed since and "
                                                     "were left as they are." if kept else ""), "info")

        toast(self, f"{changed} bid{'s' if changed != 1 else ''} marked {word}.", "success",
              action=("Undo", undo))

    @on_database_error
    def archive_bids(self, *_):
        chosen = [b for b in self._selected() if not b.archived]
        if not chosen:
            return
        from slate.gui.components.feedback import confirm, toast, warn
        names = ", ".join(b.title for b in chosen[:5]) + (" …" if len(chosen) > 5 else "")
        if not confirm(self, "Archive bids",
                       f"Archive {len(chosen)} bid{'s' if len(chosen) != 1 else ''}: {names}?",
                       yes_label=f"Archive {len(chosen)}",
                       informative="Archived bids leave the list and the pipeline but are kept "
                                   "with their history; tick Archived to see or restore them."):
            return
        ids = [b.id for b in chosen]
        try:
            count = self.repo.archive(ids, by=self.username)
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.exception("Bids not archived")
            warn(self, "Archive bids", f"Nothing was archived: {exc}")
            return
        self.load_data()

        def undo():
            try:
                self.repo.restore(ids, by=self.username)
            except DatabaseUnavailableError:
                raise
            except Exception as exc:
                warn(self, "Undo archive", f"The bids could not be restored: {exc}")
            self.load_data()

        toast(self, f"Archived {count} bid{'s' if count != 1 else ''}.", "success", action=("Undo", undo))

    @on_database_error
    def restore_bids(self, *_):
        chosen = [b for b in self._selected() if b.archived]
        if not chosen:
            return
        from slate.gui.components.feedback import toast, warn
        try:
            count = self.repo.restore([b.id for b in chosen], by=self.username)
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            warn(self, "Restore bids", f"Nothing was restored: {exc}")
            return
        self.load_data()
        toast(self, f"Restored {count} bid{'s' if count != 1 else ''}.", "success")

    @on_database_error
    def delete_draft(self, *_):
        chosen = self._selected()
        if len(chosen) != 1:
            return
        bid = chosen[0]
        from slate.gui.components.feedback import toast, warn
        typed, ok = QInputDialog.getText(
            self, "Delete draft permanently",
            f"This deletes {bid.title} and its lines for good - there is no undo, and archiving "
            f"keeps it instead.\n\nType DELETE to confirm:")
        if not ok or typed.strip() != "DELETE":
            return
        try:
            self.repo.delete_draft(bid.id, by=self.username)
        except DatabaseUnavailableError:
            raise
        except (DB.BidError, PermissionError) as exc:
            warn(self, "Delete draft", str(exc))
            return
        self.load_data()
        toast(self, f"Deleted {bid.title}.", "success")

    @on_database_error
    def duplicate_bid(self, *_):
        chosen = self._selected()
        if len(chosen) != 1:
            return
        bid = chosen[0]
        projects = self._projects() or []
        choices = [f"{c} – {n}" if n and n.casefold() != c.casefold() else c for c, n in projects]
        if not choices:
            return
        text, ok = QInputDialog.getItem(self, "Duplicate to project",
                                        f"Copy the lines of {bid.title} into a new draft for:",
                                        choices, 0, False)
        if not ok:
            return
        code = projects[choices.index(text)][0]
        from slate.gui.components.feedback import toast, warn
        try:
            new_id = self.repo.duplicate_to_project(bid.id, code, by=self.username)
        except DatabaseUnavailableError:
            raise
        except (DB.BidError, PermissionError) as exc:
            warn(self, "Duplicate to project", str(exc))
            return
        self.load_data()
        select_keys(self.grid, [new_id])
        toast(self, f"New draft for {code} from {bid.title}.", "success")

    @on_database_error
    def compare_revisions(self, *_):
        chosen = self._selected()
        if len(chosen) != 1:
            return
        from slate.gui.tabs.bid_compare_dialog import CompareDialog
        revisions = self.repo.revisions(chosen[0].bid_group or chosen[0].id)
        if len(revisions) < 2:
            from slate.gui.components.feedback import inform
            inform(self, "Compare revisions", f"{chosen[0].project_code} has only one revision.")
            return
        CompareDialog(self, revisions, self.repo.lines).exec()

    @on_database_error
    def export_pdf(self, *_, path=None):
        chosen = self._selected()
        if len(chosen) != 1:
            return None
        bid = self.repo.get(chosen[0].id)
        if bid is None:
            self.load_data()
            return None
        lines = self.repo.lines(bid.id)
        # An old bid saved with a margin no longer allowed prints as it was saved.
        totals, _priced = bid.totals(lines)
        return export_bid_pdf(self, bid, lines, totals, path=path)

    def export_list(self, *_, path=None):
        """The bids shown (visible rows), amounts as plain numbers with a currency column."""
        from PySide6.QtWidgets import QFileDialog
        from slate.core.domain.bid_export import bid_list_rows
        from slate.core.domain.table_export import default_filename, export_rows
        from slate.gui.components.feedback import toast
        if path is None:
            default = str(Path.home() / "Documents" / default_filename("bids"))
            path, _ = QFileDialog.getSaveFileName(self, "Export bids", default,
                                                  "CSV (*.csv);;Excel workbook (*.xlsx)")
            if not path:
                return None
        headers, rows = bid_list_rows(self.visible_bids(), names=self._names)
        try:
            count = export_rows(path, headers, rows)
        except OSError as exc:
            toast(self, f"Could not write {path}: {exc}", "error")
            return None
        toast(self, f"Exported {count} bid{'s' if count != 1 else ''} to {Path(path).name}.", "success")
        return path

    @on_database_error
    def create_shots(self, *_):
        chosen = self._selected()
        if len(chosen) != 1 or chosen[0].status != DB.WON:
            return
        bid = chosen[0]
        from slate.gui.components.feedback import confirm, inform, warn
        wanted = DB.shots_to_create(self.repo.lines(bid.id))
        if not wanted:
            inform(self, "Create shots", f"{bid.title} names no shots.",
                   "Give lines a shot name (the Shot column of the bid) to create them on the dashboard.")
            return
        if not confirm(self, "Create shots",
                       f"Add the {len(wanted)} shot{'s' if len(wanted) != 1 else ''} {bid.title} names "
                       f"to the {bid.project_code} dashboard, with their bid days?",
                       yes_label="Create shots",
                       informative="Shots already on the dashboard are left exactly as they are."):
            return
        try:
            result = self.repo.create_shots(bid.id, by=self.username)
        except DatabaseUnavailableError:
            raise
        except (DB.BidError, PermissionError) as exc:
            warn(self, "Create shots", str(exc))
            return
        if result["error"]:
            warn(self, "Create shots", f"Not all shots were created: {result['error']}")
        lines = [f"Created {len(result['created'])} shot{'s' if len(result['created']) != 1 else ''}."]
        if result["existing"]:
            lines.append(f"{len(result['existing'])} were already on the dashboard and were left as they were.")
        if result.get("refused"):
            lines.append(f"{len(result['refused'])} not created because of the shot name: "
                         + "; ".join(reason for _name, reason in result["refused"][:3]))
        if result["group_lines"]:
            lines.append(f"{result['group_lines']} line{'s' if result['group_lines'] != 1 else ''} "
                         "without a shot name created nothing.")
        inform(self, "Create shots", lines[0], " ".join(lines[1:]))
        self._selection_changed()

    def open_settings(self, *_):
        from slate.gui.tabs.bid_settings_dialog import BiddingSettingsDialog
        from slate.gui.components.feedback import toast
        dialog = BiddingSettingsDialog(self, username=self.username, can_edit_rates=self.can_edit_rates)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            toast(self, "Bidding settings saved for the studio.", "success")
