"""
Where the studio's password actually lives.

It used to be a literal in half a dozen maintenance scripts. That is merely
untidy in a private checkout and a disclosure in a public one, so it now comes
from the machine instead - see find_db_password() for the one order every
reader uses.

These scripts run outside the application, often before it can start, so this
module deliberately depends on nothing but the standard library at import
time; the application's settings, keyring and cryptography are each tried
only if they import.
"""

from __future__ import annotations

import json
import logging
import os
import sys
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
                # utf-8-sig: setup.bat writes this file with Windows
                # PowerShell's "Set-Content -Encoding UTF8", which puts a BOM
                # first, and plain utf-8 refused the whole file. Notepad too.
                with open(path, "r", encoding="utf-8-sig") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            continue
    return {}


def setting(key: str, default=None):
    value = local_config().get(key)
    return default if value in (None, "") else value


def _from_settings():
    from slate.core.infra.global_config import GlobalConfig
    value = GlobalConfig.get("db_password") or GlobalConfig.get("password")
    return value if value and str(value).strip() else None


def _from_keyring():
    import keyring
    return keyring.get_password("Slate", "db_password")


def _from_encrypted_file():
    """What tools/setup_credentials.py writes: a Fernet-encrypted JSON and its key."""
    folder = Path(os.getenv("LOCALAPPDATA", "")) / "Slate"
    secret, key = folder / ".db_credentials", folder / ".encryption_key"
    if not (secret.exists() and key.exists()):
        return None
    from cryptography.fernet import Fernet
    return json.loads(Fernet(key.read_bytes()).decrypt(secret.read_bytes()).decode()).get("db_password")


def _from_db_config():
    from slate.core.infra.global_config import GlobalConfig
    return (GlobalConfig.get("db_config", {}) or {}).get("password")


# The one order. The first four are the order the client has always used to
# connect at start-up (PostgresManager), unchanged - a workstation must find
# exactly the password it found before (never lock a studio out). The others
# were each read by only one of the three lookups this replaces, and are kept
# so that nothing which found a password before finds none now:
# SLATE_DB_PASSWORD (a one-off maintenance session on a machine with no saved
# password), db_config's password (the SQLAlchemy factory) and the first local
# config file (the maintenance scripts).
PASSWORD_SOURCES = (
    ("the DB_PASSWORD environment variable", lambda: os.environ.get("DB_PASSWORD")),
    ("the settings (config.json): db_password or password", _from_settings),
    ("Windows Credential Manager", _from_keyring),
    ("the encrypted credentials file", _from_encrypted_file),
    ("the %s environment variable" % ENV_VAR, lambda: os.environ.get(ENV_VAR)),
    ("db_config in the settings", _from_db_config),
    ("the local config file", lambda: setting("db_password", "")),
)


def find_db_password_and_source():
    """(password, the PASSWORD_SOURCES name it came from); ('', '') when none has one."""
    for name, read in PASSWORD_SOURCES:
        try:
            value = read()
        except ImportError as exc:
            logging.debug("Password source %s not available: %s", name, exc)
            continue
        except Exception as exc:
            logging.warning("Could not read the database password from %s: %s", name, exc)
            continue
        if value:
            logging.info("Database password loaded from %s", name)
            return value, name
    return "", ""


def find_db_password() -> str:
    """
    The database password for this machine, from the first source in
    PASSWORD_SOURCES that has one; '' when none does. Never stripped -
    passwords may have spaces. A source that cannot be read is skipped.
    """
    return find_db_password_and_source()[0]


# ------------------------------------------------- the protected store (SYS-110)
#
# The password is kept in Windows Credential Manager (keyring "Slate"), never
# written to config.json in plain text. A password found in a config.json is
# moved there once it has been used to connect: stored, read back, and only
# then removed from the file - so a PC whose Credential Manager does not work
# keeps the file exactly as it was.
#
# Rotation: Slate Server publishes the studio's next password in the database
# (table app_password_next). A workstation that can connect reads it, keeps it
# as "db_password_next" and says so in app_password_learned. When the server
# switches over, the current password is refused; the workstation then tries
# the next one, and keeps it.

