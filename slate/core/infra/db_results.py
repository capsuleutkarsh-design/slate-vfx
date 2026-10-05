"""
What a database call reports back, the same on PostgreSQL and SQLite.

Before this module the two backends disagreed, and most callers were wrong on
at least one of them:

    PostgreSQL  execute_query(..., fetch=False)   -> None, success or failure
                a rejected statement             -> logged, then None / False
                execute_update on 0 rows         -> True
    SQLite      a rejected statement             -> RuntimeError

So "if execute_query(insert, fetch=False):" never ran its success branch (the
new milestone saved, the table did not refresh, people clicked Save again and
made duplicates), a failed write read as "saved" wherever the caller waited
for an exception, and an UPDATE that matched nothing ("punch out" with no
punch in) reported success.

The contract now, on both backends:

    db.write(sql, params)          -> WriteResult. Never raises for a rejected
                                      statement: .ok is False and .error says
                                      why. Raises DatabaseUnavailableError when
                                      the database cannot be reached at all -
                                      that is an outage, not a refusal.
    db.write(..., strict=True)     -> the same, but a refusal raises
                                      DatabaseWriteError.
    db.execute_update(sql, params) -> db.write(sql, params). Truthy exactly
                                      when the database accepted it, as before.
    db.execute_query(sql, params, fetch=False | None | "none")
                                   -> db.write(sql, params) too.
    db.atomic()                    -> several statements that commit or roll
                                      back together, with the same %s
                                      parameters on both backends.
    db.execute_sql(sql)            -> SqlResult: columns, rows, rowcount,
                                      error. For consoles that run whatever a
                                      person typed.

A WriteResult is truthy when the statement was accepted, so existing
"if db.execute_update(...):" code keeps working. Whether it actually changed
anything is .rows (or .changed): an UPDATE that matched no row, or an
INSERT ... ON CONFLICT DO NOTHING that hit a duplicate, is ok with rows == 0.
"""

from __future__ import annotations

import re as _re
from typing import Any, List, Optional, Sequence


class DatabaseUnavailableError(ConnectionError):
    """
    The database could not be reached, so this operation did not happen.

    Raised instead of quietly returning "no rows". A read that silently returns
    nothing looks like an empty project; a write that silently does nothing
    looks like a successful save. Both are worse than an error message, and
    both are exactly what a connection shortage produces.
    """


class DatabaseWriteError(RuntimeError):
    """
    The database refused a statement (a constraint, a bad value, a missing
    column). Raised by write(..., strict=True) and inside db.atomic(), where a
    refusal must stop the whole unit rather than let the next statement run.

    It is a RuntimeError so code written for the SQLite backend, which always
    raised RuntimeError for a failed query, still catches it.
    """

    def __init__(self, message: str, kind: str = "error", result: "WriteResult" = None):
        super().__init__(message)
        self.kind = kind
        self.result = result


class DatabaseReadError(DatabaseWriteError):
    """
    A read the database refused, raised by execute_query(..., strict=True).
    Without strict a failed read returns None, which callers turn into "no
    rows" - fine for a list on screen, wrong for a check whose empty answer
    charges leave, credits comp-off or re-ingests a library.
    """


class NoRowsError(DatabaseWriteError):
    """An atomic step said it must change a row, and it matched none."""

    def __init__(self, message: str, result: "WriteResult" = None):
        super().__init__(message, kind="no_rows", result=result)


# The kinds of refusal a caller may want to tell apart. "duplicate" is the one
# screens most often need: "there is already a holiday on 2 Oct".
KIND_DUPLICATE = "duplicate"
KIND_CONSTRAINT = "constraint"
KIND_TOO_LONG = "too_long"
KIND_INVALID = "invalid"
KIND_ERROR = "error"


def classify_error(exc: BaseException) -> str:
    """One of the KIND_ values for a driver exception, the same for both drivers."""
    code = str(getattr(exc, "pgcode", "") or "")
    text = str(exc).lower()
    if code == "23505" or "unique constraint" in text or "duplicate key" in text:
        return KIND_DUPLICATE
    if code == "22001" or "value too long" in text:
        return KIND_TOO_LONG
    if code.startswith("23") or "constraint failed" in text or "violates" in text:
        return KIND_CONSTRAINT
    if code.startswith("22") or "invalid input" in text or "datatype mismatch" in text:
        return KIND_INVALID
    return KIND_ERROR


def error_text(exc: BaseException) -> str:
    """
    The database's own words, without the driver's decoration.

    psycopg2 appends a LINE / caret block that means nothing on a status bar;
    the first line is the part a person can act on.
    """
    raw = getattr(exc, "pgerror", None) or str(exc) or exc.__class__.__name__
    first = str(raw).strip().splitlines()[0] if str(raw).strip() else exc.__class__.__name__
    if first.upper().startswith("ERROR:"):
        first = first[6:].strip()
    return first


