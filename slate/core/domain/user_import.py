"""
Adding many people at once from a list, and writing the list back out.

The list needs a Username column and usually a Display Name; it may also carry
Department, Joined, Reports To, Location, Employment and Role - the columns
leave and attendance need, which used to be ignored, so a hundred imported
people accrued leave from 1 January and had nobody to approve it. Everybody
gets the first password chosen at import time and must choose their own at
first sign-in; a person with no Role column gets the role chosen for all.

An import adds. Updating people who already exist is opt-in ("Update existing
people"), shown as a per-person preview first, and only ever changes profile
fields - never a password or a role.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from slate.core.domain.table_export import neutralise

USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,39}$")
USERNAME_RULE = ("2 to 40 characters: letters, numbers, dot, dash or underscore, "
                 "starting with a letter or number")

_USERNAME_HEADERS = {"username", "user name", "user", "login", "login id", "user id", "userid",
                     "employee id", "emp id", "id"}
_NAME_HEADERS = {"display name", "displayname", "name", "full name", "employee name"}

_EXTRA_HEADERS = {
    "job_title": {"department", "dept", "job title", "designation"},
    "joined_on": {"joined", "joined on", "joining date", "date of joining", "doj", "start date"},
    "reports_to": {"reports to", "reports_to", "manager", "reporting manager", "supervisor"},
    "location": {"location", "office", "city"},
    "employment": {"employment", "employment type"},
    "role": {"role", "roles"},
}
# Column titles that mark a header row even with no username column in it.
_KNOWN_HEADERS = (_NAME_HEADERS | {"email", "e-mail", "mail", "phone", "mobile"}
                  | set().union(*_EXTRA_HEADERS.values()))

TEMPLATE_HEADERS = ("Username", "Display Name", "Department", "Joined", "Reports To",
                    "Location", "Employment", "Role")


@dataclass
class Row:
    line: int                   # row number in the file, for the report
    username: str
    display_name: str
    status: str = "new"         # new | update | exists | duplicate | invalid (+ created/updated/failed)
    reason: str = ""
    fields: dict = field(default_factory=dict)    # the optional columns, checked
    changes: dict = field(default_factory=dict)   # update mode: {field: (old, new)}


@dataclass
class ImportPlan:
    rows: List[Row] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)       # optional columns found
    needs_column: List[str] = field(default_factory=list)  # a header with no username column

    @property
    def to_create(self) -> List[Row]:
        return [r for r in self.rows if r.status == "new"]

    @property
    def to_update(self) -> List[Row]:
        return [r for r in self.rows if r.status == "update"]

    @property
    def skipped(self) -> List[Row]:
        return [r for r in self.rows if r.status not in ("new", "update")]


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


def _extra_columns(header: List[str]) -> dict:
    """{field: column} for the optional columns this header names."""
    lowered = [h.strip().lower() for h in header]
    found = {}
    for field, names in _EXTRA_HEADERS.items():
        for i, h in enumerate(lowered):
            if h in names:
                found[field] = i
                break
    return found


def _looks_like_header(row: List[str]) -> bool:
    """
    A first row of column titles with no username column among them -
    'Name,Email'. Planning that as data imported the header as a user called
    'name' and failed every real row.
    """
    cells = [c.strip().lower() for c in row if c and c.strip()]
    if not cells:
        return False
    return any(c in _KNOWN_HEADERS for c in cells)


def normalise_username(text: str) -> str:
    return str(text or "").strip().lower()


def _parse_joined(text):
    from slate.core.domain.dates import parse_date
    return parse_date(text)


def plan_import(path, existing_usernames, *, user_manager=None, allowed_roles=None,
                update_existing: bool = False, username_column: int = None) -> ImportPlan:
    """
    Read the list and decide, row by row, what an import would do. Changes nothing.

    Optional columns are read when the header names them: Department, Joined,
    Reports To, Location, Employment and Role (the export's own columns, so an
    exported list can be edited and imported back). Each is checked: the date
    must read as a date, Reports To must be somebody in Slate or in the file,
    Employment one of Staff / Freelance / Contract, Role one the importer may
    give (allowed_roles).

    update_existing: people already in Slate get a row with what would change
    in their profile (never their password or roles); off, they are skipped.

    A first row of titles with no username column comes back with
    plan.needs_column set; ask which column holds the username and plan again
    with username_column.
    """
    cells = read_cells(path)
    existing = {normalise_username(u) for u in existing_usernames}
    records = {}
    if user_manager is not None:
        try:
            records = {normalise_username(u): d for u, d in (user_manager.get_all_users() or {}).items()}
        except Exception:
            records = {}
    allowed = {str(r).strip().lower(): str(r) for r in allowed_roles} if allowed_roles is not None else None

    start, user_col, name_col, extra = 0, 0, 1, {}
    header: List[str] = []
    for index, row in enumerate(cells):
        if not any(row):
            continue
        found = _columns(row)
        if username_column is not None:
            header = list(row)
            start, user_col = index + 1, int(username_column)
            lowered = [h.strip().lower() for h in row]
            name_col = next((i for i, h in enumerate(lowered) if h in _NAME_HEADERS and i != user_col), None)
            extra = _extra_columns(row)
        elif found:
            header = list(row)
            start, (user_col, name_col) = index + 1, found
            extra = _extra_columns(row)
        elif _looks_like_header(row):
            plan = ImportPlan()
            plan.needs_column = [c.strip() for c in row]
            return plan
        else:
            start = index              # no header row: column A and column B
        break

    def cell(row, col):
        return row[col].strip() if col is not None and col < len(row) else ""

    in_file = set()
    for index in range(start, len(cells)):
        uname = normalise_username(cell(cells[index], user_col))
        if uname:
            in_file.add(uname)

    plan = ImportPlan(columns=sorted(extra))
    seen = set()
    file_roles = {}
    from slate.core.domain.onboarding_service import EMPLOYMENT_TYPES, employment_value
    for index in range(start, len(cells)):
        row = cells[index]
        if not any(row):
            continue
        raw_user = cell(row, user_col)
        name = cell(row, name_col)
        username = normalise_username(raw_user)
        entry = Row(line=index + 1, username=username or raw_user, display_name=name or username)

        problems = []
        fields = {}
        if "job_title" in extra and cell(row, extra["job_title"]):
            fields["job_title"] = cell(row, extra["job_title"])
        if "location" in extra and cell(row, extra["location"]):
            fields["location"] = cell(row, extra["location"])
        if "joined_on" in extra and cell(row, extra["joined_on"]):
            joined = _parse_joined(cell(row, extra["joined_on"]))
            if joined is None:
                problems.append("Joined %r is not a date" % cell(row, extra["joined_on"]))
            else:
                fields["joined_on"] = joined.isoformat()
        if "employment" in extra and cell(row, extra["employment"]):
            value = employment_value(cell(row, extra["employment"]))
            if value not in EMPLOYMENT_TYPES:
                problems.append("Employment must be Staff, Freelance or Contract")
            else:
                fields["employment"] = value
        if "reports_to" in extra and cell(row, extra["reports_to"]):
            boss = normalise_username(cell(row, extra["reports_to"]))
            if boss == username:
                problems.append("Cannot report to themselves")
            elif boss not in existing and boss not in in_file:
                problems.append("Reports to %r is nobody in Slate or in this file" % boss)
            elif boss in existing and user_manager is not None and \
                    boss != str(records.get(username, {}).get("reports_to") or "").lower():
                # The rule Add and Edit user use (an approver, no loops).
                why = user_manager.reports_to_problem(username, boss)
                if why:
                    problems.append("Reports to: " + why)
                else:
                    fields["reports_to"] = boss
            else:
                fields["reports_to"] = boss
        if "role" in extra and cell(row, extra["role"]):
            wanted = [r.strip() for r in cell(row, extra["role"]).replace(",", "|").split("|") if r.strip()]
            refused = [r for r in wanted if allowed is not None and r.lower() not in allowed]
            if refused:
                problems.append("Role %s cannot be given here" % ", ".join(refused))
            else:
                fields["roles"] = [allowed[r.lower()] if allowed else r for r in wanted]
        entry.fields = fields
        file_roles[username] = fields.get("roles")

        if not username:
            entry.status, entry.reason = "invalid", "No username"
        elif not USERNAME_PATTERN.match(username):
            entry.status, entry.reason = "invalid", "Username must be " + USERNAME_RULE
        elif username in seen:
            entry.status, entry.reason = "duplicate", "Listed twice in the file"
        elif problems:
            entry.status, entry.reason = "invalid", "; ".join(problems)
        elif username in existing:
            if not update_existing:
                entry.status, entry.reason = "exists", "Already in Slate - left unchanged"
            else:
                changes = _profile_changes(records.get(username, {}), name, fields)
                if changes:
                    entry.status = "update"
                    entry.changes = changes
                    entry.reason = "; ".join("%s: %s -> %s" % (
                        FIELD_LABELS.get(k, k), field_text(k, old) or "-", field_text(k, new))
                        for k, (old, new) in changes.items())
                    if "roles" in fields:
                        entry.reason += " (roles are not changed by an import)"
                else:
                    entry.status, entry.reason = "exists", "Already in Slate - nothing to change"
        seen.add(username)
        plan.rows.append(entry)
    # A manager who is new in this file is checked once the file's roles are
    # known. With no Role column they get the role chosen for everybody, so
    # create_user checks them (reports_to_problem) when the import runs.
    if user_manager is not None:
        for entry in plan.rows:
            boss = entry.fields.get("reports_to")
            roles = file_roles.get(boss) if boss and boss not in existing else None
            if entry.status in ("new", "update") and roles and not user_manager.can_approve(roles):
                entry.status = "invalid"
                entry.reason = "Reports to: %s (%s) cannot approve leave" % (boss, ", ".join(roles))
    return plan


FIELD_LABELS = {"display_name": "Name", "job_title": "Department", "joined_on": "Joined",
                "employment": "Employment", "reports_to": "Reports to", "location": "Location"}


def field_text(key, value) -> str:
    """A planned value as the preview shows it: dates as '5 Oct 2026', like every screen."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if key == "joined_on" and value:
        from slate.core.domain.dates import format_date
        return format_date(value) or str(value)
    return str(value or "")


