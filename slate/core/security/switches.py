"""
Security feature switches: one place, every one of them off until enabled.

Each hardening step in the security list gets a switch here and reads it before
it does anything. A switch has three positions:

    off       the old behaviour, exactly. The default for every switch.
    log_only  behave as before, but log what the new rule WOULD have refused.
              Signing and anything else that can refuse a whole studio rolls
              out this way first, so a mistake shows up as log lines rather
              than as nobody being able to work.
    on        enforce.

Where the positions live
------------------------
    the database   table security_switches (one row per switch). Every
                   workstation reads this, so a change reaches the studio.
    a local file   security_switches.json in the server's recovery folder
                   (beside the database). It can only force switches OFF, and
                   it wins over the database. The recovery tool writes it, which
                   works even when the database cannot be reached at all, and
                   the server copies it into the database when it next starts.

Anything that goes wrong while reading - no table, no database, a corrupt
file - answers "off". That is deliberate: an unreadable switch must never be
the thing that locks a studio out. The hardening phase must keep it that way.

Usage
-----
    from slate.core.security import switches

    switches.register("refuse_inactive_signin", "Refuse sign-in for deactivated accounts")
    mode = switches.mode("refuse_inactive_signin")       # 'off' | 'log_only' | 'on'
    if switches.is_on("refuse_inactive_signin"): ...
    if switches.is_logging("refuse_inactive_signin"): log what would be refused
    switches.set_mode("refuse_inactive_signin", switches.ON, by="admin")

``db`` may be passed to every function: anything with execute_query(sql,
params, fetch=) and execute_update(sql, params) - a DatabaseManager, or
slate.core.security.dbapi.ConnectionDB around a raw psycopg2 connection.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional

logger = logging.getLogger(__name__)

OFF = "off"
LOG_ONLY = "log_only"
ON = "on"
MODES = (OFF, LOG_ONLY, ON)

TABLE = "security_switches"
DDL = (
    "CREATE TABLE IF NOT EXISTS security_switches ("
    " name TEXT PRIMARY KEY,"
    " mode TEXT NOT NULL DEFAULT 'off',"
    " changed_by TEXT DEFAULT '',"
    " changed_at TEXT DEFAULT '',"
    " note TEXT DEFAULT '')"
)

# Environment override for the local file's location (tests, a second server).
OVERRIDE_ENV = "SLATE_SECURITY_OVERRIDES"
OVERRIDE_FILENAME = "security_switches.json"

# The switches the security list needs, named after what they turn on. The
# hardening phase adds to this with register(); a name nobody registered is
# refused by set_mode() so a typo cannot create a switch that nothing reads.
CATALOGUE: Dict[str, str] = {
    "split_superuser_password":
        "The postgres superuser uses its own password (db_admin_password), "
        "not the workstations' one (SEC-001).",
    "strict_pg_hba":
        "pg_hba.conf lets postgres in from this PC only, and workstations only "
        "into the studio database (SEC-001).",
    "pgbouncer_hba":
        "PgBouncer checks addresses too, and postgres cannot use its admin "
        "console from the network (SEC-028).",
    "refuse_inactive_signin":
        "Deactivated people and people past their last day cannot sign in "
        "(SEC-011).",
    "no_plaintext_passwords":
        "Plain-text stored passwords no longer open an account (SEC-010).",
    "no_sqlite_fallback":
        "No local admin/admin123 database when the server is unreachable "
        "(SEC-021).",
    "signed_fleet_commands":
        "Fleet commands on the share must carry a valid signature (SEC-022). "
        "Roll out as log_only first.",
    "signed_updates":
        "Updates must be signed (SEC-023). Roll out as log_only first.",
    "readonly_sql_console":
        "The Data Center SQL console is read-only (SEC-016, SEC-017).",
    "hide_password_hashes":
        "Password hashes are never shown or editable in the Data Center "
        "(SEC-039).",
    "no_default_accounts":
        "admin/admin123, tester and artist are not seeded or brought back "
        "(SEC-009, SEC-030, SEC-031).",
}

_lock = threading.Lock()
_override_path: Optional[Path] = None
_cache: Optional[Dict[str, str]] = None
_cache_at = 0.0
_TTL = 30.0


# ------------------------------------------------------------------ catalogue

def register(name: str, description: str) -> str:
    """Add a switch to the catalogue. Returns its normalised name."""
    key = _key(name)
    CATALOGUE.setdefault(key, str(description or ""))
    return key


def known() -> Dict[str, str]:
    return dict(CATALOGUE)


def _key(name) -> str:
    return str(name or "").strip().lower()


def _clean_mode(value) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in ("1", "true", "yes", "enforce", "enforced", "enabled"):
        return ON
    if text in ("log", "logonly", "log_only", "warn", "audit"):
        return LOG_ONLY
    return ON if text == ON else OFF


# -------------------------------------------------------------- local file

def set_override_file(path) -> None:
    """Where this process finds the local overrides (the server sets this)."""
    global _override_path
    _override_path = Path(path) if path else None
    reset_cache()


def override_file() -> Optional[Path]:
    from_env = os.environ.get(OVERRIDE_ENV)
    if from_env:
        return Path(from_env)
    return _override_path


def local_overrides(path=None) -> Dict[str, str]:
    """
    {name: 'off'} from the local file. A file that exists but cannot be read
    forces every switch off - '*' - rather than being ignored.
    """
    target = Path(path) if path else override_file()
    if target is None or not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        forced = data.get("forced_off", {}) if isinstance(data, dict) else {}
        if not isinstance(forced, dict):
            raise ValueError("forced_off is not a mapping")
        return {_key(k): OFF for k in forced}
    except Exception as exc:
        logger.warning("The local security switch file %s cannot be read (%s); "
                       "every security switch is treated as off.", target, exc)
        return {"*": OFF}


def force_off_locally(names: Optional[Iterable[str]], path, by: str = "",
                      reason: str = "") -> Path:
    """
    Force switches off in the local file. None means every switch.

    Works with no database at all - this is what the recovery tool uses when
    a hardening step has locked people out.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {"forced_off": {}}
    try:
        if target.exists():
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("forced_off"), dict):
                data = loaded
    except Exception:
        pass                                   # a corrupt file is replaced
    wanted = list(CATALOGUE) + ["*"] if names is None else [_key(n) for n in names]
    stamp = datetime.now().isoformat(timespec="seconds")
    for name in wanted:
        data["forced_off"][name] = {"by": by or "recovery", "at": stamp,
                                    "reason": reason}
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    reset_cache()
    return target


