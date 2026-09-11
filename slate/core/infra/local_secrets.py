"""
Where the studio's password actually lives.

It used to be a literal in half a dozen maintenance scripts. That is merely
untidy in a private checkout and a disclosure in a public one, so it now comes
from the machine instead:

    1. the SLATE_DB_PASSWORD environment variable, if it is set
    2. slate/config.json - written by setup.bat, and git-ignored

The password itself has not changed. It moved.

These scripts run outside the application, often before it can start, so this
module deliberately depends on nothing but the standard library.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


ENV_VAR = "SLATE_DB_PASSWORD"

# slate/core/infra/local_secrets.py -> infra -> core -> slate -> repo root
_PACKAGE = Path(__file__).resolve().parent.parent.parent
_ROOT = _PACKAGE.parent


def _candidates():
    """Every place a local config is allowed to be, nearest first."""
    yield _PACKAGE / "config.json"
    yield _ROOT / "config.json"
    yield _ROOT / "client_config.json"
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        yield Path(local_app_data) / "Slate" / "config.json"


def local_config() -> dict:
    """The first local config that parses. Empty dict when there is none."""
    for path in _candidates():
        try:
            if path.is_file():
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            continue
    return {}


def setting(key: str, default=None):
    value = local_config().get(key)
    return default if value in (None, "") else value


def db_password(required: bool = True) -> str:
    """
    The database password for this machine.

    With required=True a missing password raises rather than silently
    connecting as nobody - a maintenance script that quietly does nothing is
    worse than one that stops and says why.
    """
    password = os.environ.get(ENV_VAR) or setting("db_password", "")
    if not password and required:
        raise RuntimeError(
            "No database password on this machine.\n"
            "Run setup.bat, or set %s for this session.\n"
            "It is deliberately not in the source - this repository is public."
            % ENV_VAR
        )
    return password or ""


def db_settings() -> dict:
    """Host, port, name, user and password, with the usual defaults."""
    config = local_config()
    return {
        "host": config.get("db_host") or "localhost",
        "port": int(config.get("db_port") or 5440),
        "dbname": config.get("db_name") or "slate",
        "user": config.get("db_user") or "ut_vfx_app",
        "password": db_password(required=False),
    }
