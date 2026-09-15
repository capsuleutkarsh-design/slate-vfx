"""
Backing the studio's database up, and putting it back.

Everything needed for this has been sitting in the install all along:
``pg_dump.exe``, ``pg_restore.exe`` and ``psql.exe`` are bundled beside the
server, and there is a backup script in tools. What there was not, anywhere, was
a button - so taking a backup required knowing that a script existed and how to
run it. For the only copy of a studio's tracking data, that is the gap that
matters most.

Two rules it holds to:

    a backup is verified      pg_dump writes to a temporary name and is renamed
                              only after it exits cleanly, so a half-written
                              dump can never be mistaken for a good one

    a restore is refused      unless the caller says explicitly that it may
                              overwrite. Restoring is the one operation here
                              that destroys data
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Dumps are named so they sort by date and so the database they came from is
# obvious from the filename alone. Restoring the wrong database is a bad
# afternoon.
NAME_PATTERN = "slate_%(database)s_%(stamp)s.dump"
_NAME_RE = re.compile(r"^slate_(?P<database>.+)_(?P<stamp>\d{8}_\d{6})\.dump$")

DEFAULT_KEEP_DAYS = 30
DEFAULT_KEEP_AT_LEAST = 7


def _no_window():
    """Keep the console window hidden when the GUI shells out."""
    if os.name != "nt":
        return {}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"startupinfo": info, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


class BackupEngine:
    """pg_dump and pg_restore, wrapped so a screen can drive them."""

    def __init__(self, bin_dir, backup_dir, port: int = 5440, dbname: str = ""):
        self.bin_dir = Path(str(bin_dir))
        self.backup_dir = Path(str(backup_dir))
        self.port = int(port)
        if not dbname:
            try:
                from slate_server.core.db_credentials import database_name
                dbname = database_name()
            except Exception:
                dbname = "ut_vfx"
        self.dbname = dbname

    # ------------------------------------------------------------------ tools
    @property
    def pg_dump(self) -> Path:
        return self.bin_dir / "pg_dump.exe"

    @property
    def pg_restore(self) -> Path:
        return self.bin_dir / "pg_restore.exe"

    def is_available(self) -> bool:
        return self.pg_dump.exists() and self.pg_restore.exists()

    def _env(self):
        from slate_server.core.db_credentials import env_with_password
        return env_with_password()

    # ----------------------------------------------------------------- listing
    def backups(self) -> list:
        """
        Every backup on disk, newest first, with its age and size.

        Age is the figure that matters on a dashboard: "last backup 14 days ago"
        is a sentence somebody acts on, where a filename is not.
        """
        out = []
        if not self.backup_dir.exists():
            return out

        for path in self.backup_dir.glob("*.dump"):
            match = _NAME_RE.match(path.name)
            taken = None
            if match:
                try:
                    taken = datetime.strptime(match.group("stamp"), "%Y%m%d_%H%M%S")
                except ValueError:
                    taken = None
            if taken is None:
                try:
                    taken = datetime.fromtimestamp(path.stat().st_mtime)
                except OSError:
                    continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 0

            out.append({
                "path": path,
                "name": path.name,
                "database": match.group("database") if match else "",
                "taken_at": taken,
                "age_days": max(0, (datetime.now() - taken).days),
                "size_bytes": size,
            })

        out.sort(key=lambda row: row["taken_at"], reverse=True)
        return out

    def latest(self):
        found = self.backups()
        return found[0] if found else None

    # ------------------------------------------------------------------- write
    def back_up(self, progress=None) -> dict:
        """
        Take a backup now. Returns {"ok", "path", "message"}.

        Written to a .partial name and renamed on success, so a dump interrupted
        half way through is never left looking like a usable one - which is the
        failure that turns a backup policy into a false sense of security.
        """
        def say(message):
            logger.info(message)
            if progress:
                progress(message)

        if not self.is_available():
            return {"ok": False, "path": None,
                    "message": "pg_dump is not in %s, so no backup can be taken."
                               % self.bin_dir}

        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "path": None,
                    "message": "Cannot write to %s: %s" % (self.backup_dir, exc)}

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.backup_dir / (NAME_PATTERN % {"database": self.dbname, "stamp": stamp})
        partial = target.with_suffix(".partial")

        say("Backing up %s to %s" % (self.dbname, target.name))
        command = [
            str(self.pg_dump),
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "--username", self._username(),
            "--dbname", self.dbname,
            "--format", "custom",
            "--file", str(partial),
            "--no-password",
        ]

        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    env=self._env(), timeout=60 * 60, **_no_window())
        except subprocess.TimeoutExpired:
            partial.unlink(missing_ok=True)
            return {"ok": False, "path": None,
                    "message": "The backup took over an hour and was stopped."}
        except OSError as exc:
            partial.unlink(missing_ok=True)
            return {"ok": False, "path": None, "message": "Could not run pg_dump: %s" % exc}

        if result.returncode != 0:
            partial.unlink(missing_ok=True)
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            return {"ok": False, "path": None,
                    "message": "pg_dump failed: %s" % (detail[-1] if detail else "no reason given")}

        if not partial.exists() or partial.stat().st_size == 0:
            partial.unlink(missing_ok=True)
            return {"ok": False, "path": None,
                    "message": "pg_dump reported success but wrote nothing."}

        partial.replace(target)
        say("Backup complete: %s" % self._describe(target))
        return {"ok": True, "path": target,
                "message": "Backed up to %s (%s)." % (target.name, self._describe(target))}

    def _username(self) -> str:
        try:
            from slate_server.core.db_credentials import admin_user
            return admin_user()
        except Exception:
            return "postgres"

    @staticmethod
    def _describe(path: Path) -> str:
        from slate_server.core.server_facts import human_size
        try:
            return human_size(path.stat().st_size)
        except OSError:
            return "unknown size"

    # ----------------------------------------------------------------- retention
    def prune(self, keep_days: int = DEFAULT_KEEP_DAYS,
              keep_at_least: int = DEFAULT_KEEP_AT_LEAST, apply: bool = True) -> list:
        """
        Delete backups older than keep_days, but never the newest keep_at_least.

        The floor matters. A studio that has not run the server for two months
        comes back to every backup being older than the retention window, and a
        policy that only looked at age would delete all of them at once.
        """
        found = self.backups()
        cutoff = datetime.now() - timedelta(days=max(0, int(keep_days)))
        keep_at_least = max(0, int(keep_at_least))

        removed = []
        for row in found[keep_at_least:]:
            if row["taken_at"] < cutoff:
                if apply:
                    try:
                        row["path"].unlink()
                    except OSError as exc:
                        logger.warning("Could not remove %s: %s", row["name"], exc)
                        continue
                removed.append(row)
        return removed

    # ------------------------------------------------------------------ restore
    def restore(self, dump_path, confirm_overwrite: bool = False, progress=None) -> dict:
        """
        Put a backup back, over the top of what is there now.

        Refused unless the caller has said in as many words that overwriting is
        intended. This is the only operation in this module that destroys data,
        and it destroys all of it.
        """
        def say(message):
            logger.info(message)
            if progress:
                progress(message)

        dump_path = Path(str(dump_path))
        if not confirm_overwrite:
            return {"ok": False, "message":
                    "Restoring replaces everything in %s. Nothing was done, "
                    "because the caller did not confirm." % self.dbname}
        if not dump_path.exists():
            return {"ok": False, "message": "%s is not there." % dump_path}
        if not self.is_available():
            return {"ok": False, "message": "pg_restore is not in %s." % self.bin_dir}

        say("Restoring %s over %s" % (dump_path.name, self.dbname))
        command = [
            str(self.pg_restore),
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "--username", self._username(),
            "--dbname", self.dbname,
            "--clean", "--if-exists",
            "--no-owner",
            "--no-password",
            str(dump_path),
        ]

        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    env=self._env(), timeout=60 * 60, **_no_window())
        except subprocess.TimeoutExpired:
            return {"ok": False, "message": "The restore took over an hour and was stopped."}
        except OSError as exc:
            return {"ok": False, "message": "Could not run pg_restore: %s" % exc}

        # pg_restore reports a non-zero code for warnings as well as failures,
        # so the message carries what it said rather than only whether it was
        # happy. A restore that half worked is something a person has to read.
        detail = (result.stderr or "").strip().splitlines()
        if result.returncode != 0:
            return {"ok": False,
                    "message": "pg_restore finished with problems: %s"
                               % (detail[-1] if detail else "no reason given")}

        say("Restore complete.")
        return {"ok": True, "message": "Restored %s. Restart Slate on every "
                                       "workstation." % dump_path.name}
