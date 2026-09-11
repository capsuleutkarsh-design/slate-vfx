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
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)

# Read once; the settings file does not change while the server runs.
_cache: Optional[dict] = None


def _settings() -> dict:
    """
    Where the server's credentials come from.

    The shipped default_config.json used to carry the password. It does not any
    more - this repository is public - so the local, git-ignored config that
    setup.bat writes is consulted first, and the shipped file is left as the
    source of the non-secret settings.
    """
    global _cache
    if _cache is not None:
        return _cache

    root = Path(__file__).resolve().parents[2]
    for candidate in (root / "slate" / "config.json",
                      root / "config.json",
                      root / "client_config.json",
                      root / "slate" / "default_config.json"):
        try:
            if candidate.exists():
                _cache = json.loads(candidate.read_text(encoding="utf-8"))
                return _cache
        except Exception as exc:
            logger.debug("Could not read %s: %s", candidate, exc)

    _cache = {}
    return _cache


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
