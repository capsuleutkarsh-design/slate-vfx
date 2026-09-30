"""
The portable unit of work behind db.atomic().

Both managers had a transaction() that handed back the raw driver connection,
so code using it had to know which database it was talking to: psycopg2 wants
%s and returns dicts only with the right cursor, sqlite3 wants ? and has no
RETURNING in older builds. Nothing in the domain code could use it, so things
that belong together - a loan in the ledger and the machine marked as issued -
were written as separate statements, each committed on its own, and any
failure between them left the two disagreeing.

AtomicUnit is what the block receives on either backend:

    with db.atomic() as tx:
        loan = tx.write("INSERT INTO asset_assignments (machine_name, user_id) "
                        "VALUES (%s, %s) RETURNING id", (machine, person))
        tx.write("UPDATE hardware_inventory SET assigned_to = %s WHERE machine_name = %s",
                 (person, machine), expect_rows=True)
        rows = tx.query("SELECT ... WHERE id = %s", (loan.last_id,))

A refused statement raises DatabaseWriteError, which leaves the block and
rolls everything in it back. expect_rows=True makes "matched nothing" a
failure too (NoRowsError), for updates that must hit an existing row.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, List, Optional

from .db_results import DatabaseWriteError, NoRowsError, WriteResult, classify_error, error_text

logger = logging.getLogger(__name__)

_RETURNING_RE = re.compile(r"\s+RETURNING\s+\w+(?:\s*,\s*\w+)*", re.IGNORECASE)


class AtomicUnit:
    """
    Runs statements on one connection inside one open transaction.

    The manager that creates it commits or rolls back; this class never does.
    """

    def __init__(self, conn, *, cursor_factory=None,
                 translate: Optional[Callable[[str], str]] = None,
                 is_unavailable: Optional[Callable[[BaseException], bool]] = None,
                 strip_returning: bool = False):
        self._conn = conn
        self._cursor_factory = cursor_factory
        self._translate = translate
        self._is_unavailable = is_unavailable or (lambda exc: False)
        self._strip_returning = strip_returning
        self.statements = 0

    def _cursor(self):
        if self._cursor_factory is not None:
            return self._conn.cursor(cursor_factory=self._cursor_factory)
        return self._conn.cursor()

    def _prepare(self, sql: str):
        wants_id = bool(_RETURNING_RE.search(sql))
        if self._strip_returning and wants_id:
            sql = _RETURNING_RE.sub("", sql)
        if self._translate is not None:
            sql = self._translate(sql)
        return sql, wants_id

    def _run(self, sql: str, params):
        cur = self._cursor()
        cur.execute(sql, tuple(params) if params is not None else ())
        self.statements += 1
        return cur

    def write(self, sql: str, params=None, *, expect_rows: bool = False) -> WriteResult:
        """
        One statement of the unit. Returns a WriteResult (always ok - a
        refusal raises DatabaseWriteError so the unit stops and rolls back).
        """
        prepared, wants_id = self._prepare(sql)
        try:
            cur = self._run(prepared, params)
        except Exception as exc:
            if self._is_unavailable(exc):
                raise
            result = WriteResult.failed(exc)
            logger.error("Atomic step refused: %s | %s", result.error, " ".join(sql.split())[:160])
            raise DatabaseWriteError(result.error, kind=result.kind, result=result) from exc

        rows = getattr(cur, "rowcount", 0)
        last_id = None
        if getattr(cur, "description", None) is not None:
            first = cur.fetchone()
            if first:
                last_id = list(first.values())[0] if hasattr(first, "values") else first[0]
        elif wants_id:
            last_id = getattr(cur, "lastrowid", None)
        try:
            cur.close()
        except Exception:
            pass
        result = WriteResult(True, rows=rows, last_id=last_id)
        if expect_rows and result.rows <= 0:
            raise NoRowsError("Nothing matched: " + " ".join(sql.split())[:120], result=result)
        return result

    def query(self, sql: str, params=None) -> List[dict]:
        """Rows (as dicts) read inside the unit, so they see its own writes."""
        prepared, _ = self._prepare(sql)
        try:
            cur = self._run(prepared, params)
        except Exception as exc:
            if self._is_unavailable(exc):
                raise
            raise DatabaseWriteError(error_text(exc), kind=classify_error(exc)) from exc
        rows = cur.fetchall() if getattr(cur, "description", None) is not None else []
        try:
            cur.close()
        except Exception:
            pass
        return [dict(r) for r in rows]

    def one(self, sql: str, params=None) -> Optional[dict]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def value(self, sql: str, params=None, default: Any = None) -> Any:
        """The first column of the first row, or default."""
        row = self.one(sql, params)
        if not row:
            return default
        return next(iter(row.values()), default)


class _SequentialUnit:
    """
    The same interface for a handle with no atomic() - a test double, or an
    older object that only has execute_query / execute_update. Statements run
    one by one and a refusal still raises, so the caller stops at the first
    failure; there is simply nothing to roll back to.
    """

    def __init__(self, db):
        self._db = db

    def write(self, sql, params=None, *, expect_rows=False):
        result = self._db.execute_update(sql, params)
        if not isinstance(result, WriteResult):
            # An old-style boolean.
            result = WriteResult(bool(result), rows=1 if result else 0,
                                 error="" if result else "The database refused the change.")
        result.raise_for_error()
        if expect_rows and result.rows <= 0:
            raise NoRowsError("Nothing matched: " + " ".join(sql.split())[:120], result=result)
        return result

    def query(self, sql, params=None):
        rows = self._db.execute_query(sql, params, fetch="all")
        if rows is None:
            raise DatabaseWriteError("The database refused a read inside a unit of work.")
        return [dict(r) for r in rows]

    def one(self, sql, params=None):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def value(self, sql, params=None, default=None):
        row = self.one(sql, params)
        if not row:
            return default
        return next(iter(row.values()), default)


from contextlib import contextmanager as _contextmanager  # noqa: E402


@_contextmanager
def atomic(db):
    """
    db.atomic() when the handle has one, otherwise a sequential stand-in.

    Domain code calls this rather than db.atomic() directly so it also runs
    against the small fakes the tests hand it.
    """
    opener = getattr(db, "atomic", None)
    if callable(opener):
        try:
            unit_cm = opener()
        except TypeError:
            unit_cm = None
        if unit_cm is not None and hasattr(unit_cm, "__enter__"):
            with unit_cm as unit:
                yield unit
            return
    yield _SequentialUnit(db)
