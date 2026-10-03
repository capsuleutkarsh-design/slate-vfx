"""
The marker that guarantees a trust window is closed, even after a crash.

Before pg_hba.conf is ever loosened, the exact file it replaces is written into
<recovery folder>\\trust_window.json. Closing the window puts that file back
and deletes the marker. If the process dies before it can close - power cut,
killed, crashed - the marker is still there, and the next thing to start (the
Slate Server window, or the recovery tool itself) puts the original back first,
before anything else happens.

This module has no dependencies beyond the standard library on purpose: the
database engine calls it on every start.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

MARKER_NAME = "trust_window.json"
RECOVERY_DIR_NAME = "slate_recovery"
# A window lasts at most this long (the opener's watchdog closes it sooner).
DEFAULT_MAX_SECONDS = 120


def marker_path(data_dir) -> Path:
    return Path(data_dir).parent / RECOVERY_DIR_NAME / MARKER_NAME


def read_marker(data_dir) -> Optional[dict]:
    path = marker_path(data_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"corrupt": True}
    except Exception:
        return {"corrupt": True}


def open_marker(data_dir, original_hba: str, reason: str,
                max_seconds: int = DEFAULT_MAX_SECONDS) -> Path:
    """Record what pg_hba.conf was, BEFORE it is changed. Raises if it cannot."""
    path = marker_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    hba = Path(data_dir) / "pg_hba.conf"
    # A plain copy as well, so a person can restore it by hand if all else fails.
    keep = Path(data_dir) / "pg_hba.conf.before-trust-window"
    keep.write_text(original_hba, encoding="utf-8", newline="")
    body = {
        "hba_path": str(hba),
        "original": original_hba,
        "opened_at": datetime.now().isoformat(timespec="seconds"),
        "opened_epoch": time.time(),
        "max_seconds": int(max_seconds),
        "pid": os.getpid(),
        "reason": reason,
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def clear_marker(data_dir) -> None:
    path = marker_path(data_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    keep = Path(data_dir) / "pg_hba.conf.before-trust-window"
    try:
        keep.unlink()
    except FileNotFoundError:
        pass


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return code.value == 259                       # STILL_ACTIVE
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_live(marker: dict) -> bool:
    """
    Whether the window's owner is still running and inside its time box.

    A wrong clock only ever makes this answer "no" (and the window is closed),
    never "yes" for longer than the box.
    """
    if not marker or marker.get("corrupt"):
        return False
    age = time.time() - float(marker.get("opened_epoch") or 0)
    limit = float(marker.get("max_seconds") or DEFAULT_MAX_SECONDS) + 30
    return _pid_alive(marker.get("pid")) and -60 <= age <= limit


def restore_original(data_dir, marker: Optional[dict] = None) -> bool:
    """Put back the pg_hba.conf recorded in the marker. True when it did."""
    marker = marker if marker is not None else read_marker(data_dir)
    if not marker:
        return False
    original = marker.get("original")
    hba = Path(data_dir) / "pg_hba.conf"
    if original is None:
        keep = Path(data_dir) / "pg_hba.conf.before-trust-window"
        if keep.exists():
            original = keep.read_text(encoding="utf-8")
    if original is None:
        logger.error("A trust window was left open in %s and the original access "
                     "rules were not recorded. Removing every trust line instead.", hba)
        try:
            text = hba.read_text(encoding="utf-8")
            kept = [l for l in text.splitlines()
                    if not (l.split("#", 1)[0].split() or [""])[-1].lower() == "trust"]
            original = "\n".join(kept) + "\n"
        except OSError:
            return False
    tmp = hba.with_name(hba.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        handle.write(original)
    os.replace(tmp, hba)
    return True


def close_stale_window(data_dir, reload: Optional[Callable[[], object]] = None,
                       say: Optional[Callable[[str], None]] = None) -> bool:
    """
    Close a trust window nobody is using any more. Called at every start.

    Returns True when one was found and closed. A window whose owner is still
    running inside its time box is left to that owner.
    """
    marker = read_marker(data_dir)
    if marker is None:
        return False
    if is_live(marker):
        logger.info("A recovery is in progress (process %s); leaving its window to it.",
                    marker.get("pid"))
        return False
    message = ("A recovery was interrupted (started %s) and left the database's "
               "access rules open on this PC. They have been put back as they were."
               % marker.get("opened_at", "at an unknown time"))
    logger.warning(message)
    if say:
        say(message)
    if not restore_original(data_dir, marker):
        return False
    if reload is not None:
        try:
            reload()
        except Exception as exc:
            logger.error("The access rules were restored but could not be reloaded: %s", exc)
    clear_marker(data_dir)
    return True
