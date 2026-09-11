"""
Connection pooling, and the server no longer building an open door.

Two things here. The pooler itself - configuration written correctly, and a
studio that has not installed it yet carrying on unchanged. And the bug behind
the security problem: the server *generated* "let anybody in without a
password" on every fresh install, so fixing the file alone would have been
undone by the next new server.
"""

import inspect
import re
from pathlib import Path

import pytest

from slate_server.core.db_engine import DatabaseEngine
from slate_server.core.pgbouncer_engine import (
    DEFAULT_POOL_SIZE, MAX_CLIENT_CONN, PgBouncerEngine,
)


@pytest.fixture
def engine(tmp_path):
    data = tmp_path / "Database"
    data.mkdir()
    return PgBouncerEngine(str(data), db_port=5440, listen_port=6432,
                           db_user="ut_vfx_app", db_password="secret")


def _write_config(engine, verifiers=None):
    """Write the config with the database lookup stubbed out."""
    engine._scram_verifiers = lambda psql_exe: (
        verifiers if verifiers is not None
        else {"ut_vfx_app": "SCRAM-SHA-256$4096:abc$def:ghi"}
    )
    return engine.write_config(psql_exe=Path("psql.exe"))


class TestTheServerNoLongerBuildsAnOpenDoor:
    """
    The rule that let the whole internet in without a password was written by
    this function, not typed by a person. That is why it kept coming back.
    """

    # What initdb -A trust actually leaves behind. The rules have to be here,
    # not an empty file, because the defect was never a rule that got written -
    # it was these rules surviving underneath the hardened ones. PostgreSQL
    # takes the first match, so anything appended below them never applied.
    INITDB_TRUST = (
        "local   all             all                                     trust\n"
        "host    all             all             127.0.0.1/32            trust\n"
        "host    all             all             ::1/128                 trust\n"
        "local   replication     all                                     trust\n"
        "host    replication     all             127.0.0.1/32            trust\n"
    )

    @pytest.fixture
    def generated(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        (data / "postgresql.conf").write_text("", encoding="utf-8")
        (data / "pg_hba.conf").write_text(self.INITDB_TRUST, encoding="utf-8")

        engine = DatabaseEngine(str(data), port=5440)
        engine._configure_network_access()      # postgresql.conf
        engine._harden_access()                 # pg_hba.conf, once accounts exist
        return {
            "conf": (data / "postgresql.conf").read_text(encoding="utf-8"),
            "hba": (data / "pg_hba.conf").read_text(encoding="utf-8"),
        }


    def _rules(self, hba):
        out = []
        for line in hba.splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                out.append(line)
        return out

    def test_it_no_longer_writes_a_trust_rule(self, generated):
        """
        With the fixture seeding what initdb really writes, this now checks the
        thing that actually went wrong: not that a trust rule is never written,
        but that the ones already in the file are gone afterwards. Appending
        the hardened rules below them left every one of these in force.
        """
        offenders = [r for r in self._rules(generated["hba"]) if "trust" in r]
        assert not offenders, f"still granting trust: {offenders}"

    def test_it_no_longer_opens_the_database_to_every_address(self, generated):
        offenders = [r for r in self._rules(generated["hba"])
                     if "0.0.0.0/0" in r or "::/0" in r]
        assert not offenders, f"still generating open rules: {offenders}"

    def test_every_rule_it_writes_needs_a_password(self, generated):
        weak = [r for r in self._rules(generated["hba"])
                if not r.endswith("scram-sha-256")]
        assert not weak, f"rules without a password: {weak}"

    def test_it_allows_the_studio_networks(self, generated):
        for network in DatabaseEngine.STUDIO_NETWORKS:
            assert network in generated["hba"]

    def test_it_asks_for_hashed_passwords(self, generated):
        assert "password_encryption = scram-sha-256" in generated["conf"]

    def test_it_keeps_connections_back_for_an_administrator(self, generated):
        assert "superuser_reserved_connections" in generated["conf"]


class TestWhenThePoolerIsNotInstalled:
    """A studio that has not installed it must carry on exactly as before."""

    def test_it_reports_that_it_is_missing(self, engine):
        engine.exe = Path("nowhere") / "pgbouncer.exe"

        assert engine.is_installed() is False

    def test_starting_it_fails_quietly_rather_than_raising(self, engine):
        engine.exe = Path("nowhere") / "pgbouncer.exe"

        assert engine.start() is False

    def test_it_says_so_in_words_a_person_can_read(self, engine):
        engine.exe = Path("nowhere") / "pgbouncer.exe"

        assert "Not installed" in engine.status_text()

    def test_stopping_something_never_started_is_harmless(self, engine):
        engine.stop()


class TestTheConfigurationItWrites:

    def test_it_writes_both_files(self, engine):
        assert _write_config(engine) is True
        assert engine.ini_path.exists()
        assert engine.userlist_path.exists()

    def test_clients_are_checked_against_the_database_s_own_verifiers(self, engine):
        _write_config(engine, {"ut_vfx_app": "SCRAM-SHA-256$4096:aa$bb:cc"})

        userlist = engine.userlist_path.read_text(encoding="utf-8")

        assert '"ut_vfx_app" "SCRAM-SHA-256$4096:aa$bb:cc"' in userlist

    def test_it_refuses_to_write_a_user_list_it_cannot_fill(self, engine):
        """
        An empty user list would make PgBouncer reject every client. Better to
        not start at all and let people connect directly.
        """
        assert _write_config(engine, {}) is False
        assert not engine.userlist_path.exists()

    def test_it_pools_per_transaction(self, engine):
        """Session pooling would hold a connection for as long as the app is open."""
        _write_config(engine)

        assert "pool_mode = transaction" in engine.ini_path.read_text(encoding="utf-8")

    def test_no_reset_query_in_transaction_mode(self, engine):
        """It would cost a round trip on every single piece of work."""
        _write_config(engine)
        ini = engine.ini_path.read_text(encoding="utf-8")

        assert re.search(r"^server_reset_query\s*=\s*$", ini, re.M)

    def test_it_requires_a_password_from_clients(self, engine):
        _write_config(engine)

        assert "auth_type = scram-sha-256" in engine.ini_path.read_text(encoding="utf-8")

    def test_it_holds_far_fewer_database_connections_than_the_limit(self, engine):
        """The whole point: well under PostgreSQL's 100, with room to spare."""
        assert DEFAULT_POOL_SIZE < 100
        _write_config(engine)

        assert f"default_pool_size = {DEFAULT_POOL_SIZE}" in engine.ini_path.read_text(
            encoding="utf-8")

    def test_it_accepts_far_more_clients_than_a_full_studio(self, engine):
        """150 machines at 2 connections each is 300."""
        assert MAX_CLIENT_CONN >= 300

    def test_it_reaches_the_database_over_the_loopback_only(self, engine):
        _write_config(engine)

        assert "host=127.0.0.1" in engine.ini_path.read_text(encoding="utf-8")

    def test_paths_use_forward_slashes(self, engine):
        """PgBouncer will not read a Windows path with backslashes."""
        _write_config(engine)
        ini = engine.ini_path.read_text(encoding="utf-8")

        for line in ini.splitlines():
            if line.startswith(("auth_file", "logfile", "pidfile")):
                assert "\\" not in line, line


class TestTheClientPrefersThePooler:

    @pytest.fixture
    def manager(self):
        from slate.core.infra.postgres_manager import PostgresManager
        return PostgresManager()

    def test_the_pooler_is_tried_before_the_database(self, manager):
        manager.pooler_port = 6432
        manager.port = 5440

        labels = [label for label, _ in manager._ports_to_try()]

        assert labels[0] == "PgBouncer"
        assert labels[-1] == "the database directly"

    def test_the_database_is_still_reachable_if_the_pooler_is_not(self, manager):
        """A studio mid-rollout must not be locked out."""
        manager.pooler_port = 6432
        manager.port = 5440

        ports = [port for _, port in manager._ports_to_try()]

        assert 5440 in ports

    def test_no_pooler_configured_means_the_database_only(self, manager):
        manager.pooler_port = 0
        manager.port = 5440

        assert manager._ports_to_try() == [("the database directly", 5440)]

    def test_a_pooler_on_the_database_port_is_not_tried_twice(self, manager):
        manager.pooler_port = 5440
        manager.port = 5440

        assert len(manager._ports_to_try()) == 1


class TestTheShippedClientSettings:

    def test_clients_know_where_the_pooler_is(self):
        import json
        root = Path(__file__).resolve().parents[1]
        config = json.loads(
            (root / "slate" / "default_config.json").read_text(encoding="utf-8"))

        assert config["db_pooler_port"] == 6432


class TestTheServerStartsAndStopsIt:
    """The pool must not outlive the database it points at."""

    def test_the_server_starts_the_pooler_after_the_database(self):
        from slate_server.gui import app_window

        source = inspect.getsource(app_window)
        start_at = source.index("self.engine.start(")
        pooler_at = source.index("pooler.start(")

        assert start_at < pooler_at, "the pool comes up before the database"

    def test_the_server_stops_the_pooler_before_the_database(self):
        from slate_server.gui import app_window

        source = inspect.getsource(app_window)
        pooler_at = source.index("pooler.stop(")
        stop_at = source.index("self.engine.stop(")

        assert pooler_at < stop_at, "the database goes down before the pool"


class TestStartingUpWhenThePoolerIsDown:
    """
    A missing pooler must cost a moment, not half a minute.

    Clients try PgBouncer first. When it is not running the port refuses
    instantly - but that was being treated as a network blip and retried five
    times with a growing wait, so every machine took nearly thirty seconds to
    start before falling through to the database that was there all along.
    """

    def test_only_the_last_option_is_worth_retrying(self):
        import inspect
        from slate.core.infra.postgres_manager import PostgresManager

        source = inspect.getsource(PostgresManager._init_pool)

        assert "_create_pool_once" in source, (
            "every option is still retried; a missing pooler will stall start-up"
        )
        assert "is_last" in source

    def test_the_quick_attempt_does_not_retry(self):
        """It must not carry the retry decorator, or it is not quick."""
        from slate.core.infra.postgres_manager import PostgresManager

        assert not hasattr(PostgresManager._create_pool_once, "retry"), (
            "_create_pool_once is decorated with retry, so it is not a single attempt"
        )

    def test_the_final_attempt_still_retries(self):
        """A genuine network blip on the real database deserves another go."""
        from slate.core.infra.postgres_manager import PostgresManager

        assert hasattr(PostgresManager._create_pool_with_retry, "retry")


class TestPermanentFailuresAreNotRetried:
    """
    A wrong password fails the same way every time.

    Retrying it five times with a growing wait turned an instant, clear message
    into most of a minute of silence followed by the same message.
    """

    @pytest.mark.parametrize("message", [
        'database "nope" does not exist',
        'password authentication failed for user "x"',
        'role "ghost" does not exist',
    ])
    def test_a_permanent_problem_is_not_retried(self, message):
        import psycopg2
        from slate.core.infra.postgres_manager import PostgresManager

        assert not PostgresManager._is_worth_retrying(
            psycopg2.OperationalError(message))

    @pytest.mark.parametrize("message", [
        "connection refused",
        "server closed the connection unexpectedly",
        "timeout expired",
    ])
    def test_a_transient_problem_is_retried(self, message):
        import psycopg2
        from slate.core.infra.postgres_manager import PostgresManager

        assert PostgresManager._is_worth_retrying(
            psycopg2.OperationalError(message))
