"""
The jobs that keep the database healthy, and a record of when they last ran.

Vacuum, analyze and reindex all existed as bundled executables and none of them
was ever run. Comp-off crediting is the same story one layer up: the service was
written, nothing called it, and the ledger stayed empty for ever.

The record of when each job last ran is the point of this module as much as the
running is. A maintenance job with no last-run time is one nobody can tell has
stopped - and the way these fail is silently, by not happening.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


JOBS = (
    ("vacuum", "Vacuum and analyze",
     "Reclaims space from deleted rows and refreshes the planner's statistics. "
     "Without it queries get slower as the database is used.", 7),
    ("reindex", "Rebuild indexes",
     "Rebuilds the indexes. Worth doing after a large ingest or a restore.", 30),
    ("backup", "Backup",
     "A dump of the whole database, kept under the retention you set.", 1),
    ("comp_off", "Credit comp off",
     "Turns the attendance record into comp-off days people have earned back.", 1),
)

JOB_TITLES = {key: title for key, title, _why, _days in JOBS}
JOB_WHY = {key: why for key, _title, why, _days in JOBS}
JOB_EXPECTED_DAYS = {key: days for key, _title, _why, days in JOBS}


def _no_window():
    if os.name != "nt":
        return {}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"startupinfo": info, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


class MaintenanceLog:
    """
    When each job last ran, and what happened.

    Kept beside the database rather than in the program folder, so updating the
    software never erases the history - the same reason the pool keeps its config
    there.
    """

    def __init__(self, path):
        self.path = Path(str(path))

    def _read(self) -> dict:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError) as exc:
            logger.debug("Could not read the maintenance log: %s", exc)
        return {}

    def record(self, job: str, ok: bool, message: str = "") -> None:
        data = self._read()
        data[job] = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "ok": bool(ok),
            "message": str(message or "")[:400],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write the maintenance log: %s", exc)

    def last(self, job: str) -> dict:
        entry = self._read().get(job) or {}
        at = entry.get("at")
        when = None
        if at:
            try:
                when = datetime.fromisoformat(at)
            except ValueError:
                when = None
        return {"at": when, "ok": entry.get("ok"), "message": entry.get("message", "")}

    def ran_today(self, job: str) -> bool:
        when = self.last(job)["at"]
        return bool(when and when.date() == datetime.now().date())

    def summary(self) -> list:
        """
        Every job with its last run and whether it is overdue.

        Overdue is worked out from how often the job is meant to happen, so the
        screen can say "27 days late" rather than only printing a date and
        leaving the arithmetic to whoever is reading it.
        """
        out = []
        now = datetime.now()
        for key, title, why, expected_days in JOBS:
            last = self.last(key)
            when = last["at"]
            age_days = None if when is None else (now - when).days
            overdue = when is None or age_days > expected_days

            if when is None:
                state = "never run"
            elif last["ok"] is False:
                state = "failed"
            elif overdue:
                state = "%d day(s) late" % max(0, age_days - expected_days)
            else:
                state = "up to date"

            out.append({
                "job": key,
                "title": title,
                "why": why,
                "every_days": expected_days,
                "last_at": when,
                "last_ok": last["ok"],
                "last_message": last["message"],
                "age_days": age_days,
                "overdue": overdue,
                "state": state,
            })
        return out


class Maintenance:
    """Runs the jobs, and records that it did."""

    def __init__(self, bin_dir, data_dir, port: int = 5440, dbname: str = ""):
        self.bin_dir = Path(str(bin_dir))
        self.data_dir = Path(str(data_dir))
        self.port = int(port)
        if not dbname:
            try:
                from slate_server.core.db_credentials import database_name
                dbname = database_name()
            except Exception:
                dbname = "ut_vfx"
        self.dbname = dbname
        self.log = MaintenanceLog(self.data_dir.parent / "maintenance.json")

    def _run_tool(self, exe_name: str, extra_args: list, job: str, label: str,
                  progress=None) -> dict:
        exe = self.bin_dir / exe_name
        if not exe.exists():
            outcome = {"ok": False, "message": "%s is not in %s." % (exe_name, self.bin_dir)}
            self.log.record(job, False, outcome["message"])
            return outcome

        if progress:
            progress("%s..." % label)

        from slate_server.core.db_credentials import admin_user, env_with_password
        command = [str(exe), "--host", "127.0.0.1", "--port", str(self.port),
                   "--username", admin_user(), "--no-password",
                   "--dbname", self.dbname] + list(extra_args)

        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    env=env_with_password(), timeout=60 * 60,
                                    **_no_window())
        except subprocess.TimeoutExpired:
            outcome = {"ok": False, "message": "%s took over an hour and was stopped." % label}
            self.log.record(job, False, outcome["message"])
            return outcome
        except OSError as exc:
            outcome = {"ok": False, "message": "Could not run %s: %s" % (exe_name, exc)}
            self.log.record(job, False, outcome["message"])
            return outcome

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            outcome = {"ok": False, "message": "%s failed: %s"
                                               % (label, detail[-1] if detail else "no reason given")}
            self.log.record(job, False, outcome["message"])
            return outcome

        outcome = {"ok": True, "message": "%s finished." % label}
        self.log.record(job, True, outcome["message"])
        return outcome

    def vacuum(self, progress=None) -> dict:
        return self._run_tool("vacuumdb.exe", ["--analyze"], "vacuum",
                              "Vacuum and analyze", progress)

    def reindex(self, progress=None) -> dict:
        return self._run_tool("reindexdb.exe", [], "reindex",
                              "Rebuilding indexes", progress)

    def credit_comp_off(self, progress=None) -> dict:
        """
        Turn the attendance record into comp-off days.

        Skipped, rather than failed, when the studio does not operate comp off -
        a job that reports failure for doing the right thing teaches people to
        ignore the column.
        """
        try:
            from slate.core.domain import leave_policy as lp
            if not lp.policy(None).get("comp_off_enabled"):
                message = "This studio does not operate comp off - nothing to do."
                self.log.record("comp_off", True, message)
                return {"ok": True, "message": message, "skipped": True}

            from slate.core.domain.comp_off_service import CompOffService
            if progress:
                progress("Reviewing the attendance record...")
            result = CompOffService().run()
            message = "Credited %d of %d qualifying day(s)." % (
                result.get("credited", 0), result.get("found", 0))
            self.log.record("comp_off", True, message)
            return {"ok": True, "message": message}
        except Exception as exc:
            message = "Comp off crediting failed: %s" % exc
            self.log.record("comp_off", False, message)
            return {"ok": False, "message": message}

    def record_backup(self, ok: bool, message: str) -> None:
        """Backups are taken by BackupEngine; this is how they get on the list."""
        self.log.record("backup", ok, message)

    def due(self) -> list:
        """Jobs that are overdue, for a caller that wants to run them unattended."""
        return [row["job"] for row in self.log.summary() if row["overdue"]]
