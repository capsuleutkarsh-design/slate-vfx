"""
The deployment log: a record of what was installed on which machine.

Slate does not install anything; somebody records what they did. So the
record has to be correctable (Edit, Delete), say why something failed
(Notes), which version went on (Version), and who recorded the outcome and
when (completed_by / completed_at) - "Deployed At" is when the row was typed.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, List, Optional

try:
    from .db_results import DatabaseUnavailableError
except ImportError:                                   # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""

logger = logging.getLogger(__name__)

STATUSES = ("Pending", "Success", "Failed")
OPTIONAL = ("version", "notes", "completed_at", "completed_by")


class DeploymentError(ValueError):
    """A refused change, with a sentence a person can act on."""


def split_machines(text: str) -> List[str]:
    """'WS-01, WS-02; WS-03' -> ['WS-01', 'WS-02', 'WS-03'] (no duplicates, order kept)."""
    seen, out = set(), []
    for part in str(text or "").replace(";", ",").replace("\n", ",").split(","):
        name = part.strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def success_rate(rows: Iterable[dict]) -> Optional[int]:
    """Successes out of the finished ones, rounded (2 of 3 is 67%); None when none finished."""
    rows = list(rows)
    ok = sum(1 for r in rows if normalise_status(r.get("status")) == "Success")
    bad = sum(1 for r in rows if normalise_status(r.get("status")) == "Failed")
    if not ok + bad:
        return None
    return int(round(100.0 * ok / (ok + bad)))


def normalise_status(value) -> str:
    text = str(value or "").strip()
    for known in STATUSES:
        if known.lower() == text.lower():
            return known
    return text or "Pending"


class DeploymentRepository:
    def __init__(self, db=None):
        if db is None:
            from .database_manager import database_manager
            db = database_manager
        self.db = db
        self._cols = None

    def columns(self) -> set:
        if self._cols is None:
            from .migrations.workplace_schema import _column_exists
            self._cols = {c for c in OPTIONAL if _column_exists(self.db, "it_deployments", c)}
        return self._cols

    def all(self) -> list:
        rows = self.db.execute_query(
            "SELECT id, package_name, target_machine, deployed_by, status, deployed_at%s "
            "FROM it_deployments ORDER BY id DESC" % "".join(", " + c for c in sorted(self.columns())),
            fetch="all")
        if rows is None:
            reason = self.db.last_error() if hasattr(self.db, "last_error") else ""
            raise RuntimeError("The deployment log could not be read. %s" % (reason or ""))
        out = []
        for r in rows:
            r = dict(r)
            r["status"] = normalise_status(r.get("status"))
            out.append(r)
        return out

    def known_machines(self) -> List[str]:
        rows = self.db.execute_query(
            "SELECT machine_name FROM hardware_inventory ORDER BY LOWER(machine_name)", fetch="all") or []
        return [dict(r)["machine_name"] for r in rows]

    def record(self, package: str, machines: Iterable[str], by: str, version: str = "",
               notes: str = "") -> List[int]:
        """One Pending record per machine. Returns the new ids."""
        package = (package or "").strip()
        machines = [m for m in machines if m]
        if not package or not machines:
            raise DeploymentError("Give both the package and at least one machine.")
        by = (by or "").strip() or "unknown"
        extra = [c for c in ("version", "notes") if c in self.columns()]
        values_extra = {"version": (version or "").strip() or None, "notes": (notes or "").strip() or None}
        ids = []
        from .transaction import atomic
        with atomic(self.db) as tx:
            for machine in machines:
                names = ["package_name", "target_machine", "deployed_by", "status", "deployed_at"] + extra
                params = [package, machine, by, "Pending", datetime.now().replace(microsecond=0)] + \
                         [values_extra[c] for c in extra]
                result = tx.write("INSERT INTO it_deployments (%s) VALUES (%s) RETURNING id"
                                  % (", ".join(names), ", ".join(["%s"] * len(names))), tuple(params))
                ids.append(result.last_id)
        return ids

    def update(self, dep_id, package: str, machine: str, version: str = "", notes: str = ""):
        package, machine = (package or "").strip(), (machine or "").strip()
        if not package or not machine:
            raise DeploymentError("Give both the package and the machine.")
        sets = {"package_name": package, "target_machine": machine}
        if "version" in self.columns():
            sets["version"] = (version or "").strip() or None
        if "notes" in self.columns():
            sets["notes"] = (notes or "").strip() or None
        names = sorted(sets)
        result = self.db.execute_update(
            "UPDATE it_deployments SET %s WHERE id = %%s" % ", ".join("%s = %%s" % n for n in names),
            tuple(sets[n] for n in names) + (int(dep_id),))
        if not getattr(result, "changed", bool(result)):
            raise DeploymentError(getattr(result, "error", "") or "That record is no longer there.")
        return result

    def set_outcome(self, dep_id, status: str, by: str, note: str = None):
        """Mark Success / Failed (or back to Pending), recording who and when."""
        status = normalise_status(status)
        sets = {"status": status}
        if "completed_at" in self.columns():
            finished = status in ("Success", "Failed")
            sets["completed_at"] = datetime.now().replace(microsecond=0) if finished else None
            sets["completed_by"] = (by or "unknown") if finished else None
        if note and note.strip() and "notes" in self.columns():
            # Added to what the record already says - it used to replace it,
            # losing the notes typed when the install was recorded.
            row = self.db.execute_query("SELECT notes FROM it_deployments WHERE id = %s",
                                        (int(dep_id),), fetch="one")
            existing = str((dict(row).get("notes") if row else "") or "").strip()
            stamp = datetime.now().strftime("%d %b %Y")
            line = "%s (%s, %s): %s" % (status, by or "unknown", stamp, note.strip())
            sets["notes"] = (existing + chr(10) + line) if existing else line
        names = sorted(sets)
        result = self.db.execute_update(
            "UPDATE it_deployments SET %s WHERE id = %%s" % ", ".join("%s = %%s" % n for n in names),
            tuple(sets[n] for n in names) + (int(dep_id),))
        if not getattr(result, "changed", bool(result)):
            raise DeploymentError(getattr(result, "error", "") or "That record is no longer there.")
        return result

    def delete(self, dep_id):
        result = self.db.execute_update("DELETE FROM it_deployments WHERE id = %s", (int(dep_id),))
        if not getattr(result, "changed", bool(result)):
            raise DeploymentError(getattr(result, "error", "") or "That record is no longer there.")
        return result
