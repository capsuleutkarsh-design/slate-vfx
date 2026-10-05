"""
The month's attendance as an Excel workbook (what payroll reads).

Built with the same rules as the screen (slate.core.domain.attendance_rules),
so the sheet and the grid agree: hours across midnight and across a second
session, today's open session up to the time of the export (said in a note),
late only on days somebody was expected in, absences, missing punches and
approved leave. It used to work hours out as out - in, so a 20:00-05:30
shift was -14.5 hours, and Sunday arrivals counted as late.

Each day is a code and the hours: P present, L late, S short day, A absent,
LV leave, H holiday, WO weekly off, M missing punch, W still working, AU
closed by the automatic punch-out (the hours end at the cutoff, not at a
punch); '*' after a code marks a day HR corrected.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime

from PySide6.QtCore import QThread, Signal

from slate.core.domain import attendance_rules as rules
from slate.core.domain import leave_policy as lp


CODES = {
    rules.PRESENT: "P", rules.WORKED_OFF: "P", rules.LATE: "L", rules.SHORT: "S",
    rules.ABSENT: "A", rules.LEAVE: "LV", rules.HOLIDAY: "H", rules.WEEKLY_OFF: "WO",
    rules.MISSING_OUT: "M", rules.MISSING_IN: "M", rules.AUTO: "AU", rules.WORKING: "W",
    rules.FUTURE: "", rules.NONE: "",
}

# Plain colours for the file itself (Excel, not the app's theme).
FILL = {
    "header": "2E5090", "off": "E7E6E6", "late": "FFF3E0", "absent": "FFEBEE",
    "leave": "EDE7F6", "missing": "FFCDD2", "total": "E3F2FD", "auto": "E1F5FE",
}


def summarise(row, user_log, year, month, leave=None, now=None, today=None):
    """
    One person's month: {"days": [(code, hours, state, entry)], totals...}.
    Pure - the tests and the workbook use it alike. The counting is
    rules.month_summary, the one the grid uses; row["expected"] is the
    person's (first, last) expected day, so nobody is absent on the sheet
    before they joined or after they left.
    """
    summary = rules.month_summary(user_log, year, month, row.get("holidays") or set(), leave,
                                  row.get("expected"), now, today)
    summary["days"] = [(day_code(d["state"], d["entry"]), d["hours"], d["state"], d["entry"])
                       for d in summary["days"]]
    return summary


def day_code(state, entry) -> str:
    """The sheet's code for a day: an auto punch-out is AU, a corrected day ends in '*'."""
    code = CODES.get(state, "")
    entry = entry or {}
    if code and (entry.get("corrected") or entry.get("edited_by")):
        code += "*"
    return code


def build_workbook(path, year, month, rows, data, leave=None, now=None, studio_holidays=None):
    """
    Write the workbook. rows: [{"username", "name", "holidays", "expected"}] in
    sheet order. studio_holidays: the holidays for everybody ('All'), shaded in
    the header as the grid does (only Sundays were).
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from slate.core.domain.table_export import put

    now = now or datetime.now()
    leave = leave or {}
    days = calendar.monthrange(year, month)[1]
    wb = Workbook()
    ws = wb.active
    ws.title = f"{calendar.month_name[month]} {year}"

    totals = ["Present", "Late", "Absent", "Leave", "Missing", "Hours", "WFH"]
    headers = ["Name", "User ID"] + totals + [
        "%02d %s" % (d, date(year, month, d).strftime("%a")) for d in range(1, days + 1)]

    def fill(colour):
        return PatternFill(start_color=colour, end_color=colour, fill_type="solid")

    thin = Side(style="thin", color="D0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    centre = Alignment(horizontal="center", vertical="center")
    first_day_col = 3 + len(totals)
    studio_off = [not lp.is_working_day(date(year, month, d), studio_holidays)
                  for d in range(1, days + 1)]

    for c, text in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=text)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill(FILL["header"])
        cell.alignment = centre
        cell.border = border

    open_today = False
    r = 2
    for row in rows:
        uid = row["username"]
        summary = summarise(row, data.get(uid.lower(), {}), year, month,
                            leave.get(uid.lower(), {}), now)
        open_today = open_today or summary["open_today"]
        # Text a person typed could be a formula; Excel would run it.
        put(ws.cell(row=r, column=1), row.get("name") or uid)
        put(ws.cell(row=r, column=2), uid)
        for i, key in enumerate(("present", "late", "absent", "leave", "missing", "hours", "wfh")):
            cell = ws.cell(row=r, column=3 + i, value=summary[key])
            cell.fill = fill(FILL["total"])
        for d, (code, hours, state, _entry) in enumerate(summary["days"], 1):
            text = code
            if hours:
                text = ("%s %.1f" % (code, hours)).strip()
            cell = ws.cell(row=r, column=first_day_col + d - 1, value=text)
            if state in (rules.HOLIDAY, rules.WEEKLY_OFF):
                cell.fill = fill(FILL["off"])
            elif state == rules.LATE:
                cell.fill = fill(FILL["late"])
            elif state == rules.AUTO:
                cell.fill = fill(FILL["auto"])
            elif state == rules.ABSENT:
                cell.fill = fill(FILL["absent"])
            elif state == rules.LEAVE:
                cell.fill = fill(FILL["leave"])
            elif state in (rules.MISSING_OUT, rules.MISSING_IN):
                cell.fill = fill(FILL["missing"])
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=r, column=c)
            cell.border = border
            if c > 2:
                cell.alignment = centre
        r += 1

    # Non-working days shaded in the header row as well.
    for d, off in enumerate(studio_off, 1):
        if off:
            ws.cell(row=1, column=first_day_col + d - 1).fill = fill("7F7F7F")

    notes = r + 1
    ws.cell(row=notes, column=1,
            value="Codes: P present, L late, S short day, A absent, LV leave, H holiday, "
                  "WO weekly off, M missing punch, W still working, AU closed by the automatic "
                  "punch-out (hours end at the cutoff), * corrected by HR. The number is hours.")
    if open_today:
        ws.cell(row=notes + 1, column=1,
                value="Includes today's open sessions up to %s." % now.strftime("%H:%M"))

    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 16
    for c in range(3, first_day_col):
        ws.column_dimensions[get_column_letter(c)].width = 10
    for c in range(first_day_col, len(headers) + 1):
        ws.column_dimensions[get_column_letter(c)].width = 9
    ws.freeze_panes = ws.cell(row=2, column=3)
    wb.save(path)
    return path


class ExcelExportWorker(QThread):
    """Background worker for openpyxl Excel export to prevent UI freezes."""

    finished_export = Signal(bool, str)  # success, saved path or the reason

    def __init__(self, path, year, month, rows, data, leave=None, now=None, studio_holidays=None):
        super().__init__()
        self.studio_holidays = studio_holidays
        self.path = path
        self.year = year
        self.month = month
        self.rows = rows
        self.data = data
        self.leave = leave or {}
        self.now = now

    def run(self):
        try:
            build_workbook(self.path, self.year, self.month, self.rows, self.data,
                           self.leave, self.now, self.studio_holidays)
            self.finished_export.emit(True, self.path)
        except Exception as exc:
            self.finished_export.emit(False, str(exc))

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()
