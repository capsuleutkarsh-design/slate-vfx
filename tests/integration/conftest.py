"""
These tests exercise the dashboard round trip, not the offline rule.

Slate deliberately refuses dashboard writes when it has *fallen back* to a
local database: edits made then would never reach anybody else, so blocking
them is correct, and it is covered on purpose elsewhere.

But "fell back" and "deliberately running standalone" are different things, and
`is_offline_fallback()` tells them apart only by the `fallback_used` flag. On a
machine with no studio database - a fresh clone, or a CI runner - these tests
asked for Postgres, got SQLite, and every write raised OfflineError.

They did not fail before because running the whole suite first left the global
manager already pointing at a deliberate local database, so by the time this
directory ran the flag was False. The tests passed for a reason that had
nothing to do with them, and went red the moment anybody ran this directory on
its own - which is exactly what CI does.

So the standalone case is made explicit here rather than inherited by luck.
"""

import pytest


@pytest.fixture(autouse=True)
def deliberate_local_database(monkeypatch):
    """
    Report the database as a standalone local one rather than a fallback.

    A studio genuinely running on SQLite is a supported configuration, and in
    it dashboard writes are allowed. That is the configuration these tests
    need; nothing here pretends the central database is up.

    Patched on the class rather than on an instance, because ``mock_db`` builds
    a fresh ``DatabaseManager`` part way through several of these tests and
    installs it as the global one. An instance patched up front is discarded at
    that moment, which is a quietly ineffective fixture - and was.
    """
    try:
        from slate.core.infra.database_manager import DatabaseManager
    except Exception:                       # pragma: no cover - import guard
        yield
        return

    original = DatabaseManager.get_runtime_status

    def standalone(self):
        status = dict(original(self))
        status["fallback_used"] = False
        return status

    monkeypatch.setattr(DatabaseManager, "get_runtime_status", standalone)
    monkeypatch.setattr(DatabaseManager, "is_local_mode", lambda self: False)
    yield
