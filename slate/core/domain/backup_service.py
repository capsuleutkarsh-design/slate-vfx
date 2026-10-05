"""
The workstation's automatic database backup.

Backups are the server's job: Slate Server has pg_dump beside it and a screen
to take, list and restore them. A workstation has neither, and the installed
build does not carry the script this used to launch - so on every studio
machine this thread woke up every twelve hours, logged "Backup script not
found", and went back to sleep. Forever.

It now does one of two things. On a machine that has pg_dump - a developer
checkout with the server binaries, or a machine where SLATE_PG_DUMP points at
one - it takes a dump itself, in this process, into the studio's Backups
folder, and keeps thirty days of them. On any other machine it says once,
at INFO, that backups are taken on the server, and stops.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread

logger = logging.getLogger(__name__)

RETENTION_DAYS = int(os.environ.get("SLATE_BACKUP_RETENTION_DAYS", "30"))


def find_pg_dump() -> Path | None:
    """pg_dump on this machine, or None. Nothing is downloaded or guessed."""
    override = os.environ.get("SLATE_PG_DUMP")
    if override and Path(override).exists():
        return Path(override)
    # A checkout with the server's binaries beside the package.
    root = Path(__file__).resolve().parent.parent.parent.parent
    bundled = root / "slate_server" / "bin" / "pgsql" / "bin" / "pg_dump.exe"
    if bundled.exists():
        return bundled
    return None


def backup_dir() -> Path:
    """Where dumps go: the studio's shared folder if it is reachable, else local."""
    override = os.environ.get("SLATE_BACKUP_DIR")
    if override:
        return Path(override)
    try:
        from slate.core.infra.global_config import GlobalConfig
        root = Path(str(GlobalConfig.server_root()))
        if root.exists():
            return root / "Backups" / "Workstation"
    except Exception as exc:
        logger.debug("Backup folder: could not read SERVER_ROOT (%s)", exc)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(local) / "Slate" / "Backups" / "Database"


def _record(ok: bool, message: str) -> None:
    """The outcome on the Audit Logs screen, where an admin will see it."""
    try:
        from slate.core.infra.audit_logger import AuditLogger
        AuditLogger().log_event("BACKUP", "SYSTEM", f"{socket.gethostname()}: {message}",
                                "SUCCESS" if ok else "FAILURE")
    except Exception as exc:
        logger.warning("Backup outcome not written to the audit log: %s", exc)


def take_backup(pg_dump: Path, target_dir: Path) -> Path | None:
    """One dump of the studio database. Returns the file, or None with the reason logged."""
    from slate.core.infra.local_secrets import db_settings

    settings = db_settings()
    if not settings.get("password"):
        logger.warning("Backup skipped: no database password on this machine.")
        _record(False, "Workstation backup skipped: no database password on this machine.")
        return None

    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    target = target_dir / f"slate_backup_{stamp}.dump"
    partial = target.with_suffix(".partial")

    env = dict(os.environ, PGPASSWORD=settings["password"])
    command = [str(pg_dump), "-h", settings["host"], "-p", str(settings["port"]),
               "-U", settings["user"], "-F", "c", "-b", "-f", str(partial),
               # The studio's tables only. The server's own part (slate_secure:
               # hidden passwords, the fleet key) this account may not read,
               # and the server's backup has it.
               "--schema=public", settings["dbname"]]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(command, capture_output=True, text=True, env=env,
                                timeout=3600, creationflags=creationflags)
    except (OSError, subprocess.SubprocessError) as exc:
        partial.unlink(missing_ok=True)
        logger.error("Backup could not run pg_dump: %s", exc)
        _record(False, f"Workstation backup could not run pg_dump: {exc}")
        return None
    if result.returncode != 0 or not partial.exists() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        reason = detail[-1] if detail else "pg_dump gave no reason"
        logger.error("Backup failed: %s", reason)
        _record(False, f"Workstation backup failed: {reason}")
        return None

    partial.replace(target)
    logger.info("Backup written: %s", target)
    _record(True, f"Workstation backup written: {target}")
    return target


def prune(target_dir: Path, keep_days: int = RETENTION_DAYS) -> int:
    """Remove dumps older than keep_days. Returns how many went."""
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for path in target_dir.glob("slate_backup_*"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError as exc:
            logger.warning("Could not remove old backup %s: %s", path.name, exc)
    return removed


class AutoBackupThread(QThread):
    """Takes a backup every interval, on machines that are able to."""

    def __init__(self, interval_hours=12, parent=None):
        super().__init__(parent)
        self.interval_seconds = interval_hours * 3600
        self.running = True
        self.pg_dump = find_pg_dump()

    def run(self):
        if self.pg_dump is None:
            logger.info("Automatic backups are taken on Slate Server; this workstation "
                        "has no pg_dump, so none are taken here.")
            return

        logger.info("AutoBackupThread started. Interval: %.1f hours, pg_dump: %s",
                    self.interval_seconds / 3600, self.pg_dump)

        # Initial wait so it doesn't slow down startup (wait 5 minutes)
        if not self._sleep(300):
            return

        while self.running:
            self._run_backup()
            if not self._sleep(int(self.interval_seconds)):
                return

    def _sleep(self, seconds: int) -> bool:
        for _ in range(seconds):
            if not self.running:
                return False
            time.sleep(1)
        return self.running

    def _run_backup(self):
        try:
            target_dir = backup_dir()
            if take_backup(self.pg_dump, target_dir):
                gone = prune(target_dir)
                if gone:
                    logger.info("Removed %d backup(s) older than %d days.", gone, RETENTION_DAYS)
        except Exception as exc:
            logger.error("AutoBackupThread: backup did not run: %s", exc)
            _record(False, f"Workstation backup did not run: {exc}")

    def stop(self):
        self.running = False
        self.wait()
