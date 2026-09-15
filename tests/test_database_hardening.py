"""
The database must not be open to the world, and it must not fail quietly.

Two problems this pins down, both found by connecting to the running database
rather than by reading the code:

  * ``pg_hba.conf`` ended with "let anyone in from any address, no password",
    while clients logged in as the postgres superuser. Anyone who could reach
    the port was a database administrator.
  * A failed query returned ``None`` and a failed save returned ``False``, so a
    connection shortage looked like an empty project and a save that worked.
    That is the failure mode a 150-seat studio would actually hit.
"""

import json
import re
from pathlib import Path

import pytest

from slate.core.infra.circuit_breaker import CircuitBreakerError
from slate.core.infra.postgres_manager import (
    DatabaseUnavailableError, PostgresManager, _client_identity,
)


ROOT = Path(__file__).resolve().parents[1]

# A PostgreSQL cluster is machine state, not source: its data directory changes
# on every query and is not in the repository. So these checks run against
# whatever cluster this machine happens to have, and skip when it has none -
# they still fail loudly wherever a database actually exists, which is the only
# place the door can be left open.
# One location: the development server's home is the checkout (see
# _server_home in slate_server/gui/app_window.py). The cluster that used to
# sit under slate_server/gui was a stray from an older working directory, and
# listing it here kept it looking legitimate.
CLUSTERS = [
    ROOT / "LocalDatabase",
]
HBA_FILES = [c / "pg_hba.conf" for c in CLUSTERS]
CONF_FILES = [c / "postgresql.conf" for c in CLUSTERS]


def _require_cluster(path):
    """Skip when there is no cluster here; fail when there is one but no file."""
    cluster = path.parent
    if not cluster.is_dir():
        pytest.skip(
            "no PostgreSQL cluster at %s - nothing to check on this machine. "
            "Run setup.bat /server to create one." % cluster)
    if not path.exists():
        pytest.fail("%s has a cluster but no %s" % (cluster, path.name))


def _require_local_password():
    """Skip where no credentials are configured - a fresh clone, or CI."""
    from slate_server.core.db_credentials import admin_password
    if not admin_password():
        pytest.skip(
            "no database password configured on this machine. Run setup.bat, "
            "or set SLATE_DB_PASSWORD, to check the server can still get in.")


