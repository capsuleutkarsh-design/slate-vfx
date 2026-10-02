"""
The change history: who changed what on which shot, written and read one way.

Three faults, one table:

  * The author. Writers stored the numeric user id ("18") and the reader
    joined ut_users.username = change_history.user_id, so every row read back
    as "Unknown". And when the dashboard could not turn a display name into an
    id it used 1 - so any mismatch recorded the admin as the author.
  * The shot. A department change was stored as entity_id 'SH010_comp' and a
    shot's history looked up with LIKE 'SH010_%' - '_' matches any character,
    so SH010's history included SH010A's, and the reel was never considered,
    so ReelA/SH010 and ReelB/SH010 shared one history.
  * The keys. The reader returned user_name / field_changed and the History
    dialog read user / field, so even a correct row showed 'Unknown' / 'None'.

Now:

  * The author is the username - the stable identity, the same one the
    rest of the app signs in with. A change with no known author is not
    written (and says so in the log) rather than being pinned on user 1.
    Old rows holding a numeric id are still read correctly.
  * shot_id, shot_name, reel and department are columns of their own
    (migrations/foundation_data.py adds them and back-fills old rows).
    History is looked up by shot_id; rows from before the columns existed
    are matched by exact name, with '_' escaped.
  * Rows come back with both key spellings (user and user_name, field and
    field_changed), plus the shot details.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .db_results import DatabaseUnavailableError, WriteResult

logger = logging.getLogger(__name__)


def _escape_like(text: str) -> str:
    return (str(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))


def _has_shot_columns(db) -> bool:
    cached = getattr(db, "_change_history_has_shot_columns", None)
    if cached is not None:
        return cached
    try:
        from .migrations.workplace_schema import _column_exists
        present = _column_exists(db, "change_history", "shot_id")
    except Exception:
        present = False
    if present:
        # Only a positive answer is kept: the column appears when the
        # migration runs, and it never goes away again.
        try:
            setattr(db, "_change_history_has_shot_columns", True)
        except Exception:
            pass
    return present


def log_change(db, project_code: str, entity_type: str, entity_id: str, author: str,
               action_type: str, field: str, old_val: Any, new_val: Any, *,
               shot_id: Optional[int] = None, shot_name: Optional[str] = None,
               reel: Optional[str] = None, department: Optional[str] = None) -> WriteResult:
    """
    Record one change. author is the username of the person who made it.

    Returns the WriteResult. With no author, nothing is written: a history
    that names the wrong person is worse than a gap, and the gap is logged.
    """
    author = str(author or "").strip()
    if not author:
        logger.error("History not written for %s %s.%s: nobody is named as the author.",
                     entity_type, entity_id, field)
        return WriteResult(False, error="no author", kind="invalid")

    def _text(value):
        return "" if value is None else str(value)

    if _has_shot_columns(db):
        return db.execute_update(
            "INSERT INTO change_history "
            "(project_code, entity_type, entity_id, user_id, action_type, field_changed, "
            " old_value, new_value, shot_id, shot_name, reel, department) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (project_code, entity_type, entity_id, author, action_type, field,
             _text(old_val), _text(new_val), shot_id, shot_name, reel, department))
    return db.execute_update(
        "INSERT INTO change_history "
        "(project_code, entity_type, entity_id, user_id, action_type, field_changed, old_value, new_value) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (project_code, entity_type, entity_id, author, action_type, field,
         _text(old_val), _text(new_val)))


def _history_filters(db, project_code, shot_name, shot_id, reel, since, until):
    """The WHERE clauses and parameters shared by read_history and count_history."""
    where = ["1=1"]
    params: List[Any] = []
    shot_cols = _has_shot_columns(db)

    if project_code:
        where.append("ch.project_code = %s")
        params.append(project_code)

    # A date range for the Audit Logs: since is inclusive, until exclusive.
    if since is not None:
        where.append("ch.timestamp >= %s")
        params.append(since)
    if until is not None:
        where.append("ch.timestamp < %s")
        params.append(until)

    # A shot is best named by shot_id. Given only a name (and optionally a
    # reel), rows are matched by the shot_name column, and rows written before
    # that column existed by their exact entity_id or '<name>_<department>'.
    if shot_id is not None and shot_cols:
        clause = "ch.shot_id = %s"
        params.append(int(shot_id))
        if shot_name:
            # Rows older than the shot columns, not yet back-filled.
            clause = ("(" + clause + " OR (ch.shot_id IS NULL AND ch.shot_name IS NULL AND "
                      "(ch.entity_id = %s OR ch.entity_id LIKE %s ESCAPE '\\')))")
            params.extend([shot_name, _escape_like(shot_name) + "\\_%"])
        where.append(clause)
    elif shot_name:
        legacy = "(ch.entity_id = %s OR ch.entity_id LIKE %s ESCAPE '\\')"
        legacy_params = [shot_name, _escape_like(shot_name) + "\\_%"]
        if shot_cols:
            named = "LOWER(ch.shot_name) = LOWER(%s)"
            named_params: List[Any] = [shot_name]
            if reel is not None:
                named += " AND COALESCE(ch.reel, '') IN (%s, '')"
                named_params.append(reel or "")
            where.append("((" + named + ") OR (ch.shot_name IS NULL AND " + legacy + "))")
            params.extend(named_params + legacy_params)
        else:
            where.append(legacy)
            params.extend(legacy_params)

    return where, params, shot_cols


def count_history(db, project_code: Optional[str] = None, *, since=None, until=None) -> int:
    """How many history rows a read_history() with the same filters could page through."""
    where, params, _cols = _history_filters(db, project_code, None, None, None, since, until)
    rows = db.execute_query(
        f"SELECT COUNT(*) AS n FROM change_history ch WHERE {' AND '.join(where)}",
        tuple(params), fetch="all")
    if not rows:
        return 0
    row = rows[0]
    value = row.get("n") if isinstance(row, dict) else row[0]
    return int(value or 0)


def read_history(db, project_code: Optional[str] = None, shot_name: Optional[str] = None,
                 limit: int = 200, *, shot_id: Optional[int] = None,
                 reel: Optional[str] = None, since=None, until=None,
                 offset: int = 0) -> List[Dict[str, Any]]:
    """
    History, newest first: everything, one project, or one shot.

    since / until (datetimes or ISO text) narrow it to a time range and offset
    pages through it - the Audit Logs use both.
    """
    where, params, shot_cols = _history_filters(db, project_code, shot_name, shot_id, reel,
                                                since, until)
    extra = (", ch.shot_id, ch.shot_name, ch.reel, ch.department" if shot_cols else "")
    params.append(int(limit))
    params.append(max(0, int(offset or 0)))
    query = f"""
        SELECT
            ch.timestamp,
            COALESCE(NULLIF(u.display_name, ''), u.username, ch.user_id, 'Unknown') AS user_name,
            u.username AS username,
            ch.user_id AS author,
            ch.field_changed,
            ch.old_value,
            ch.new_value,
            ch.entity_type,
            ch.entity_id,
            ch.action_type,
            ch.project_code{extra}
        FROM change_history ch
        LEFT JOIN ut_users u
               ON u.username = ch.user_id OR CAST(u.id AS TEXT) = CAST(ch.user_id AS TEXT)
        WHERE {' AND '.join(where)}
        ORDER BY ch.timestamp DESC, ch.id DESC
        LIMIT %s OFFSET %s
    """
    try:
        rows = db.execute_query(query, tuple(params), fetch="all")
    except DatabaseUnavailableError:
        raise
    if rows is None:
        logger.error("History could not be read: %s",
                     getattr(db, "last_error", lambda: "")() if callable(getattr(db, "last_error", None)) else "")
        return []
    out = []
    for row in rows:
        r = dict(row)
        # Every spelling a reader uses: the History dialog reads user / field,
        # the Audit Logs viewer display_name.
        r["user"] = r.get("user_name")
        r["display_name"] = r.get("user_name")
        r["field"] = r.get("field_changed")
        if not shot_cols:
            r.setdefault("shot_id", None)
            r.setdefault("shot_name", None)
            r.setdefault("reel", None)
            r.setdefault("department", None)
        out.append(r)
    return out
