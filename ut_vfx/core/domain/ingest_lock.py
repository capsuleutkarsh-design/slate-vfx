"""
One ingest per project at a time.

Two people pointing Build & Ingest at the same project can both allocate scan
v002, and two deliveries end up mixed in one folder. This takes a lock for the
duration of a run, and tells a second attempt who is holding it rather than
failing with no explanation.

The lock is a small file inside the project, so it works over a shared drive
without needing the database - an ingest has to be safe even when the central
database is unreachable.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


LOCK_FILENAME = ".ingest.lock"

# A run that has not touched its lock for this long is treated as abandoned -
# the machine crashed, or the app was killed. Long enough to cover a big
# delivery, short enough that nobody waits until tomorrow.
STALE_AFTER = timedelta(hours=6)


@dataclass
class LockInfo:
    holder: str = ""
    machine: str = ""
    started_at: str = ""

    def describe(self) -> str:
        who = self.holder or "someone"
        where = f" on {self.machine}" if self.machine else ""
        when = f" since {self.started_at}" if self.started_at else ""
        return f"{who}{where}{when}"


class IngestLocked(RuntimeError):
    """Raised when another ingest is already running on this project."""

    def __init__(self, info: LockInfo):
        self.info = info
        super().__init__(
            f"An ingest is already running on this project: {info.describe()}."
        )


def _lock_path(project_root) -> Path:
    return Path(project_root) / LOCK_FILENAME


def _read(path: Path) -> Optional[LockInfo]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return LockInfo(
            holder=str(data.get("holder") or ""),
            machine=str(data.get("machine") or ""),
            started_at=str(data.get("started_at") or ""),
        )
    except Exception:
        return LockInfo()          # unreadable but present: still a lock


def _is_stale(path: Path) -> bool:
    try:
        age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
        return age > STALE_AFTER
    except Exception:
        return False


class IngestLock:
    """
    Hold an ingest lock for a project.

    Use it as a context manager:

        with IngestLock(project_root, holder="coord1"):
            ...run the ingest...
    """

    def __init__(self, project_root, holder: str = ""):
        self.path = _lock_path(project_root)
        self.holder = holder or os.environ.get("USERNAME") or "unknown"
        self.machine = socket.gethostname()
        self.acquired = False

    def acquire(self) -> "IngestLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)

        if self.path.exists() and _is_stale(self.path):
            logging.warning("Clearing an abandoned ingest lock at %s", self.path)
            try:
                self.path.unlink()
            except OSError:
                pass

        payload = json.dumps({
            "holder": self.holder,
            "machine": self.machine,
            "started_at": datetime.now().isoformat(timespec="seconds"),
        })

        try:
            # Exclusive create: fails if somebody else got there first.
            with open(self.path, "x", encoding="utf-8") as handle:
                handle.write(payload)
        except FileExistsError:
            raise IngestLocked(_read(self.path) or LockInfo())
        except OSError as exc:
            # A project we cannot write a lock into is one we cannot ingest to,
            # but do not block the run on the lock itself.
            logging.warning("Could not take an ingest lock at %s: %s", self.path, exc)
            return self

        self.acquired = True
        return self

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            self.path.unlink()
        except OSError as exc:
            logging.warning("Could not release the ingest lock: %s", exc)
        finally:
            self.acquired = False

    def touch(self) -> None:
        """Mark the lock as alive during a long run, so it is not judged stale."""
        if not self.acquired:
            return
        try:
            os.utime(self.path, None)
        except OSError:
            pass

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


def current_holder(project_root) -> Optional[LockInfo]:
    """Who holds the lock on this project, if anyone."""
    path = _lock_path(project_root)
    if not path.exists() or _is_stale(path):
        return None
    return _read(path)