KEYRING_SERVICE = "Slate"
NEXT_KEY = "db_password_next"
NEXT_TABLE = "app_password_next"
LEARNED_TABLE = "app_password_learned"

# Every Slate before 2.2.0 shipped this password in default_config.json, and it
# is public. A workstation set up without one of its own still tries it - only
# when it has no other password at all - so updating it never cuts it off from
# a studio that has not switched yet. A studio that has switched (or was set up
# with 2.2.0) does not accept it. ponytail: delete once every studio has switched.
LEGACY_PASSWORD = "Tango$"
LEGACY_SOURCE = "the password older Slate versions shipped with"


def protected_password(name: str = "db_password") -> str:
    """The value in Credential Manager; '' when there is none or it cannot be read."""
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, name) or ""
    except Exception as exc:
        logging.debug("Credential Manager not readable (%s): %s", name, exc)
        return ""


def _protect(name: str, value: str) -> bool:
    """Into Credential Manager; True only when it reads back the same."""
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, name, value)
        return keyring.get_password(KEYRING_SERVICE, name) == value
    except Exception as exc:
        logging.warning("Could not keep the database password in Credential Manager: %s", exc)
        return False


def _forget(name: str) -> None:
    try:
        import keyring
        if keyring.get_password(KEYRING_SERVICE, name) is not None:
            keyring.delete_password(KEYRING_SERVICE, name)
    except Exception as exc:
        logging.debug("Could not remove %s from Credential Manager: %s", name, exc)


def _password_files():
    """Every settings file on this PC a password can be read from."""
    yield from _candidates()
    yield Path.home() / "RuntimeData" / "Slate" / "config.json"
    if getattr(sys, "frozen", False):
        yield Path(sys.executable).resolve().parent / "client_config.json"


def _strip_password_from_files() -> None:
    """Remove db_password from each settings file that has one. Unwritable ones stay."""
    for path in _password_files():
        try:
            if not path.is_file():
                continue
            with open(path, "r", encoding="utf-8-sig") as handle:
                data = json.load(handle)
            if not isinstance(data, dict) or "db_password" not in data:
                continue
            data.pop("db_password")
            tmp = path.with_name(path.name + ".writing")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=4)
            os.replace(tmp, path)
            logging.info("The database password was removed from %s; it is kept in "
                         "Credential Manager now.", path)
        except (OSError, ValueError) as exc:
            logging.warning("%s still holds the database password (%s). Slate tries the "
                            "one in Credential Manager when that one is refused.", path, exc)


def save_db_password(password: str) -> bool:
    """
    Keep this PC's database password: Credential Manager first, read back,
    then out of every config.json. True when protected. When Credential
    Manager does not work it goes into config.json as before (False) - a PC
    must never be left without the password it was just given.
    """
    if not password:
        return False
    if _protect("db_password", password):
        _strip_password_from_files()
        if protected_password(NEXT_KEY) == password:
            _forget(NEXT_KEY)
        return True
    write_local_config({"db_password": password})
    return False


def passwords_to_try(tried) -> list:
    """After a refusal: every other password this PC knows, the studio's next one first."""
    found = [protected_password(NEXT_KEY), protected_password("db_password")]
    return [p for p in dict.fromkeys(found) if p and p not in tried]


def machine_name() -> str:
    """This PC and Windows account, as app_password_learned lists it."""
    import getpass
    import socket
    try:
        user = getpass.getuser()
    except Exception:
        user = "?"
    return "%s\\%s" % (socket.gethostname(), user)


