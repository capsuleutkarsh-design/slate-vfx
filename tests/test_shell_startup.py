"""
Start-up with the studio database down: one quick try, one answer, an error
the sign-in window knows - not 214 s of retries and a crash.
"""
import pytest

import slate.core.infra.database_manager as dbm
from slate.core.infra.db_results import DatabaseUnavailableError


def test_no_fallback_raises_the_unavailable_error_after_one_try(monkeypatch):
    import slate.core.infra.postgres_manager as pg
    tries = []

    class DeadServer:
        def _init_pool(self, retry=True):
            tries.append(retry)
            raise ConnectionError("Connection refused")

    monkeypatch.setattr(pg, "PostgresManager", DeadServer)
    manager = dbm.DatabaseManager.__new__(dbm.DatabaseManager)
    manager.requested_mode, manager.allow_fallback = "postgres", False
    with pytest.raises(DatabaseUnavailableError):
        manager._bootstrap_backend()
    assert tries == [False]                 # one attempt, no retry ladder


def test_a_failed_build_is_not_repeated_until_reload(monkeypatch):
    builds = []

    def failing():
        builds.append(1)
        raise DatabaseUnavailableError("down")

    monkeypatch.setattr(dbm, "_manager_instance", None)
    monkeypatch.setattr(dbm, "_manager_failure", None)
    monkeypatch.setattr(dbm, "DatabaseManager", failing)
    for _ in range(5):                      # every screen used to rebuild (and wait) again
        with pytest.raises(DatabaseUnavailableError):
            dbm.database_manager.get_runtime_status()
    assert len(builds) == 1
    dbm.database_manager.reload_from_config()   # Try again: forgets the failure, does not block
    assert len(builds) == 1
    with pytest.raises(DatabaseUnavailableError):
        dbm.database_manager.get_runtime_status()
    assert len(builds) == 2
