"""
Adding many people at once from a list, and writing the list back out.

The list is two columns - Username and Display Name - in Excel (.xlsx) or CSV.
Everybody in it gets the one role and the one first password chosen at import
time, and must choose their own password at first sign-in. Everything else
(department, joining date, reports-to, ...) is filled in afterwards, person by
person, on Users & Roles.

An import only ever adds. Somebody who already exists is skipped and reported,
never changed - a list from last year must not be able to rename anybody.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,39}$")
USERNAME_RULE = ("2 to 40 characters: letters, numbers, dot, dash or underscore, "
                 "starting with a letter or number")

_USERNAME_HEADERS = {"username", "user name", "user", "login", "login id", "user id", "userid",
                     "employee id", "emp id", "id"}
_NAME_HEADERS = {"display name", "displayname", "name", "full name", "employee name"}

TEMPLATE_HEADERS = ("Username", "Display Name")


@dataclass
class Row:
    line: int                   # row number in the file, for the report
    username: str
    display_name: str
    status: str = "new"         # new | exists | duplicate | invalid
    reason: str = ""


@dataclass
class ImportPlan:
    rows: List[Row] = field(default_factory=list)

    @property
    def to_create(self) -> List[Row]:
        return [r for r in self.rows if r.status == "new"]

    @property
    def skipped(self) -> List[Row]:
        return [r for r in self.rows if r.status != "new"]


# ------------------------------------------------------------------- reading

def _cells_from_xlsx(path: Path) -> List[List[str]]:
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = book.worksheets[0]
        rows = []
        for values in sheet.iter_rows(values_only=True):
            rows.append(["" if v is None else str(v).strip() for v in values])
        return rows
    finally:
        book.close()


def _cells_from_csv(path: Path) -> List[List[str]]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):     # Excel saves "CSV" in either
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [[c.strip() for c in row] for row in csv.reader(text.splitlines(), dialect)]


def read_cells(path) -> List[List[str]]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return _cells_from_xlsx(path)
    if suffix in (".csv", ".txt"):
        return _cells_from_csv(path)
    raise ValueError("Use an Excel file (.xlsx) or a CSV file (.csv).")


def _columns(header: List[str]):
    """(username column, name column) if this row is a header, else None."""
    lowered = [h.strip().lower() for h in header]
    user_col = next((i for i, h in enumerate(lowered) if h in _USERNAME_HEADERS), None)
    if user_col is None:
        return None
    name_col = next((i for i, h in enumerate(lowered) if h in _NAME_HEADERS and i != user_col), None)
    return user_col, name_col


def normalise_username(text: str) -> str:
    return str(text or "").strip().lower()


def plan_import(path, existing_usernames) -> ImportPlan:
    """Read the list and decide, row by row, what an import would do. Changes nothing."""
    cells = read_cells(path)
    existing = {normalise_username(u) for u in existing_usernames}

    start, user_col, name_col = 0, 0, 1
    for index, row in enumerate(cells):
        if not any(row):
            continue
        found = _columns(row)
        if found:
            start, (user_col, name_col) = index + 1, found
        else:
            start = index              # no header row: column A and column B
        break

    plan = ImportPlan()
    seen = set()
    for index in range(start, len(cells)):
        row = cells[index]
        if not any(row):
            continue
        raw_user = row[user_col] if user_col < len(row) else ""
        name = row[name_col].strip() if name_col is not None and name_col < len(row) else ""
        username = normalise_username(raw_user)
        entry = Row(line=index + 1, username=username or raw_user, display_name=name or username)
        if not username:
            entry.status, entry.reason = "invalid", "No username"
        elif not USERNAME_PATTERN.match(username):
            entry.status, entry.reason = "invalid", "Username must be " + USERNAME_RULE
        elif username in existing:
            entry.status, entry.reason = "exists", "Already in Slate - left unchanged"
        elif username in seen:
            entry.status, entry.reason = "duplicate", "Listed twice in the file"
        seen.add(username)
        plan.rows.append(entry)
    return plan


# ------------------------------------------------------------------ applying

def apply_import(user_manager, plan: ImportPlan, role: str, first_password: str) -> ImportPlan:
    """
    Create everybody marked new. Each gets the role and first password, and
    must choose a new password at first sign-in. The rows come back marked
    created or failed.
    """
    if not role:
        raise ValueError("Choose a role for the imported people.")
    if len(first_password or "") < user_manager.MIN_PASSWORD_LENGTH:
        raise ValueError(f"The first password needs at least {user_manager.MIN_PASSWORD_LENGTH} characters.")

    # Checked again now, in case someone was added while the preview was open.
    existing = {normalise_username(u) for u in (user_manager.get_all_users() or {})}
    for row in plan.to_create:
        if row.username in existing:
            row.status, row.reason = "exists", "Already in Slate - left unchanged"
            continue
        ok = user_manager.add_user(row.username, first_password, [role], row.display_name, "")
        if ok:
            user_manager.set_must_change_password(row.username, True)
            row.status, row.reason = "created", ""
            existing.add(row.username)
        else:
            row.status, row.reason = "failed", "Could not be saved"
    return plan


# ------------------------------------------------------------ files to hand out

def write_template(path) -> Path:
    """An Excel file with the two columns, ready to fill in."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    path = Path(path)
    book = Workbook()
    sheet = book.active
    sheet.title = "People"
    sheet.append(list(TEMPLATE_HEADERS))
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F6F80")
    sheet.column_dimensions["A"].width = 24
    sheet.column_dimensions["B"].width = 34
    sheet.freeze_panes = "A2"

    notes = book.create_sheet("How to fill")
    for line in (
        "One person per row on the People sheet.",
        "Username: what they sign in with - " + USERNAME_RULE + ". Stored in lower case.",
        "Display Name: how their name is shown in Slate. Optional.",
        "Everybody in the file gets the role and first password you choose when importing,",
        "and must choose their own password the first time they sign in.",
        "People already in Slate are skipped, never changed.",
        "Department, joining date, reports-to and the rest are filled in afterwards on Users & Roles.",
    ):
        notes.append([line])
    notes.column_dimensions["A"].width = 110
    book.save(path)
    return path


def export_users_csv(user_manager, path) -> Path:
    """Everybody, with the details Users & Roles shows. Never includes passwords."""
    path = Path(path)
    columns = ("Username", "Display Name", "Department", "Roles", "Joined", "Employment",
               "Reports To", "Location")
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for username, data in sorted((user_manager.get_all_users() or {}).items()):
            roles = data.get("roles") or []
            if isinstance(roles, str):
                roles = [roles]
            joined = data.get("joined_on")
            writer.writerow([
                username, data.get("display_name", ""), data.get("job_title", ""),
                " | ".join(str(r) for r in roles if r),
                str(joined)[:10] if joined else "", data.get("employment") or "",
                data.get("reports_to") or "", data.get("location") or "",
            ])
    return path


def write_report_csv(plan: ImportPlan, path) -> Path:
    path = Path(path)
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(("Row", "Username", "Display Name", "Result", "Reason"))
        for row in plan.rows:
            writer.writerow((row.line, row.username, row.display_name, row.status, row.reason))
    return path


def summary(plan: ImportPlan) -> str:
    counts = {}
    for row in plan.rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    labels = (("created", "created"), ("new", "to create"), ("exists", "already in Slate"),
              ("duplicate", "listed twice"), ("invalid", "not valid"), ("failed", "failed"))
    parts = [f"{counts[key]} {text}" for key, text in labels if counts.get(key)]
    return ", ".join(parts) or "The file has no people in it."
