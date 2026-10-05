"""
Real-tools sweep, server and share track: shared fixtures.

The throwaway cluster is the recovery lab's (tests/test_recovery_lab.py), made
once per module here so a dozen checks cost one initdb. It listens on
127.0.0.1 on a free port and is stopped and deleted afterwards - never the
studio's 5440/6432.
"""

import pytest

from tests.test_recovery_lab import APP_PASSWORD, BIN, DBNAME, Lab  # noqa: F401
from slate.core.infra import network_discovery

# The real one, for the loopback discovery test; everything else gets the stub.
REAL_DISCOVER = network_discovery.discover_server_details


@pytest.fixture(autouse=True)
def _no_lan_broadcast(monkeypatch):
    """Building GlobalConfig with no db_host broadcasts on the LAN. Not from here."""
    monkeypatch.setattr(network_discovery, "discover_server_details",
                        lambda timeout=2.0: None)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """A bootstrapped, hardened throwaway Slate server (database + accounts)."""
    if not (BIN / "initdb.exe").exists():
        pytest.skip("the bundled PostgreSQL is not here")
    from slate_server.core import db_credentials

    mp = pytest.MonkeyPatch()
    for name in ("SLATE_DB_PASSWORD", "SLATE_DB_ADMIN_PASSWORD", "SLATE_DB_PATH", "PGPASSWORD"):
        mp.delenv(name, raising=False)
    lab = Lab(tmp_path_factory.mktemp("rtserver"))
    mp.setattr(db_credentials, "_config_layers", lambda: [lab.credentials])
    db_credentials.reload()
    try:
        lab.initdb()
        lab.pg_start()
        lab.bootstrap()
        yield lab
    finally:
        try:
            from slate_server.core.pgbouncer_engine import PgBouncerEngine
            PgBouncerEngine(str(lab.data), db_port=lab.port, listen_port=lab.pooler_port).stop()
        except Exception:
            pass
        lab.pg_stop()
        mp.undo()
        db_credentials._cache = None


@pytest.fixture
def share(tmp_path, monkeypatch):
    """
    SERVER_ROOT on a temp folder. The home folder (the local fallback for a
    missing share) is moved into the sandbox too.
    """
    from slate.core.infra.global_config import GlobalConfig

    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    if GlobalConfig._instance is None:
        GlobalConfig._instance = GlobalConfig()
    root = tmp_path / "share"
    root.mkdir()
    monkeypatch.setitem(GlobalConfig._instance.data, "SERVER_ROOT", str(root))
    return root
