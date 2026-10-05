"""
The schema registry (slate/core/infra/migrations) on a fresh, real PostgreSQL
database, run the way a workstation runs it: as the ordinary account
ut_vfx_app that Slate Server creates - not as the superuser the rest of the
suite uses. A step that needs more than the database owner's rights fails here.
"""

import pytest

from conftest import APP_PASSWORD

pytestmark = pytest.mark.realtools


def test_every_step_runs_as_the_workstations_account_on_a_fresh_database(server, monkeypatch):
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.global_config import GlobalConfig
    from slate.core.infra.migrations.registry import run_migrations
    from slate.core.infra.postgres_manager import PostgresManager

    lab = server
    lab.sql('DROP DATABASE IF EXISTS "slate_rt_fresh" WITH (FORCE)')
    lab.sql('CREATE DATABASE "slate_rt_fresh" OWNER ut_vfx_app')

    if GlobalConfig._instance is None:
        GlobalConfig._instance = GlobalConfig()
    live = GlobalConfig._instance.data
    for key, value in {"db_mode": "postgres", "db_host": "127.0.0.1", "db_port": lab.port,
                       "db_name": "slate_rt_fresh", "db_user": "ut_vfx_app",
                       "db_password": APP_PASSWORD, "db_pooler_port": 0,
                       "db_host_candidates": ["127.0.0.1"]}.items():
        monkeypatch.setitem(live, key, value)
    monkeypatch.setattr(PostgresManager, "_instance", None)

    manager = db_module.DatabaseManager()
    try:
        assert manager.get_runtime_status().get("active_mode") == "postgres"
        from slate.core.infra.migrations.registry import all_steps
        assert set(manager.migration_report) == {s.name for s in all_steps()}
        from slate_server.core.server_facts import schema_state
        with monkeypatch.context() as m:
            from slate_server.core import db_credentials
            m.setattr(db_credentials, "database_name", lambda: "slate_rt_fresh")
            state = schema_state(lab.port)
        assert state["error"] == "" and state["missing"] == [], state
        bad = {name: outcome for name, (outcome, _secs) in manager.migration_report.items()
               if outcome != "ok" and not outcome.startswith("skipped")}
        assert not bad, bad
        again = run_migrations(manager.backend)
        bad = {name: outcome for name, (outcome, _secs) in again.items()
               if outcome != "ok" and not outcome.startswith("skipped")}
        assert not bad, bad
    finally:
        try:
            manager.backend._close_pool()
        except Exception:
            pass
        PostgresManager._instance = None
        lab.sql('DROP DATABASE IF EXISTS "slate_rt_fresh" WITH (FORCE)')
