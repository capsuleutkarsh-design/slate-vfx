"""
Everything the Hardware tab reads and writes.

The loan ledger (asset_assignments) decides who has a machine; it is kept by
OnboardingService (issue_machine / return_machine) because joining and
leaving read it. This module keeps the inventory's own rules next to it:

    a machine's status can never contradict the ledger - "In service" becomes
    Active or Available from who holds it, and Repair or end of life on an
    issued machine means collecting it first

    a machine with loan history is retired, not deleted, so its history stays

    a rename moves the machine's whole history with it (ledger, checklist
    lines, deployment records), in one transaction

    the Live Ops sync never overwrites a spec somebody typed with an empty one,
    and says which reports it could not read
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from slate.core.domain import hardware as hw

try:
    from .db_results import DatabaseUnavailableError, WriteResult
except ImportError:                                   # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""
    WriteResult = None

from .transaction import atomic

logger = logging.getLogger(__name__)

OPTIONAL = ("type", "location", "cpu", "purchased_on", "warranty_until", "serial_number", "asset_tag")
TEXT_FIELDS = ("type", "cpu", "gpu", "ram", "storage", "location", "serial_number", "asset_tag")
WARRANTY_SOON_DAYS = 60


class HardwareError(ValueError):
    """A refused change, with a sentence a person can act on."""


class HardwareRepository:
    def __init__(self, db=None, service=None):
        if db is None:
            from .database_manager import database_manager
            db = database_manager
        self.db = db
        self._service = service
        self._cols = None

    # ------------------------------------------------------------- helpers
    @property
    def service(self):
        if self._service is None:
            from slate.core.domain.onboarding_service import OnboardingService
            self._service = OnboardingService(self.db)
        return self._service

    def columns(self) -> set:
        if self._cols is None:
            from .migrations.workplace_schema import _column_exists
            self._cols = {c for c in OPTIONAL if _column_exists(self.db, "hardware_inventory", c)}
        return self._cols

    def holder(self, machine_name) -> Optional[str]:
        held = self.service.held_by_machine(machine_name)
        return held[0].get("user_id") if held else None

    # ------------------------------------------------------------- reading
    def machines(self) -> list:
        """
        Every machine, with who holds it on the ledger (holder), their name,
        and how many loans it has ever had. Raises on a refused read - a failed
        read is not an empty inventory.

        Who holds a machine is the ledger's alone. The old typed-in
        assigned_to used to stand in for it, so a machine read "Assigned to
        artist16" while Collect was off and the leaving checklist never asked
        for it back; it is only a note now. A machine in service is Active
        when somebody holds it and Available when nobody does, whatever the
        status column was left saying. Names sort as people count them:
        WS-COMP-2 before WS-COMP-10.
        """
        from slate.core.domain.batch_rename import natural_key
        extra = sorted(self.columns() - {"location", "cpu"})
        rows = self.db.execute_query(
            "SELECT h.id, h.machine_name, h.gpu, h.cpu, h.storage, h.ram, h.status, h.location, "
            "       h.assigned_to%s, a.user_id AS holder, u.display_name, "
            "       (SELECT COUNT(*) FROM asset_assignments x "
            "        WHERE LOWER(x.machine_name) = LOWER(h.machine_name)) AS loans "
            "FROM hardware_inventory h "
            "LEFT JOIN asset_assignments a ON LOWER(a.machine_name) = LOWER(h.machine_name) "
            "     AND a.returned_on IS NULL "
            "LEFT JOIN ut_users u ON LOWER(u.username) = LOWER(a.user_id) "
            "ORDER BY LOWER(h.machine_name)" % "".join(", h." + c for c in extra), fetch="all")
        if rows is None:
            reason = self.db.last_error() if hasattr(self.db, "last_error") else ""
            raise RuntimeError("The machine list could not be read. %s" % (reason or ""))
        out, seen = [], set()
        for r in rows:
            r = dict(r)
            key = str(r.get("machine_name") or "").lower()
            if key in seen:            # two open loans on one machine: show it once
                continue
            seen.add(key)
            r["status"] = hw.normalise_status(r.get("status"))
            if r["status"] in (hw.ACTIVE, hw.AVAILABLE, ""):
                r["status"] = hw.status_for_service(bool(r.get("holder")))
            out.append(r)
        out.sort(key=lambda r: natural_key(r.get("machine_name") or ""))
        return out

    @staticmethod
    def figure(row) -> str:
        """
        The one figure a machine counts in: 'retired' (end of life), else
        'on_loan' (somebody holds it - in repair or not), else 'repair', else
        'available'. The figures add up to the total, and clicking one shows
        exactly what it counted; a machine in repair while still on loan was in
        two of them, an Active one with no loan in none.
        """
        if hw.is_end_of_life(row.get("status")):
            return "retired"
        if row.get("holder"):
            return "on_loan"
        if row.get("status") == hw.REPAIR:
            return "repair"
        return "available"

    @classmethod
    def counts(cls, rows) -> dict:
        """The figures: total in service, available, in repair, out on loan."""
        figures = [cls.figure(r) for r in rows]
        return {
            "total": len(rows) - figures.count("retired"),
            "available": figures.count("available"),
            "repair": figures.count("repair"),
            "on_loan": figures.count("on_loan"),
            "retired": figures.count("retired"),
        }

    @staticmethod
    def warranty_state(value, today: date = None) -> str:
        """'' (none recorded / fine), 'soon' (within 60 days) or 'expired'."""
        from slate.core.domain.dates import parse_date
        until = parse_date(value)
        if until is None:
            return ""
        today = today or date.today()
        if until < today:
            return "expired"
        if until <= today + timedelta(days=WARRANTY_SOON_DAYS):
            return "soon"
        return ""

    def exists(self, name, exclude: str = None) -> bool:
        row = self.db.execute_query(
            "SELECT machine_name FROM hardware_inventory WHERE LOWER(machine_name) = LOWER(%s)",
            (name,), fetch="one")
        if not row:
            return False
        return not (exclude and str(dict(row)["machine_name"]).lower() == exclude.lower())

    def events(self, machine_name) -> list:
        """Status changes and renames of this machine, newest first."""
        from .migrations.workplace_schema import _table_exists
        if not _table_exists(self.db, "hardware_events"):
            return []
        rows = self.db.execute_query(
            "SELECT happened_at, done_by, what FROM hardware_events "
            "WHERE LOWER(machine_name) = LOWER(%s) ORDER BY happened_at DESC, id DESC",
            (machine_name,), fetch="all")
        return [dict(r) for r in rows or []]

    def _log(self, tx, machine_name, by, what):
        """A line for the machine's History (inside the change's own transaction)."""
        from datetime import datetime
        from .migrations.workplace_schema import _table_exists
        if _table_exists(self.db, "hardware_events"):
            tx.write("INSERT INTO hardware_events (machine_name, happened_at, done_by, what) "
                     "VALUES (%s, %s, %s, %s)",
                     (machine_name, datetime.now().replace(microsecond=0), by or None, what))

    def history(self, machine_name) -> list:
        """Every loan of this machine, newest first."""
        rows = self.db.execute_query(
            "SELECT user_id, issued_on, issued_by, returned_on, note FROM asset_assignments "
            "WHERE LOWER(machine_name) = LOWER(%s) ORDER BY issued_on DESC, id DESC",
            (machine_name,), fetch="all")
        return [dict(r) for r in rows or []]

    # ------------------------------------------------------------- writing
    def _fields(self, fields: dict) -> dict:
        allowed = set(TEXT_FIELDS) | {"purchased_on", "warranty_until"}
        present = self.columns() | {"gpu", "ram", "storage"}
        out = {}
        for key, value in fields.items():
            if key not in allowed or (key in OPTIONAL and key not in present):
                continue
            out[key] = hw.blank_to_none(value) if key in TEXT_FIELDS else (value or None)
        return out

    def add(self, name: str, fields: dict, status: str = hw.AVAILABLE):
        """A new machine (never Active - nobody has it yet). Returns the WriteResult."""
        name = str(name or "").strip()
        problem = hw.name_problem(name, (fields or {}).get("type"))
        if problem:
            raise HardwareError(problem)
        if self.exists(name):
            raise HardwareError("%s is already in the inventory. Select it and use Edit." % name)
        status = hw.normalise_status(status)
        if status not in hw.ADD_STATUSES:
            status = hw.AVAILABLE
        values = self._fields(fields)
        if "type" in self.columns():
            values.setdefault("type", "Workstation")
        if "location" in self.columns():
            # Written as nothing, not left to the column default ('N/A' on
            # PostgreSQL, '' on SQLite) - one way to say "not recorded".
            values.setdefault("location", None)
        names = ["machine_name", "status"] + sorted(values)
        params = [name, status] + [values[k] for k in sorted(values)]
        return self.db.execute_update(
            "INSERT INTO hardware_inventory (%s) VALUES (%s)"
            % (", ".join(names), ", ".join(["%s"] * len(names))), tuple(params))

    def update(self, name: str, fields: dict):
        """Change a machine's details (not its status - see set_status)."""
        values = self._fields(fields)
        if not values:
            return None
        names = sorted(values)
        return self.db.execute_update(
            "UPDATE hardware_inventory SET %s WHERE LOWER(machine_name) = LOWER(%%s)"
            % ", ".join("%s = %%s" % n for n in names),
            tuple(values[n] for n in names) + (name,))

    def status_after(self, name: str, choice: str) -> str:
        """What a status choice from Edit really means for this machine."""
        choice = hw.normalise_status(choice) if choice != hw.IN_SERVICE else choice
        if choice == hw.IN_SERVICE:
            return hw.status_for_service(bool(self.holder(name)))
        return choice

    def set_status(self, name: str, choice: str, by: str = "", collect: bool = False) -> str:
        """
        Apply a status chosen in Edit. Returns the status the machine now has.

        In service resolves from the ledger. Repair, Retired, Lost or Disposed
        on a machine somebody holds raises HardwareError unless collect=True,
        in which case it is collected back first (the loan closed, so the
        leaving checklist stops waiting for it).
        """
        target = self.status_after(name, choice)
        holder = self.holder(name)
        if target in (hw.REPAIR,) + hw.END_OF_LIFE and holder:
            if not collect:
                from slate.core.domain import people
                raise HardwareError("%s is out with %s. Collect it back first."
                                    % (name, people.display_name(holder)))
            if not self.service.return_machine(name, holder):
                raise HardwareError("%s could not be collected back, so its status was not changed." % name)
        try:
            with atomic(self.db) as tx:
                tx.write("UPDATE hardware_inventory SET status = %s WHERE LOWER(machine_name) = LOWER(%s)",
                         (target, name), expect_rows=True)
                self._log(tx, name, by, "Status set to %s" % target)
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            raise HardwareError("%s was not changed: %s" % (
                name, str(exc) or "it is no longer in the inventory"))
        return target

    def rename(self, old: str, new: str, by: str = "") -> bool:
        """Rename a machine and everything that names it, together."""
        new = str(new or "").strip()
        kind = self.db.execute_query("SELECT type FROM hardware_inventory WHERE LOWER(machine_name) = LOWER(%s)",
                                     (old,), fetch="one") if "type" in self.columns() else None
        problem = hw.name_problem(new, dict(kind).get("type") if kind else None)
        if problem:
            raise HardwareError(problem)
        if new.lower() != old.lower() and self.exists(new):
            raise HardwareError("Another machine is already called %s." % new)
        from .migrations.workplace_schema import _table_exists, _column_exists
        with atomic(self.db) as tx:
            tx.write("UPDATE hardware_inventory SET machine_name = %s WHERE LOWER(machine_name) = LOWER(%s)",
                     (new, old), expect_rows=True)
            tx.write("UPDATE asset_assignments SET machine_name = %s WHERE LOWER(machine_name) = LOWER(%s)",
                     (new, old))
            if _table_exists(self.db, "hardware_events"):
                tx.write("UPDATE hardware_events SET machine_name = %s WHERE LOWER(machine_name) = LOWER(%s)",
                         (new, old))
            self._log(tx, new, by, "Renamed from %s" % old)
            if _table_exists(self.db, "onboarding_workflows") and _column_exists(
                    self.db, "onboarding_workflows", "asset_name"):
                tx.write("UPDATE onboarding_workflows SET asset_name = %s "
                         "WHERE LOWER(asset_name) = LOWER(%s)", (new, old))
            if _table_exists(self.db, "it_deployments"):
                tx.write("UPDATE it_deployments SET target_machine = %s "
                         "WHERE LOWER(target_machine) = LOWER(%s)", (new, old))
        return True

    def delete(self, name: str):
        """
        Remove a machine entered by mistake. One that was ever issued has a
        history; it is retired instead, so the history stays.
        """
        if self.holder(name):
            raise HardwareError("%s is out on loan. Collect it back first." % name)
        if self.history(name):
            raise HardwareError(
                "%s has been issued before, so deleting it would lose who had it and when. "
                "Set its status to Retired, Lost or Disposed instead (Edit)." % name)
        result = self.db.execute_update(
            "DELETE FROM hardware_inventory WHERE LOWER(machine_name) = LOWER(%s)", (name,))
        if not getattr(result, "changed", bool(result)):
            raise HardwareError("%s was not deleted: %s" % (
                name, getattr(result, "error", "") or "it is no longer in the inventory"))
        return result

    # ------------------------------------------------------------- Live Ops
    @staticmethod
    def read_report(path: Path) -> dict:
        """A Live Ops report as specs: cpu, gpu, ram, storage ('' when missing)."""
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("not a Live Ops report")
        name = data.get("ComputerName") or data.get("pc_name") or Path(path).stem
        return {
            "machine_name": str(name or "").strip(),
            "cpu": str(data.get("CPU") or "").strip(),
            "gpu": str(data.get("GPU") or "").strip(),
            "ram": hw.gb_text(data.get("RAM_GB")),
            "storage": hw.storage_text(data.get("Drives")),
        }

    def sync_from_reports(self, status_dir: Path) -> dict:
        """
        Add machines Live Ops has seen; refresh the specs of known ones without
        ever blanking a value somebody typed. Returns {added, updated, failed,
        unreadable: [file names], bad_names: [names], end_of_life: [(name,
        status)]} - a Retired, Lost or Disposed machine reporting in is named,
        not refreshed in silence.
        """
        summary = {"added": 0, "updated": 0, "failed": 0, "unreadable": [], "names": [],
                   "bad_names": [], "end_of_life": []}
        statuses = {str(r.get("machine_name") or "").lower(): r.get("status")
                    for r in self.machines()}
        for path in sorted(Path(status_dir).glob("*.json")):
            try:
                spec = self.read_report(path)
            except Exception as exc:
                logger.warning("Live Ops report %s could not be read: %s", path.name, exc)
                summary["unreadable"].append(path.name)
                continue
            name = spec["machine_name"]
            if not name:
                summary["unreadable"].append(path.name)
                continue
            if hw.name_problem(name):
                summary["bad_names"].append(name)
                continue
            if hw.is_end_of_life(statuses.get(name.lower())):
                summary["end_of_life"].append((name, statuses[name.lower()]))
            try:
                if self.exists(name):
                    result = self.db.execute_update(
                        "UPDATE hardware_inventory SET "
                        "cpu = COALESCE(NULLIF(%s, ''), cpu), gpu = COALESCE(NULLIF(%s, ''), gpu), "
                        "ram = COALESCE(NULLIF(%s, ''), ram), storage = COALESCE(NULLIF(%s, ''), storage) "
                        "WHERE LOWER(machine_name) = LOWER(%s)",
                        (spec["cpu"], spec["gpu"], spec["ram"], spec["storage"], name))
                    summary["updated" if result else "failed"] += 1
                else:
                    # No owner: who was sitting at it is not who it was issued to.
                    result = self.add(name, {k: spec[k] for k in ("cpu", "gpu", "ram", "storage")})
                    if result:
                        summary["added"] += 1
                        summary["names"].append(name)
                    else:
                        summary["failed"] += 1
            except DatabaseUnavailableError:
                raise
            except Exception as exc:
                logger.warning("Live Ops sync could not save %s: %s", name, exc)
                summary["failed"] += 1
        return summary
