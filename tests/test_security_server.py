"""Server switches without a database: what strict_pg_hba allows, and what log_only reports."""

import pytest
from slate.core.security.precheck import check_hba, hba_decision
from slate_server.core.db_engine import DatabaseEngine
from slate_server.core.recovery import hardening


def _row(user, client, database="ut_vfx"):
    return {"pid": 1, "client": client, "user": user, "database": database}


def test_strict_rules_keep_the_server_and_the_workstations_in(monkeypatch):
    monkeypatch.setattr("slate_server.core.db_credentials._settings", lambda: {})
    rules = hardening.strict_rules()
    assert check_hba(rules).ok
    assert hba_decision(rules, database="ut_vfx", user="postgres", address="10.0.0.5") == "reject"
    assert hba_decision(rules, database="postgres", user="ut_vfx_app",
                        address="192.168.1.9") is None, "workstations: studio database only"
    assert hba_decision(rules, database="ut_vfx", user="ut_vfx_app",
                        address="172.16.4.2") == "scram-sha-256"


def test_off_is_the_rules_the_server_always_wrote():
    rules = DatabaseEngine.__new__(DatabaseEngine).standard_rules()
    assert rules.startswith(DatabaseEngine.HBA_SIGNATURE)
    for network in DatabaseEngine.STUDIO_NETWORKS:
        assert "host    all   all   %-16s scram-sha-256" % network in rules
    assert hardening.STRICT_MARK not in rules


def test_would_refuse_names_network_connections_only(monkeypatch):
    monkeypatch.setattr("slate_server.core.db_credentials._settings", lambda: {})
    rows = [_row("postgres", "10.0.0.5"), _row("postgres", "127.0.0.1"),
            _row("ut_vfx_app", "10.0.0.6"), _row("ut_vfx_app", "10.0.0.7", "postgres")]
    assert hardening.would_refuse(rows, "split_superuser_password") == \
        ["postgres from 10.0.0.5 into ut_vfx"]
    assert hardening.would_refuse(rows, "strict_pg_hba") == \
        ["postgres from 10.0.0.5 into ut_vfx", "ut_vfx_app from 10.0.0.7 into postgres"]


@pytest.mark.parametrize("name", ["signed_fleet_commands", "signed_updates"])
def test_a_switch_with_nothing_built_behind_it_is_refused(tmp_path, name):
    from slate_server.core.recovery.layout import ServerLayout
    result = hardening.turn_on(ServerLayout(data_dir=tmp_path), name)
    assert not result.applied and "Nothing was changed" in result.message