def _profile_changes(record: dict, name: str, fields: dict) -> dict:
    """{field: (old, new)} for the profile fields a row would change."""
    wanted = dict(fields)
    wanted.pop("roles", None)
    if name:
        wanted["display_name"] = name
    out = {}
    for key, new in wanted.items():
        old = record.get(key)
        old_text = str(old or "")[:10] if key == "joined_on" else str(old or "")
        if old_text.strip().lower() != str(new).strip().lower():
            out[key] = (old_text, new)
    return out


# ------------------------------------------------------------------ applying

def apply_import(user_manager, plan: ImportPlan, role: str, first_password: str,
                 progress=None) -> ImportPlan:
    """
    Create everybody marked new, with the file's own columns as well; apply
    the profile changes of rows marked update. A new person gets the file's
    Role if it has one, otherwise the role chosen for everybody, and the first
    password, which they must change at first sign-in. The rows come back
    marked created / updated / failed, the reason saying what was set.

    progress(done, total) is called after each row (the import runs off the
    screen's thread; bcrypt takes a moment per person).
    """
    needs_role = any(not r.fields.get("roles") for r in plan.to_create)
    if needs_role and not role:
        raise ValueError("Choose a role for the imported people.")
    # Trimmed and checked the way every password is (UserManager.clean_password):
    # six spaces used to pass this check and lock every imported person out.
    clean = getattr(user_manager, "clean_password", lambda value: str(value or "").strip())
    first_password = clean(first_password)
    if plan.to_create and not first_password:
        raise ValueError("The first password cannot be empty or only spaces.")
    if plan.to_create and len(first_password) < user_manager.MIN_PASSWORD_LENGTH:
        raise ValueError(f"The first password needs at least {user_manager.MIN_PASSWORD_LENGTH} characters.")

    # Checked again now, in case someone was added while the preview was open.
    existing = {normalise_username(u) for u in (user_manager.get_all_users() or {})}
    work = plan.to_create + plan.to_update
    total = len(work)
    # Managers first, so somebody can report to a person created in the same file.
    work.sort(key=lambda r: 0 if any(o.fields.get("reports_to") == r.username for o in work) else 1)
    for done, row in enumerate(work, 1):
        try:
            if row.status == "update":
                changes = {k: new for k, (old, new) in row.changes.items()}
                ok = user_manager.update_user(row.username, **changes)
                row.status, row.reason = ("updated", row.reason) if ok else ("failed", "Could not be saved")
            elif row.username in existing:
                row.status, row.reason = "exists", "Already in Slate - left unchanged"
            else:
                fields = dict(row.fields)
                roles = fields.pop("roles", None) or [role]
                ok, message = user_manager.create_user(
                    row.username, first_password, roles, row.display_name,
                    fields.pop("job_title", ""), **fields)
                if ok:
                    user_manager.set_must_change_password(row.username, True)
                    set_ = [FIELD_LABELS.get(k, k) for k in row.fields if k not in ("roles",)]
                    row.status = "created"
                    row.reason = ("Set: " + ", ".join(set_)) if set_ else ""
                    existing.add(row.username)
                else:
                    row.status, row.reason = "failed", message
        except PermissionError as refused:
            row.status, row.reason = "failed", str(refused)
        except Exception as exc:                 # noqa: BLE001 - one bad row must not stop the rest
            row.status, row.reason = "failed", "Could not be saved: %s" % exc
        if progress is not None:
            progress(done, total)
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
    for letter, width in zip("ABCDEFGH", (24, 34, 18, 14, 18, 14, 14, 16)):
        sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = "A2"

    notes = book.create_sheet("How to fill")
    for line in (
        "One person per row on the People sheet.",
        "Username: what they sign in with - " + USERNAME_RULE + ". Stored in lower case.",
        "Display Name: how their name is shown in Slate. Optional.",
        "Department, Joined (a date), Reports To (a username), Location, Employment",
        "(Staff, Freelance or Contract) and Role are optional; leave a cell empty to skip it.",
        "Everybody gets the first password you choose when importing and must choose their own",
        "the first time they sign in. A person with no Role gets the role you choose for all.",
        "People already in Slate are skipped unless you tick 'Update existing people', which",
        "changes only their profile - never a password or a role.",
    ):
        notes.append([line])
    notes.column_dimensions["A"].width = 110
    book.save(path)
    return path