def after_connect(db, password: str, source: str) -> None:
    """
    ``password`` (from ``source``) has just connected this PC. Keep it in
    Credential Manager (moving it out of config.json), and learn the password
    the studio has published to switch to next. Never raises.
    """
    try:
        if password and source and "environment" not in source \
                and source != LEGACY_SOURCE and protected_password() != password:
            save_db_password(password)
        _learn_published_password(db, password)
    except Exception as exc:
        logging.warning("Database password housekeeping skipped: %s", exc)


def _learn_published_password(db, current: str) -> None:
    row = db.execute_query("SELECT to_regclass(%s) AS t", ("public." + NEXT_TABLE,),
                           fetch="one")
    if not row or not row.get("t"):
        return                                    # a server older than 2.2.0
    row = db.execute_query("SELECT password FROM %s" % NEXT_TABLE, fetch="one") or {}
    published = row.get("password") or ""
    if not published or published == current:
        _forget(NEXT_KEY)
        return
    if protected_password(NEXT_KEY) != published and not _protect(NEXT_KEY, published):
        return                                    # not kept, so not reported as learned
    from datetime import datetime
    db.execute_update(
        "INSERT INTO %s (machine, learned_at) VALUES (%%s, %%s) ON CONFLICT (machine) "
        "DO UPDATE SET learned_at = EXCLUDED.learned_at" % LEARNED_TABLE,
        (machine_name(), datetime.now().isoformat(timespec="seconds")))
    logging.info("This PC has the studio's next database password, for when the server "
                 "switches to it.")


def db_password(required: bool = True) -> str:
    """
    The database password for this machine (find_db_password()).

    With required=True a missing password raises rather than silently
    connecting as nobody - a maintenance script that quietly does nothing is
    worse than one that stops and says why.
    """
    password = find_db_password()
    if not password and required:
        raise RuntimeError(
            "No database password on this machine.\n"
            "Run setup.bat, or set %s for this session.\n"
            "It is deliberately not in the source - this repository is public."
            % ENV_VAR
        )
    return password or ""


def local_config_path() -> Path:
    """
    The file the studio's own settings belong in.

    The first candidate that already exists, so a machine set up by setup.bat
    keeps using the file setup.bat wrote.

    Where there is none yet, the choice is about what can actually be written.
    A setup.bat install lives in a folder the person owns, so the package-local
    file is right and is the one GlobalConfig reads last. An installed build
    lives under Program Files, where the package directory is read-only for a
    normal user - so writing there fails, and the first attempt to save the
    database details from Settings would fail with it. Those get the per-user
    file instead, which is the one GlobalConfig already reads for an installed
    build.
    """
    for path in _candidates():
        if path.is_file():
            return path

    if not getattr(sys, "frozen", False):
        return _PACKAGE / "config.json"

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Slate" / "config.json"
    return _PACKAGE / "config.json"


def write_local_config(values: dict) -> Path:
    """
    Merge settings into the local config, and say where they went.

    There were two writers. setup.bat wrote the database details here, and the
    Settings tab wrote them to a second file under LOCALAPPDATA which
    GlobalConfig read last - so the values somebody typed into Settings won over
    the ones the installer had put in, until a reinstall reversed it. Two files,
    each authoritative depending on which read them.

    Everything database-shaped now comes here, and GlobalConfig reads this file
    last. One writer, one winner.
    """
    path = local_config_path()
    existing = {}
    try:
        if path.is_file():
            with open(path, "r", encoding="utf-8-sig") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                existing = loaded
    except (OSError, ValueError):
        existing = {}

    existing.update({k: v for k, v in (values or {}).items() if v is not None})

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(existing, handle, indent=4)
    return path


def db_settings() -> dict:
    """Host, port, name, user and password, with the usual defaults."""
    config = local_config()
    return {
        "host": config.get("db_host") or "localhost",
        "port": int(config.get("db_port") or 5440),
        "dbname": config.get("db_name") or "ut_vfx",
        "user": config.get("db_user") or "ut_vfx_app",
        "password": db_password(required=False),
    }
