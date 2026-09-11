"""
Everything the licence module reads and writes.

Two tables behind it: what was bought (software_licenses) and what has actually
been used (licence_readings). The second one is the whole point - a purchase
record on its own cannot tell anybody whether the purchase was right.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from ..domain import licence_compliance as lc


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
        except Exception:
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
        except Exception:
            return False

    def remove(self, licence_id) -> bool:
        try:
            self.db.execute_update(
                "DELETE FROM software_licenses WHERE id = %s", (licence_id,))
            return True
        except Exception:
            return False

    # -------------------------------------------------------------- readings
    def record(self, software_name, seats_in_use, seats_total, taken_at=None) -> bool:
        """
        Write down what the licence server said at one moment.

        active_seats on the purchase row is kept as the latest reading, so a
        screen that only knows about the purchase table is not left stale.
        """
        try:
            self.db.execute_update(
                "INSERT INTO licence_readings "
                "(software_name, taken_at, seats_in_use, seats_total) "
                "VALUES (%s, %s, %s, %s)",
                (software_name, taken_at or datetime.now(),
                 int(seats_in_use or 0), int(seats_total or 0)))
            self.db.execute_update(
                "UPDATE software_licenses SET active_seats = %s WHERE software_name = %s",
                (int(seats_in_use or 0), software_name))
            return True
        except Exception:
            return False

    def peaks(self, days: int = 90) -> dict:
        """
        Highest concurrent use per product over a window.

        The peak is the number that decides a renewal. An average hides exactly
        the moment everybody was comping at once, which is the moment the
        studio is either fine or stuck.
        """
        since = datetime.now() - timedelta(days=max(1, days))
        try:
            rows = self.db.execute_query(
                "SELECT software_name, MAX(seats_in_use) AS peak, COUNT(*) AS samples "
                "FROM licence_readings WHERE taken_at >= %s "
                "GROUP BY software_name", (since,), fetch="all") or []
        except Exception:
            return {}

        out = {}
        for row in rows:
            row = dict(row)
            name = row.get("software_name")
            if name:
                out[name] = {"peak": int(row.get("peak") or 0),
                             "samples": int(row.get("samples") or 0)}
        return out

    def history(self, software_name, days: int = 90) -> list:
        since = datetime.now() - timedelta(days=max(1, days))
        try:
            rows = self.db.execute_query(
                "SELECT taken_at, seats_in_use, seats_total FROM licence_readings "
                "WHERE software_name = %s AND taken_at >= %s ORDER BY taken_at",
                (software_name, since), fetch="all") or []
            return [dict(r) for r in rows]
        except Exception:
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
            reading = peaks.get(name)
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
