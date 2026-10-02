"""
Everything the leave module reads and writes.

The views had SQL in them, which is how the balance on one screen and the queue
on another end up disagreeing about the same person. One place computes a
balance, one place decides a day count, and both of them go through the policy
rather than through arithmetic typed into a widget.
"""

from __future__ import annotations

import json
import logging

from datetime import date, datetime

from ..domain import leave_policy as lp

# A database that is down must not look like a studio with no data. The
# manager raises DatabaseUnavailableError precisely so a read cannot quietly
# come back empty; catching it here and returning a fallback puts the fault
# straight back. So it is re-raised, and anything else is logged before the
# fallback is used.
try:
    from .postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    try:
        from ..infra.postgres_manager import DatabaseUnavailableError
    except ImportError:
        class DatabaseUnavailableError(ConnectionError):
            """Fallback when the manager cannot be imported."""

logger = logging.getLogger(__name__)


def as_date(value):
    """
    Whatever the driver handed back, as a date. None when it is not one.

    The two backends do not agree about dates. PostgreSQL returns a date
    object; SQLite returns the text it stored. Every comparison in this module
    used to assume the first, so on the local database the holiday set came
    back empty - each row was dropped for not being a date - and comparing a
    comp-off expiry against today raised TypeError outright.

    One place to convert means the rest of the module can just use dates.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None



class LeaveRepository:
    def __init__(self, db=None):
        if db is None:
            from .database_manager import database_manager
            db = database_manager
        self.db = db

    # ------------------------------------------------------------- reference
    def holidays(self, year: int = None, location: str = None,
                 through_year: int = None) -> set:
        """
        Public holidays, as a set of dates the policy can count against.

        Two things this gets right that the first version did not.

        The year filter is a date range rather than EXTRACT(YEAR FROM ...),
        which only PostgreSQL understands. On the local database that query
        raised, the holidays came back as an empty set, and the sandwich rule
        quietly stopped applying - so leave was under-charged and nobody could
        see why.

        And a holiday belongs to a place. One office's festival is an ordinary
        working day in another, so a request is charged against the holidays of
        the person asking. A row with no location, or "All", applies everywhere.

        Pass through_year for a request that crosses a new year; asking for one
        year and charging a January day against it is how the sandwich rule
        missed the holidays on the far side of the boundary.
        """
        clauses, params = [], []
        if year:
            clauses.append("holiday_date >= %s AND holiday_date < %s")
            params.append(date(int(year), 1, 1))
            params.append(date(int(through_year or year) + 1, 1, 1))
        if location:
            clauses.append("(location IS NULL OR location = '' OR location = 'All' "
                           "OR LOWER(location) = LOWER(%s))")
            params.append(location)

        sql = "SELECT holiday_date FROM holiday_calendar"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)

        try:
            rows = self.db.execute_query(sql, tuple(params) if params else None,
                                         fetch="all")
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("holidays failed")
            return set()

        out = set()
        for row in rows or []:
            value = as_date(row["holiday_date"] if isinstance(row, dict) else row[0])
            if value is not None:
                out.add(value)
        return out

    def holiday_rows(self, year=None) -> list:
        """
        The calendar, newest year last. A year narrows it.

        Without the filter this is every holiday the studio has ever had, which
        is the list HR scrolls past to reach the one they came to fix.
        """
        sql = "SELECT id, holiday_date, name, location FROM holiday_calendar"
        params = None
        if year:
            sql += " WHERE holiday_date >= %s AND holiday_date < %s"
            params = (date(int(year), 1, 1), date(int(year) + 1, 1, 1))
        sql += " ORDER BY holiday_date"

        try:
            rows = self.db.execute_query(sql, params, fetch="all") or []
            return [dict(r) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("holiday_rows failed")
            return []

    def holiday_years(self) -> list:
        """Every year the calendar has anything in, newest first."""
        try:
            rows = self.db.execute_query(
                "SELECT DISTINCT holiday_date FROM holiday_calendar", fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("holiday_years failed")
            return []

        years = set()
        for row in rows:
            value = as_date(row["holiday_date"] if isinstance(row, dict) else row[0])
            if value is not None:
                years.add(value.year)
        return sorted(years, reverse=True)

    def update_holiday(self, holiday_id, day, name, location="All") -> bool:
        """
        Change a holiday that is already there.

        There was no way to do this. A date announced wrongly, or a name typed
        wrongly, had to be removed and added again - two steps, and the removal
        succeeds on its own, so an interruption between them loses the day
        entirely and silently changes what leave costs around it.
        """
        try:
            # The return value matters. Two holidays cannot share a date for
            # the same place, so this can be refused - and reporting success
            # anyway shows the old value back and reads as a lost edit.
            result = self.db.execute_update(
                "UPDATE holiday_calendar SET holiday_date = %s, name = %s, "
                "location = %s WHERE id = %s",
                (day, name, location or "All", holiday_id))
            # A holiday removed by someone else meanwhile matches no row.
            return bool(getattr(result, "changed", result))
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("update_holiday failed")
            return False

    def locations(self) -> list:
        """
        The places this studio actually has people in.

        The holiday screen offered a fixed list of three city names belonging to
        the studio this was written for. Every other studio had to type theirs
        in and hope the spelling matched what the user records say, because a
        holiday only applies to a location whose name matches exactly.
        """
        try:
            rows = self.db.execute_query(
                "SELECT DISTINCT location FROM ut_users "
                "WHERE location IS NOT NULL AND location <> ''", fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("locations failed")
            return []

        out = set()
        for row in rows:
            value = row["location"] if isinstance(row, dict) else row[0]
            text = str(value or "").strip()
            if text and text.lower() != "all":
                out.add(text)
        return sorted(out)

    def add_holiday(self, day, name, location="All") -> bool:
        """
        Add a holiday. False when it was not added - including when there is
        already one on that date for that place.

        The insert says ON CONFLICT DO NOTHING, which the database accepts
        and does nothing with. That used to read as success: the typed name
        was cleared, the list did not change and nobody was told why. What
        was actually inserted is what counts now.
        """
        try:
            result = self.db.execute_update(
                "INSERT INTO holiday_calendar (holiday_date, name, location) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (day, name, location))
            return bool(getattr(result, "changed", result))
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("add_holiday failed")
            return False

    def remove_holiday(self, holiday_id) -> bool:
        try:
            result = self.db.execute_update(
                "DELETE FROM holiday_calendar WHERE id = %s", (holiday_id,))
            return bool(getattr(result, "changed", result))
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("remove_holiday failed")
            return False

    # ------------------------------------------------------------------ people
    def joined_on(self, username: str):
        """When somebody started. Accrual is meaningless without it."""
        try:
            row = self.db.execute_query(
                "SELECT joined_on FROM ut_users WHERE LOWER(username) = LOWER(%s)",
                (username,), fetch="one")
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("joined_on failed")
            return None
        if not row:
            return None
        return as_date(row["joined_on"] if isinstance(row, dict) else row[0])

    def last_day(self, username: str):
        """
        The person's last working day, if they are leaving or have left.
        Leave stops accruing after it (see balance).
        """
        try:
            row = self.db.execute_query(
                "SELECT last_day FROM ut_users WHERE LOWER(username) = LOWER(%s)",
                (username,), fetch="one")
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.debug("last_day not read for %s", username, exc_info=True)
            return None
        if not row:
            return None
        try:
            return as_date(row["last_day"] if isinstance(row, dict) else row[0])
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def reports_to(self, manager: str) -> set:
        """
        The people whose first approval stage is this person, lower-cased.

        A supervisor decides for their own team. Without a reporting line every
        supervisor in the studio saw every request in it, and could approve any
        of them - which is not a queue, it is a free-for-all that happens to be
        sorted by date.
        """
        manager = str(manager or "").strip()
        if not manager:
            return set()
        try:
            rows = self.db.execute_query(
                "SELECT username FROM ut_users WHERE LOWER(reports_to) = LOWER(%s)",
                (manager,), fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("reports_to failed")
            return set()

        out = set()
        for row in rows:
            name = (row["username"] if isinstance(row, dict) else row[0]) or ""
            if str(name).strip():
                out.add(str(name).strip().lower())
        return out

    # ---------------------------------------------------------------- requests
    def for_user(self, username: str) -> list:
        try:
            rows = self.db.execute_query(
                "SELECT * FROM leave_requests WHERE LOWER(user_id) = LOWER(%s) "
                "ORDER BY start_date DESC, id DESC", (username,), fetch="all") or []
            return [dict(r) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("for_user failed")
            return []

    def all_requests(self) -> list:
        try:
            rows = self.db.execute_query(
                "SELECT * FROM leave_requests ORDER BY start_date DESC, id DESC",
                fetch="all") or []
            return [dict(r) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("all_requests failed")
            return []

    def clash(self, username, start, end, ignore_id=None) -> list:
        """
        Requests this person already has that cover any of these days.

        Nothing stopped somebody asking for the same week twice, and both
        requests were held against the balance - so a fortnight disappeared for
        one week away and the only way to notice was to read the list.

        Cancelled and rejected requests are not clashes: those days are free
        again. An approved one is, because it has already been spent - and so
        is one waiting on a withdrawal, which is still approved until HR agree.
        """
        try:
            rows = self.db.execute_query(
                "SELECT id, start_date, end_date, type, status FROM leave_requests "
                "WHERE LOWER(user_id) = LOWER(%s) "
                "AND start_date <= %s AND end_date >= %s",
                (username, end, start), fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("clash failed")
            return []

        out = []
        for row in rows:
            row = dict(row)
            if ignore_id is not None and row.get("id") == ignore_id:
                continue
            if lp.normalise_status(row.get("status")) in lp.LIVE_STATUSES:
                out.append(row)
        return out

    def location_of(self, username: str) -> str:
        """
        Where this person works, so their leave is charged against the right
        holiday calendar. One office's festival is a working day in another.
        """
        try:
            row = self.db.execute_query(
                "SELECT location FROM ut_users WHERE LOWER(username) = LOWER(%s)",
                (username,), fetch="one")
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("location_of failed")
            return ""
        if not row:
            return ""
        value = row["location"] if isinstance(row, dict) else row[0]
        return str(value or "")

    def holidays_for(self, username: str, start: date, end: date = None) -> set:
        """
        The holidays a request by this person is charged against.

        Covers both years when a request crosses a new year - asking for one
        year and charging a January day against it is how the sandwich rule
        missed the holidays on the far side of the boundary.
        """
        end = end or start
        return self.holidays(start.year, self.location_of(username),
                             through_year=end.year)

    # ------------------------------------------------------------- approvers
    def _user_row(self, username) -> dict:
        try:
            row = self.db.execute_query(
                "SELECT * FROM ut_users WHERE LOWER(username) = LOWER(%s)",
                (str(username or "").strip(),), fetch="one")
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.debug("ut_users row not read for %s", username, exc_info=True)
            return {}
        return dict(row) if row else {}

    @staticmethod
    def _roles_of(record) -> list:
        raw = record.get("roles")
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                value = json.loads(raw)
                return [value] if isinstance(value, str) else list(value or [])
            except ValueError:
                return [raw]
        return []

    @staticmethod
    def _is_active_record(record) -> bool:
        value = record.get("active")
        if value is not None and str(value).strip().lower() in ("0", "false", "f", "no"):
            return False
        left = as_date(record.get("last_day"))
        return not (left and left < date.today())

    def approver_kind(self, username) -> str:
        """
        Which stage of the leave chain this person works: "hr", "supervisor"
        or "" (none).

        Mirrors how the Leave tab decides which queue somebody gets, so a
        request is never routed to a stage the named person cannot act on.
        That is how requests sat at "Pending Supervisor" for ever: people
        with no manager, a manager who was an artist, and a manager in HR -
        whose queue only ever offered the HR stage.
        """
        record = self._user_row(username)
        if not record or not self._is_active_record(record):
            return ""
        roles = self._roles_of(record)
        from slate.core.domain import access
        from slate.core.domain.workplace_access import manages_leave
        stored = access._role_permission_lists()
        tabs = set()
        for role in roles:
            tabs.update(stored.get(str(role).strip().lower(), []))
        if manages_leave(roles, tabs):
            return "hr"
        if access.can(roles, "approve_leave"):
            return "supervisor"
        return ""

    def first_stage(self, username) -> dict:
        """
        Where a new request from this person starts.

        {"status": ..., "approver": manager username or "", "note": why a
        stage was skipped}. The supervisor stage is skipped - straight to HR,
        with the reason recorded - when there is nobody who can take it.
        """
        record = self._user_row(username)
        manager = str(record.get("reports_to") or "").strip()
        if not manager:
            return {"status": lp.STATUS_PENDING_HR, "approver": "",
                    "note": "No supervisor recorded, so it went straight to HR."}
        if manager.lower() == str(username or "").strip().lower():
            return {"status": lp.STATUS_PENDING_HR, "approver": "",
                    "note": "Recorded as their own manager, so it went straight to HR."}
        kind = self.approver_kind(manager)
        if kind == "supervisor":
            return {"status": lp.STATUS_PENDING_SUPERVISOR, "approver": manager, "note": ""}
        from slate.core.domain import people
        name = people.display_name(manager, self.db)
        if kind == "hr":
            return {"status": lp.STATUS_PENDING_HR, "approver": "",
                    "note": "%s decides at the HR stage, so it went straight to HR." % name}
        return {"status": lp.STATUS_PENDING_HR, "approver": "",
                "note": "%s cannot approve leave, so it went straight to HR." % name}

    def waiting_on(self, row) -> str:
        """
        Who holds a request up, by name: the supervisor's display name, or
        'HR'. 'No approver set - contact HR' when a request is stuck at the
        supervisor stage with nobody able to take it.
        """
        status = lp.normalise_status(row.get("status"))
        stage = lp.awaiting(status)
        if stage != "Supervisor":
            return stage
        manager = str(self._user_row(row.get("user_id")).get("reports_to") or "").strip()
        if manager and self.approver_kind(manager) == "supervisor":
            from slate.core.domain import people
            return people.display_name(manager, self.db)
        return NO_APPROVER

    # --------------------------------------------------------------- submit
    def submit(self, username, kind, start, end, half_day, reason, rules=None,
               half_day_part=None) -> "Outcome":
        """
        Record a request, with the day count worked out now.

        Storing the charge at submission means a later change to the sandwich
        rule cannot quietly alter what somebody was already deducted.

        Returns an Outcome: truthy when saved, with the new request's id and
        where it went (.detail["status"], ["approver"]); falsy with the reason.
        """
        # Checked here as well as in the dialog. The dialog gives the better
        # message; this is what makes the rule true rather than advisory.
        if self.clash(username, start, end):
            logger.info("Refused an overlapping leave request for %s", username)
            return Outcome(False, "You already have a request covering those days.", "clash")

        # A half day only means anything on a single day. Applied to a range it
        # took half a day off the whole request, so five days away cost 4.5.
        part = str(half_day_part or "").strip().lower() or None
        if part not in (None, "first", "second"):
            part = None
        half_day = bool(half_day or part)
        if half_day and start != end:
            half_day, part = False, None
        if half_day and part is None:
            part = "first"

        charge = lp.days_charged(start, end, self.holidays_for(username, start, end),
                                 rules, half_day=half_day)
        # A request for days nobody works costs nothing and decides nothing.
        # Only the dialog refused it; a Sunday-only request sent another way
        # sat in the queue as "0 days" for somebody to approve.
        if not charge["working_days"]:
            return Outcome(False, "Those dates contain no working days.", "no_working_days")

        route = self.first_stage(username)
        try:
            # A real boolean, and the result checked. This wrote 1 or 0 into a
            # column PostgreSQL created as BOOLEAN, which PostgreSQL refuses;
            # execute_update logged that and returned False, and this returned
            # True anyway. So on a real server no leave request was ever
            # saved, and the screen said it had been. SQLite accepts integers
            # for booleans, which is why every test passed.
            written = self.db.execute_update(
                "INSERT INTO leave_requests "
                "(user_id, start_date, end_date, type, status, half_day, half_day_part, "
                " reason, days_charged, route_note, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (username, start, end, kind, route["status"], half_day, part,
                 reason, charge["total"], route["note"] or None, datetime.now()))
            if not written:
                logger.error("The leave request for %s was not saved.", username)
                return Outcome(False, "The request was not saved.", "not_saved")
            return Outcome(True, "", "sent", request_id=getattr(written, "last_id", None),
                           status=route["status"], approver=route["approver"],
                           note=route["note"], days=charge["total"])
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("submit failed")
            return Outcome(False, "The request was not saved.", "not_saved")

    def grant_project_rest(self, username, start, end, by_whom, reason) -> "Outcome":
        """
        HR grant Project Rest at the end of a project.

        It is a decision, not an entitlement, so nobody requests it: HR record
        it, already approved, with themselves as the approver and a reason.
        """
        if not lp.policy().get("project_rest_enabled"):
            return Outcome(False, "This studio does not grant project rest.", "disabled")
        if not str(reason or "").strip():
            return Outcome(False, "Say what the rest is for.", "no_reason")
        if str(by_whom or "").strip().lower() == str(username or "").strip().lower():
            return Outcome(False, "Somebody else in HR has to grant your own rest.", "own")
        if end < start:
            return Outcome(False, "The end date is before the start date.", "invalid")
        if self.clash(username, start, end):
            return Outcome(False, "They already have leave covering those days.", "clash")
        charge = lp.days_charged(start, end, self.holidays_for(username, start, end))
        if not charge["working_days"]:
            return Outcome(False, "Those dates contain no working days.", "no_working_days")
        now = datetime.now()
        written = self.db.execute_update(
            "INSERT INTO leave_requests "
            "(user_id, start_date, end_date, type, status, half_day, reason, days_charged, "
            " supervisor_by, supervisor_at, hr_by, hr_at, hr_note, decision_note, route_note, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (username, start, end, "Project Rest", lp.STATUS_APPROVED, False, reason,
             charge["total"], by_whom, now, by_whom, now, reason, reason,
             "Granted by HR.", now))
        if not written:
            return Outcome(False, "The grant was not saved.", "not_saved")
        return Outcome(True, "", "granted", request_id=getattr(written, "last_id", None),
                       days=charge["total"])

    def request(self, request_id) -> dict:
        """One request, whole. Empty dict when there is no such row."""
        try:
            row = self.db.execute_query(
                "SELECT * FROM leave_requests WHERE id = %s", (request_id,), fetch="one")
            return dict(row) if row else {}
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("request failed")
            return {}

    # --------------------------------------------------------------- decide
    def decide(self, request_id, stage, approved, by_whom, note="") -> "Outcome":
        """
        Move a request along the supervisor then HR chain.

        The update only lands if the request is still where the decider saw
        it. A supervisor approving a row that had been withdrawn meanwhile
        used to bring it back to life: Cancelled became Pending HR and HR
        could approve leave the person no longer wanted.

        HR may also decide a request still waiting on a supervisor, for the
        supervisor - the way out for requests whose supervisor cannot act -
        and that is recorded as such. Nobody decides their own request.
        """
        try:
            existing = self.request(request_id)
            if not existing:
                return Outcome(False, "That request no longer exists.", "stale")
            requester = str(existing.get("user_id") or "").strip().lower()
            if requester and requester == str(by_whom or "").strip().lower():
                return Outcome(False, "Somebody else has to decide your own request.", "own")

            current = lp.normalise_status(existing.get("status"))
            now = datetime.now()
            note = str(note or "").strip()

            if stage == "Supervisor":
                if current != lp.STATUS_PENDING_SUPERVISOR:
                    return STALE
                new_status = lp.next_status(current, stage, approved)
                written = self.db.execute_update(
                    "UPDATE leave_requests SET status = %s, supervisor_by = %s, "
                    "supervisor_at = %s, supervisor_note = %s, decision_note = %s "
                    "WHERE id = %s AND LOWER(status) = LOWER(%s)",
                    (new_status, by_whom, now, note, note, request_id, current))
            elif current == lp.STATUS_CANCEL_REQUESTED:
                return self._decide_cancellation(existing, approved, by_whom, note)
            elif current == lp.STATUS_PENDING_SUPERVISOR:
                # HR deciding for the supervisor: both stages at once.
                new_status = lp.STATUS_APPROVED if approved else lp.STATUS_REJECTED
                written = self.db.execute_update(
                    "UPDATE leave_requests SET status = %s, supervisor_by = %s, "
                    "supervisor_at = %s, supervisor_note = %s, hr_by = %s, hr_at = %s, "
                    "hr_note = %s, decision_note = %s "
                    "WHERE id = %s AND LOWER(status) = LOWER(%s)",
                    (new_status, by_whom, now, ON_BEHALF_NOTE, by_whom, now, note, note,
                     request_id, current))
            elif current == lp.STATUS_PENDING_HR:
                new_status = lp.next_status(current, stage, approved)
                written = self.db.execute_update(
                    "UPDATE leave_requests SET status = %s, hr_by = %s, hr_at = %s, "
                    "hr_note = %s, decision_note = %s "
                    "WHERE id = %s AND LOWER(status) = LOWER(%s)",
                    (new_status, by_whom, now, note, note, request_id, current))
            else:
                return STALE

            if not written:
                # A decision that did not reach the database is not a decision.
                logger.error("The %s decision on request %s was not saved.", stage, request_id)
                return Outcome(False, "The decision was not saved.", "not_saved")
            if not getattr(written, "changed", True):
                return STALE

            # Comp-off is the one type with its own ledger, and approving it
            # used to leave that ledger untouched: the days were granted and
            # the balance never went down, so the same comp-off day could be
            # spent for ever.
            if new_status == lp.STATUS_APPROVED:
                self.spend_comp_off(request_id)
            return Outcome(True, "", "decided", status=new_status)
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("decide failed")
            return Outcome(False, "The decision was not saved.", "not_saved")

    def cancel(self, request_id, username) -> "Outcome":
        """
        Withdraw your own request, while it is still waiting on somebody.

        There was no way to do this. A request sent by mistake held days
        against the balance until an approver happened to reject it, and the
        only way to explain the missing days was to read the list.

        Only the person who asked can cancel, and only while it is pending -
        approved leave is withdrawn with request_cancellation, which HR agree.
        """
        row = self.request(request_id)
        if not row:
            return Outcome(False, "That request no longer exists.", "stale")
        if str(row.get("user_id") or "").strip().lower() != str(username or "").strip().lower():
            logger.warning("Refused to cancel request %s: it is not %s's",
                           request_id, username)
            return Outcome(False, "Only the person who asked can withdraw a request.", "not_yours")
        current = lp.normalise_status(row.get("status"))
        if current not in lp.PENDING_STATUSES:
            return STALE

        try:
            written = self.db.execute_update(
                "UPDATE leave_requests SET status = %s, cancelled_by = %s, cancelled_at = %s "
                "WHERE id = %s AND LOWER(status) = LOWER(%s)",
                (lp.STATUS_CANCELLED, username, datetime.now(), request_id, current))
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("cancel failed")
            return Outcome(False, "The request was not withdrawn.", "not_saved")
        if not written:
            return Outcome(False, "The request was not withdrawn.", "not_saved")
        if not getattr(written, "changed", True):
            return STALE
        return Outcome(True, "", "cancelled")

    # ------------------------------------------------------------ withdrawal
    def request_cancellation(self, request_id, username, reason="") -> "Outcome":
        """
        Ask to withdraw approved leave that has not started yet.

        Approved leave could not be undone at all, so a change of plan still
        cost the days. The request goes to HR as "Cancellation requested"; the
        days stay deducted until HR agree (decide), then come back.
        """
        row = self.request(request_id)
        if not row:
            return Outcome(False, "That request no longer exists.", "stale")
        if str(row.get("user_id") or "").strip().lower() != str(username or "").strip().lower():
            return Outcome(False, "Only the person who asked can withdraw it.", "not_yours")
        current = lp.normalise_status(row.get("status"))
        if current in lp.PENDING_STATUSES:
            # Nothing has been granted yet - simply withdraw it.
            return self.cancel(request_id, username)
        if current != lp.STATUS_APPROVED:
            return STALE
        start = as_date(row.get("start_date"))
        if start is not None and start <= date.today():
            return Outcome(False, "Leave that has already started cannot be withdrawn here - "
                                  "ask HR to correct it.", "started")
        written = self.db.execute_update(
            "UPDATE leave_requests SET status = %s, cancel_reason = %s, cancel_requested_at = %s "
            "WHERE id = %s AND LOWER(status) = LOWER(%s)",
            (lp.STATUS_CANCEL_REQUESTED, str(reason or "").strip() or None, datetime.now(),
             request_id, current))
        if not written:
            return Outcome(False, "The withdrawal was not saved.", "not_saved")
        if not getattr(written, "changed", True):
            return STALE
        return Outcome(True, "", "cancel_requested")

    def _decide_cancellation(self, row, approved, by_whom, note) -> "Outcome":
        """HR agree to a withdrawal (days back) or refuse it (leave stands)."""
        request_id = row.get("id")
        now = datetime.now()
        if approved:
            written = self.db.execute_update(
                "UPDATE leave_requests SET status = %s, cancelled_by = %s, cancelled_at = %s, "
                "hr_note = %s, decision_note = %s "
                "WHERE id = %s AND LOWER(status) = LOWER(%s)",
                (lp.STATUS_CANCELLED, by_whom, now, note or "Withdrawal agreed.",
                 note or "Withdrawal agreed.", request_id, lp.STATUS_CANCEL_REQUESTED))
        else:
            text = "Withdrawal refused" + (": " + note if note else ".")
            written = self.db.execute_update(
                "UPDATE leave_requests SET status = %s, hr_note = %s, decision_note = %s "
                "WHERE id = %s AND LOWER(status) = LOWER(%s)",
                (lp.STATUS_APPROVED, text, text, request_id, lp.STATUS_CANCEL_REQUESTED))
        if not written:
            return Outcome(False, "The decision was not saved.", "not_saved")
        if not getattr(written, "changed", True):
            return STALE
        if approved:
            self.unspend_comp_off(request_id)
        return Outcome(True, "", "decided",
                       status=lp.STATUS_CANCELLED if approved else lp.STATUS_APPROVED)

    def revoke(self, request_id, by_whom, reason) -> "Outcome":
        """
        HR take back leave they approved - plans changed, or it was approved
        by mistake. A reason is required and the person sees it; the days
        (and any comp-off spent on it) go back.
        """
        reason = str(reason or "").strip()
        if not reason:
            return Outcome(False, "Say why - the person will see it.", "no_reason")
        row = self.request(request_id)
        if not row:
            return Outcome(False, "That request no longer exists.", "stale")
        if str(row.get("user_id") or "").strip().lower() == str(by_whom or "").strip().lower():
            return Outcome(False, "Somebody else has to change your own leave.", "own")
        current = lp.normalise_status(row.get("status"))
        if current not in lp.GRANTED_STATUSES:
            return STALE
        written = self.db.execute_update(
            "UPDATE leave_requests SET status = %s, cancelled_by = %s, cancelled_at = %s, "
            "cancel_reason = %s, hr_note = %s, decision_note = %s "
            "WHERE id = %s AND LOWER(status) = LOWER(%s)",
            (lp.STATUS_CANCELLED, by_whom, datetime.now(), reason,
             "Revoked by HR: " + reason, "Revoked by HR: " + reason, request_id, current))
        if not written:
            return Outcome(False, "The change was not saved.", "not_saved")
        if not getattr(written, "changed", True):
            return STALE
        self.unspend_comp_off(request_id)
        return Outcome(True, "", "revoked")

    # ------------------------------------------------------------ recharging
    def recharge_pending(self, start, end, rules=None) -> int:
        """
        Re-cost requests still waiting for a decision that cover these days.

        A request's charge is stored when it is sent. Removing (or adding) a
        holiday changed what those days cost, but the pending requests kept
        the old figure - and would have been approved at it.
        """
        try:
            rows = self.db.execute_query(
                "SELECT * FROM leave_requests WHERE start_date <= %s AND end_date >= %s",
                (end, start), fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("recharge_pending failed")
            return 0
        changed = 0
        for row in rows:
            row = dict(row)
            if lp.normalise_status(row.get("status")) not in lp.PENDING_STATUSES:
                continue
            first, last = as_date(row.get("start_date")), as_date(row.get("end_date"))
            if first is None or last is None:
                continue
            charge = lp.days_charged(first, last, self.holidays_for(row.get("user_id"), first, last),
                                     rules, half_day=bool(row.get("half_day")) and first == last)
            old = float(row.get("days_charged") or 0)
            if abs(old - charge["total"]) < 1e-9:
                continue
            if self.db.execute_update(
                    "UPDATE leave_requests SET days_charged = %s WHERE id = %s",
                    (charge["total"], row.get("id"))):
                changed += 1
        return changed

    # --------------------------------------------------------------- the team
    def also_away(self, row) -> list:
        """
        Other people from the requester's team with leave over the same days -
        approved, or still waiting. What an approver needs before saying yes.

        The team is everybody with the same manager, and the manager.
        Somebody with no manager is compared with people of the same
        department (job title).
        """
        requester = str(row.get("user_id") or "").strip()
        start, end = as_date(row.get("start_date")), as_date(row.get("end_date"))
        if not requester or start is None or end is None:
            return []
        record = self._user_row(requester)
        manager = str(record.get("reports_to") or "").strip()
        try:
            if manager:
                team_rows = self.db.execute_query(
                    "SELECT username FROM ut_users WHERE LOWER(reports_to) = LOWER(%s) "
                    "OR LOWER(username) = LOWER(%s)", (manager, manager), fetch="all") or []
            else:
                team_rows = self.db.execute_query(
                    "SELECT username FROM ut_users WHERE COALESCE(job_title, '') <> '' "
                    "AND LOWER(job_title) = LOWER(%s)", (record.get("job_title") or "",),
                    fetch="all") or []
            team = {str(dict(r)["username"]).strip().lower() for r in team_rows}
            team.discard(requester.lower())
            if not team:
                return []
            rows = self.db.execute_query(
                "SELECT * FROM leave_requests WHERE start_date <= %s AND end_date >= %s "
                "ORDER BY start_date", (end, start), fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("also_away failed")
            return []
        out = []
        for other in rows:
            other = dict(other)
            if str(other.get("user_id") or "").strip().lower() not in team:
                continue
            if lp.normalise_status(other.get("status")) not in lp.LIVE_STATUSES:
                continue
            out.append(other)
        return out

    # ---------------------------------------------------------------- comp-off
    def _comp_off_ledger(self, username) -> list:
        try:
            rows = self.db.execute_query(
                "SELECT id, days, consumed, expires_on FROM comp_off_ledger "
                "WHERE LOWER(user_id) = LOWER(%s) "
                "ORDER BY CASE WHEN expires_on IS NULL THEN 1 ELSE 0 END, expires_on, id",
                (username,), fetch="all") or []
            return [dict(r) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("comp-off ledger not read")
            return []

    def comp_off_balance(self, username: str, on: date = None) -> float:
        """
        Unspent comp-off still valid on a day (today unless given).

        A comp-off day is valid until it expires. Whether it can pay for a
        day of leave depends on the day of that leave, not on the day somebody
        got round to approving it.
        """
        on = on or date.today()
        total = 0.0
        for row in self._comp_off_ledger(username):
            expires = as_date(row.get("expires_on"))
            if expires and expires < on:
                continue
            total += float(row.get("days") or 0) - float(row.get("consumed") or 0)
        return max(0.0, total)

    def spend_comp_off(self, request_id) -> float:
        """
        Draw an approved Comp Off request down against the ledger.

        Oldest first, so the day closest to expiring is the one used up. Days
        earned expire; days held back for no reason are days lost.

        What counts is whether a ledger day was still valid on the day of the
        leave. This compared expiry with the day of approval instead, so a
        comp-off day earned in July and taken in September was skipped as
        "expired" if HR approved it a fortnight late - and the next one along
        was spent in its place.

        Each spend is recorded against the request (comp_off_spends) so a
        withdrawn or revoked request gives back exactly what it took.

        Returns how much was actually spent, which can be less than the request
        if the ledger is short - the shortfall is logged rather than invented,
        because a negative comp-off balance is a conversation, not a number.
        """
        row = self.request(request_id)
        if not row:
            return 0.0
        if str(row.get("type") or "").strip().title() != "Comp Off":
            return 0.0

        username = row.get("user_id")
        wanted = float(row.get("days_charged") or 0)
        if wanted <= 0:
            return 0.0
        taken_on = as_date(row.get("start_date")) or date.today()

        spent = 0.0
        for entry in self._comp_off_ledger(username):
            if spent >= wanted:
                break
            expires = as_date(entry.get("expires_on"))
            if expires and expires < taken_on:
                continue

            available = float(entry.get("days") or 0) - float(entry.get("consumed") or 0)
            if available <= 0:
                continue

            take = min(available, wanted - spent)
            try:
                written = self.db.execute_update(
                    "UPDATE comp_off_ledger SET consumed = %s WHERE id = %s",
                    (float(entry.get("consumed") or 0) + take, entry.get("id")))
                if not written:
                    continue
                spent += take
                self.db.execute_update(
                    "INSERT INTO comp_off_spends (request_id, ledger_id, days) VALUES (%s, %s, %s)",
                    (request_id, entry.get("id"), take))
            except DatabaseUnavailableError:
                raise
            except Exception:
                logger.exception("spend_comp_off failed to write the ledger")

        if spent < wanted:
            logger.warning(
                "Comp Off request %s approved for %g day(s) but only %g were in "
                "%s's ledger", request_id, wanted, spent, username)
        return spent

    def unspend_comp_off(self, request_id) -> float:
        """Give back what a cancelled Comp Off request took from the ledger."""
        try:
            rows = self.db.execute_query(
                "SELECT id, ledger_id, days FROM comp_off_spends WHERE request_id = %s",
                (request_id,), fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("unspend_comp_off failed")
            return 0.0
        returned = 0.0
        for spend in rows:
            spend = dict(spend)
            days = float(spend.get("days") or 0)
            written = self.db.execute_update(
                "UPDATE comp_off_ledger SET consumed = CASE WHEN consumed - %s < 0 THEN 0 "
                "ELSE consumed - %s END WHERE id = %s",
                (days, days, spend.get("ledger_id")))
            if written:
                returned += days
                self.db.execute_update("DELETE FROM comp_off_spends WHERE id = %s",
                                       (spend.get("id"),))
        return returned

    def credit_comp_off(self, username, earned_on, days, reason, rules=None) -> bool:
        if days <= 0:
            return False
        try:
            return bool(self.db.execute_update(
                "INSERT INTO comp_off_ledger "
                "(user_id, earned_on, days, reason, expires_on, source) "
                "VALUES (%s, %s, %s, %s, %s, 'attendance')",
                (username, earned_on, days, reason,
                 lp.comp_off_expires(earned_on, rules))))
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("credit_comp_off failed")
            return False

    # --------------------------------------------------------------- year end
    def last_close(self, username: str) -> dict:
        """
        The most recent year drawn under for this person, if any.

        Everything after it is measured from that line rather than from the
        joining date - which is the only way a carry-forward cap can ever bite.
        """
        try:
            row = self.db.execute_query(
                "SELECT leave_year, carried, lapsed, closing_balance FROM leave_year_close "
                "WHERE LOWER(user_id) = LOWER(%s) ORDER BY leave_year DESC LIMIT 1",
                (username,), fetch="one")
            return dict(row) if row else {}
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("last_close failed")
            return {}

    def closes(self, year: int = None) -> list:
        try:
            if year is None:
                rows = self.db.execute_query(
                    "SELECT user_id, leave_year, closing_balance, carried, lapsed, "
                    "       closed_on, closed_by FROM leave_year_close "
                    "ORDER BY leave_year DESC, user_id", fetch="all") or []
            else:
                rows = self.db.execute_query(
                    "SELECT user_id, leave_year, closing_balance, carried, lapsed, "
                    "       closed_on, closed_by FROM leave_year_close "
                    "WHERE leave_year = %s ORDER BY user_id", (year,), fetch="all") or []
            return [dict(r) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("closes failed")
            return []

    def last_closed_year(self):
        """The latest leave year the studio has closed, or None."""
        try:
            row = self.db.execute_query(
                "SELECT MAX(leave_year) AS latest FROM leave_year_close", fetch="one")
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("last_closed_year failed")
            return None
        value = (dict(row) or {}).get("latest") if row else None
        return int(value) if value is not None else None

    def close_refusal(self, year: int, today: date = None) -> str:
        """
        Why this year cannot be closed now, or "" when it can.

        Two rules. A year is closed once it has finished - closing the year
        you are in lapses leave people have not had the chance to take. And
        years close in order: closing 2026 before 2025 made 2026 the line every
        balance is measured from, so 2025 could never affect anything again.
        """
        today = today or date.today()
        year = int(year)
        if year >= today.year:
            return "%d has not finished yet. A leave year is closed after it ends." % year
        latest = self.last_closed_year()
        if latest is not None:
            if year <= latest:
                if year == latest:
                    return ""      # finishing a year some people were already closed for
                return "%d is closed already, and so is everything before it." % latest
            if year > latest + 1:
                return "Close %d first - years are closed in order." % (latest + 1)
        return ""

    def unclosed_year(self, today: date = None):
        """
        The finished leave year waiting to be closed (the one HR should close
        next), or None when there is none.
        """
        today = today or date.today()
        latest = self.last_closed_year()
        wanted = (latest + 1) if latest is not None else today.year - 1
        return wanted if wanted < today.year else None

    @staticmethod
    def _is_service(record) -> bool:
        from slate.core.domain.people import SERVICE_USERNAMES
        name = str(record.get("username") or "").strip().lower()
        flag = record.get("is_service")
        if flag is not None and str(flag).strip().lower() not in ("", "0", "false", "f", "no"):
            return True
        return name in SERVICE_USERNAMES

    def preview_close(self, year: int, rules=None, include_no_joining: bool = False) -> list:
        """
        What closing a year would do to everybody, without doing it.

        HR see the working before anything is written. A year end that silently
        deletes leave people believed they had is the fastest way to lose their
        trust in the whole module.

        Who is left out: accounts that are not people (admin, tester, anything
        marked as a service account), people who had left or been deactivated
        before the year began, and people who joined after it ended. Somebody
        with no joining date is listed with status "no_joining_date" and is not
        closed unless HR include them: their balance would otherwise be made
        up from 1 January.
        """
        try:
            # SELECT *: last_day / deactivated_on may not exist on an old table.
            rows = self.db.execute_query(
                "SELECT * FROM ut_users ORDER BY username", fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("preview_close failed")
            return []

        as_of = date(year, 12, 31)
        year_start = date(year, 1, 1)
        out = []
        for row in rows:
            record = dict(row) if hasattr(row, "keys") else {"username": row[0]}
            name = record.get("username") or ""
            if not name or self._is_service(record):
                continue
            # Somebody who had left (or was deactivated) before the year began
            # has nothing to close: their balance stopped with their last day.
            # They were listed - and written - every year for ever.
            ended = [as_date(record.get(k)) for k in ("last_day", "deactivated_on")]
            ended = [d for d in ended if d]
            if ended and min(ended) < year_start:
                continue
            active = record.get("active")
            if active is not None and str(active).strip().lower() in ("0", "false", "f") \
                    and not ended:
                continue
            joined = as_date(record.get("joined_on"))
            if joined and joined > as_of:
                continue
            closing = self.balance(name, rules, as_of)["available"]
            split = lp.carry_forward(closing, rules)
            out.append({
                "user_id": name,
                "display_name": str(record.get("display_name") or "").strip() or name,
                "closing_balance": round(closing, 2),
                "carried": round(split["carried"], 2),
                "lapsed": round(split["lapsed"], 2),
                "status": "ok" if joined else "no_joining_date",
            })
        return out

    def close_year(self, year: int, by_whom: str, rules=None,
                   include_no_joining: bool = False, today: date = None) -> dict:
        """
        Draw the line under a leave year.

        Idempotent by (user, year) - running it twice does not lapse anybody's
        leave a second time. Refused (dict["refused"]) for a year that has not
        finished or is out of order (close_refusal).
        """
        refusal = self.close_refusal(year, today)
        if refusal:
            return {"closed": 0, "skipped": 0, "no_joining_date": 0, "refused": refusal}
        already = {str(r["user_id"]).lower() for r in self.closes(year)}
        written = 0
        no_date = 0
        for entry in self.preview_close(year, rules):
            if entry["user_id"].lower() in already:
                continue
            if entry.get("status") == "no_joining_date" and not include_no_joining:
                no_date += 1
                continue
            try:
                saved = self.db.execute_update(
                    "INSERT INTO leave_year_close "
                    "(user_id, leave_year, closing_balance, carried, lapsed, closed_on, closed_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (entry["user_id"], year, entry["closing_balance"],
                     entry["carried"], entry["lapsed"], date.today(), by_whom))
                if saved:
                    written += 1
                else:
                    logger.error("Year close for %s was not saved.", entry["user_id"])
            except DatabaseUnavailableError:
                raise
            except Exception:
                logger.exception("close_year failed")
                continue
        return {"closed": written, "skipped": len(already), "no_joining_date": no_date,
                "refused": ""}

    # ----------------------------------------------------------------- balance
    def balance(self, username: str, rules=None, as_of: date = None) -> dict:
        """
        What this person has, has used, and has pending.

        Accrued types draw from one earned pool - which is how a two-days-a-month
        policy actually works. Comp-off comes from its own ledger, and the rest
        are granted or unpaid and so have no balance to speak of.

        Two ways to ask:

          as_of left out - the live balance. Every request of the current
          leave year onwards is held against it, including leave booked for
          next month: it skipped anything that had not started yet, so a
          nine-day December request left "Available" untouched and people
          could book far more than they had.

          as_of given - the balance at the end of that day, as the year end
          sees it. Requests starting after it have not happened yet; without
          that cut-off a year-end preview on 31 December counted January's
          leave against the year being closed, so the closing balance was
          short and the shortfall lapsed.

        Leave years nobody closed still have the carry-forward cap applied:
        each finished year is walked with the same arithmetic as the year end
        (earned - taken, at most the cap carried). Accrual from the joining
        date otherwise grew for ever - 180 days for somebody who joined in
        2019 - until HR happened to run Year end.
        """
        live = as_of is None
        today = date.today()
        as_of = as_of or today
        requests = self.for_user(username)
        joined = self.joined_on(username)
        left = self.last_day(username)
        pool = set(lp.ACCRUED_TYPES)

        def start_of(row):
            return as_date(row.get("start_date"))

        def granted(row):
            return lp.normalise_status(row.get("status")) in lp.GRANTED_STATUSES

        def pending(row):
            return lp.normalise_status(row.get("status")) in lp.PENDING_STATUSES

        def days_of(row):
            charge = row.get("days_charged")
            return float(charge) if charge is not None else 0.0

        def kind_of(row):
            return (row.get("type") or "Casual").title()

        # Where this person's count starts. Once a year has been closed, the
        # opening balance is whatever survived the cap and accrual restarts
        # from the new year - so leave taken before the line is already
        # accounted for and must not be deducted twice.
        close = self.last_close(username)
        opening = float(close.get("carried") or 0.0) if close else 0.0
        first_open_year = (int(close["leave_year"]) + 1) if close else (
            joined.year if joined else as_of.year)

        # Finished years nobody closed: the year end's arithmetic, applied.
        walked = []
        for year in range(first_open_year, as_of.year):
            year_start, year_end = date(year, 1, 1), date(year, 12, 31)
            earned = lp.accrued_by(year_end, max(year_start, joined) if joined else year_start,
                                   rules, left=left)
            taken = sum(days_of(r) for r in requests
                        if kind_of(r) in pool and (granted(r) or pending(r))
                        and start_of(r) and year_start <= start_of(r) <= year_end)
            closing = opening + earned - taken
            opening = lp.carry_forward(closing, rules)["carried"]
            walked.append(year)
        since = date(max(first_open_year, as_of.year), 1, 1)
        if close or walked:
            counting_from = since
        else:
            counting_from = None

        used, pending_days, booked = {}, {}, {}
        for row in requests:
            start = start_of(row)
            if start and start < since:
                continue
            if not live and start and start > as_of:
                continue
            kind = kind_of(row)
            days = days_of(row)
            if granted(row):
                used[kind] = used.get(kind, 0.0) + days
                if start and start > today:
                    booked[kind] = booked.get(kind, 0.0) + days
            elif pending(row):
                pending_days[kind] = pending_days.get(kind, 0.0) + days

        earned_from = since if (close or walked) else joined
        if earned_from and joined and joined > earned_from:
            earned_from = joined
        # Nothing is earned after somebody's last day. It used to keep
        # crediting a leaver every month for as long as the account existed.
        accrued = opening + lp.accrued_by(as_of, earned_from, rules, left=left)
        spent = sum(used.get(k, 0.0) for k in pool)
        held = sum(pending_days.get(k, 0.0) for k in pool)

        # Comp-off: the ledger, less comp-off requests still waiting, so two
        # pending requests cannot both be granted against one earned day.
        comp_off = self.comp_off_balance(username)
        comp_off_pending = pending_days.get("Comp Off", 0.0)

        return {
            "accrued": accrued,
            "opening": opening,
            "counting_from": counting_from,
            "years_walked": walked,
            "joined_on": joined,
            "used": used,
            "pending": pending_days,
            "booked": booked,
            "booked_from_pool": sum(booked.get(k, 0.0) for k in pool),
            "spent_from_pool": spent,
            "pending_from_pool": held,
            # Signed: an overdrawn balance is shown as overdrawn, not as 0.
            "available": accrued - spent - held,
            "comp_off": comp_off,
            "comp_off_pending": comp_off_pending,
            "comp_off_available": max(0.0, comp_off - comp_off_pending),
            "as_of": as_of,
        }

    def taken_by_type(self, username: str, year: int = None) -> dict:
        """Days granted this leave year per type ('sick days taken'), for reports."""
        year = year or date.today().year
        out = {}
        for row in self.for_user(username):
            start = as_date(row.get("start_date"))
            if not start or start.year != year:
                continue
            if lp.normalise_status(row.get("status")) not in lp.GRANTED_STATUSES:
                continue
            kind = (row.get("type") or "Casual").title()
            out[kind] = out.get(kind, 0.0) + float(row.get("days_charged") or 0)
        return out


# ------------------------------------------------------------------ outcomes

class Outcome:
    """
    What happened to a leave action. Truthy when it was done.

    A bare False could not say *why* - an approval refused because the request
    had been withdrawn meanwhile looked exactly like a database fault - so
    every action returns one of these: .reason is a sentence for the person,
    .code a word for the code ("stale", "own", "clash", "not_saved" ...).
    """

    __slots__ = ("ok", "reason", "code", "request_id", "detail")

    def __init__(self, ok, reason="", code="", request_id=None, **detail):
        self.ok = bool(ok)
        self.reason = reason
        self.code = code
        self.request_id = request_id
        self.detail = detail

    def __bool__(self):
        return self.ok

    def __eq__(self, other):
        if isinstance(other, bool):
            return self.ok is other
        return NotImplemented

    __hash__ = None

    def __repr__(self):
        return "Outcome(%s, %r, %r)" % (self.ok, self.code, self.reason)


STALE = Outcome(False, "Already decided or withdrawn - the list has been refreshed.", "stale")

ON_BEHALF_NOTE = "Decided by HR on behalf of the supervisor."

NO_APPROVER = "No approver set - contact HR"