def clear_local_override(names: Optional[Iterable[str]], path) -> None:
    """Lift local overrides (None: all of them), so the database decides again."""
    target = Path(path)
    if not target.exists():
        return
    if names is None:
        target.unlink()
    else:
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            data = {"forced_off": {}}
        for name in names:
            data.get("forced_off", {}).pop(_key(name), None)
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    reset_cache()


# ----------------------------------------------------------------- database

def _default_db():
    from slate.core.infra.database_manager import database_manager
    return database_manager


def ensure_table(db=None) -> bool:
    try:
        result = (db or _default_db()).execute_update(DDL)
        return bool(result) or result is None
    except Exception as exc:
        logger.debug("security_switches table not created: %s", exc)
        return False


def _read_db(db=None) -> Dict[str, str]:
    try:
        rows = (db or _default_db()).execute_query(
            "SELECT name, mode FROM security_switches", fetch="all") or []
    except Exception as exc:
        logger.debug("Security switches not read (treated as off): %s", exc)
        return {}
    out = {}
    for row in rows:
        try:
            out[_key(row["name"])] = _clean_mode(row["mode"])
        except Exception:
            continue
    return out


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def all_modes(db=None, override_path=None) -> Dict[str, str]:
    """{name: mode} for every known switch, local overrides applied."""
    global _cache, _cache_at
    use_cache = db is None and override_path is None
    if use_cache:
        with _lock:
            if _cache is not None and time.monotonic() - _cache_at < _TTL:
                return dict(_cache)
    stored = _read_db(db)
    result = {name: stored.get(name, OFF) for name in CATALOGUE}
    for name, mode in stored.items():
        result.setdefault(name, mode)
    forced = local_overrides(override_path)
    if "*" in forced:
        result = {name: OFF for name in result}
    else:
        for name in forced:
            result[name] = OFF
    if use_cache:
        with _lock:
            _cache, _cache_at = dict(result), time.monotonic()
    return result


