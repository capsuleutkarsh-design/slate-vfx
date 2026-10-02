"""
Central Attendance Manager — works with both PostgreSQL and SQLite backends.

One row per person per day (attendance_log, unique on user_id + day_date).
punch_in is the day's first arrival and punch_out its last departure - NULL
while somebody is still in. Everything else about the day lives in the row's
metadata (JSONB on PostgreSQL, text on SQLite):

    wfh                 working from home that day
    sessions            [{"in": "09:30:00", "out": "13:00:00"}, {"in": ..}]
                        when somebody punched in again after punching out
                        (people punch out by mistake, or leave and come
                        back). A day with one session has no list.
    auto_logout, cutoff closed by the automatic punch-out at the next punch-in
    missing_punch_out   a forgotten punch-out the automatic one could not
                        close honestly (the cutoff was before the punch-in)
    overnight           the day's out time is on the next calendar day
    edited_by, edit_reason, edited_at, edit_history
                        HR corrections: who, why, when, and what it was before

Closing Slate or signing out never punches anybody out (studio decision);
forgotten punch-outs are closed by the automatic punch-out instead.
"""
import datetime
import json
import logging
import socket
from ..infra.database_manager import DatabaseManager

logger = logging.getLogger(__name__)


def _is_postgres(db) -> bool:
    """Check whether the active backend resolves to PostgresManager."""
    backend = getattr(db, "backend", db)
    return type(backend).__name__ == "PostgresManager"