class WriteResult:
    """
    The outcome of one write.

    ok       the database accepted the statement
    rows     rows it changed (0 for DDL, for an UPDATE that matched nothing, or
             for an INSERT ... ON CONFLICT DO NOTHING that hit a duplicate)
    last_id  the id from "RETURNING id" (PostgreSQL) or the new row's id
             (SQLite), when there is one
    error    the database's reason, when ok is False
    kind     what sort of refusal: "duplicate", "constraint", "too_long",
             "invalid" or "error"

    Truthy exactly when ok, so "if db.execute_update(...):" keeps its meaning.
    """

    __slots__ = ("ok", "rows", "last_id", "error", "kind")

    def __init__(self, ok: bool, rows: int = 0, last_id: Any = None,
                 error: str = "", kind: str = ""):
        self.ok = bool(ok)
        try:
            self.rows = max(int(rows or 0), 0)
        except (TypeError, ValueError):
            self.rows = 0
        self.last_id = last_id
        self.error = str(error or "")
        self.kind = kind or ("" if ok else KIND_ERROR)

    @classmethod
    def failed(cls, exc: BaseException) -> "WriteResult":
        return cls(False, error=error_text(exc), kind=classify_error(exc))

    @property
    def changed(self) -> bool:
        """Accepted and changed at least one row."""
        return self.ok and self.rows > 0

    @property
    def duplicate(self) -> bool:
        return (not self.ok) and self.kind == KIND_DUPLICATE

    def raise_for_error(self) -> "WriteResult":
        """Raise DatabaseWriteError when refused; otherwise return self."""
        if not self.ok:
            raise DatabaseWriteError(self.error or "The database refused the change.",
                                     kind=self.kind, result=self)
        return self

    def __bool__(self) -> bool:
        return self.ok

    # Comparing with True / False compares acceptance, so code and tests that
    # wrote "== True" against the old boolean still read the same.
    def __eq__(self, other):
        if isinstance(other, bool):
            return self.ok == other
        if isinstance(other, WriteResult):
            return (self.ok, self.rows, self.last_id, self.error) == (
                other.ok, other.rows, other.last_id, other.error)
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    __hash__ = object.__hash__

    def __repr__(self) -> str:
        if self.ok:
            extra = f", last_id={self.last_id!r}" if self.last_id is not None else ""
            return f"WriteResult(ok, rows={self.rows}{extra})"
        return f"WriteResult(failed, kind={self.kind!r}, error={self.error!r})"


class SqlResult:
    """
    What a typed-in statement produced, for screens that run arbitrary SQL.

    columns    column names when the statement returned rows, else []
    rows       the rows (dicts), at most max_rows of them
    rowcount   rows returned (a query) or changed (a write)
    truncated  more rows existed than were returned
    is_query   the statement produced a result set
    error      the database's reason when it refused; '' when it worked
    """

    __slots__ = ("columns", "rows", "rowcount", "truncated", "is_query", "error", "kind")

    def __init__(self, columns: Optional[Sequence[str]] = None, rows: Optional[List[dict]] = None,
                 rowcount: int = 0, truncated: bool = False, is_query: bool = False,
                 error: str = "", kind: str = ""):
        self.columns = list(columns or [])
        self.rows = list(rows or [])
        self.rowcount = int(rowcount or 0)
        self.truncated = bool(truncated)
        self.is_query = bool(is_query)
        self.error = str(error or "")
        self.kind = kind

    @property
    def ok(self) -> bool:
        return not self.error

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:
        if self.error:
            return f"SqlResult(error={self.error!r})"
        return (f"SqlResult(columns={len(self.columns)}, rows={len(self.rows)}, "
                f"rowcount={self.rowcount}, truncated={self.truncated})")


# Statements that change data or schema. Used to decide whether to commit and
# how to report the outcome. Leading comments and a WITH clause are allowed.
_WRITE_RE = _re.compile(
    r"^\s*(?:--[^\n]*\n\s*|/\*.*?\*/\s*)*(?:WITH\s+.*?\s+)?\b"
    r"(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|REPLACE|UPSERT|GRANT|REVOKE|VACUUM|REINDEX|COMMENT)\b",
    _re.IGNORECASE | _re.DOTALL)


def is_write_statement(query: str) -> bool:
    return bool(query) and bool(_WRITE_RE.search(query))


def is_legacy_write_fetch(fetch: Any) -> bool:
    """
    The fetch values that mean "this is a write, tell me whether it worked".

    fetch=False and fetch=None were never valid modes - the PostgreSQL manager
    matched no branch and returned None whether the statement worked or not.
    Callers that passed them meant a write, so they get a WriteResult.
    """
    return fetch is False or fetch is None or fetch == "none"
