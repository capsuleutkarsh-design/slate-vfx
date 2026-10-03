"""
Bids out of Slate: the bid list as CSV/XLSX, and one bid as a document for
the client (HTML here; the Bidding tab prints it to PDF).

There was no way to get a bid out of Slate at all - and bids are sent to
clients. Amounts go out as plain numbers with their currency beside them in
the list (so a spreadsheet can add them up), and formatted in the bid's own
currency in the document (₹ with Indian grouping for rupee bids).

    rows = bid_list_rows(bids, names)        -> (headers, rows) for export_rows
    rows = tracking_rows(bid, tracking)      -> the tracking table, the same way
    html = bid_document_html(bid, lines, totals, studio="UT Studios")
"""

from __future__ import annotations

from datetime import date, timedelta
from html import escape
from typing import Iterable, List, Sequence, Tuple

from . import bidding as DB
from .dates import format_date, parse_date
from .money import format_money

LIST_HEADERS = ["Project", "Project name", "Client", "Rev", "Status", "Currency", "Shots",
                "Artist days", "Cost", DB.WORDS["taxable"], "Tax", "Total", "Created", "Created by",
                "Username"]

# How long a client may take the bid up, printed on the PDF.
# ponytail: a fixed 30 days; make it a bidding setting when a studio asks.
VALID_DAYS = 30


def bid_list_rows(bids: Iterable, names=None) -> Tuple[List[str], List[list]]:
    """
    The bid list as a table of plain values (numbers stay numbers). Created by
    is the person's name, as the table shows it (names: username -> name),
    with the username in a column of its own.
    """
    names = names or {}
    rows = []
    for b in bids:
        rows.append([b.project_code, b.project_name, b.client_name, b.revision,
                     DB.status_label(b.status), b.currency, b.shot_count, b.estimated_days,
                     b.estimated_cost, b.estimated_budget, b.tax_amount, b.total_amount,
                     parse_date(b.created_at), names.get(b.created_by, b.created_by), b.created_by])
    return list(LIST_HEADERS), rows


TRACKING_HEADERS = ["Department", "Bid days", "Planned days", "Delivered days", "Actual days",
                    "Delivered %", "Remaining days", "Plan vs bid (days)", "Bid cost", "Planned cost",
                    "Currency"]


def department_name(key: str) -> str:
    """'Prep / Paint' for 'prep': what a person (and a client) reads."""
    if not key or key == DB.UNASSIGNED:
        return "No department"
    try:
        from .departments import get_department
        dept = get_department(key)
        if dept is not None:
            return dept.name or dept.label or key
    except Exception:
        pass
    return key


def tracking_rows(bid, tracking: DB.Tracking) -> Tuple[List[str], List[list]]:
    """The tracking table as plain numbers with a currency column, and a total row."""
    rows = []
    for d in tracking.departments:
        rows.append([department_name(d.department), d.bid_days, d.planned_days, d.done_days,
                     d.actual_days if tracking.actual_recorded else None, d.burn_percent,
                     d.remaining_days, d.variance_days, d.bid_cost, d.planned_cost, bid.currency])
    rows.append(["Total", tracking.bid_days, tracking.planned_days, tracking.done_days,
                 tracking.actual_days if tracking.actual_recorded else None, None,
                 tracking.bid_days - tracking.done_days, tracking.planned_days - tracking.bid_days,
                 tracking.bid_cost, tracking.planned_cost, bid.currency])
    return list(TRACKING_HEADERS), rows


def bid_document_html(bid, lines: Sequence[DB.BidLine], totals: DB.BidTotals, *,
                      studio: str = "", show_cost: bool = False, today: date = None) -> str:
    """
    The bid as the client should see it: who, what, the lines with their days
    and amounts, then price, discount, tax and total. Cost and margin are
    internal and left out unless show_cost.
    """
    code = bid.currency
    today = today or date.today()
    tax_name = bid.tax_label or DB.tax_label(code)

    def m(value):
        return escape(format_money(value, code))

    title = studio or f"Bid {bid.project_code} v{bid.revision}"
    head = f"""
    <h1 style="margin-bottom:2px">{escape(title)}</h1>
    <p style="color:#555;margin-top:0">Bid {escape(bid.project_code)} v{bid.revision}
       &middot; {escape(format_date(today))}
       &middot; valid until {escape(format_date(today + timedelta(days=VALID_DAYS)))}</p>
    <table cellspacing="0" cellpadding="3">
      <tr><td><b>Project</b></td><td>{escape(bid.project_code)}
          {('&ndash; ' + escape(bid.project_name)) if bid.project_name and bid.project_name != bid.project_code else ''}</td></tr>
      <tr><td><b>Client</b></td><td>{escape(bid.client_name or '-')}</td></tr>
      <tr><td><b>Currency</b></td><td>{escape(code)}</td></tr>
    </table>"""

    body = ["<table width='100%' cellspacing='0' cellpadding='4' border='1' "
            "style='border-collapse:collapse;border-color:#bbb'>",
            "<tr style='background:#eee'><th align='left'>Item</th><th align='left'>Department</th>"
            "<th align='right'>Shots</th><th align='right'>Days / shot</th>"
            "<th align='right'>Artist days</th>"
            + ("<th align='right'>Day rate</th><th align='right'>Cost</th>" if show_cost else "")
            + "</tr>"]
    for line in lines:
        name = line.label or line.shot_name
        if line.shot_name and line.label and line.shot_name not in line.label:
            name = f"{line.label} ({line.shot_name})"
        body.append(
            f"<tr><td>{escape(name)}</td><td>{escape(department_name(line.department))}</td>"
            f"<td align='right'>{line.shot_count}</td>"
            f"<td align='right'>{escape(DB.fmt_days(line.days_per_shot))}</td>"
            f"<td align='right'>{escape(DB.fmt_days(line.days))}</td>"
            + (f"<td align='right'>{m(line.day_rate)}</td><td align='right'>{m(line.cost)}</td>"
               if show_cost else "")
            + "</tr>")
    body.append("</table>")

    sums = [("Artist days", escape(DB.fmt_days(totals.days)))]
    if show_cost:
        sums += [("Cost", m(totals.cost)),
                 (f"Margin ({escape(DB.fmt_percent(totals.margin_percent))})", m(totals.margin_amount))]
    sums.append(("Price", m(totals.price)))
    if totals.discount_amount:
        sums.append((f"Discount ({escape(DB.fmt_percent(totals.discount_percent))})",
                     "&minus;" + m(totals.discount_amount)))
        sums.append(("Subtotal", m(totals.taxable)))
    if totals.tax_amount or totals.tax_percent:
        sums.append((f"{escape(tax_name)} ({escape(DB.fmt_percent(totals.tax_percent))})",
                     m(totals.tax_amount)))
    sums.append(("<b>Total</b>", f"<b>{m(totals.total)}</b>"))
    figures = ["<table align='right' cellspacing='0' cellpadding='3'>"]
    figures += [f"<tr><td>{label}</td><td align='right'>{value}</td></tr>" for label, value in sums]
    figures.append("</table>")

    notes = f"<p><b>Notes</b><br>{escape(bid.notes).replace(chr(10), '<br>')}</p>" if bid.notes else ""
    return ("<html><body style='font-family:Segoe UI, Arial, sans-serif;font-size:10pt'>"
            + head + "<br>" + "".join(body) + "<br>" + "".join(figures)
            + "<br clear='all'>" + notes + "</body></html>")
