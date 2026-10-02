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
        Every machine, with who holds it on the ledger (holder) and their name.
        Raises on a refused read - a failed read is not an empty inventory.
        """
        extra = sorted(self.columns() - {"location", "cpu"})
        rows = self.db.execute_query(
            "SELECT h.id, h.machine_name, h.gpu, h.cpu, h.storage, h.ram, h.status, h.location, "
            "       h.assigned_to%s, a.user_id AS holder, u.display_name "
            "FROM hardware_inventory h "
            "LEFT JOIN asset_assignments a ON LOWER(a.machine_name) = LOWER(h.machine_name) "
            "     AND a.returned_on IS NULL "
            "LEFT JOIN ut_users u ON LOWER(u.username) = LOWER(COALESCE(a.user_id, h.assigned_to)) "
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
            out.append(r)
        return out

    @staticmethod
    def counts(rows) -> dict:
        """The figures: total in service, available, in repair, out on loan."""
        live = [r for r in rows if not hw.is_end_of_life(r.get("status"))]
        return {
            "total": len(live),
            "available": sum(1 for r in live if r.get("status") == hw.AVAILABLE and not r.get("holder")),
            "repair": sum(1 for r in live if r.get("status") == hw.REPAIR),
            "on_loan": sum(1 for r in live if r.get("holder")),
            "retired": len(rows) - len(live),
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
        problem = hw.name_problem(name)
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
        result = self.db.execute_update(
            "UPDATE hardware_inventory SET status = %s WHERE LOWER(machine_name) = LOWER(%s)",
            (target, name))
        if not getattr(result, "changed", bool(result)):
            raise HardwareError("%s was not changed: %s" % (
                name, getattr(result, "error", "") or "it is no longer in the inventory"))
        return target

    def rename(self, old: str, new: str) -> bool:
        """Rename a machine and everything that names it, together."""
        new = str(new or "").strip()
        problem = hw.name_problem(new)
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
        unreadable: [file names]}.
        """
        summary = {"added": 0, "updated": 0, "failed": 0, "unreadable": [], "names": []}
        for path in sorted(Path(status_dir).glob("*.json")):
            try:
                spec = self.read_report(path)
            except Exception as exc:
                logger.warning("Live Ops report %s could not be read: %s", path.name, exc)
                summary["unreadable"].append(path.name)
                continue
            name = spec["machine_name"]
            if not name or hw.name_problem(name):
                summary["unreadable"].append(path.name)
                continue
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
