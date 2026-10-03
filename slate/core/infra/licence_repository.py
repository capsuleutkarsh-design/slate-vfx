"""
Everything the licence module reads and writes.

Two tables behind it: what was bought (software_licenses) and what has actually
been used (licence_readings). The second one is the whole point - a purchase
record on its own cannot tell anybody whether the purchase was right.

Readings belong to a licence by id. Readings taken before the id existed carry
only a product name ("legacy" readings); they count for a licence only when it
is the one licence with that name. With two contracts of the same product
nobody can tell whose the old readings were, and giving them to both is how a
5-seat project contract was reported 440% over-subscribed on the studio
contract's usage.
"""

from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional

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

from .transaction import atomic

logger = logging.getLogger(__name__)

EXTRA_COLUMNS = ("annual_cost", "currency", "vendor", "contract_ref", "notes")

# Days before expiry at which IT are reminded (the studio's window first).
REMINDER_DAYS = (14, 0)


class LicenceRepository:
    def __init__(self, db=None):
        if db is None:
            from .database_manager import database_manager
            db = database_manager
        self.db = db
        self._has = {}

    # ------------------------------------------------------------- helpers
    def _column(self, table, column) -> bool:
        key = (table, column)
        if key not in self._has:
            try:
                from .migrations.workplace_schema import _column_exists
                self._has[key] = _column_exists(self.db, table, column)
            except Exception:
                self._has[key] = False
        return self._has[key]

    def _extras(self) -> List[str]:
        return [c for c in EXTRA_COLUMNS if self._column("software_licenses", c)]

    @staticmethod
    def _name_key(name) -> str:
        return str(name or "").strip().lower()

    # ------------------------------------------------------------- purchases
    def licences(self) -> list:
        """
        Every licence bought. Raises on a refused read, like the other IT
        repositories: it used to become [] and the screen said "No licences
        recorded" over a broken query.
        """
        extra = self._extras()
        rows = self.db.execute_query(
            "SELECT id, software_name, total_seats, active_seats, expiration_date%s "
            "FROM software_licenses ORDER BY software_name"
            % "".join(", " + c for c in extra), fetch="all")
        if rows is None:
            reason = self.db.last_error() if hasattr(self.db, "last_error") else ""
            raise RuntimeError("The licences could not be read. %s" % (reason or ""))
        return [dict(r) for r in rows]

    # The column's size, so the dialog can stop a name the database would refuse.
    NAME_MAX = 100

    def same_name(self, software_name, exclude_id=None) -> list:
        """Other licences with this name, ignoring case ('nuke' and 'Nuke')."""
        key = self._name_key(software_name)
        return [r for r in self.licences()
                if self._name_key(r.get("software_name")) == key and r.get("id") != exclude_id]

    def save(self, software_name, total_seats, expiry, licence_id=None, **details) -> bool:
        """
        Add or change a licence. True only when the database kept it.

        details: annual_cost, currency, vendor, contract_ref, notes (each only
        written when this database has the column). expiry may be None - a
        perpetual licence has no renewal.

        The result of the write used to be thrown away and True returned, so a
        name longer than the column was refused, the dialog closed, and the
        licence simply was not there. An edit of a licence that no longer
        exists is a failure too.
        """
        extra = [c for c in self._extras() if c in details]
        values = []
        for c in extra:
            value = details[c]
            if value is None or (isinstance(value, str) and not value.strip()):
                value = None
            elif c == "annual_cost":
                # Exact: text into NUMERIC on PostgreSQL, text on SQLite.
                from slate.core.domain.money import quantize
                value = str(quantize(value))
            elif c == "currency":
                value = str(value).strip().upper()[:3]
            values.append(value)
        try:
            if licence_id:
                # Renamed while it was the only licence with its old name: its
                # name-only readings are its own, so they are tied to it by id
                # in the same transaction - left behind, they stopped counting
                # for it and haunted the next licence given the old name.
                before = next((r for r in self.licences() if r.get("id") == licence_id), None)
                renamed = (before is not None and self._name_key(before.get("software_name"))
                           != self._name_key(software_name) and self._legacy_is_theirs(before))
                with atomic(self.db) as tx:
                    tx.write(
                        "UPDATE software_licenses SET software_name = %%s, total_seats = %%s, "
                        "expiration_date = %%s%s WHERE id = %%s"
                        % "".join(", %s = %%s" % c for c in extra),
                        tuple([software_name, int(total_seats or 0), expiry] + values + [licence_id]),
                        expect_rows=True)
                    if renamed:
                        tx.write("UPDATE licence_readings SET licence_id = %s WHERE licence_id IS NULL "
                                 "AND LOWER(software_name) = LOWER(%s)",
                                 (licence_id, before.get("software_name")))
                return True
            result = self.db.execute_update(
                "INSERT INTO software_licenses "
                "(software_name, total_seats, active_seats, expiration_date%s) "
                "VALUES (%%s, %%s, 0, %%s%s)"
                % ("".join(", " + c for c in extra), ", %s" * len(extra)),
                tuple([software_name, int(total_seats or 0), expiry] + values))
            return bool(result)
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("save failed")
            return False

    def _legacy_is_theirs(self, licence: dict) -> bool:
        """Whether this licence is the only one with its name (so name-only readings are its)."""
        return not self.same_name(licence.get("software_name"), exclude_id=licence.get("id"))

    def reading_count(self, licence: dict) -> int:
        """Exactly how many readings remove() would delete with this licence."""
        try:
            n = self.db.execute_query(
                "SELECT COUNT(*) AS n FROM licence_readings WHERE licence_id = %s",
                (licence.get("id"),), fetch="one") or {}
            count = int(dict(n).get("n") or 0)
            if self._legacy_is_theirs(licence):
                legacy = self.db.execute_query(
                    "SELECT COUNT(*) AS n FROM licence_readings WHERE licence_id IS NULL "
                    "AND LOWER(software_name) = LOWER(%s)",
                    (licence.get("software_name"),), fetch="one") or {}
                count += int(dict(legacy).get("n") or 0)
            return count
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("reading_count failed")
            return 0

    def remove(self, licence_id) -> bool:
        """
        Delete a licence and the readings taken against it - both or neither.

        Name-only readings go too when this was the only licence with that
        name: left behind they kept counting, so a re-added "Maya" showed the
        deleted contract's peak the moment it was saved. With another contract
        of the same name they are left for it.
        """
        try:
            licence = next((r for r in self.licences() if r.get("id") == licence_id), None)
            sole = licence is not None and self._legacy_is_theirs(licence)
            with atomic(self.db) as tx:
                tx.write("DELETE FROM licence_readings WHERE licence_id = %s", (licence_id,))
                if sole:
                    tx.write("DELETE FROM licence_readings WHERE licence_id IS NULL "
                             "AND LOWER(software_name) = LOWER(%s)", (licence.get("software_name"),))
                if self._table_exists("licence_reminders"):
                    tx.write("DELETE FROM licence_reminders WHERE licence_id = %s", (licence_id,))
                tx.write("DELETE FROM software_licenses WHERE id = %s", (licence_id,),
                         expect_rows=True)
            return True
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("remove failed")
            return False

    def _table_exists(self, name) -> bool:
        key = ("table", name)
        if key not in self._has:
            try:
                from .migrations.workplace_schema import _table_exists
                self._has[key] = _table_exists(self.db, name)
            except Exception:
                self._has[key] = False
        return self._has[key]

    # -------------------------------------------------------------- readings
    def record(self, software_name, seats_in_use, seats_total, taken_at=None,
               licence_id=None, source: str = "manual", recorded_by: str = None) -> bool:
        """
        Write down what the licence server said at one moment.

        Tied to the licence, not to the product name. taken_at may be in the
        past (a back-filled reading) but not in the future. active_seats on
        the purchase row is kept as the latest reading.
        """
        taken_at = taken_at or datetime.now().replace(microsecond=0)
        if taken_at > datetime.now() + timedelta(minutes=5):
            logger.warning("A reading in the future was refused (%s).", taken_at)
            return False
        columns = ["software_name", "licence_id", "taken_at", "seats_in_use", "seats_total"]
        values = [software_name, licence_id, taken_at, int(seats_in_use or 0), int(seats_total or 0)]
        if self._column("licence_readings", "source"):
            columns.append("source")
            values.append(source)
        if self._column("licence_readings", "recorded_by") and recorded_by:
            columns.append("recorded_by")
            values.append(recorded_by)
        try:
            with atomic(self.db) as tx:
                tx.write("INSERT INTO licence_readings (%s) VALUES (%s)"
                         % (", ".join(columns), ", ".join(["%s"] * len(values))), tuple(values))
                self._sync_latest(tx, licence_id, software_name)
            return True
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("record failed")
            return False

    def _sync_latest(self, tx, licence_id, software_name=None):
        """active_seats = the newest reading of this licence."""
        if not licence_id:
            if software_name:
                latest = tx.one("SELECT seats_in_use FROM licence_readings WHERE licence_id IS NULL "
                                "AND LOWER(software_name) = LOWER(%s) ORDER BY taken_at DESC, id DESC",
                                (software_name,))
                if latest:
                    tx.write("UPDATE software_licenses SET active_seats = %s "
                             "WHERE LOWER(software_name) = LOWER(%s)",
                             (int(latest["seats_in_use"] or 0), software_name))
            return
        latest = tx.one("SELECT seats_in_use FROM licence_readings WHERE licence_id = %s "
                        "ORDER BY taken_at DESC, id DESC", (licence_id,))
        tx.write("UPDATE software_licenses SET active_seats = %s WHERE id = %s",
                 (int(latest["seats_in_use"] or 0) if latest else 0, licence_id))

    def history(self, licence, days: int = None) -> list:
        """
        The readings behind a licence's peak, newest first: its own, plus
        name-only ones when it is the only licence with that name. Each row
        says which (legacy True/False) so the screen can explain it.
        """
        if not isinstance(licence, dict):
            licence = next((r for r in self.licences()
                            if r.get("id") == licence or r.get("software_name") == licence), None)
            if licence is None:
                return []
        clauses = ["licence_id = %s"]
        params = [licence.get("id")]
        if self._legacy_is_theirs(licence):
            clauses.append("(licence_id IS NULL AND LOWER(software_name) = LOWER(%s))")
            params.append(licence.get("software_name"))
        where = "(" + " OR ".join(clauses) + ")"
        if days:
            where += " AND taken_at >= %s"
            params.append(datetime.now() - timedelta(days=max(1, days)))
        extra = [c for c in ("source", "recorded_by") if self._column("licence_readings", c)]
        try:
            rows = self.db.execute_query(
                "SELECT id, licence_id, taken_at, seats_in_use, seats_total%s FROM licence_readings "
                "WHERE %s ORDER BY taken_at DESC, id DESC"
                % ("".join(", " + c for c in extra), where), tuple(params), fetch="all") or []
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("history failed")
            return []
        out = []
        for r in rows:
            r = dict(r)
            r["legacy"] = r.get("licence_id") is None
            out.append(r)
        return out

    def delete_reading(self, reading_id) -> bool:
        """Take out a wrong reading (a typo of 25 for 2), and re-sync the latest."""
        try:
            row = self.db.execute_query(
                "SELECT licence_id, software_name FROM licence_readings WHERE id = %s",
                (reading_id,), fetch="one")
            if not row:
                return False
            row = dict(row)
            with atomic(self.db) as tx:
                tx.write("DELETE FROM licence_readings WHERE id = %s", (reading_id,), expect_rows=True)
                self._sync_latest(tx, row.get("licence_id"), row.get("software_name"))
            return True
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("delete_reading failed")
            return False

    def update_reading(self, reading_id, seats_in_use, taken_at=None) -> bool:
        """Correct a reading's number (and, optionally, its time)."""
        if taken_at is not None and taken_at > datetime.now() + timedelta(minutes=5):
            return False
        try:
            row = self.db.execute_query(
                "SELECT licence_id, software_name FROM licence_readings WHERE id = %s",
                (reading_id,), fetch="one")
            if not row:
                return False
            row = dict(row)
            with atomic(self.db) as tx:
                if taken_at is None:
                    tx.write("UPDATE licence_readings SET seats_in_use = %s WHERE id = %s",
                             (int(seats_in_use), reading_id), expect_rows=True)
                else:
                    tx.write("UPDATE licence_readings SET seats_in_use = %s, taken_at = %s WHERE id = %s",
                             (int(seats_in_use), taken_at, reading_id), expect_rows=True)
                self._sync_latest(tx, row.get("licence_id"), row.get("software_name"))
            return True
        except DatabaseUnavailableError:
            raise
        except Exception:
            logger.exception("update_reading failed")
            return False

    def import_readings(self, rows: Iterable[tuple], taken_at=None, recorded_by: str = None) -> tuple:
        """
        Readings from a licence server report: [(licence row, in_use)].
        Returns (written, skipped): a reading already there for that licence
        at that moment with that number is skipped, so opening the same saved
        report twice does not double the readings.
        """
        taken_at = taken_at or datetime.now().replace(microsecond=0)
        written = skipped = 0
        for licence, in_use in rows:
            there = self.db.execute_query(
                "SELECT id FROM licence_readings WHERE licence_id = %s AND taken_at = %s "
                "AND seats_in_use = %s", (licence.get("id"), taken_at, int(in_use or 0)), fetch="one")
            if there:
                skipped += 1
                continue
            if self.record(licence.get("software_name"), in_use, licence.get("total_seats"),
                           taken_at=taken_at, licence_id=licence.get("id"),
                           source="server report", recorded_by=recorded_by):
                written += 1
        return written, skipped

    def peaks(self, days: int = 90) -> dict:
        """
        Highest concurrent use over a window.

        Keyed by licence id for readings that carry one, and by ("name",
        lower-case name) for the name-only readings taken before the id
        existed. The two are kept apart: merging them under one key is what
        gave a second contract the first one's usage.

        Every key also carries "last": its newest reading, inside the window
        or not (peak None and samples 0 when only older readings exist), so a
        licence measured before the window is not called never measured.
        """
        since = datetime.now() - timedelta(days=max(1, days))
        rows = self.db.execute_query(
            "SELECT licence_id, LOWER(software_name) AS name, "
            "       MAX(CASE WHEN taken_at >= %s THEN seats_in_use END) AS peak, "
            "       SUM(CASE WHEN taken_at >= %s THEN 1 ELSE 0 END) AS samples, "
            "       MAX(taken_at) AS last "
            "FROM licence_readings GROUP BY licence_id, LOWER(software_name)",
            (since, since), fetch="all")
        if rows is None:
            reason = self.db.last_error() if hasattr(self.db, "last_error") else ""
            raise RuntimeError("The licence readings could not be read. %s" % (reason or ""))

        out = {}
        for row in rows:
            row = dict(row)
            key = row.get("licence_id")
            if key is None:
                key = ("name", str(row.get("name") or "").strip())
            entry = out.setdefault(key, {"peak": None, "samples": 0, "last": None})
            if row.get("peak") is not None:
                entry["peak"] = max(entry["peak"] or 0, int(row.get("peak") or 0))
            entry["samples"] += int(row.get("samples") or 0)
            last = lc.as_date(row.get("last"))
            if last and (entry["last"] is None or last > entry["last"]):
                entry["last"] = last
        return out

    # -------------------------------------------------------------- reporting
    def compliance(self, days: int = 90, today: date = None, renewal_days: int = None) -> list:
        """
        Every licence with its finding attached, worst first.

        This is the whole module in one call - the view renders it and does no
        arithmetic of its own.
        """
        renewal_days = renewal_days or lc.renewal_window(self.db)
        peaks = self.peaks(days)
        licences = self.licences()
        name_count = {}
        for row in licences:
            key = self._name_key(row.get("software_name"))
            name_count[key] = name_count.get(key, 0) + 1

        out = []
        for row in licences:
            name = row.get("software_name") or ""
            key = self._name_key(name)
            seats = int(row.get("total_seats") or 0)
            own = peaks.get(row.get("id"))
            legacy = peaks.get(("name", key))
            unattributed = False
            if legacy and name_count.get(key, 0) > 1:
                # Old readings of a product with several contracts: whose
                # they were cannot be known, so they count for nobody.
                unattributed, legacy = True, None
            readings = [r for r in (own, legacy) if r]
            measured = [r["peak"] for r in readings if r["peak"] is not None]
            peak = max(measured) if measured else None
            samples = sum(r["samples"] for r in readings)
            lasts = [r["last"] for r in readings if r.get("last")]
            last_days = ((today or date.today()) - max(lasts)).days if lasts and peak is None else None
            expiry = row.get("expiration_date")

            currency = row.get("currency") or None
            spare_cost = lc.spare_cost(row.get("annual_cost"), seats, peak)
            spare_text = lc.money_text(spare_cost, currency) if spare_cost else ""
            finding = lc.state(seats, peak, expiry, today, renewal_days, last_days)
            text = lc.describe(seats, peak, expiry, today, renewal_days, spare_text, last_days)
            if unattributed and peak is None:
                text += (" Older readings of %s were taken before contracts were told apart "
                         "and cannot be counted for one of them." % name)
            left = lc.days_until(expiry, today)
            out.append({
                **row,
                "peak": peak,
                "samples": samples,
                "state": finding,
                "tone": lc.tone(finding),
                "days_left": left,
                "renewal_due": lc.is_renewal_due(left, renewal_days),
                "utilisation": lc.utilisation(peak, seats) if peak is not None else None,
                "spare_cost": spare_cost,
                "unattributed_legacy": unattributed,
                "finding": text,
            })

        out.sort(key=lambda r: (lc.SEVERITY.get(r["state"], 9),
                                str(r.get("software_name") or "").lower()))
        return out

    # ----------------------------------------------------------- reminders
    _last_reminder_check = {}

    def send_renewal_reminders(self, today: date = None, throttle_seconds: float = 3600) -> int:
        """
        Remind IT and the people who approve renewals (the header bell) once
        per licence per threshold: when it
        enters the studio's renewal window, 14 days before, and on the day it
        expires. Recorded in licence_reminders so no workstation repeats it.
        Returns how many reminders went out. Never raises.
        """
        key = id(getattr(self.db, "backend", self.db))
        now = _time.monotonic()
        if today is None and now - self._last_reminder_check.get(key, -1e9) < throttle_seconds:
            return 0
        self._last_reminder_check[key] = now
        try:
            if not self._table_exists("licence_reminders"):
                return 0
            today = today or date.today()
            window = lc.renewal_window(self.db)
            thresholds = sorted({window, *REMINDER_DAYS}, reverse=True)
            sent = 0
            staff = None
            for row in self.licences():
                left = lc.days_until(row.get("expiration_date"), today)
                if left is None:
                    continue
                due = [t for t in thresholds if left <= t]
                if not due:
                    continue
                threshold = min(due)
                expiry = lc.as_date(row.get("expiration_date"))
                result = self.db.execute_update(
                    "INSERT INTO licence_reminders (licence_id, threshold, expiry) VALUES (%s, %s, %s) "
                    "ON CONFLICT (licence_id, threshold, expiry) DO NOTHING",
                    (row.get("id"), threshold, expiry))
                if not getattr(result, "changed", bool(result)):
                    continue            # already reminded at this threshold
                if staff is None:
                    # IT, and the people who approve renewals (view_licences).
                    from .ticket_repository import TicketRepository
                    people_repo = TicketRepository(self.db)
                    staff = sorted(set(people_repo.it_staff())
                                   | set(people_repo.it_staff(ability="view_licences")), key=str.lower)
                name = row.get("software_name")
                message = ("%s: %s - decide the renewal now." % (name, lc.renewal_phrase(left))
                           if left >= 0 else "%s %s - renew or remove it." % (name, lc.renewal_phrase(left).lower()))
                try:
                    from slate.core.domain.notification_manager import NotificationManager
                    NotificationManager(self.db).notify(staff, message, "licence")
                    sent += 1
                except Exception as exc:
                    logger.warning("Renewal reminder not sent: %s", exc)
            return sent
        except DatabaseUnavailableError:
            return 0
        except Exception:
            logger.exception("renewal reminders failed")
            return 0
