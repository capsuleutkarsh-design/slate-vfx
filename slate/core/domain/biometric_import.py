"""
Attendance from a biometric machine, whatever the machine.

Every device exports the same three facts per line - who, when, and
sometimes which way - and disagrees with every other device about everything
else: the column names, their order, whether the date and the time share a
cell, whether the date is written day-first, what separates the fields.

So this does not know any brand. It reads the file, works out which column
is which from the header names and from the shape of the values, and lets a
person correct the guess once. The corrected mapping is saved in the studio's
shared folder against the file's header, so the next export from the same
machine imports in one click, and a second machine gets a profile of its own.

Then it reduces punches to days the way the rest of the software already
thinks about them - earliest punch in, latest punch out, one row per person
per day - and writes each day through the attendance service, marked as
coming from the machine, so importing the same file twice changes nothing.

Standard library only, apart from openpyxl for .xlsx exports.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# What a column can be.
ROLES = ("ignore", "code", "name", "date", "time", "datetime", "direction")
ROLE_LABELS = {
    "ignore": "Ignore",
    "code": "Employee code",
    "name": "Name",
    "date": "Date",
    "time": "Time",
    "datetime": "Date + time",
    "direction": "In / Out",
}

# Header words that give a column away. Lower-case, matched as substrings.
_HEADER_HINTS = {
    "code": ("emp", "code", "ac-no", "acno", "ac no", "enroll", "badge", "userid",
             "user id", "pin", "empid", "staff", "id"),
    "name": ("name",),
    "date": ("date",),
    "time": ("time",),
    "datetime": ("datetime", "date time", "timestamp", "punch time", "log time",
                 "record time", "clock"),
    "direction": ("state", "status", "in/out", "inout", "in out", "direction",
                  "punch", "type", "mode", "io", "event", "verify"),
}

_IN_WORDS = {"in", "i", "c/in", "check in", "checkin", "check-in", "entry", "enter",
             "clock in", "clockin", "0", "login", "start"}
_OUT_WORDS = {"out", "o", "c/out", "check out", "checkout", "check-out", "exit",
              "leave", "clock out", "clockout", "1", "logout", "end"}

_DATE_FORMATS_DAYFIRST = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y",
                          "%d %b %Y", "%d-%b-%Y", "%d-%b-%y", "%d %B %Y")
_DATE_FORMATS_MONTHFIRST = ("%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y", "%m/%d/%y", "%m-%d-%y",
                            "%b %d %Y", "%b %d, %Y", "%B %d, %Y")
_DATE_FORMATS_ISO = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d")
_TIME_FORMATS = ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p", "%I:%M:%S%p", "%I:%M%p", "%H%M")


# ----------------------------------------------------------------- reading

def read_table(path) -> tuple[list[str], list[list[str]]]:
    """
    The file as a header row and data rows of strings.

    CSV and text files are sniffed for their separator and tried in UTF-8,
    then in the Windows code page most devices write. Excel .xlsx is read
    through openpyxl. The old binary .xls is refused with a sentence that
    says what to do instead, because guessing at it silently would be worse.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in (".xlsx", ".xlsm"):
        return _read_xlsx(path)
    if suffix == ".xls":
        raise ValueError("This is an old Excel .xls file. Open it in Excel and save it "
                         "as CSV or as .xlsx, then import that.")

    raw = path.read_bytes()
    text = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("The file could not be read as text.")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = "\t" if sample.count("\t") > sample.count(",") else ","

    rows = [row for row in csv.reader(text.splitlines(), delimiter=delimiter)
            if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError("The file is empty.")

    width = max(len(r) for r in rows)
    rows = [[c.strip() for c in r] + [""] * (width - len(r)) for r in rows]
    return _split_header(rows)


def _read_xlsx(path: Path) -> tuple[list[str], list[list[str]]]:
    try:
        import openpyxl
    except ImportError as exc:
        raise ValueError("Reading .xlsx needs openpyxl, which is not installed. "
                         "Save the export as CSV instead.") from exc
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = book.worksheets[0]
    rows = []
    for values in sheet.iter_rows(values_only=True):
        cells = []
        for v in values:
            if v is None:
                cells.append("")
            elif isinstance(v, dt.datetime):
                cells.append(v.strftime("%Y-%m-%d %H:%M:%S"))
            elif isinstance(v, dt.date):
                cells.append(v.isoformat())
            elif isinstance(v, dt.time):
                cells.append(v.strftime("%H:%M:%S"))
            else:
                cells.append(str(v).strip())
        if any(cells):
            rows.append(cells)
    book.close()
    if not rows:
        raise ValueError("The first sheet is empty.")
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return _split_header(rows)


def _split_header(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """The first row is a header when it names things rather than holding a date."""
    first = rows[0]
    looks_like_header = any(re.search(r"[A-Za-z]{2,}", c) for c in first) and not any(
        parse_date(c, True) or parse_datetime(c, True) for c in first)
    if looks_like_header:
        return [c or f"Column {i + 1}" for i, c in enumerate(first)], rows[1:]
    return [f"Column {i + 1}" for i in range(len(first))], rows


# ----------------------------------------------------------------- parsing

def parse_date(text: str, dayfirst: bool) -> Optional[dt.date]:
    text = (text or "").strip()
    if not text:
        return None
    ordered = (_DATE_FORMATS_ISO + _DATE_FORMATS_DAYFIRST + _DATE_FORMATS_MONTHFIRST
               if dayfirst else
               _DATE_FORMATS_ISO + _DATE_FORMATS_MONTHFIRST + _DATE_FORMATS_DAYFIRST)
    for fmt in ordered:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_time(text: str) -> Optional[dt.time]:
    text = (text or "").strip().upper().replace(".", ":")
    if not text:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def parse_datetime(text: str, dayfirst: bool) -> Optional[dt.datetime]:
    """A date and a time in one cell, in either order, separated by a space or a T."""
    text = (text or "").strip()
    if not text:
        return None
    for sep in ("T", " "):
        if sep in text:
            head, tail = text.split(sep, 1)
            date_part, time_part = head.strip(), tail.strip()
            day, clock = parse_date(date_part, dayfirst), parse_time(time_part)
            if day and clock:
                return dt.datetime.combine(day, clock)
            # "09:12 01/09/2026" - time first
            day, clock = parse_date(time_part, dayfirst), parse_time(date_part)
            if day and clock:
                return dt.datetime.combine(day, clock)
            # Time with its own internal space, e.g. "01/09/2026 09:12 AM"
            parts = text.split()
            if len(parts) >= 3:
                day = parse_date(parts[0], dayfirst)
                clock = parse_time(" ".join(parts[1:]))
                if day and clock:
                    return dt.datetime.combine(day, clock)
    return None


def normalise_direction(text: str) -> Optional[str]:
    """
    "in" or "out" from whatever the device calls it, or None.

    Matched on whole words, so "OverTime In", "C/Out" and "check-in" are
    understood while a name like "Nitin" is not mistaken for an in-punch.
    """
    value = (text or "").strip().lower()
    if not value:
        return None
    if value in _IN_WORDS:
        return "in"
    if value in _OUT_WORDS:
        return "out"
    compact = value.replace(" ", "").replace("-", "").replace("_", "")
    if compact in {w.replace(" ", "").replace("-", "") for w in _IN_WORDS}:
        return "in"
    if compact in {w.replace(" ", "").replace("-", "") for w in _OUT_WORDS}:
        return "out"
    words = set(re.split(r"[^a-z]+", value)) - {""}
    if words & {"out", "exit", "checkout", "logout"}:
        return "out"
    if words & {"in", "entry", "enter", "checkin", "login"}:
        return "in"
    return None


# ----------------------------------------------------------------- mapping

@dataclass
class Mapping:
    """Which column plays which part. Column indexes; None when absent."""
    code: Optional[int] = None
    name: Optional[int] = None
    date: Optional[int] = None
    time: Optional[int] = None
    datetime: Optional[int] = None
    direction: Optional[int] = None
    dayfirst: bool = True

    def role_of(self, column: int) -> str:
        for role in ("code", "name", "date", "time", "datetime", "direction"):
            if getattr(self, role) == column:
                return role
        return "ignore"

    def set_role(self, column: int, role: str) -> None:
        for r in ("code", "name", "date", "time", "datetime", "direction"):
            if getattr(self, r) == column:
                setattr(self, r, None)
        if role != "ignore":
            setattr(self, role, column)

    def problems(self) -> list[str]:
        out = []
        if self.code is None:
            out.append("no column is marked as the employee code")
        if self.datetime is None and (self.date is None or self.time is None):
            out.append("no column gives the time of the punch: mark a 'Date + time' "
                       "column, or both a 'Date' and a 'Time' column")
        return out

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Mapping":
        known = {k: data.get(k) for k in ("code", "name", "date", "time", "datetime", "direction")}
        return cls(dayfirst=bool(data.get("dayfirst", True)), **known)


def guess_mapping(header: list[str], rows: list[list[str]], dayfirst: bool = True) -> Mapping:
    """
    Which column is which, from the header names first and the values second.

    Header words decide when they are unambiguous. Otherwise a column that
    parses as a date-and-time is the timestamp, one that parses as a date is
    the date, one that parses as a time is the time, one whose values are
    drawn from a handful of in/out words is the direction, and the column
    whose values are short and repeat across many rows is the code.
    """
    sample = rows[:300]
    columns = len(header)
    mapping = Mapping(dayfirst=dayfirst)

    def frac(col, test):
        values = [r[col] for r in sample if col < len(r) and r[col].strip()]
        if not values:
            return 0.0
        return sum(1 for v in values if test(v)) / len(values)

    shapes = {}
    for col in range(columns):
        shapes[col] = {
            "datetime": frac(col, lambda v: parse_datetime(v, dayfirst) is not None),
            "date": frac(col, lambda v: parse_date(v, dayfirst) is not None),
            "time": frac(col, lambda v: parse_time(v) is not None and parse_date(v, dayfirst) is None),
            "direction": frac(col, lambda v: normalise_direction(v) is not None),
        }

    # 1. Values first for the time columns: a header that says "Time" but holds
    #    "2026-09-01 09:12:33" is a timestamp whatever it is called.
    taken = set()

    def pick(role, threshold=0.9):
        best, best_score = None, 0.0
        for col in range(columns):
            if col in taken:
                continue
            score = shapes[col][role]
            if score >= threshold and score > best_score:
                best, best_score = col, score
        return best

    col = pick("datetime")
    if col is not None:
        mapping.datetime = col
        taken.add(col)
    else:
        col = pick("date")
        if col is not None:
            mapping.date = col
            taken.add(col)
        col = pick("time")
        if col is not None:
            mapping.time = col
            taken.add(col)

    # 2. Direction: a column of in/out words, and nothing else.
    col = pick("direction", threshold=0.95)
    if col is not None:
        # But not a column that is really a code or a name that happens to
        # contain the letters "in".
        distinct = {r[col].strip().lower() for r in sample if col < len(r) and r[col].strip()}
        if len(distinct) <= 6:
            mapping.direction = col
            taken.add(col)

    # 3. Header words for the rest.
    lowered = [h.strip().lower() for h in header]
    for role in ("code", "name"):
        for col, name in enumerate(lowered):
            if col in taken:
                continue
            if any(hint in name for hint in _HEADER_HINTS[role]):
                # "name" must not capture "username"-style code columns.
                if role == "code" and "name" in name and "user" not in name:
                    continue
                setattr(mapping, role, col)
                taken.add(col)
                break

    # 4. Still no code column: the one whose values are short, repeat across
    #    rows, and are not dates or times.
    if mapping.code is None:
        best, best_score = None, 0.0
        for col in range(columns):
            if col in taken or shapes[col]["date"] > 0.5 or shapes[col]["time"] > 0.5:
                continue
            values = [r[col].strip() for r in sample if col < len(r) and r[col].strip()]
            if not values:
                continue
            distinct = len(set(values))
            avg_len = sum(len(v) for v in values) / len(values)
            if avg_len > 24:
                continue
            score = (len(values) - distinct) / len(values)     # how much it repeats
            if score > best_score:
                best, best_score = col, score
        if best is not None:
            mapping.code = best

    return mapping


# ----------------------------------------------------------------- punches

@dataclass
class Punch:
    code: str
    when: dt.datetime
    direction: Optional[str] = None
    name: str = ""
    row: int = 0


def extract_punches(header: list[str], rows: list[list[str]], mapping: Mapping
                    ) -> tuple[list[Punch], list[tuple[int, str]]]:
    """Every usable line as a punch, and every unusable one with its reason."""
    punches, skipped = [], []
    for number, row in enumerate(rows, start=2 if header else 1):
        def cell(index):
            return row[index].strip() if index is not None and index < len(row) else ""

        code = cell(mapping.code)
        if not code:
            skipped.append((number, "no employee code"))
            continue

        when = None
        if mapping.datetime is not None:
            when = parse_datetime(cell(mapping.datetime), mapping.dayfirst)
            if when is None:
                # Some devices leave the time out of the timestamp column.
                day = parse_date(cell(mapping.datetime), mapping.dayfirst)
                clock = parse_time(cell(mapping.time)) if mapping.time is not None else None
                if day and clock:
                    when = dt.datetime.combine(day, clock)
        else:
            day = parse_date(cell(mapping.date), mapping.dayfirst)
            clock = parse_time(cell(mapping.time))
            if day and clock:
                when = dt.datetime.combine(day, clock)
        if when is None:
            skipped.append((number, "the date or time could not be read"))
            continue

        direction = normalise_direction(cell(mapping.direction)) if mapping.direction is not None else None
        punches.append(Punch(code=code, when=when, direction=direction,
                             name=cell(mapping.name), row=number))
    return punches, skipped


@dataclass
class DayRecord:
    code: str
    user_id: str
    day: dt.date
    punch_in: Optional[str]
    punch_out: Optional[str]
    punches: int
    name: str = ""


def reduce_to_days(punches: Iterable[Punch], known_ids: Iterable[str],
                   code_map: Optional[dict] = None
                   ) -> tuple[list[DayRecord], dict]:
    """
    One record per person per day: earliest punch in, latest punch out.

    A code is matched to a Slate Employee ID case-insensitively, either
    directly or through the studio's code-to-ID table. Codes that match
    nobody are counted and returned rather than guessed at.
    """
    known = {str(k).strip().lower(): str(k).strip() for k in known_ids}
    code_map = {str(k).strip().lower(): str(v).strip() for k, v in (code_map or {}).items()
                if str(v).strip()}

    unknown: dict[str, int] = {}
    grouped: dict[tuple[str, dt.date], list[Punch]] = {}
    names: dict[str, str] = {}
    for punch in punches:
        key = punch.code.strip().lower()
        user_id = code_map.get(key) or known.get(key)
        if user_id is None:
            unknown[punch.code] = unknown.get(punch.code, 0) + 1
            continue
        grouped.setdefault((user_id, punch.when.date()), []).append(punch)
        if punch.name and punch.code not in names:
            names[punch.code] = punch.name

    days = []
    for (user_id, day), hits in sorted(grouped.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        hits.sort(key=lambda p: p.when)
        ins = [p for p in hits if p.direction == "in"]
        outs = [p for p in hits if p.direction == "out"]
        first = (ins[0] if ins else hits[0]).when
        last = (outs[-1] if outs else hits[-1]).when
        punch_in = first.strftime("%H:%M:%S")
        punch_out = last.strftime("%H:%M:%S") if last > first else None
        days.append(DayRecord(code=hits[0].code, user_id=user_id, day=day,
                              punch_in=punch_in, punch_out=punch_out,
                              punches=len(hits), name=names.get(hits[0].code, "")))
    return days, unknown


# ----------------------------------------------------------------- applying

def _earlier(a: Optional[str], b: Optional[str]) -> Optional[str]:
    if not a:
        return b
    if not b:
        return a
    return min(a, b)


def _later(a: Optional[str], b: Optional[str]) -> Optional[str]:
    if not a:
        return b
    if not b:
        return a
    return max(a, b)


def apply_days(days: Iterable[DayRecord], attendance, source: str = "") -> dict:
    """
    Write the days into attendance, merging with what is already recorded.

    A day the machine and a workstation both saw keeps the earlier in and the
    later out. A day that already says exactly this is left alone, which is
    what makes importing the same file twice harmless.
    """
    written = unchanged = failed = 0
    failures = []
    for record in days:
        try:
            existing = attendance.get_day(record.user_id, record.day)
            p_in = _earlier(existing.get("punch_in"), record.punch_in) if existing else record.punch_in
            p_out = _later(existing.get("punch_out"), record.punch_out) if existing else record.punch_out
            if existing and (existing.get("punch_in") or None) == p_in and (existing.get("punch_out") or None) == p_out:
                unchanged += 1
                continue
            ok, message = attendance.write_day(
                record.user_id, record.day, p_in, p_out, pc_name="BIOMETRIC",
                metadata={"source": "biometric", "file": source,
                          "imported_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                          "punches": record.punches})
            if ok:
                written += 1
            else:
                failed += 1
                failures.append((record.user_id, record.day.isoformat(), message))
        except Exception as exc:               # noqa: BLE001 - one bad day must not stop the rest
            failed += 1
            failures.append((record.user_id, record.day.isoformat(), str(exc)))
    return {"written": written, "unchanged": unchanged, "failed": failed, "failures": failures}


# ----------------------------------------------------------------- profiles

def _profile_key(header: list[str]) -> str:
    return "|".join(h.strip().lower() for h in header)


def _profiles_path() -> Optional[Path]:
    try:
        from slate.core.infra.global_config import GlobalConfig
        root = Path(str(GlobalConfig.server_root()))
        return root / "Config" / "biometric_profiles.json"
    except Exception as exc:
        logger.debug("No studio folder for biometric profiles: %s", exc)
        return None


def load_profiles(path: Optional[Path] = None) -> dict:
    path = path or _profiles_path()
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        logger.warning("Could not read the biometric profiles: %s", exc)
        return {}


def find_profile(header: list[str], profiles: Optional[dict] = None) -> Optional[dict]:
    """The saved mapping for a file with this header, or None."""
    profiles = load_profiles() if profiles is None else profiles
    return profiles.get(_profile_key(header))


def save_profile(header: list[str], mapping: Mapping, code_map: dict,
                 path: Optional[Path] = None, label: str = "") -> Optional[Path]:
    """Remember how this kind of file maps, for the next import from the same machine."""
    path = path or _profiles_path()
    if path is None:
        return None
    profiles = load_profiles(path)
    profiles[_profile_key(header)] = {
        "label": label or ", ".join(header[:4]),
        "header": list(header),
        "mapping": mapping.to_dict(),
        "code_map": {str(k): str(v) for k, v in (code_map or {}).items() if str(v).strip()},
        "saved_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
        return path
    except OSError as exc:
        logger.warning("Could not save the biometric profile: %s", exc)
        return None
