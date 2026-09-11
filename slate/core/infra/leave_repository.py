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



class LeaveRepository:
    def __init__(self, db=None):
        if db is None:
            from .database_manager import database_manager
            db = database_manager
        self.db = db

    # ------------------------------------------------------------- reference
    def holidays(self, year: int = None) -> set:
        """Public holidays, as a set of dates the policy can count against."""
        try:
            if year:
                rows = self.db.execute_query(
                    "SELECT holiday_date FROM holiday_calendar "
                    "WHERE EXTRACT(YEAR FROM holiday_date) = %s", (year,), fetch="all")
            else:
                rows = self.db.execute_query(
                    "SELECT holiday_date FROM holiday_calendar", fetch="all")
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("holidays failed")
            return set()

        out = set()
        for row in rows or []:
            value = row["holiday_date"] if isinstance(row, dict) else row[0]
            if isinstance(value, datetime):
                value = value.date()
            if isinstance(value, date):
                out.add(value)
        return out

    def holiday_rows(self) -> list:
        try:
            rows = self.db.execute_query(
                "SELECT id, holiday_date, name, location FROM holiday_calendar "
                "ORDER BY holiday_date", fetch="all") or []
            return [dict(r) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("holiday_rows failed")
            return []

    def add_holiday(self, day, name, location="All") -> bool:
        try:
            self.db.execute_update(
                "INSERT INTO holiday_calendar (holiday_date, name, location) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (day, name, location))
            return True
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("add_holiday failed")
            return False

    def remove_holiday(self, holiday_id) -> bool:
        try:
            self.db.execute_update(
                "DELETE FROM holiday_calendar WHERE id = %s", (holiday_id,))
            return True
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
        value = row["joined_on"] if isinstance(row, dict) else row[0]
        if isinstance(value, datetime):
            return value.date()
        return value

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

    def submit(self, username, kind, start, end, half_day, reason, rules=None) -> bool:
        """
        Record a request, with the day count worked out now.

        Storing the charge at submission means a later change to the sandwich
        rule cannot quietly alter what somebody was already deducted.
        """
        charge = lp.days_charged(start, end, self.holidays(start.year), rules,
                                 half_day=bool(half_day))
        try:
            self.db.execute_update(
                "INSERT INTO leave_requests "
                "(user_id, start_date, end_date, type, status, half_day, reason, days_charged) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (username, start, end, kind, lp.STATUS_PENDING_SUPERVISOR,
                 1 if half_day else 0, reason, charge["total"]))
            return True
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("submit failed")
            return False

    def decide(self, request_id, stage, approved, by_whom, note="") -> bool:
        """Move a request along the supervisor then HR chain."""
        try:
            row = self.db.execute_query(
                "SELECT status FROM leave_requests WHERE id = %s",
                (request_id,), fetch="one")
            current = (row["status"] if isinstance(row, dict) else row[0]) if row else ""
            new_status = lp.next_status(current, stage, approved)

            if stage == "Supervisor":
                self.db.execute_update(
                    "UPDATE leave_requests SET status = %s, supervisor_by = %s, "
                    "supervisor_at = %s, decision_note = %s WHERE id = %s",
                    (new_status, by_whom, datetime.now(), note, request_id))
            else:
                self.db.execute_update(
                    "UPDATE leave_requests SET status = %s, hr_by = %s, "
                    "hr_at = %s, decision_note = %s WHERE id = %s",
                    (new_status, by_whom, datetime.now(), note, request_id))
            return True
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("decide failed")
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
            expires = row.get("expires_on")
            if isinstance(expires, datetime):
                expires = expires.date()
            if expires and expires < today:
                continue
            total += float(row.get("days") or 0) - float(row.get("consumed") or 0)
        return max(0.0, total)

    def credit_comp_off(self, username, earned_on, days, reason, rules=None) -> bool:
        if days <= 0:
            return False
        try:
            self.db.execute_update(
                "INSERT INTO comp_off_ledger "
                "(user_id, earned_on, days, reason, expires_on, source) "
                "VALUES (%s, %s, %s, %s, %s, 'attendance')",
                (username, earned_on, days, reason,
                 lp.comp_off_expires(earned_on, rules)))
            return True
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
                self.db.execute_update(
                    "INSERT INTO leave_year_close "
                    "(user_id, leave_year, closing_balance, carried, lapsed, closed_on, closed_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (entry["user_id"], year, entry["closing_balance"],
                     entry["carried"], entry["lapsed"], date.today(), by_whom))
                written += 1
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
            if since is not None:
                start = row.get("start_date")
                if isinstance(start, datetime):
                    start = start.date()
                if start and str(start)[:10] < since.isoformat():
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
