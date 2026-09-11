"""
Turning attendance into comp-off.

This is the join between two modules that had nothing to do with each other.
Comp-off is *earned by working* - a Sunday, a public holiday, or a long enough
shift - and the only record of who worked what is the attendance log. So the
ledger is credited from punch data, not typed in by hand.

Three things this is careful about:

    it never double-credits      a day already credited is skipped
    it says why                  every credit carries its reason
    it does nothing when off     UT does not operate comp-off, so by default
                                 this is inert - it exists for the studios that do
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from . import leave_policy as lp


def _parse_day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except Exception:
        return None


def hours_between(punch_in, punch_out) -> float:
    """
    Hours worked from two clock times.

    A punch-out earlier than the punch-in means the shift crossed midnight -
    which in a crunch week is exactly the shift that earns comp-off, so it is
    counted rather than discarded as bad data.
    """
    if not punch_in or not punch_out:
        return 0.0
    try:
        start = datetime.strptime(str(punch_in)[:8], "%H:%M:%S")
        end = datetime.strptime(str(punch_out)[:8], "%H:%M:%S")
    except Exception:
        return 0.0

    delta = (end - start).total_seconds() / 3600.0
    if delta < 0:
        delta += 24.0
    return round(delta, 2)


class CompOffService:
    def __init__(self, db=None, repo=None):
        if db is None:
            from ..infra.database_manager import database_manager
            db = database_manager
        self.db = db
        if repo is None:
            from ..infra.leave_repository import LeaveRepository
            repo = LeaveRepository(db)
        self.repo = repo

    # ------------------------------------------------------------------ reads
    def _attendance(self, since: date) -> list:
        try:
            # day_date is stored as text, not a date, so it is compared as
            # text. ISO strings sort correctly, which is the only reason this
            # works - it is not a date comparison the database understands.
            rows = self.db.execute_query(
                "SELECT user_id, day_date, punch_in, punch_out FROM attendance_log "
                "WHERE day_date >= %s ORDER BY day_date",
                (since.isoformat(),), fetch="all") or []
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _already_credited(self) -> set:
        """(user, day) pairs the ledger has already paid for."""
        try:
            rows = self.db.execute_query(
                "SELECT user_id, earned_on FROM comp_off_ledger "
                "WHERE source = 'attendance'", fetch="all") or []
        except Exception:
            return set()
        out = set()
        for row in rows:
            row = dict(row)
            day = _parse_day(row.get("earned_on"))
            if day:
                out.add((str(row.get("user_id") or "").lower(), day))
        return out

    # ----------------------------------------------------------------- review
    def review(self, since: date = None, rules=None) -> list:
        """
        What the attendance log says people have earned, without writing anything.

        Returns one entry per qualifying day so HR can see the working before it
        is credited - a comp-off ledger nobody can audit is worse than none.
        """
        rules = lp.policy(rules)
        if not rules["comp_off_enabled"]:
            return []

        since = since or (date.today() - timedelta(days=90))
        holidays = self.repo.holidays()
        done = self._already_credited()

        found = []
        for row in self._attendance(since):
            user = str(row.get("user_id") or "").strip()
            day = _parse_day(row.get("day_date"))
            if not user or not day:
                continue
            if (user.lower(), day) in done:
                continue

            hours = hours_between(row.get("punch_in"), row.get("punch_out"))
            earned = lp.comp_off_earned(day, hours, holidays, rules)
            if earned["days"] <= 0:
                continue

            found.append({
                "user_id": user,
                "day": day,
                "hours": hours,
                "days": earned["days"],
                "reason": earned["reason"],
                "expires_on": lp.comp_off_expires(day, rules),
            })
        return found

    # ------------------------------------------------------------------ write
    def credit(self, entries) -> int:
        """Write the reviewed entries to the ledger. Returns how many landed."""
        made = 0
        for entry in entries or []:
            if self.repo.credit_comp_off(
                entry["user_id"], entry["day"], entry["days"], entry["reason"]
            ):
                made += 1
        return made

    def run(self, since: date = None, rules=None) -> dict:
        """Review and credit in one go."""
        entries = self.review(since, rules)
        return {"found": len(entries), "credited": self.credit(entries)}

    # ----------------------------------------------------------------- expiry
    def expire(self, rules=None) -> int:
        """
        Lapse comp-off nobody used in time.

        Consuming the remainder is how it lapses: the row stays, so the ledger
        still shows it was earned and why it went - which is what somebody will
        ask about.
        """
        try:
            rows = self.db.execute_query(
                "SELECT id, days, consumed, expires_on FROM comp_off_ledger "
                "WHERE expires_on IS NOT NULL", fetch="all") or []
        except Exception:
            return 0

        today = date.today()
        lapsed = 0
        for row in rows:
            row = dict(row)
            expires = _parse_day(row.get("expires_on"))
            if not expires or expires >= today:
                continue
            remaining = float(row.get("days") or 0) - float(row.get("consumed") or 0)
            if remaining <= 0:
                continue
            try:
                self.db.execute_update(
                    "UPDATE comp_off_ledger SET consumed = days, "
                    "reason = reason || ' (lapsed unused)' WHERE id = %s", (row["id"],))
                lapsed += 1
            except Exception:
                continue
        return lapsed
