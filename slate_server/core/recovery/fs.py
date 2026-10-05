"""Small file helpers: atomic writes and folders only an administrator can read."""

from __future__ import annotations

import getpass
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Tests point this at False so a temp folder is not ACL'd for every case; the
# behaviour under test does not depend on it.
RESTRICT_PERMISSIONS = os.environ.get("SLATE_RECOVERY_NO_ACL", "") != "1"


def write_atomic(path, text: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    os.replace(tmp, target)
    return target


def write_json(path, data) -> Path:
    return write_atomic(path, json.dumps(data, indent=2, sort_keys=True))


def read_json(path, default=None):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data
    except Exception:
        return default


def _current_sid() -> str:
    try:
        out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True,
                             text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        sid = out.strip().split(",")[-1].strip().strip('"')
        return sid if sid.startswith("S-1-") else ""
    except Exception:
        return ""


def _still_usable(target: Path) -> bool:
    probe = (target / ".access-check") if target.is_dir() else target
    try:
        if target.is_dir():
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        else:
            with open(probe, "r+b"):
                pass
        return True
    except OSError:
        return False


def restrict(path) -> None:
    """
    Readable only by SYSTEM, Administrators and the account running this.

    The account running this has to keep access - the server runs as it - so
    it is granted too, by its SID. Afterwards the folder is tried; if this
    account can no longer use it, the change is undone (icacls /reset). A
    folder the server cannot use is a worse outcome than one that is too open,
    and locking ourselves out of the recovery folder is the one thing this
    must never do.
    """
    if not RESTRICT_PERMISSIONS:
        return
    target = Path(path)
    if sys.platform != "win32":
        try:
            target.chmod(0o700 if target.is_dir() else 0o600)
        except OSError:
            pass
        return
    inherit = "(OI)(CI)" if target.is_dir() else ""
    sid = _current_sid()
    me = ("*" + sid) if sid else getpass.getuser()
    grants = ["/grant:r", "*S-1-5-18:%sF" % inherit,        # SYSTEM
              "/grant:r", "*S-1-5-32-544:%sF" % inherit,    # Administrators
              "/grant:r", "%s:%sF" % (me, inherit)]         # this account
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(["icacls", str(target)] + grants, capture_output=True,
                       timeout=20, creationflags=flags)
        subprocess.run(["icacls", str(target), "/inheritance:r"], capture_output=True,
                       timeout=20, creationflags=flags)
    except Exception as exc:
        logger.debug("Could not tighten permissions on %s: %s", target, exc)
    if not _still_usable(target):
        logger.warning("Tightening %s locked this account out of it; undoing that.", target)
        try:
            subprocess.run(["icacls", str(target), "/reset", "/t"], capture_output=True,
                           timeout=20, creationflags=flags)
        except Exception:
            pass


def ensure_private_dir(path) -> Path:
    target = Path(path)
    fresh = not target.exists()
    target.mkdir(parents=True, exist_ok=True)
    if fresh:
        restrict(target)
    return target
