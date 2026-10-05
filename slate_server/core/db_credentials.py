"""
How the server's own tools reach the database.

The database used to accept connections from this machine without a password,
so the server's dashboard, its analytics view and its web interface all simply
connected as "postgres" with nothing else. Closing that hole broke all three at
once - quietly, because each of them catches its own errors and shows an empty
panel rather than an explanation.

Everything server-side now asks here for its connection details, so there is
one place that knows where the password lives.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)

# Read once; the settings files do not change while the server runs. The
# protected store can (Recover Slate is another process), so its file's time
# is part of what the cache is for.
_cache: Optional[dict] = None
_cache_key = None
_sources: list = []


# ------------------------------------------------ the protected store
#
# The studio's database passwords live on the server PC only, encrypted with
# Windows DPAPI (machine scope) in slate_recovery\db_secrets.dat beside the
# database - the folder only administrators and the server's own account can
# read. Machine scope rather than one Windows account's Credential Manager, so
# Recover Slate run by another administrator reads and writes the very
# passwords the server uses: two different copies is how a server ends up
# locked out of its own database.
#
#   app_password           the workstations' (ut_vfx_app)
#   admin_password         the superuser's, once split_superuser_password gave it its own
#   app_password_next      published for the workstations to learn, not in use yet
#   app_password_previous  the one before the last switch, to set back if ever needed

SECRETS_NAME = "db_secrets.dat"
_secrets_path: Optional[Path] = None
_adopted = ""          # proved at start-up but not storable (see adopt)


def use_data_dir(data_dir) -> None:
    """The server's database folder; its protected store sits beside it."""
    global _secrets_path, _cache
    _secrets_path = Path(data_dir).parent / "slate_recovery" / SECRETS_NAME
    _cache = None


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows DPAPI, machine scope, no prompts."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    crypt32 = ctypes.windll.crypt32
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ui_forbidden, local_machine = 0x1, 0x4
    if not call(ctypes.byref(blob_in), None, None, None, None,
                ui_forbidden | local_machine, ctypes.byref(blob_out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def stored() -> dict:
    """What the protected store holds; {} when there is none or it cannot be read."""
    path = _secrets_path
    if path is None or not path.is_file():
        return {}
    try:
        import base64
        data = json.loads(_dpapi(base64.b64decode(path.read_text(encoding="ascii")),
                                 False).decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.error("The server's protected passwords (%s) cannot be read: %s", path, exc)
        return {}


def store(**values) -> Path:
    """
    Merge into the protected store ('' or None removes a value). Raises when
    there is nowhere to keep it or it does not read back the same.
    """
    import base64
    from slate_server.core.recovery import fs
    if _secrets_path is None:
        raise RuntimeError("the server's database folder is not known yet")
    data = stored()
    for key, value in values.items():
        if value:
            data[key] = str(value)
        else:
            data.pop(key, None)
    fs.ensure_private_dir(_secrets_path.parent)
    fs.write_atomic(_secrets_path, base64.b64encode(
        _dpapi(json.dumps(data).encode("utf-8"), True)).decode("ascii"))
    if stored() != data:
        raise RuntimeError("%s did not read back the same" % _secrets_path)
    reload()
    if values.get("app_password"):
        strip_from_files("db_password")
        _for_slate_on_this_pc(str(values["app_password"]))
    return _secrets_path


def _for_slate_on_this_pc(password: str) -> None:
    """
    The same password for a Slate on this PC (its Credential Manager); the
    plain copies in the settings files are gone (store). A stale one left
    there is what this server would fall back to - and set on the
    workstations' account - if the protected store were ever unreadable.
    """
    try:
        from slate.core.infra.local_secrets import LEGACY_PASSWORD, save_db_password
        if password != LEGACY_PASSWORD:
            save_db_password(password)
    except Exception as exc:
        logger.warning("The Slate on this PC was not given the new password: %s", exc)


def adopt(password: str) -> None:
    """A password proved by logging in: kept protected, or for this run only if it cannot be."""
    global _adopted, _cache
    try:
        store(app_password=password)
    except Exception as exc:
        logger.error("The database password could not be kept protected (%s); using it "
                     "for this run only.", exc)
        _adopted = password
        _cache = None


def keep_in_protected_store() -> bool:
    """
    The passwords this server reads from plain settings files go into the
    protected store, which then wins, and leave the files (db_password via
    store(), which hands it to this PC's Credential Manager as well, since a
    Slate here reads the same config.json). True if anything was stored.
    """
    if _secrets_path is None:
        return False
    have, merged = stored(), _settings()
    updates = {}
    if not have.get("app_password") and merged.get("db_password"):
        updates["app_password"] = merged["db_password"]
    if not have.get("admin_password") and merged.get("db_admin_password"):
        updates["admin_password"] = merged["db_admin_password"]
    if updates:
        store(**updates)
    if stored().get("admin_password"):
        strip_from_files("db_admin_password")
    return bool(updates)


def strip_from_files(key: str) -> None:
    """Remove one setting from every settings file this server reads that has it."""
    from slate_server.core.recovery import fs
    for path in _config_layers():
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict) and key in data:
                data.pop(key)
                fs.write_atomic(path, json.dumps(data, indent=4))
        except Exception as exc:
            logger.warning("Could not remove %s from %s: %s", key, path, exc)
    reload()


def _config_layers() -> list:
    """
    Every file the server takes settings from, weakest first.

    A list rather than a first-match search, because the failure this was
    rewritten after is a layering one and a first match hides it. The old
    version looked only alongside its own source. In an installed build there
    is nothing alongside its own source: PyInstaller unpacks the code into a
    temporary folder that holds no settings and is deleted on exit. So a frozen
    server read no settings at all, got an empty password, could not create any
    accounts with it, hardened the cluster regardless, and left behind a
    database that nothing can ever log into. Every number on its dashboard was
    still correct.
    """
    here = Path(__file__).resolve().parents[2]

    layers = [
        here / "slate" / "default_config.json",
        here / "client_config.json",
        here / "config.json",
        here / "slate" / "config.json",
    ]

    if getattr(sys, "frozen", False):
        # Inside the bundle. Named by PyInstaller rather than worked out from
        # __file__, because whether __file__'s grandparent is the bundle root
        # depends on how the module was packaged and being wrong about it is
        # what left a server with no password at all.
        unpacked = getattr(sys, "_MEIPASS", "")
        if unpacked:
            layers.insert(0, Path(unpacked) / "slate" / "default_config.json")

        # And beside the executable, which is a different place from inside the
        # bundle and is the one that survives the process.
        beside_exe = Path(sys.executable).resolve().parent
        layers += [
            beside_exe / "slate" / "default_config.json",
            beside_exe / "client_config.json",
            beside_exe / "config.json",
        ]

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        # Where the Settings screen saves the database details, on both the
        # server and the workstations. Last, so a correction made on this
        # machine outranks anything that was shipped.
        #
        # slate_server_config.json is deliberately not read here. It is the
        # server's own file - where its cluster lives, which ports it uses - and
        # its "db_path" means the data directory while the same key in these
        # files means something else entirely. It carries no credentials, so
        # reading it would add nothing but that collision.
        layers.append(Path(local_app_data) / "Slate" / "config.json")

    # The same file can be reached by more than one of the routes above, and a
    # settings list that names it twice reads like a problem.
    seen = set()
    unique = []
    for path in layers:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _settings() -> dict:
    global _cache, _cache_key
    try:
        key = (str(_secrets_path), _secrets_path.stat().st_mtime_ns if _secrets_path else 0)
    except OSError:
        key = (str(_secrets_path), 0)
    if _cache is not None and key == _cache_key:
        return _cache

    merged: dict = {}
    found: list = []

    for path in _config_layers():
        try:
            if not path.is_file():
                continue
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            logger.debug("Could not read %s: %s", path, exc)
            continue
        if not isinstance(loaded, dict):
            continue

        # A later file that mentions a setting wins. One that leaves it blank
        # is not a correction - treating it as one is how a password gets
        # erased by a file that never carried one in the first place.
        merged.update({k: v for k, v in loaded.items() if v not in (None, "")})
        found.append(str(path))

    # The protected store wins over every file (see store()).
    protected = stored()
    for name, setting_key in (("app_password", "db_password"),
                              ("admin_password", "db_admin_password")):
        if protected.get(name):
            merged[setting_key] = protected[name]
    if not merged.get("db_password"):
        # A Slate on this PC that moved it into its Credential Manager, or one
        # proved at start-up that could not be stored (adopt).
        try:
            from slate.core.infra.local_secrets import protected_password
            kept = protected_password() or _adopted
        except Exception:
            kept = _adopted
        if kept:
            merged["db_password"] = kept

    _cache, _cache_key = merged, key
    del _sources[:]
    _sources.extend(found)
    return _cache


def setting(key: str, default=""):
    """One value from the settings, with the same layering as the rest."""
    value = _settings().get(key)
    return default if value in (None, "") else value


def settings_sources() -> list:
    """The settings files that were actually read, weakest first."""
    _settings()
    return list(_sources)


def reload() -> dict:
    """Forget the cached settings. For after the Settings screen writes them."""
    global _cache
    _cache = None
    return _settings()


def app_password() -> str:
    """
    The workstations' database password (the application account's).

    SLATE_DB_PASSWORD wins over every file, so a one-off maintenance session
    can supply it without writing it down anywhere.
    """
    from_env = os.environ.get("SLATE_DB_PASSWORD")
    if from_env:
        return from_env
    return str(_settings().get("db_password") or "")


def admin_password() -> str:
    """
    The superuser's (postgres) password, for the server's own queries.

    Its own setting, db_admin_password (or SLATE_DB_ADMIN_PASSWORD), when one
    has been set - the recovery tool sets it, and the security work will. Until
    then it falls back to the workstations' password, which is what both
    accounts have always had, so nothing changes on a server that has not been
    given a separate one. Reading the separate one first is what lets the
    superuser get its own password without the server then locking itself out
    (SEC-001's guard: "ship the reader before changing any password").
    """
    from_env = os.environ.get("SLATE_DB_ADMIN_PASSWORD")
    if from_env:
        return from_env
    separate = _settings().get("db_admin_password")
    if separate not in (None, ""):
        return str(separate)
    return app_password()


def has_separate_admin_password() -> bool:
    return bool(os.environ.get("SLATE_DB_ADMIN_PASSWORD")
                or _settings().get("db_admin_password") not in (None, ""))


def admin_user() -> str:
    """
    The account the server's own tools use.

    Deliberately the administrator: these are the server's monitoring and
    maintenance queries, which need to see the whole instance. The software
    that artists run uses an ordinary account instead.
    """
    return "postgres"


def application_user() -> str:
    """
    The ordinary account the artists' software logs in as.

    Deliberately not the administrator: it owns the studio's database and
    nothing else on the instance, and it cannot spend the connection slots held
    back for an administrator during an incident.

    Read from the same settings the clients read, because the server has to
    create this account with exactly the name every workstation will ask for.
    A default here that disagreed with the clients would produce the failure
    this was written after: PostgreSQL reporting that the role does not exist,
    on every machine, at the login screen.
    """
    return str(_settings().get("db_user") or "ut_vfx_app")


def database_name() -> str:
    """
    The name of the database inside the cluster.

    It is not the product's name and it does not follow the product's name.
    The cluster calls this database ut_vfx, and it still will after the software
    is renamed, because renaming a database that has a studio's work in it is a
    migration rather than a decision anybody makes in passing.

    Keeping it in one place matters more than the value: a literal spread across
    the server, the pool and the maintenance scripts is exactly what let a
    rename point half of them somewhere else, and PostgreSQL answers a request
    for the wrong database by creating an empty one rather than complaining.
    """
    return str(_settings().get("db_name") or "ut_vfx")


def connect_kwargs(port: int, dbname: str = "",
                   connect_timeout: int = 2) -> dict:
    """
    Arguments for psycopg2.connect, with the password filled in.

    Named so the server's connections can be told apart from the 150
    workstations on the connection list.
    """
    kwargs = {
        "host": "127.0.0.1",
        "port": int(port),
        "dbname": dbname or database_name(),
        "user": admin_user(),
        "connect_timeout": int(connect_timeout),
        "application_name": "Slate Central Server",
    }
    password = admin_password()
    if password:
        kwargs["password"] = password
    return kwargs


def env_with_password(base: Optional[dict] = None) -> dict:
    """
    An environment for the bundled command-line tools.

    createdb, psql and friends read the password from PGPASSWORD; without it
    they now stop and ask, which a background process can never answer.
    """
    env = dict(base if base is not None else os.environ)
    password = admin_password()
    if password:
        env["PGPASSWORD"] = password
    return env