def _meta_dict(raw) -> dict:
    """A row's metadata as a dict, whatever the backend handed back."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        from ..infra.migrations.foundation_data import merge_json_objects
        return merge_json_objects(raw)
    except Exception:
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}


def _time_text(value, seconds=False) -> str:
    """'09:30' (or '09:30:00') from a time, a datetime or stored text; '' for none."""
    if value is None or value == "":
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M:%S" if seconds else "%H:%M")
    text = str(value).strip()
    if not text:
        return ""
    if seconds:
        return text[:8] if len(text) >= 8 else (text[:5] + ":00")
    return text[:5]


class CentralAttendance:
    """
    Manages Attendance using the centralized database.
    Works with both PostgreSQL and SQLite backends.
    """
    def __init__(self, db=None):
        # db is the backend to use; left out, it is the studio's own. Passed
        # in, a test or a tool can point this at a database of its own.
        self.db = db if db is not None else DatabaseManager()
        self.pc_name = socket.gethostname()

    # The studio's start time, read when it is used. It used to be copied in
    # when this object was made - and the object is kept for the life of the
    # application, so a "Late after" saved in Settings reached the hero card
    # (which asked the policy) but not the tables (which asked this), and the
    # two disagreed until Slate was restarted.
    @property
    def LATE_CUTOFF_HOUR(self):
        from slate.core.domain.leave_policy import late_cutoff
        return late_cutoff()[0]

    @property
    def LATE_CUTOFF_MINUTE(self):
        from slate.core.domain.leave_policy import late_cutoff
        return late_cutoff()[1]

    def _json_merge_sql(self, column: str, param_placeholder: str = "%s") -> str:
        """
        Generate backend-appropriate JSON merge expression.
        PostgreSQL: column || %s::jsonb
        SQLite:     json_patch(column, %s)  — or Python-side merge via update
        """
        if _is_postgres(self.db):
            return f"COALESCE({column}, '{{}}'::jsonb) || {param_placeholder}::jsonb"
        else:
            return f"json_patch(COALESCE({column}, '{{}}'), {param_placeholder})"

    # ------------------------------------------------------------- the clock
    def _server_now(self):
        """Today's date (ISO text) and the time, from the database's clock."""
        if _is_postgres(self.db):
            now_res = self.db.execute_query(
                "SELECT CURRENT_DATE AS today, LOCALTIME AS now_time", fetch="all")
        else:
            # Local time, like the server's: date('now') alone is UTC, so on
            # the local database a punch after 18:30 IST landed on the wrong day.
            now_res = self.db.execute_query(
                "SELECT date('now','localtime') AS today, "
                "time('now','localtime') AS now_time", fetch="all")
        if not now_res:
            raise RuntimeError("Could not read the time from the database.")
        first = now_res[0]
        if isinstance(first, dict):
            today_date, now_time = first["today"], first["now_time"]
        else:
            today_date, now_time = first[0], first[1]
        today_date = today_date.isoformat() if hasattr(today_date, "isoformat") else str(today_date)
        if isinstance(now_time, str):
            parts = now_time.split(":")
            now_time = datetime.time(int(parts[0]), int(parts[1]),
                                     int(parts[2].split(".")[0]) if len(parts) > 2 else 0)
        return today_date, now_time

    def _row(self, user_id, day_key):
        row = self.db.execute_query(
            "SELECT id, punch_in, punch_out, pc_name, metadata FROM attendance_log "
            "WHERE user_id = %s AND day_date = %s", (user_id, day_key), fetch="one")
        return dict(row) if row else None

    @staticmethod
    def _sessions(row) -> list:
        """The row's sessions as [{"in": "HH:MM:SS", "out": "HH:MM:SS" | None}]."""
        meta = _meta_dict(row.get("metadata"))
        stored = [dict(s) for s in (meta.get("sessions") or []) if isinstance(s, dict) and s.get("in")]
        if stored:
            return stored
        if row.get("punch_in"):
            return [{"in": _time_text(row.get("punch_in"), True),
                     "out": _time_text(row.get("punch_out"), True) or None}]
        return []

    # --------------------------------------------------------------- punching
    def today_state(self, user_name) -> dict:
        """
        Where somebody's day stands, for the punch buttons:

            {"state": "out" | "working" | "done", "in": "09:42", "out": "",
             "since": "09:42", "sessions": 1, "wfh": bool, "date": "2026-10-02"}

        out      not punched in today          -> Punch in
        working  a session is open             -> Punch out
        done     punched out (all sessions)    -> Punch in again
        """
        user_id = str(user_name or "").lower().strip()
        today_date, _ = self._server_now()
        row = self._row(user_id, today_date) if user_id else None
        if not row:
            return {"state": "out", "in": "", "out": "", "since": "", "sessions": 0,
                    "wfh": False, "date": today_date}
        sessions = self._sessions(row)
        meta = _meta_dict(row.get("metadata"))
        open_session = bool(sessions) and not sessions[-1].get("out")
        state = "working" if open_session else ("done" if sessions else "out")
        return {
            "state": state,
            "in": _time_text(row.get("punch_in")),
            "out": _time_text(row.get("punch_out")),
            "since": _time_text(sessions[-1]["in"]) if sessions else "",
            "sessions": len(sessions),
            "wfh": bool(meta.get("wfh")),
            "date": today_date,
        }

    def log_action(self, user_name, action, metadata=None, automatic=False):
        """
        Punch 'in' or 'out'. Returns {"date", "time", "session"} - the date
        and time the database stored, which is what the person is told (the
        message used to show the workstation's clock while the record had the
        server's).

        Punch in:
          nothing today          -> the day starts
          a session open         -> refused: "Already punched in at 09:42"
          punched out already    -> a new session the same day (allowed by
                                    studio decision; kept in the metadata)
        automatic=True is the punch-in Slate makes when somebody signs in. It
        only ever starts the day: signing in again later, or after punching
        out, changes nothing.

        Punch out:
          a session open         -> closed
          nothing today          -> refused: nothing to punch out of
          already punched out    -> refused, so a second click can never
                                    overwrite the first out time
        """
        try:
            if not user_name:
                logging.warning("Attendance log_action called with None/empty user_name, skipping")
                return None

            user_id = user_name.lower().strip()
            today_date, now_time = self._server_now()

            # SMART AUTO-LOGOUT: close the forgotten days before today.
            if action == "in":
                self._check_and_fix_previous_day(user_id, today_date)

            time_str = now_time.strftime("%H:%M:%S") if hasattr(now_time, "strftime") else str(now_time)
            row = self._row(user_id, today_date)

            if action == "in":
                return self._punch_in(user_id, today_date, time_str, row, metadata, automatic)
            if action == "out":
                return self._punch_out(user_id, today_date, time_str, row, metadata)
            raise ValueError("Unknown attendance action %r" % (action,))
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Attendance DB Error: {e}", exc_info=True)
            raise

    def _punch_in(self, user_id, today_date, time_str, row, metadata, automatic):
        meta = dict(metadata or {})
        if not row:
            written = self.db.execute_update(
                "INSERT INTO attendance_log (user_id, day_date, punch_in, pc_name, metadata) "
                "VALUES (%s, %s, %s, %s, %s)",
                (user_id, today_date, time_str, self.pc_name, json.dumps(meta)))
            if not written:
                # The database refused it; the person must not be told
                # they are punched in.
                raise RuntimeError("The punch-in was not saved: %s"
                                   % (getattr(written, "error", "") or "the database refused it"))
            logger.info(f"Punch IN success: {user_id} at {time_str}")
            return {"date": today_date, "time": time_str, "session": 1}

        if automatic:
            # Signing in again is not arriving again.
            return None

        sessions = self._sessions(row)
        if not sessions:
            raise ValueError(
                "Today has a punch-out but no punch-in. Ask HR to correct today's times.")
        if not sessions[-1].get("out"):
            raise ValueError("Already punched in at %s." % _time_text(sessions[-1]["in"]))

        sessions.append({"in": time_str, "out": None})
        meta["sessions"] = sessions
        written = self.db.execute_update(
            "UPDATE attendance_log SET punch_out = NULL, metadata = %s "
            "WHERE id = %%s AND punch_out IS NOT NULL"
            % self._json_merge_sql("metadata"),
            (json.dumps(meta), row["id"]))
        if not written:
            raise RuntimeError("The punch-in was not saved: %s"
                               % (getattr(written, "error", "") or "the database refused it"))
        if not getattr(written, "changed", True):
            raise ValueError("Already punched in - refresh to see today's times.")
        logger.info(f"Punch IN (session {len(sessions)}) success: {user_id} at {time_str}")
        return {"date": today_date, "time": time_str, "session": len(sessions)}

    def _punch_out(self, user_id, today_date, time_str, row, metadata):
        if not row:
            # An UPDATE that matched no row is accepted by the database and
            # changed nothing. That used to count as success - the status
            # bar said "Successfully Logged OUT" for a day with no record
            # at all. With no punch-in there is nothing to close, so say
            # so; HR can still add the day with Edit punch.
            logger.warning(f"Punch OUT refused for {user_id}: no punch-in on {today_date}.")
            raise ValueError(
                "You have not punched in today, so there is nothing to punch out of. "
                "If you forgot to punch in, ask HR to add today's times.")
        sessions = self._sessions(row)
        if not sessions or sessions[-1].get("out"):
            last = _time_text(row.get("punch_out")) or (
                _time_text(sessions[-1].get("out")) if sessions else "")
            raise ValueError(
                "Already punched out at %s. Punch in again to start a new session." % last
                if last else "You are not punched in.")

        meta = dict(metadata or {})
        if len(sessions) > 1:
            sessions[-1]["out"] = time_str
            meta["sessions"] = sessions
        result = self.db.execute_update(
            "UPDATE attendance_log SET punch_out = %%s, metadata = %s "
            "WHERE id = %%s AND punch_out IS NULL" % self._json_merge_sql("metadata"),
            (time_str, json.dumps(meta), row["id"]))
        if not result:
            raise RuntimeError("The punch-out was not saved: %s"
                               % (getattr(result, "error", "") or "the database refused it"))
        if getattr(result, "rows", 1) == 0:
            raise ValueError("Already punched out - refresh to see today's times.")
        logger.info(f"Punch OUT success: {user_id} at {time_str}")
        return {"date": today_date, "time": time_str, "session": len(sessions)}

    def set_wfh(self, user_name, day, flag: bool) -> bool:
        """
        Mark a day as worked from home, or not.

        The toggle only mattered at the moment of punching in; turned on or
        off later it changed nothing and said nothing.
        """
        user_id = str(user_name or "").lower().strip()
        day_key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        result = self.db.execute_update(
            "UPDATE attendance_log SET metadata = %s WHERE user_id = %%s AND day_date = %%s"
            % self._json_merge_sql("metadata"),
            (json.dumps({"wfh": bool(flag)}), user_id, day_key))
        return bool(getattr(result, "changed", result))

    # ----------------------------------------------------------- auto logout
    def _check_and_fix_previous_day(self, user_id, today_date):
        """
        Close every forgotten punch-out before today.

        At the studio's automatic punch-out time - but never earlier than the
        punch-in it closes. It used to close only the latest open day (LIMIT 1),
        so older ones stayed open for ever, and it set 19:30 on a day somebody
        had punched in at 20:15, which read as a 23-hour overnight shift and
        flowed into the totals, overtime and comp-off. A day like that is
        flagged "missing punch-out" for HR instead of being given hours.
        """
        try:
            # The studio's time, from the studio policy - it was a
            # per-machine setting, so machines closed forgotten days at
            # different times.
            from slate.core.domain import leave_policy as _lp
            auto_logout_time = str(_lp.policy().get("auto_logout_time") or "19:30").strip()
            if len(auto_logout_time.split(":")) < 2:
                auto_logout_time = "19:30"
            if len(auto_logout_time.split(":")) == 2:
                auto_logout_time += ":00"

            rows = self.db.execute_query(
                "SELECT id, day_date, punch_in, punch_out, metadata FROM attendance_log "
                "WHERE user_id = %s AND day_date < %s AND punch_out IS NULL "
                "ORDER BY day_date", (user_id, today_date), fetch="all") or []

            for raw in rows:
                row = dict(raw)
                meta = _meta_dict(row.get("metadata"))
                if meta.get("missing_punch_out"):
                    continue
                sessions = self._sessions(row)
                if not sessions:
                    continue        # an out-only day: nothing to close
                last_in = _time_text(sessions[-1]["in"], True)
                if auto_logout_time > last_in:
                    patch = {"auto_logout": True, "cutoff": auto_logout_time}
                    if len(sessions) > 1:
                        sessions[-1]["out"] = auto_logout_time
                        patch["sessions"] = sessions
                    logger.info("Auto-closing missing punch-out for %s on %s at %s",
                                user_id, row.get("day_date"), auto_logout_time)
                    self.db.execute_update(
                        "UPDATE attendance_log SET punch_out = %%s, metadata = %s WHERE id = %%s"
                        % self._json_merge_sql("metadata"),
                        (auto_logout_time, json.dumps(patch), row["id"]))
                else:
                    logger.info("Missing punch-out for %s on %s (in at %s, after the %s cutoff)",
                                user_id, row.get("day_date"), last_in, auto_logout_time)
                    self.db.execute_update(
                        "UPDATE attendance_log SET metadata = %s WHERE id = %%s"
                        % self._json_merge_sql("metadata"),
                        (json.dumps({"missing_punch_out": True}), row["id"]))
        except Exception as e:
            logger.error(f"Auto-logout check failed: {e}")

    # ----------------------------------------------------------------- reading
    @staticmethod
    def _entry(row) -> dict:
        """One stored row as the dict the screens use."""
        meta = _meta_dict(row.get("metadata"))
        sessions = []
        for s in meta.get("sessions") or []:
            if isinstance(s, dict) and s.get("in"):
                sessions.append({"in": _time_text(s.get("in")), "out": _time_text(s.get("out"))})
        return {
            "in": _time_text(row.get("punch_in")),
            "out": _time_text(row.get("punch_out")),
            "pc": row.get("pc_name"),
            "wfh": bool(meta.get("wfh", False)),
            "auto_logout": bool(meta.get("auto_logout", False)),
            "missing_punch_out": bool(meta.get("missing_punch_out", False)),
            "overnight": bool(meta.get("overnight", False)),
            "sessions": sessions if len(sessions) > 1 else [],
            "edited_by": meta.get("edited_by") or "",
            "edit_reason": meta.get("edit_reason") or "",
            "edited_at": meta.get("edited_at") or "",
            "edit_history": list(meta.get("edit_history") or []),
            "admin_edit": bool(meta.get("admin_edit", False)),
            "source": meta.get("source") or "",
        }

    @staticmethod
    def _as_date(d):
        if isinstance(d, datetime.datetime):
            return d.date()
        if isinstance(d, datetime.date):
            return d
        return datetime.date.fromisoformat(str(d)[:10])

    def _rows_between(self, start, end, user_id=None):
        clauses = ["day_date >= %s", "day_date <= %s"]
        params = [start.isoformat(), end.isoformat()]
        if user_id:
            clauses.append("user_id = %s")
            params.append(str(user_id).lower().strip())
        return self.db.execute_query(
            "SELECT user_id, day_date, punch_in, punch_out, pc_name, metadata "
            "FROM attendance_log WHERE " + " AND ".join(clauses),
            tuple(params), fetch="all") or []

    def get_full_month_data(self, year=None, month=None):
        """
        Everybody's month: {user_id: {"01": entry, ...}}.

        A date range rather than EXTRACT/strftime, so the same statement runs
        on both databases and can use the day_date index.
        """
        if not year or not month:
            now = datetime.datetime.now()
            year, month = now.year, now.month
        import calendar
        start = datetime.date(int(year), int(month), 1)
        end = datetime.date(int(year), int(month), calendar.monthrange(int(year), int(month))[1])
        data = {}
        for raw in self._rows_between(start, end):
            row = dict(raw)
            d = self._as_date(row["day_date"])
            data.setdefault(row["user_id"], {})[f"{d.day:02d}"] = self._entry(row)
        return data

    def get_user_month(self, user_name, year, month) -> dict:
        """
        One person's month: {"01": entry, ...}.

        The personal view used to read the whole studio's month every minute
        to show one person - 4,600 rows a minute per workstation at 150 people.
        """
        import calendar
        start = datetime.date(int(year), int(month), 1)
        end = datetime.date(int(year), int(month), calendar.monthrange(int(year), int(month))[1])
        out = {}
        for raw in self._rows_between(start, end, user_name):
            row = dict(raw)
            out[f"{self._as_date(row['day_date']).day:02d}"] = self._entry(row)
        return out

    def get_user_days(self, user_name, start, end) -> dict:
        """One person's days over any range: {date: entry} (the streak crosses months)."""
        out = {}
        for raw in self._rows_between(start, end, user_name):
            row = dict(raw)
            out[self._as_date(row["day_date"])] = self._entry(row)
        return out

    # ------------------------------------------------------------ corrections
    def update_record(self, user_name, year, month, day, in_time, out_time,
                      editor=None, reason="", overnight=False):
        """
        HR correcting a day. Returns (ok, message).

        Who and why are kept with the day, and what it said before, so a
        corrected day can be told from one somebody punched - attendance is
        what payroll is run from. Both times empty is refused: that wrote an
        empty row. Use clear_day to remove a day.
        """
        try:
            target_date = datetime.date(int(year), int(month), int(day))
        except (TypeError, ValueError) as exc:
            return False, str(exc)
        in_text = str(in_time or "").strip()
        out_text = str(out_time or "").strip()
        if not in_text and not out_text:
            return False, "Enter an in time, an out time or both. To remove the day, clear it."
        if editor is not None and not str(reason or "").strip():
            return False, "Say why the day is being corrected."
        before = self.get_day(user_name, target_date) or {}
        meta = {"admin_edit": True,
                "edited_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "overnight": bool(overnight),
                # HR typed the day's times: it is no longer an auto-closed or
                # missing punch-out day. The flags were merged in and kept, so
                # a corrected day still showed (and counted) as Missing punch.
                # 'corrected' keeps the fact that it once was.
                "auto_logout": False, "missing_punch_out": False, "cutoff": None,
                "corrected": True}
        if editor is not None:
            history = list(_meta_dict(before.get("metadata")).get("edit_history") or [])
            history.append({
                "by": str(editor), "at": meta["edited_at"], "reason": str(reason).strip(),
                "was_in": _time_text(before.get("punch_in")),
                "was_out": _time_text(before.get("punch_out")),
            })
            meta.update({"edited_by": str(editor), "edit_reason": str(reason).strip(),
                         "edit_history": history})
        # A correction replaces the day's times, so any sessions it had are
        # replaced by the one HR typed.
        meta["sessions"] = []
        return self.write_day(user_name, target_date, in_text, out_text,
                              pc_name="ADMIN_EDIT", metadata=meta)

    def clear_day(self, user_name, day, editor, reason) -> tuple:
        """
        Remove a day's record (punched by mistake, a test). Returns (ok, message).
        Written to the audit log with what the day said, because the row
        itself is gone.
        """
        if not str(reason or "").strip():
            return False, "Say why the day is being cleared."
        user_id = str(user_name).lower().strip()
        day_key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        before = self.get_day(user_id, day) or {}
        if not before:
            return False, "There is nothing recorded on that day."
        result = self.db.execute_update(
            "DELETE FROM attendance_log WHERE user_id = %s AND day_date = %s", (user_id, day_key))
        if not getattr(result, "changed", result):
            return False, "The day could not be cleared."
        try:
            from ..infra.audit_logger import AuditLogger
            AuditLogger().log_event(
                "ATTENDANCE", str(editor),
                "Cleared %s on %s (was %s-%s): %s" % (
                    user_id, day_key, _time_text(before.get("punch_in")) or "-",
                    _time_text(before.get("punch_out")) or "-", str(reason).strip()))
        except Exception as exc:
            logger.warning("Attendance clear not audited: %s", exc)
        return True, "Cleared"

    def get_day(self, user_name, day):
        """The recorded punches for one person on one day, or None."""
        user_id = str(user_name).lower().strip()
        day_key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        try:
            row = self.db.execute_query(
                "SELECT punch_in, punch_out, pc_name, metadata FROM attendance_log "
                "WHERE user_id = %s AND day_date = %s",
                (user_id, day_key), fetch="one")
        except Exception as exc:
            logger.error("Could not read attendance for %s on %s: %s", user_id, day_key, exc)
            return None
        if not row:
            return None
        return dict(row) if hasattr(row, "keys") else {"punch_in": row[0], "punch_out": row[1]}

    def write_day(self, user_name, target_date, in_time, out_time, pc_name="ADMIN_EDIT",
                  metadata=None):
        """
        Set one person's punches for one day, from wherever they came.

        The admin editor and the biometric import both end here. The record
        says which of them wrote it - pc_name and the metadata - so a day that
        was corrected by hand can be told from one a machine reported.
        """
        try:
            user_id = str(user_name).lower().strip()
            if not user_id:
                return False, "User is required"

            p_in = in_time.strip() if isinstance(in_time, str) and in_time.strip() else None
            p_out = out_time.strip() if isinstance(out_time, str) and out_time.strip() else None
            meta_json = json.dumps(metadata or {})
            day_key = (target_date.isoformat() if hasattr(target_date, "isoformat")
                       else str(target_date))

            if _is_postgres(self.db):
                update_sql = """
                UPDATE attendance_log
                SET punch_in = %s,
                    punch_out = %s,
                    pc_name = %s,
                    metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                WHERE user_id = %s AND day_date = %s
                """
            else:
                update_sql = """
                UPDATE attendance_log
                SET punch_in = %s,
                    punch_out = %s,
                    pc_name = %s,
                    metadata = json_patch(COALESCE(metadata, '{}'), %s)
                WHERE user_id = %s AND day_date = %s
                """

            updated_rows = self.db.execute_query(
                update_sql,
                (p_in, p_out, pc_name, meta_json, user_id, day_key),
                fetch="rowcount",
            )
            if updated_rows and int(updated_rows) > 0:
                return True, "Updated"

            insert_sql = """
            INSERT INTO attendance_log (user_id, day_date, punch_in, punch_out, pc_name, metadata)
            VALUES (%s, %s, %s, %s, %s, %s)
            """
            inserted = self.db.execute_update(
                insert_sql, (user_id, day_key, p_in, p_out, pc_name, meta_json))
            if inserted:
                return True, "Inserted"
            return False, (getattr(inserted, "error", "") or "Database rejected attendance edit")
        except Exception as e:
            logger.error(f"Update failed: {e}")
            return False, str(e)

    def sync_attendance(self, user_id, user_name, action, timestamp=None) -> bool:
        """Sync attendance event to central database."""
        try:
            word = str(action).lower()
            if word in ("logout", "close", "exit"):
                # Closing Slate or signing out never punches anybody out
                # (studio decision); only an explicit "out" does.
                return True
            act = "in" if word in ("login", "in") else "out"
            self.log_action(user_name=user_id or user_name, action=act,
                            automatic=(act == "in"))
            return True
        except Exception as e:
            logger.error(f"sync_attendance error: {e}")
            return False

    def get_team_overview(self):
        """Get overview of today's attendance across the team."""
        try:
            today_str = datetime.date.today().isoformat()
            rows = self.db.execute_query(
                "SELECT user_id, day_date, punch_in, punch_out, pc_name FROM attendance_log WHERE day_date = %s",
                (today_str,),
                fetch="all"
            ) or []
            return rows
        except Exception as e:
            logger.error(f"get_team_overview error: {e}")
            return []

    def is_user_active(self, user_id: str) -> bool:
        """Check if user has punched in and not yet punched out today."""
        try:
            today_str = datetime.date.today().isoformat()
            uid = str(user_id).lower().strip()
            row = self.db.execute_query(
                "SELECT id FROM attendance_log WHERE user_id = %s AND day_date = %s AND punch_out IS NULL",
                (uid, today_str),
                fetch="one"
            )
            return bool(row)
        except Exception as e:
            logger.error(f"is_user_active error: {e}")
            return False
