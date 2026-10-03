"""
The Recovery Key.

Made once - when the server is first set up, or the first time this version
runs on an existing server - and shown to a person exactly once, to print or
store. Only a slow scrypt hash of it is kept, in <recovery folder>\\
recovery_key.json. The key itself is never written to disk by Slate, never
logged and never sent anywhere.

    XXXXX-XXXXX-XXXXX-XXXXX-XXXXX    25 characters, about 125 bits

It is typed without caring about case, spaces or dashes, and the letters that
are easy to misread (O/0, I/L/1) are treated as the same.

Wrong keys
----------
Three wrong keys in a row are free (people mistype). After that each further
attempt has to wait - 5 s, 10 s, 20 s ... never more than five minutes - and a
right key clears the count. There is no permanent lockout, and a clock that
jumps (backwards or forwards) can never make the wait longer than five minutes.

If the key is lost
------------------
Make a new one while holding Windows administrator rights on the server PC
(run "Recover Slate" as administrator). The old key stops working at once.
That is the documented answer; see docs/RECOVERY.md.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import logging
import secrets
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import fs

logger = logging.getLogger(__name__)

# Crockford's base32: no I, L, O or U, so nothing reads as something else.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
GROUPS, GROUP_LEN = 5, 5

# scrypt cost. N=2**15, r=8: about a tenth of a second and 32 MB per guess.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
SCRYPT_MAXMEM = 128 * 1024 * 1024

FREE_ATTEMPTS = 3
FIRST_PAUSE = 5.0
MAX_PAUSE = 300.0

# Replaced in tests.
clock: Callable[[], float] = time.time


class KeyMissing(Exception):
    """No Recovery Key has been made on this server yet."""


class WrongKey(Exception):
    """The key given is not this server's Recovery Key."""


class TooManyAttempts(Exception):
    """Wait before trying again. ``seconds`` says how long - never more than MAX_PAUSE."""

    def __init__(self, seconds: float):
        self.seconds = max(0.0, float(seconds))
        super().__init__("Too many wrong keys. Wait %d seconds and try again."
                         % int(round(self.seconds + 0.49)))


# ----------------------------------------------------------------- the key

def generate() -> str:
    chars = "".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LEN))
    return "-".join(chars[i:i + GROUP_LEN] for i in range(0, len(chars), GROUP_LEN))


def normalise(text: str) -> str:
    cleaned = "".join(ch for ch in str(text or "").upper() if ch.isalnum())
    return cleaned.translate(str.maketrans({"O": "0", "I": "1", "L": "1", "U": "V"}))


def _hash(key: str, salt: bytes, n=None, r=None, p=None) -> bytes:
    return hashlib.scrypt(normalise(key).encode("ascii"), salt=salt, n=n or SCRYPT_N,
                          r=r or SCRYPT_R, p=p or SCRYPT_P, maxmem=SCRYPT_MAXMEM, dklen=32)


def has_key(layout) -> bool:
    data = fs.read_json(layout.key_file)
    return isinstance(data, dict) and bool(data.get("hash"))


def key_info(layout) -> dict:
    """When the key was made and by whom. Never anything that helps guess it."""
    data = fs.read_json(layout.key_file) or {}
    return {k: data.get(k) for k in ("created_at", "created_by", "replaced_previous")
            if k in data}


def _store(layout, key: str, replaced: bool) -> None:
    fs.ensure_private_dir(layout.recovery_dir)
    salt = secrets.token_bytes(16)
    try:
        who = "%s on %s" % (getpass.getuser(), socket.gethostname())
    except Exception:
        who = ""
    fs.write_json(layout.key_file, {
        "version": 1,
        "algorithm": "scrypt",
        "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
        "salt": base64.b64encode(salt).decode("ascii"),
        "hash": base64.b64encode(_hash(key, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)).decode("ascii"),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "created_by": who,
        "replaced_previous": bool(replaced),
    })
    fs.restrict(layout.key_file)
    _reset_attempts(layout)


def create_first_key(layout) -> Optional[str]:
    """
    Make the key if this server has none. Returns the key to show the person,
    once - or None when one already exists (nothing is changed then).
    """
    if has_key(layout):
        return None
    key = generate()
    _store(layout, key, replaced=False)
    logger.warning("A Recovery Key was made for this server (it is not logged).")
    return key


def replace_key(layout, *, old_key: Optional[str] = None, as_admin: bool = False) -> str:
    """
    A new key, replacing the old one. Needs the old key, or Windows
    administrator rights on this PC (the answer for a lost key). Returns the
    new key to show once.
    """
    if has_key(layout):
        if old_key:
            verify(layout, old_key)                    # raises when wrong
        elif not as_admin:
            raise PermissionError("Give the current Recovery Key, or run the recovery "
                                  "tool as a Windows administrator on the server PC.")
        else:
            from .layout import is_windows_admin
            if not is_windows_admin():
                raise PermissionError("Making a new Recovery Key without the old one needs "
                                      "Windows administrator rights. Right-click Recover "
                                      "Slate and choose 'Run as administrator'.")
    key = generate()
    _store(layout, key, replaced=True)
    logger.warning("The Recovery Key was replaced (%s).",
                   "old key given" if old_key else "Windows administrator")
    return key


# ------------------------------------------------------------- checking it

def _attempts(layout) -> dict:
    data = fs.read_json(layout.attempts_file) or {}
    return data if isinstance(data, dict) else {}


def _reset_attempts(layout) -> None:
    try:
        layout.attempts_file.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("Could not clear the wrong-key count: %s", exc)


def pause_for(failures: int) -> float:
    if failures < FREE_ATTEMPTS:
        return 0.0
    return min(MAX_PAUSE, FIRST_PAUSE * (2 ** (failures - FREE_ATTEMPTS)))


def wait_remaining(layout) -> float:
    """Seconds to wait before the next attempt; 0 when it may be made now."""
    data = _attempts(layout)
    failures = int(data.get("failures") or 0)
    pause = pause_for(failures)
    if pause <= 0:
        return 0.0
    last = float(data.get("last_failure") or 0)
    remaining = last + pause - clock()
    # A clock moved backwards would make this a day; moved forwards, nothing.
    # Either way it is held inside [0, pause] - a slowdown, never a lockout.
    return max(0.0, min(pause, remaining))


def verify(layout, key: str) -> bool:
    """
    True for the right key. Raises KeyMissing, TooManyAttempts or WrongKey.
    The key is never logged, and neither is any part of it.
    """
    data = fs.read_json(layout.key_file)
    if not isinstance(data, dict) or not data.get("hash"):
        raise KeyMissing("This server has no Recovery Key yet.")
    waiting = wait_remaining(layout)
    if waiting > 0:
        raise TooManyAttempts(waiting)
    try:
        salt = base64.b64decode(data["salt"])
        expected = base64.b64decode(data["hash"])
        actual = _hash(key, salt, int(data.get("n", SCRYPT_N)), int(data.get("r", SCRYPT_R)),
                       int(data.get("p", SCRYPT_P)))
    except Exception:
        actual, expected = b"x", b"y"
    if normalise(key) and hmac.compare_digest(actual, expected):
        _reset_attempts(layout)
        return True
    counts = _attempts(layout)
    failures = int(counts.get("failures") or 0) + 1
    fs.ensure_private_dir(layout.recovery_dir)
    fs.write_json(layout.attempts_file, {"failures": failures, "last_failure": clock()})
    logger.warning("A wrong Recovery Key was given (%d in a row).", failures)
    raise WrongKey("That is not this server's Recovery Key.")
