"""
Everything the licence module reads and writes.

Two tables behind it: what was bought (software_licenses) and what has actually
been used (licence_readings). The second one is the whole point - a purchase
record on its own cannot tell anybody whether the purchase was right.
"""

from __future__ import annotations

import logging

from datetime import date, datetime, timedelta

from ..domain import licence_compliance as lc

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



class LicenceRepository:
    def __init__(self, db=None):
        if db is None:
            from .database_manager import database_manager
            db = database_manager
        self.db = db

    # ------------------------------------------------------------- purchases
    def licences(self) -> list:
        try:
            rows = self.db.execute_query(
                "SELECT id, software_name, total_seats, active_seats, expiration_date "
                "FROM software_licenses ORDER BY software_name", fetch="all") or []
            return [dict(r) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("licences failed")
            return []

    def save(self, software_name, total_seats, expiry, licence_id=None) -> bool:
        try:
            if licence_id:
                self.db.execute_update(
                    "UPDATE software_licenses SET software_name = %s, total_seats = %s, "
                    "expiration_date = %s WHERE id = %s",
                    (software_name, int(total_seats or 0), expiry, licence_id))
            else:
                self.db.execute_update(
                    "INSERT INTO software_licenses "
                    "(software_name, total_seats, active_seats, expiration_date) "
                    "VALUES (%s, %s, 0, %s)",
                    (software_name, int(total_seats or 0), expiry))
            return True
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("save failed")
            return False

    def remove(self, licence_id) -> bool:
        """
        Delete a licence and the readings taken against it.

        Leaving the readings behind would leave a peak with nothing to compare
        it to, and it would keep counting towards the name-keyed fallback - so
        a deleted contract would go on flagging its replacement.
        """
        try:
            self.db.execute_update(
                "DELETE FROM licence_readings WHERE licence_id = %s", (licence_id,))
            self.db.execute_update(
                "DELETE FROM software_licenses WHERE id = %s", (licence_id,))
            return True
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("remove failed")
            return False

    # -------------------------------------------------------------- readings
    def record(self, software_name, seats_in_use, seats_total, taken_at=None,
               licence_id=None) -> bool:
        """
        Write down what the licence server said at one moment.

        Tied to the licence, not to the product name. Two contracts for the
        same product - a studio one and a project one - shared a single peak
        when readings were matched by name, so both were reported as
        over-subscribed on the strength of the other's usage.

        active_seats on the purchase row is kept as the latest reading, so a
        screen that only knows about the purchase table is not left stale.
        """
        try:
            self.db.execute_update(
                "INSERT INTO licence_readings "
                "(software_name, licence_id, taken_at, seats_in_use, seats_total) "
                "VALUES (%s, %s, %s, %s, %s)",
                (software_name, licence_id, taken_at or datetime.now(),
                 int(seats_in_use or 0), int(seats_total or 0)))
            if licence_id:
                self.db.execute_update(
                    "UPDATE software_licenses SET active_seats = %s WHERE id = %s",
                    (int(seats_in_use or 0), licence_id))
            else:
                self.db.execute_update(
                    "UPDATE software_licenses SET active_seats = %s WHERE software_name = %s",
                    (int(seats_in_use or 0), software_name))
            return True
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("record failed")
            return False

    def peaks(self, days: int = 90) -> dict:
        """
        Highest concurrent use per licence over a window.

        The peak is the number that decides a renewal. An average hides exactly
        the moment everybody was comping at once, which is the moment the studio
        is either fine or stuck.

        Keyed by licence id where a reading has one, and by name for readings
        taken before the id existed - so an existing studio's history still
        counts rather than vanishing the day this shipped.
        """
        since = datetime.now() - timedelta(days=max(1, days))
        try:
            rows = self.db.execute_query(
                "SELECT licence_id, software_name, MAX(seats_in_use) AS peak, "
                "       COUNT(*) AS samples "
                "FROM licence_readings WHERE taken_at >= %s "
                "GROUP BY licence_id, software_name", (since,), fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("peaks failed")
            return {}

        out = {}
        for row in rows:
            row = dict(row)
            peak = int(row.get("peak") or 0)
            samples = int(row.get("samples") or 0)
            for key in (row.get("licence_id"), row.get("software_name")):
                if key in (None, ""):
                    continue
                existing = out.get(key)
                if existing is None:
                    out[key] = {"peak": peak, "samples": samples}
                else:
                    # Readings for the same licence under both keys: the peak is
                    # the highest either saw.
                    existing["peak"] = max(existing["peak"], peak)
                    existing["samples"] += samples
        return out

    def history(self, software_name, days: int = 90) -> list:
        since = datetime.now() - timedelta(days=max(1, days))
        try:
            rows = self.db.execute_query(
                "SELECT taken_at, seats_in_use, seats_total FROM licence_readings "
                "WHERE software_name = %s AND taken_at >= %s ORDER BY taken_at",
                (software_name, since), fetch="all") or []
            return [dict(r) for r in rows]
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("history failed")
            return []

    # -------------------------------------------------------------- reporting
    def compliance(self, days: int = 90, today: date = None) -> list:
        """
        Every licence with its finding attached, worst first.

        This is the whole module in one call - the view renders it and does no
        arithmetic of its own.
        """
        peaks = self.peaks(days)
        out = []
        for row in self.licences():
            name = row.get("software_name") or ""
            seats = int(row.get("total_seats") or 0)
            # The licence's own readings first; the name only as a fallback for
            # history recorded before readings carried an id.
            reading = peaks.get(row.get("id")) or peaks.get(name)
            peak = reading["peak"] if reading else None
            expiry = row.get("expiration_date")

            finding = lc.state(seats, peak, expiry, today)
            out.append({
                **row,
                "peak": peak,
                "samples": reading["samples"] if reading else 0,
                "state": finding,
                "tone": lc.tone(finding),
                "days_left": lc.days_until(expiry, today),
                "utilisation": lc.utilisation(peak, seats) if peak is not None else None,
                "finding": lc.describe(seats, peak, expiry, today),
            })

        out.sort(key=lambda r: (lc.SEVERITY.get(r["state"], 9),
                                str(r.get("software_name") or "").lower()))
        return out
