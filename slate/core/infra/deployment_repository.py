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
PACKAGE_MAX = 120
VERSION_MAX = 60


def _check_lengths(package: str, version: str, machines) -> None:
    """A 400-character package name went in without complaint."""
    from slate.core.domain.hardware import NAME_MAX
    if len(package or "") > PACKAGE_MAX:
        raise DeploymentError("Keep the package to %d characters." % PACKAGE_MAX)
    if len((version or "").strip()) > VERSION_MAX:
        raise DeploymentError("Keep the version to %d characters." % VERSION_MAX)
    long = [m for m in machines if len(m) > NAME_MAX]
    if long:
        raise DeploymentError("A machine name is at most %d characters: %s" % (NAME_MAX, long[0][:40]))


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

    def retired_machines(self) -> dict:
        """{lower-case name: status} of machines at the end of their life (Retired, Lost, Disposed)."""
        from slate.core.domain import hardware as hw
        rows = self.db.execute_query("SELECT machine_name, status FROM hardware_inventory",
                                     fetch="all") or []
        return {str(dict(r)["machine_name"]).lower(): hw.normalise_status(dict(r)["status"])
                for r in rows if hw.is_end_of_life(dict(r)["status"])}

    def record(self, package: str, machines: Iterable[str], by: str, version: str = "",
               notes: str = "") -> List[int]:
        """One Pending record per machine. Returns the new ids."""
        package = (package or "").strip()
        machines = [m for m in machines if m]
        if not package or not machines:
            raise DeploymentError("Give both the package and at least one machine.")
        _check_lengths(package, version, machines)
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
        _check_lengths(package, version, [machine])
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
            # Worded like the rest of the screen: the person's name, the studio's date.
            from slate.core.domain import people
            from slate.core.domain.dates import format_date
            line = "%s (%s, %s): %s" % (status, people.display_name(by) or by or "unknown",
                                        format_date(datetime.now().date()), note.strip())
            sets["notes"] = (existing + chr(10) + line) if existing else line
        names = sorted(sets)
        result = self.db.execute_update(
            "UPDATE it_deployments SET %s WHERE id = %%s" % ", ".join("%s = %%s" % n for n in names),
            tuple(sets[n] for n in names) + (int(dep_id),))
        if not getattr(result, "changed", bool(result)):
            raise DeploymentError(getattr(result, "error", "") or "That record is no longer there.")
        return result

    def restore(self, record: dict):
        """Put an outcome back as it was (Undo after Mark success / failed)."""
        sets = {"status": normalise_status(record.get("status"))}
        for column in ("completed_at", "completed_by", "notes"):
            if column in self.columns():
                sets[column] = record.get(column)
        names = sorted(sets)
        result = self.db.execute_update(
            "UPDATE it_deployments SET %s WHERE id = %%s" % ", ".join("%s = %%s" % n for n in names),
            tuple(sets[n] for n in names) + (int(record["id"]),))
        if not getattr(result, "changed", bool(result)):
            raise DeploymentError(getattr(result, "error", "") or "That record is no longer there.")
        return result

    def delete(self, dep_id):
        result = self.db.execute_update("DELETE FROM it_deployments WHERE id = %s", (int(dep_id),))
        if not getattr(result, "changed", bool(result)):
            raise DeploymentError(getattr(result, "error", "") or "That record is no longer there.")
        return result
