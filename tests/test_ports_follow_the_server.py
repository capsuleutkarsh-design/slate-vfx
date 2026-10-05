"""
Changing the database port on the server, because something else took the
old one, should not need a visit to every workstation.

Two halves. The server: if the port it was told to use is already held by
another program, it says which and stops, instead of letting pg_ctl fail
with the reason buried in pg_server.log. The workstation: the server's
announcement on the network carries both ports, and a workstation that
already knows the server's address follows a changed port from it. It used
to throw the answer away whenever the address was one it already had.
"""

import socket

import pytest


# ------------------------------------------------------------- the server

class TestTheServerNamesABusyPort:

    @pytest.fixture
    def held_port(self):
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        try:
            yield holder.getsockname()[1]
        finally:
            holder.close()

    def test_a_held_port_is_reported_not_free(self, held_port):
        from slate_server.core.db_engine import port_is_free
        assert port_is_free(held_port) is False

    def test_a_free_port_is_reported_free(self):
        from slate_server.core.db_engine import port_is_free
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
        probe.close()
        assert port_is_free(free) is True

    def test_start_refuses_before_pg_ctl_and_names_the_port(self, held_port, tmp_path, monkeypatch):
        from slate_server.core.db_engine import DatabaseEngine, PortInUse
        data = tmp_path / "LocalDatabase"
        data.mkdir()
        (data / "PG_VERSION").write_text("17", encoding="utf-8")
        engine = DatabaseEngine(str(data), port=held_port)
        monkeypatch.setattr(DatabaseEngine, "is_installed", lambda self: True)
        monkeypatch.setattr(DatabaseEngine, "is_ready", lambda self: False)
        monkeypatch.setattr(DatabaseEngine, "binary_major", lambda self: 17)
        monkeypatch.setattr(DatabaseEngine, "_ensure_pg_directories", lambda self: None)
        monkeypatch.setattr(DatabaseEngine, "_update_port_in_conf", lambda self: None)
        monkeypatch.setattr(DatabaseEngine, "_clean_stale_pid_file", lambda self: None)
        launched = []
        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: launched.append(a))

        with pytest.raises(PortInUse) as caught:
            engine.start()

        assert caught.value.port == held_port
        assert str(held_port) in str(caught.value)
        assert "Settings" in str(caught.value)
        assert not any("pg_ctl" in str(call) for call in launched), "pg_ctl must not have been run"

    def test_the_holder_is_named_when_windows_will_say(self, held_port):
        from slate_server.core.db_engine import port_holder
        holder = port_holder(held_port)
        # This very process holds it. Whatever Windows calls the interpreter,
        # the process id is ours.
        import os
        assert holder == "" or str(os.getpid()) in holder


# ------------------------------------------------------- the announcement

class TestTheAnnouncementCarriesBothPorts:

    def test_both_ports_travel(self):
        import json
        from slate_server.core.network_broadcaster import NetworkBroadcaster
        said = json.loads(NetworkBroadcaster(db_port=5445, pooler_port=6432).announcement())
        assert said == {"status": "ONLINE", "db_port": 5445, "pooler_port": 6432}

    def test_no_pool_means_no_pool_port(self):
        import json
        from slate_server.core.network_broadcaster import NetworkBroadcaster
        said = json.loads(NetworkBroadcaster(db_port=5440).announcement())
        assert "pooler_port" not in said

    def test_the_workstation_reads_it(self):
        from slate.core.infra.network_discovery import parse_announcement
        found = parse_announcement('{"status": "ONLINE", "db_port": 5445, "pooler_port": 6432}', "10.0.0.5")
        assert found == {"host": "10.0.0.5", "db_port": 5445, "pooler_port": 6432}

    def test_an_older_server_without_a_pool_port_still_reads(self):
        from slate.core.infra.network_discovery import parse_announcement
        found = parse_announcement('{"status": "ONLINE", "db_port": 5441}', "10.0.0.5")
        assert found == {"host": "10.0.0.5", "db_port": 5441, "pooler_port": 0}

    def test_noise_is_ignored(self):
        from slate.core.infra.network_discovery import parse_announcement
        assert parse_announcement("hello", "10.0.0.5") is None
        assert parse_announcement('{"status": "OFFLINE"}', "10.0.0.5") is None


# ------------------------------------------------- the workstation follows

class TestTheWorkstationFollowsAPortChange:

    @pytest.fixture
    def manager(self, monkeypatch):
        from slate.core.infra.postgres_manager import PostgresManager
        from slate.core.infra.global_config import GlobalConfig
        saved = {}
        monkeypatch.setattr(GlobalConfig, "set", classmethod(lambda cls, k, v: saved.__setitem__(k, v)))
        m = PostgresManager.__new__(PostgresManager)
        m.host_candidates = ["10.0.0.5"]
        m.host = "10.0.0.5"
        m.port = 5442
        m.pooler_port = 6432
        m.saved_host = ""      # first setup; a saved server is pinned (NEW-4)
        m.saved = saved
        return m

    def test_a_new_port_on_a_known_address_is_taken(self, manager):
        changed = manager.adopt_announcement({"host": "10.0.0.5", "db_port": 5445, "pooler_port": 6432})
        assert changed is True
        assert manager.port == 5445
        assert manager.saved["db_port"] == 5445
        assert manager.host_candidates[0] == "10.0.0.5"

    def test_a_new_pool_port_is_taken_too(self, manager):
        manager.adopt_announcement({"host": "10.0.0.5", "db_port": 5442, "pooler_port": 6433})
        assert manager.pooler_port == 6433
        assert manager.saved["db_pooler_port"] == 6433

    def test_nothing_new_changes_nothing(self, manager):
        assert manager.adopt_announcement({"host": "10.0.0.5", "db_port": 5442, "pooler_port": 6432}) is False
        assert manager.saved == {}

    def test_a_new_address_goes_first(self, manager):
        manager.adopt_announcement({"host": "10.0.0.9", "db_port": 5442, "pooler_port": 0})
        assert manager.host_candidates[0] == "10.0.0.9"
        assert manager.saved["db_host"] == "10.0.0.9"
        assert manager.pooler_port == 6432, "a server that did not mention the pool leaves it alone"