def mode(name: str, db=None, override_path=None) -> str:
    """'off', 'log_only' or 'on'. Unknown, unreadable or missing: 'off'."""
    key = _key(name)
    forced = local_overrides(override_path)
    if "*" in forced or key in forced:
        return OFF
    return all_modes(db, override_path).get(key, OFF)


def is_on(name: str, db=None, override_path=None) -> bool:
    return mode(name, db, override_path) == ON


def is_logging(name: str, db=None, override_path=None) -> bool:
    """True for log_only AND on: either way, log what the rule refuses."""
    return mode(name, db, override_path) in (LOG_ONLY, ON)


def set_mode(name: str, new_mode: str, by: str = "", note: str = "", db=None):
    """
    Store a switch position in the database. Returns the write result.

    Turning a switch ON must go through the hardening step's own code, which
    calls can_still_get_in() first (slate.core.security.precheck) - this
    function only records the decision.
    """
    key = _key(name)
    if key not in CATALOGUE:
        raise KeyError("No security switch called %r. Known: %s"
                       % (name, ", ".join(sorted(CATALOGUE))))
    if new_mode not in MODES:
        raise ValueError("A switch is %s, not %r" % (" / ".join(MODES), new_mode))
    target = db or _default_db()
    ensure_table(target)
    stamp = datetime.now().isoformat(timespec="seconds")
    target.execute_update("DELETE FROM security_switches WHERE name=%s", (key,))
    result = target.execute_update(
        "INSERT INTO security_switches (name, mode, changed_by, changed_at, note) "
        "VALUES (%s, %s, %s, %s, %s)", (key, new_mode, by or "", stamp, note or ""))
    reset_cache()
    logger.warning("Security switch %s set to %s by %s", key, new_mode, by or "?")
    return result


def all_off_in_db(db, by: str = "recovery") -> int:
    """Every switch row in the database set to off. Returns how many changed."""
    ensure_table(db)
    stored = _read_db(db)
    changed = 0
    for name, current in stored.items():
        if current != OFF:
            db.execute_update(
                "UPDATE security_switches SET mode='off', changed_by=%s, changed_at=%s, "
                "note=%s WHERE name=%s",
                (by, datetime.now().isoformat(timespec="seconds"),
                 "turned off by the recovery tool", name))
            changed += 1
    reset_cache()
    return changed


def apply_local_overrides(db, path=None, by: str = "server start") -> int:
    """
    Copy the local file's forced-off switches into the database, so the
    workstations (which cannot see the file) follow them too. The server calls
    this when it starts. Returns how many rows were changed.
    """
    forced = local_overrides(path)
    if not forced:
        return 0
    ensure_table(db)
    if "*" in forced:
        return all_off_in_db(db, by=by)
    stored = _read_db(db)
    changed = 0
    for name in forced:
        if stored.get(name, OFF) != OFF:
            db.execute_update(
                "UPDATE security_switches SET mode='off', changed_by=%s, changed_at=%s, "
                "note=%s WHERE name=%s",
                (by, datetime.now().isoformat(timespec="seconds"),
                 "forced off on the server PC", name))
            changed += 1
    reset_cache()
    return changed
