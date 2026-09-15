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

# Read once; the settings files do not change while the server runs.
_cache: Optional[dict] = None
_sources: list = []


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
    global _cache
    if _cache is not None:
        return _cache

    merged: dict = {}
    found: list = []

    for path in _config_layers():
        try:
            if not path.is_file():
                continue
            loaded = json.loads(path.read_text(encoding="utf-8"))
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

    _cache = merged
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


def admin_password() -> str:
    """
    The database password, for the server's own administrative queries.

    The environment wins over every file, so a one-off maintenance session can
    supply it without writing it down anywhere.
    """
    from_env = os.environ.get("SLATE_DB_PASSWORD")
    if from_env:
        return from_env
    return str(_settings().get("db_password") or "")


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