def _rules(path):
    """The live (uncommented) rules in a pg_hba file."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line.split())
    return out


def _setting(path, key):
    """The last uncommented value of a postgresql.conf key."""
    found = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if re.match(r"^%s\s*=" % re.escape(key), stripped):
            found = stripped.split("=", 1)[1].strip()
    return found


class TestTheDoorIsShut:
    """Whoever edits pg_hba next must not quietly reopen it."""

    @pytest.mark.parametrize("path", HBA_FILES, ids=lambda p: p.parent.parent.name)
    def test_the_file_is_there(self, path):
        _require_cluster(path)

    @pytest.mark.parametrize("path", HBA_FILES, ids=lambda p: p.parent.parent.name)
    def test_nothing_uses_trust(self, path):
        """'trust' means the password is never checked, whatever it is set to."""
        _require_cluster(path)
        offenders = [r for r in _rules(path) if "trust" in r]
        assert not offenders, f"{path.name} still trusts connections: {offenders}"

    @pytest.mark.parametrize("path", HBA_FILES, ids=lambda p: p.parent.parent.name)
    def test_the_whole_internet_is_not_allowed(self, path):
        _require_cluster(path)
        open_to_all = [r for r in _rules(path)
                       if "0.0.0.0/0" in r or "::/0" in r]
        assert not open_to_all, f"{path.name} is open to every address: {open_to_all}"

    @pytest.mark.parametrize("path", HBA_FILES, ids=lambda p: p.parent.parent.name)
    def test_every_rule_requires_a_password(self, path):
        _require_cluster(path)
        weak = [r for r in _rules(path)
                if r and r[-1] not in ("scram-sha-256", "md5", "cert")]
        assert not weak, f"{path.name} has rules that do not need a password: {weak}"

    @pytest.mark.parametrize("path", CONF_FILES, ids=lambda p: p.parent.parent.name)
    def test_passwords_are_hashed_properly(self, path):
        _require_cluster(path)
        assert _setting(path, "password_encryption") == "scram-sha-256"

    @pytest.mark.parametrize("path", CONF_FILES, ids=lambda p: p.parent.parent.name)
    def test_slots_are_kept_for_an_administrator(self, path):
        """
        So somebody can still get in when the studio has used every connection.

        These only help because the application no longer logs in as a
        superuser - see the shipped db_user below.
        """
        _require_cluster(path)
        assert int(_setting(path, "superuser_reserved_connections") or 0) >= 3


class TestTheShippedSettings:

    @pytest.fixture
    def config(self):
        return json.loads(
            (ROOT / "slate" / "default_config.json").read_text(encoding="utf-8"))

    def test_the_app_does_not_log_in_as_a_superuser(self, config):
        """
        A superuser can spend the administrator's emergency connections, and
        can reach every other database on the server.
        """
        assert config["db_user"] != "postgres"

    def test_each_machine_holds_few_connections(self, config):
        """
        Four per machine is 600 across 150 seats, against a server limit of
        100. Two halves it.
        """
        assert config["max_db_connections"] <= 2
        assert config["min_db_connections"] >= 1

    def test_semantic_search_cannot_hoard_connections(self, config):
        """It used to be allowed 20 per machine: 3,000 across the studio."""
        assert config["max_semantic_connections"] <= 8


class TestFailuresAreLoud:
    """
    A connection shortage must look like a connection shortage.

    Returning "no rows" makes a full project look empty; returning False makes
    a lost save look like a rejected one. Both are worse than an error.
    """

    @pytest.fixture
    def manager(self):
        mgr = PostgresManager()
        original = mgr._circuit_breaker.call
        yield mgr
        mgr._circuit_breaker.call = original

    def _fail_with(self, manager, exc):
        def boom(*args, **kwargs):
            raise exc
        manager._circuit_breaker.call = boom

    def test_a_read_during_an_outage_raises(self, manager):
        self._fail_with(manager, ConnectionError("pool exhausted"))

        with pytest.raises(DatabaseUnavailableError):
            manager.execute_query("SELECT 1", fetch="one")

    def test_a_save_during_an_outage_raises(self, manager):
        """It must not come back as a plain False, which reads as 'rejected'."""
        self._fail_with(manager, ConnectionError("pool exhausted"))

        with pytest.raises(DatabaseUnavailableError):
            manager.execute_update("UPDATE tracking_shots SET status='X'")

    def test_a_tripped_circuit_breaker_raises(self, manager):
        """The 150-seat case: too many clients, breaker opens."""
        self._fail_with(manager, CircuitBreakerError("open"))

        with pytest.raises(DatabaseUnavailableError):
            manager.execute_query("SELECT 1", fetch="one")

    def test_the_message_says_the_work_is_not_lost(self, manager):
        self._fail_with(manager, ConnectionError("pool exhausted"))

        with pytest.raises(DatabaseUnavailableError) as caught:
            manager.execute_query("SELECT 1", fetch="one")

        assert "not been lost" in str(caught.value)

    def test_an_ordinary_sql_mistake_still_behaves_as_before(self, manager):
        """
        A bad column name is a bug to fix, not an outage to survive. Only
        connection problems were made loud.
        """
        self._fail_with(manager, ValueError("column \"nope\" does not exist"))

        assert manager.execute_query("SELECT nope", fetch="one") is None

    def test_it_is_a_connection_error_so_existing_handlers_still_catch_it(self):
        assert issubclass(DatabaseUnavailableError, ConnectionError)


class TestConnectionsAreIdentifiable:
    """150 anonymous connections cannot be traced to a person or a machine."""

    def test_a_client_names_itself(self):
        identity = _client_identity()

        assert identity.startswith("Slate ")
        assert "@" in identity

    def test_the_name_fits_what_postgres_stores(self):
        assert len(_client_identity()) <= 63


class TestTheConnectionCapReachesInstalledMachines:
    """
    Every workstation that already has Slate carries a settings file written
    before this limit existed, and those files override the shipped defaults.
    Lowering the default alone would therefore have changed nothing on a single
    machine already in use, which is all 150 of them.
    """

    def test_a_saved_setting_cannot_ask_for_more_than_the_cap(self):
        from slate.core.infra.postgres_manager import MAX_POOL_PER_CLIENT

        manager = PostgresManager()

        assert manager.maxconn <= MAX_POOL_PER_CLIENT

    def test_the_cap_keeps_a_full_studio_under_control(self):
        """150 machines must not be able to ask for more than a few hundred."""
        from slate.core.infra.postgres_manager import MAX_POOL_PER_CLIENT

        assert MAX_POOL_PER_CLIENT * 150 <= 300

    def test_at_least_one_connection_is_always_allowed(self):
        assert PostgresManager().minconn >= 1


class TestTheServerCanStillReachItsOwnDatabase:
    """
    Closing the "no password needed" hole broke the server's own tools.

    Its dashboard, its analytics view and its web interface all connected as
    postgres with no password, which worked only because the database was not
    asking for one. Each of them catches its own errors and shows an empty
    panel, so it broke quietly - the database looked fine and the monitoring
    just went blank.
    """

    SERVER_SOURCES = [
        ROOT / "slate_server" / "gui" / "app_window.py",
        ROOT / "slate_server" / "gui" / "views" / "analytics_view.py",
        ROOT / "slate" / "api" / "main.py",
    ]

    @pytest.mark.parametrize("path", SERVER_SOURCES, ids=lambda p: p.name)
    def test_no_connection_is_made_without_credentials(self, path):
        """
        Every psycopg2.connect on the server side must go through the shared
        credentials helper, so there is one place that knows the password.
        """
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "psycopg2.connect(" not in line:
                continue
            # The call may be spread over several lines.
            call = " ".join(lines[index:index + 4])
            assert "connect_kwargs" in call, (
                f"{path.name} line {index + 1} connects without the shared "
                f"credentials: {line.strip()}"
            )

    def test_the_helper_supplies_a_password(self):
        _require_local_password()
        from slate_server.core.db_credentials import connect_kwargs

        kwargs = connect_kwargs(5440)

        assert kwargs.get("password"), "server connections carry no password"
        assert kwargs["user"] == "postgres"

    def test_server_connections_are_named_apart_from_clients(self):
        """So 150 workstations and the server are told apart on the list."""
        from slate_server.core.db_credentials import connect_kwargs

        assert connect_kwargs(5440)["application_name"] == "Slate Central Server"

    def test_command_line_tools_are_given_the_password(self):
        _require_local_password()
        """Otherwise createdb stops to prompt, and nothing is there to answer."""
        from slate_server.core.db_credentials import env_with_password

        assert "PGPASSWORD" in env_with_password({})

    def test_creating_the_database_passes_the_password_through(self):
        """A fresh install runs createdb before anyone can type anything."""
        import inspect
        from slate_server.core.db_engine import DatabaseEngine

        source = inspect.getsource(DatabaseEngine._ensure_slate_database)

        assert "env_with_password" in source


class TestTheTwoBackendsAgree:
    """
    SQLite and PostgreSQL must offer the same methods with the same arguments.

    They drifted: the reel became part of a shot's identity, SQLite's
    update_tracking_shot_safe gained a `reel` argument, and PostgreSQL's
    wrapper did not. Every dashboard save against PostgreSQL then failed with
    a TypeError - while the whole test suite stayed green, because the tests
    run on SQLite.
    """

    @staticmethod
    def _params(cls, name):
        import inspect
        fn = getattr(cls, name, None)
        if not callable(fn):
            return None
        try:
            return [p for p in inspect.signature(fn).parameters if p != "self"]
        except (TypeError, ValueError):
            return None

    def _shared_methods(self):
        from slate.core.infra.postgres_manager import PostgresManager
        from slate.core.infra.sqlite_manager import SQLiteManager

        return [
            name for name in dir(PostgresManager)
            if not name.startswith("_")
            and callable(getattr(PostgresManager, name, None))
            and hasattr(SQLiteManager, name)
        ]

    def test_every_shared_method_takes_the_same_arguments(self):
        from slate.core.infra.postgres_manager import PostgresManager
        from slate.core.infra.sqlite_manager import SQLiteManager

        mismatches = []
        for name in self._shared_methods():
            pg = self._params(PostgresManager, name)
            lite = self._params(SQLiteManager, name)
            if pg is None or lite is None:
                continue
            missing = [p for p in lite if p not in pg]
            if missing:
                mismatches.append(f"{name}: postgres is missing {missing}")

        assert not mismatches, (
            "the backends have drifted; callers will crash on PostgreSQL only:\n  "
            + "\n  ".join(mismatches)
        )

    def test_saving_a_shot_accepts_its_reel_on_both_backends(self):
        """The exact drift that stopped the dashboard saving on PostgreSQL."""
        from slate.core.infra.postgres_manager import PostgresManager
        from slate.core.infra.sqlite_manager import SQLiteManager

        for backend in (PostgresManager, SQLiteManager):
            params = self._params(backend, "update_tracking_shot_safe")
            assert "reel" in params, (
                f"{backend.__name__}.update_tracking_shot_safe drops the reel; "
                "two reels can hold a shot of the same name"
            )


class TestStartupDoesNotEatItself:
    """
    Starting the database manager used to build 163 more of them.

    run_auto_migrations reached for the global `database_manager` from inside
    DatabaseManager.__init__ - so the proxy built a second manager, which ran
    the migrations, which asked for the manager again. Every start-up ran the
    schema migration a hundred and sixty times, wrote hundreds of errors into
    the database log, took the better part of a minute, and sometimes ended in
    "maximum recursion depth exceeded".
    """

    def test_the_migration_takes_the_backend_it_is_told_about(self):
        """So it never has to ask for a manager that is still being built."""
        import inspect
        from slate.core.infra.migrations.auto_migrate import run_auto_migrations

        params = inspect.signature(run_auto_migrations).parameters

        assert "active_mode" in params
        assert "fallback_used" in params

    def test_the_constructor_does_not_run_the_migration_at_all_now(self):
        """
        The recursion came from calling run_auto_migrations inside the
        constructor. That call has gone for a different reason - Alembic cannot
        run on an installed studio and aborted everywhere else - and with it,
        this hazard.

        The guard is kept pointed at the constructor rather than deleted: if
        anyone puts a migration call back here, it has to hand over its own
        state, which is what the test above and _get_manager's refusal below
        exist to enforce.
        """
        import inspect
        from slate.core.infra.database_manager import DatabaseManager

        source = inspect.getsource(DatabaseManager.__init__)
        live = [l for l in source.splitlines()
                if l.strip() and not l.strip().startswith("#")]

        calls_it = any("run_auto_migrations(" in l for l in live)
        if calls_it:
            assert "active_mode=self.active_mode" in source, (
                "the constructor lets the migration reach for the global "
                "manager, which builds another one, and another"
            )

    def test_asking_for_the_manager_while_it_builds_is_refused(self):
        """
        A loud refusal beats a silent hundred-and-sixty-fold recursion. If this
        ever fires in real use, the fix is to pass the backend in.
        """
        import slate.core.infra.database_manager as db_module

        was_building = db_module._manager_building
        was_instance = db_module._manager_instance
        db_module._manager_building = True
        db_module._manager_instance = None
        try:
            with pytest.raises(RuntimeError, match="still"):
                db_module._get_manager()
        finally:
            db_module._manager_building = was_building
            db_module._manager_instance = was_instance

    def test_a_migration_that_is_already_done_is_not_attempted(self):
        """
        Adding a constraint that exists worked, but the database layer logs
        every failure as an error - so a healthy start-up buried real problems
        under hundreds of alarming lines.
        """
        import inspect
        from slate.core.infra.migrations import shot_identity

        source = inspect.getsource(shot_identity._widen_postgres_constraints)

        assert "_constraint_exists" in source


class TestSavingAProject:
    """
    Creating a project on PostgreSQL did nothing, and said nothing.

    The `active` column is an integer; the code passed a Python boolean.
    PostgreSQL refuses that outright while SQLite accepts it as 1 - and the
    result was discarded, so the refusal was silent.
    """

    def test_the_active_flag_is_written_as_a_number(self):
        import inspect
        from slate.core.infra.tracking_repository import TrackingRepository

        source = inspect.getsource(TrackingRepository.save_tracking_project)

        assert "config_json, 1)" in source.replace(" ", "").replace("\n", "") \
            or "config_json,1)" in source.replace(" ", "").replace("\n", ""), \
            "a boolean here is refused by PostgreSQL"

    def test_it_reports_whether_the_project_was_written(self):
        import inspect
        from slate.core.infra.tracking_repository import TrackingRepository

        source = inspect.getsource(TrackingRepository.save_tracking_project)

        assert "return" in source, "the result is thrown away, so failure is silent"

    def test_the_ingest_refuses_to_carry_on_without_a_project(self):
        """Shots with no project to appear under are worse than an error."""
        import inspect
        from slate.core.domain import shot_registry

        source = inspect.getsource(shot_registry._ensure_project)

        assert "could not be created" in source
