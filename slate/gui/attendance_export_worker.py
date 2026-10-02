"""
The month's attendance as an Excel workbook (what payroll reads).

Built with the same rules as the screen (slate.core.domain.attendance_rules),
so the sheet and the grid agree: hours across midnight and across a second
session, today's open session up to the time of the export (said in a note),
late only on days somebody was expected in, absences, missing punches and
approved leave. It used to work hours out as out - in, so a 20:00-05:30
shift was -14.5 hours, and Sunday arrivals counted as late.

Each day is a code and the hours: P present, L late, S short day, A absent,
LV leave, H holiday, WO weekly off, M missing punch, W still working.
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
    rules.MISSING_OUT: "M", rules.MISSING_IN: "M", rules.AUTO: "P", rules.WORKING: "W",
    rules.FUTURE: "", rules.NONE: "",
}

# Plain colours for the file itself (Excel, not the app's theme).
FILL = {
    "header": "2E5090", "off": "E7E6E6", "late": "FFF3E0", "absent": "FFEBEE",
    "leave": "EDE7F6", "missing": "FFCDD2", "total": "E3F2FD",
}


def summarise(row, user_log, year, month, leave=None, now=None, today=None):
    """
    One person's month: {"days": [(code, hours, state, entry)], totals...}.
    Pure - the tests and the workbook use it alike.
    """
    now = now or datetime.now()
    today = today or now.date()
    holidays = row.get("holidays") or set()
    leave = leave or {}
    days = calendar.monthrange(year, month)[1]
    out = {"days": [], "present": 0, "late": 0, "absent": 0, "leave": 0.0,
           "missing": 0, "hours": 0.0, "wfh": 0, "open_today": False}
    for d in range(1, days + 1):
        day = date(year, month, d)
        entry = user_log.get(f"{d:02d}", {})
        day_leave = leave.get(day)
        state = rules.day_state(entry, day, today, holidays, day_leave)
        hours = rules.day_hours(entry, now, open_counts=(day == today))
        sessions = rules.sessions_of(entry)
        if sessions:
            out["present"] += 1
            if entry.get("wfh"):
                out["wfh"] += 1
            if day == today and sessions[-1][1] is None:
                out["open_today"] = True
        if state == rules.LATE:
            out["late"] += 1
        if state == rules.ABSENT:
            out["absent"] += 1
        if state in (rules.MISSING_OUT, rules.MISSING_IN):
            out["missing"] += 1
        if day_leave:
            out["leave"] += float(day_leave.get("days") or 1.0)
        out["hours"] += hours
        out["days"].append((CODES.get(state, ""), hours, state, entry))
    out["hours"] = round(out["hours"], 2)
    return out


def build_workbook(path, year, month, rows, data, leave=None, now=None):
    """Write the workbook. rows: [{"username", "name", "holidays"}] in sheet order."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from slate.core.domain.table_export import neutralise

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
    studio_off = [not lp.is_working_day(date(year, month, d)) for d in range(1, days + 1)]

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
        ws.cell(row=r, column=1, value=neutralise(row.get("name") or uid))
        ws.cell(row=r, column=2, value=neutralise(uid))
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
                  "WO weekly off, M missing punch, W still working. The number is hours.")
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

    def __init__(self, path, year, month, rows, data, leave=None, now=None):
        super().__init__()
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
                           self.leave, self.now)
            self.finished_export.emit(True, self.path)
        except Exception as exc:
            self.finished_export.emit(False, str(exc))

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()