def export_users_csv(user_manager, path) -> Path:
    """
    Everybody, with the details Users & Roles shows. Never includes passwords.
    Status and Last Day say who has left; an import does not read them.
    """
    path = Path(path)
    from slate.core.domain.user_manager import UserManager
    columns = ("Username", "Display Name", "Department", "Roles", "Joined", "Employment",
               "Reports To", "Location", "Status", "Last Day")
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for username, data in sorted((user_manager.get_all_users() or {}).items()):
            roles = data.get("roles") or []
            if isinstance(roles, str):
                roles = [roles]
            joined = data.get("joined_on")
            last = data.get("last_day")
            status, _tone = UserManager.account_status(dict(data, username=username))
            # Neutralised: a display name like '=HYPERLINK(...)' is a formula
            # Excel would run when HR opened the file.
            writer.writerow([neutralise(v) for v in (
                username, data.get("display_name", ""), data.get("job_title", ""),
                " | ".join(str(r) for r in roles if r),
                str(joined)[:10] if joined else "", data.get("employment") or "",
                data.get("reports_to") or "", data.get("location") or "",
                status, str(last)[:10] if last else "",
            )])
    return path


def write_report_csv(plan: ImportPlan, path) -> Path:
    path = Path(path)
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(("Row", "Username", "Display Name", "Result", "Reason"))
        for row in plan.rows:
            writer.writerow([neutralise(v) for v in (row.line, row.username, row.display_name,
                                                     row.status, row.reason)])
    return path


def summary(plan: ImportPlan) -> str:
    counts = {}
    for row in plan.rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    labels = (("created", "created"), ("updated", "updated"), ("new", "to create"),
              ("update", "to update"), ("exists", "already in Slate"),
              ("duplicate", "listed twice"), ("invalid", "not valid"), ("failed", "failed"))
    parts = [f"{counts[key]} {text}" for key, text in labels if counts.get(key)]
    return ", ".join(parts) or "The file has no people in it."
