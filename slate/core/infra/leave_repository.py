"""
Everything the leave module reads and writes.

The views had SQL in them, which is how the balance on one screen and the queue
on another end up disagreeing about the same person. One place computes a
balance, one place decides a day count, and both of them go through the policy
rather than through arithmetic typed into a widget.
"""

from __future__ import annotations

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
            return bool(self.db.execute_update(
                "UPDATE holiday_calendar SET holiday_date = %s, name = %s, "
                "location = %s WHERE id = %s",
                (day, name, location or "All", holiday_id)))
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
        try:
            return bool(self.db.execute_update(
                "INSERT INTO holiday_calendar (holiday_date, name, location) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (day, name, location)))
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("add_holiday failed")
            return False

    def remove_holiday(self, holiday_id) -> bool:
        try:
            return bool(self.db.execute_update(
                "DELETE FROM holiday_calendar WHERE id = %s", (holiday_id,)))
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
                "SELECT id, user_id, start_date, end_date, type, status, reason, "
                "       half_day, days_charged, supervisor_by, hr_by, decision_note "
                "FROM leave_requests WHERE LOWER(user_id) = LOWER(%s) "
                "ORDER BY start_date DESC", (username,), fetch="all") or []
            return [dict(r) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("for_user failed")
            return []

    def all_requests(self) -> list:
        try:
            rows = self.db.execute_query(
                "SELECT id, user_id, start_date, end_date, type, status, reason, "
                "       half_day, days_charged, supervisor_by, hr_by, decision_note "
                "FROM leave_requests ORDER BY start_date DESC", fetch="all") or []
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
        again. An approved one is, because it has already been spent.
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

        live = (lp.STATUS_PENDING_SUPERVISOR, lp.STATUS_PENDING_HR, lp.STATUS_APPROVED)
        out = []
        for row in rows:
            row = dict(row)
            if ignore_id is not None and row.get("id") == ignore_id:
                continue
            if lp.normalise_status(row.get("status")) in live:
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

    def submit(self, username, kind, start, end, half_day, reason, rules=None) -> bool:
        """
        Record a request, with the day count worked out now.

        Storing the charge at submission means a later change to the sandwich
        rule cannot quietly alter what somebody was already deducted.
        """
        # Checked here as well as in the dialog. The dialog gives the better
        # message; this is what makes the rule true rather than advisory.
        if self.clash(username, start, end):
            logger.info("Refused an overlapping leave request for %s", username)
            return False

        # A half day only means anything on a single day. Applied to a range it
        # took half a day off the whole request, so five days away cost 4.5.
        if half_day and start != end:
            half_day = False

        charge = lp.days_charged(start, end, self.holidays_for(username, start, end),
                                 rules, half_day=bool(half_day))
        try:
            # A real boolean, and the result checked. This wrote 1 or 0 into a
            # column PostgreSQL created as BOOLEAN, which PostgreSQL refuses;
            # execute_update logged that and returned False, and this returned
            # True anyway. So on a real server no leave request was ever
            # saved, and the screen said it had been. SQLite accepts integers
            # for booleans, which is why every test passed.
            written = self.db.execute_update(
                "INSERT INTO leave_requests "
                "(user_id, start_date, end_date, type, status, half_day, reason, days_charged) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (username, start, end, kind, lp.STATUS_PENDING_SUPERVISOR,
                 bool(half_day), reason, charge["total"]))
            if not written:
                logger.error("The leave request for %s was not saved.", username)
            return bool(written)
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("submit failed")
            return False

    def request(self, request_id) -> dict:
        """One request, whole. Empty dict when there is no such row."""
        try:
            row = self.db.execute_query(
                "SELECT id, user_id, start_date, end_date, type, status, reason, "
                "       half_day, days_charged FROM leave_requests WHERE id = %s",
                (request_id,), fetch="one")
            return dict(row) if row else {}
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("request failed")
            return {}

    def decide(self, request_id, stage, approved, by_whom, note="") -> bool:
        """Move a request along the supervisor then HR chain."""
        try:
            existing = self.request(request_id)
            current = existing.get("status") or ""
            new_status = lp.next_status(current, stage, approved)

            if stage == "Supervisor":
                written = self.db.execute_update(
                    "UPDATE leave_requests SET status = %s, supervisor_by = %s, "
                    "supervisor_at = %s, decision_note = %s WHERE id = %s",
                    (new_status, by_whom, datetime.now(), note, request_id))
            else:
                written = self.db.execute_update(
                    "UPDATE leave_requests SET status = %s, hr_by = %s, "
                    "hr_at = %s, decision_note = %s WHERE id = %s",
                    (new_status, by_whom, datetime.now(), note, request_id))
            if not written:
                # A decision that did not reach the database is not a decision.
                logger.error("The %s decision on request %s was not saved.", stage, request_id)
                return False

            # Comp-off is the one type with its own ledger, and approving it
            # used to leave that ledger untouched: the days were granted and
            # the balance never went down, so the same comp-off day could be
            # spent for ever.
            if new_status == lp.STATUS_APPROVED:
                self.spend_comp_off(request_id)
            return True
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("decide failed")
            return False

    def cancel(self, request_id, username) -> bool:
        """
        Withdraw your own request, while it is still waiting on somebody.

        There was no way to do this. A request sent by mistake held days
        against the balance until an approver happened to reject it, and the
        only way to explain the missing days was to read the list.

        Only the person who asked can cancel, and only while it is pending -
        an approved request is a decision somebody else made.
        """
        row = self.request(request_id)
        if not row:
            return False
        if str(row.get("user_id") or "").strip().lower() != str(username or "").strip().lower():
            logger.warning("Refused to cancel request %s: it is not %s's",
                           request_id, username)
            return False
        if lp.normalise_status(row.get("status")) not in (
                lp.STATUS_PENDING_SUPERVISOR, lp.STATUS_PENDING_HR):
            return False

        try:
            return bool(self.db.execute_update(
                "UPDATE leave_requests SET status = %s WHERE id = %s",
                (lp.STATUS_CANCELLED, request_id)))
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("cancel failed")
            return False

    # ---------------------------------------------------------------- comp-off
    def comp_off_balance(self, username: str) -> float:
        """Unspent comp-off that has not expired."""
        try:
            rows = self.db.execute_query(
                "SELECT days, consumed, expires_on FROM comp_off_ledger "
                "WHERE LOWER(user_id) = LOWER(%s)", (username,), fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("comp_off_balance failed")
            return 0.0

        today = date.today()
        total = 0.0
        for row in rows:
            row = dict(row)
            expires = as_date(row.get("expires_on"))
            if expires and expires < today:
                continue
            total += float(row.get("days") or 0) - float(row.get("consumed") or 0)
        return max(0.0, total)

    def spend_comp_off(self, request_id) -> float:
        """
        Draw an approved Comp Off request down against the ledger.

        Oldest first, so the day closest to expiring is the one used up. Days
        earned expire; days held back for no reason are days lost.

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

        try:
            rows = self.db.execute_query(
                "SELECT id, days, consumed, expires_on FROM comp_off_ledger "
                "WHERE LOWER(user_id) = LOWER(%s) "
                "ORDER BY CASE WHEN expires_on IS NULL THEN 1 ELSE 0 END, "
                "expires_on, id",
                (username,), fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("spend_comp_off failed to read the ledger")
            return 0.0

        today = date.today()
        spent = 0.0
        for entry in rows:
            if spent >= wanted:
                break
            entry = dict(entry)
            expires = as_date(entry.get("expires_on"))
            if expires and expires < today:
                continue

            available = float(entry.get("days") or 0) - float(entry.get("consumed") or 0)
            if available <= 0:
                continue

            take = min(available, wanted - spent)
            try:
                self.db.execute_update(
                    "UPDATE comp_off_ledger SET consumed = %s WHERE id = %s",
                    (float(entry.get("consumed") or 0) + take, entry.get("id")))
                spent += take
            except DatabaseUnavailableError:
                raise
            except Exception:
                logger.exception("spend_comp_off failed to write the ledger")

        if spent < wanted:
            logger.warning(
                "Comp Off request %s approved for %g day(s) but only %g were in "
                "%s's ledger", request_id, wanted, spent, username)
        return spent

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

    def preview_close(self, year: int, rules=None) -> list:
        """
        What closing a year would do to everybody, without doing it.

        HR see the working before anything is written. A year end that silently
        deletes leave people believed they had is the fastest way to lose their
        trust in the whole module.
        """
        try:
            rows = self.db.execute_query(
                "SELECT username FROM ut_users ORDER BY username", fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("preview_close failed")
            return []

        as_of = date(year, 12, 31)
        out = []
        for row in rows:
            name = (row["username"] if isinstance(row, dict) else row[0]) or ""
            if not name:
                continue
            closing = self.balance(name, rules, as_of)["available"]
            split = lp.carry_forward(closing, rules)
            out.append({
                "user_id": name,
                "closing_balance": round(closing, 2),
                "carried": round(split["carried"], 2),
                "lapsed": round(split["lapsed"], 2),
            })
        return out

    def close_year(self, year: int, by_whom: str, rules=None) -> dict:
        """
        Draw the line under a leave year.

        Idempotent by (user, year) - running it twice does not lapse anybody's
        leave a second time.
        """
        already = {str(r["user_id"]).lower() for r in self.closes(year)}
        written = 0
        for entry in self.preview_close(year, rules):
            if entry["user_id"].lower() in already:
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
        return {"closed": written, "skipped": len(already)}

    # ----------------------------------------------------------------- balance
    def balance(self, username: str, rules=None, as_of: date = None) -> dict:
        """
        What this person has, has used, and has pending.

        Accrued types draw from one earned pool - which is how a two-days-a-month
        policy actually works. Comp-off comes from its own ledger, and the rest
        are granted or unpaid and so have no balance to speak of.
        """
        as_of = as_of or date.today()
        requests = self.for_user(username)

        # Where this person's count starts. Once a year has been closed, the
        # opening balance is whatever survived the cap and accrual restarts
        # from the new year - so leave taken before the line is already
        # accounted for and must not be deducted twice.
        close = self.last_close(username)
        opening = float(close.get("carried") or 0.0) if close else 0.0
        since = date(int(close["leave_year"]) + 1, 1, 1) if close else None

        used = {}
        pending = {}
        for row in requests:
            start = as_date(row.get("start_date"))
            # Anything starting after the date being asked about has not
            # happened yet. Without this, a year-end preview run on 31 December
            # counted January's leave against the year being closed, so the
            # closing balance was short and the shortfall lapsed.
            if start and start > as_of:
                continue
            if since is not None and start and start < since:
                continue
            kind = (row.get("type") or "Casual").title()
            status = lp.normalise_status(row.get("status"))
            charge = row.get("days_charged")
            days = float(charge) if charge is not None else 0.0

            if status == lp.STATUS_APPROVED:
                used[kind] = used.get(kind, 0.0) + days
            elif status in (lp.STATUS_PENDING_SUPERVISOR, lp.STATUS_PENDING_HR):
                pending[kind] = pending.get(kind, 0.0) + days

        earned_from = since or self.joined_on(username)
        accrued = opening + lp.accrued_by(as_of, earned_from, rules)
        spent = sum(used.get(k, 0.0) for k in lp.ACCRUED_TYPES)
        held = sum(pending.get(k, 0.0) for k in lp.ACCRUED_TYPES)

        return {
            "accrued": accrued,
            "opening": opening,
            "counting_from": since,
            "used": used,
            "pending": pending,
            "spent_from_pool": spent,
            "pending_from_pool": held,
            "available": max(0.0, accrued - spent - held),
            "comp_off": self.comp_off_balance(username),
            "as_of": as_of,
        }
