"""
Ed25519 signatures for what Slate picks up from the share: fleet commands
(signed_fleet_commands) and update manifests (signed_updates).

Anyone who can write to the share can drop a command file or a manifest there.
A signature says who made it, and the share cannot give anybody the private key:

    fleet commands   the key is the studio's, made by the server and kept in the
                     database (slate_secure.keys, which the workstations cannot
                     read). slate_secure.fleet_signing_key() hands the private
                     half to an administrator who types their own password - the
                     Admin Panel asks for it anyway before a restart or shut
                     down. Workstations check with the public half, also read
                     from the database, never from the share.
    updates          the key is the owner's, kept off every studio PC and out of
                     git; tools/release_publisher.py signs the manifest and the
                     public half ships inside the app (updater/release_key.py).

Both switches: off = the old behaviour, log_only = log what is unsigned or
invalid and act as before, on = refuse it. Nothing here is on the sign-in path,
and nothing here raises: a check that fails is a "no".
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

FLEET_KEY = "fleet"


# ------------------------------------------------------------- the primitive

def new_keypair() -> Tuple[str, str]:
    """(private, public), each the raw 32-byte Ed25519 key as hex."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw,
                                           serialization.PublicFormat.Raw)
    return private.hex(), public.hex()


def canonical(data: dict) -> bytes:
    """The bytes that are signed: every field but the signature, in one fixed form."""
    body = {k: v for k, v in dict(data).items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sign(data: dict, private_hex: str) -> dict:
    """A copy of data with its "signature"."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex.strip()))
    return dict(data, signature=base64.b64encode(key.sign(canonical(data))).decode("ascii"))


def verify(data, public_hex) -> bool:
    """Whether data carries a valid signature by this public key. Never raises."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        signature = base64.b64decode(str(data["signature"]), validate=True)
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(str(public_hex).strip())).verify(
            signature, canonical(data))
        return True
    except Exception:
        return False


# ------------------------------------------------------------ fleet commands

def _db(db):
    if db is not None:
        return db
    from slate.core.infra.database_manager import database_manager
    return database_manager


def fleet_public_key(db=None) -> str:
    """The studio's fleet key (public half) from the database, or ''."""
    try:
        row = _db(db).execute_query(
            "SELECT public_key FROM slate_secure.keys WHERE name=%s", (FLEET_KEY,), fetch="one")
        return str((row or {}).get("public_key") or "")
    except Exception as exc:
        logger.debug("The fleet public key could not be read: %s", exc)
        return ""


def fleet_private_key(username: str, password: str, db=None) -> Optional[str]:
    """
    The private half, for an administrator who gives their own password; None
    for anybody else, or when the database cannot say. The password is checked
    inside the database (slate_secure.fleet_signing_key).
    """
    try:
        row = _db(db).execute_query("SELECT slate_secure.fleet_signing_key(%s, %s) AS k",
                                    (str(username or ""), str(password or "")), fetch="one")
        return (row or {}).get("k") or None
    except Exception as exc:
        logger.warning("The fleet signing key could not be fetched: %s", exc)
        return None


def command_allowed(cmd: dict, db=None) -> bool:
    """
    Whether a workstation may act on this command (signed_fleet_commands).
    off: always. log_only: always, and an unsigned or invalid one is logged.
    on: only with a valid signature. Never raises.
    """
    try:
        from slate.core.security import switches
        mode = switches.mode("signed_fleet_commands", db=db)
        if mode == switches.OFF:
            return True
        public = fleet_public_key(db)
        if public and verify(cmd, public):
            return True
        why = ("no fleet key could be read from the database" if not public else
               "it is not signed" if not cmd.get("signature") else "its signature is not valid")
        logger.warning("signed_fleet_commands (%s): the %r command for %s %s, because %s.",
                       mode, cmd.get("command"), cmd.get("target"),
                       "was refused" if mode == switches.ON else "would be refused", why)
        return mode != switches.ON
    except Exception as exc:
        logger.warning("signed_fleet_commands: the command check failed (%s); acting as off.", exc)
        return True
