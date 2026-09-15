"""
The cluster that locked itself out, and refusing to build another one.

The order in DatabaseEngine._bootstrap is: make the database, make the
accounts, then harden the access rules. Hardening used to run whether or not
the accounts had been made. A server with no password configured could not make
them, said so in a log file, and hardened the cluster anyway - leaving a
database that demands a password from everybody including the only process that
could have set one.

Nothing recovers from that on its own. Uninstalling does not, because the data
directory is not the program's and survives it; reinstalling adopts the same
cluster and fails again identically.
"""

import pytest

from slate_server.core import db_credentials
from slate_server.core.db_engine import DatabaseEngine


@pytest.fixture(autouse=True)
def forget_cached_settings():
    db_credentials._cache = None
    yield
    db_credentials._cache = None


@pytest.fixture
def engine(tmp_path):
    return DatabaseEngine(str(tmp_path / "LocalDatabase"), port=59999)


def _hardened(engine):
    """A pg_hba.conf of the shape this software writes."""
    engine.data_dir.mkdir(parents=True, exist_ok=True)
    path = engine.data_dir / "pg_hba.conf"
    path.write_text(engine.HBA_SIGNATURE + "\n"
                    "host    all   all   127.0.0.1/32     scram-sha-256\n",
                    encoding="utf-8")
    return path


# ------------------------------------------------------- refusing to harden

def test_no_password_means_the_access_rules_are_left_alone(engine, monkeypatch):
    """
    The whole failure. Hardening a cluster whose accounts were never created
    produces a database nobody can ever open, and the studio's work is inside
    it by the time anyone notices.
    """
    monkeypatch.setattr(db_credentials, "admin_password", lambda: "")
    hardened = []
    monkeypatch.setattr(engine, "_harden_access", lambda: hardened.append(True))
    monkeypatch.setattr(engine, "_ensure_slate_database", lambda: None)

    said = []
    assert engine._bootstrap(said.append) is False
    assert hardened == [], "a cluster with no accounts must not be locked"
    assert any("password" in line.lower() for line in said), \
        "and it has to say so on the screen, not only in a log file"


def test_a_failed_account_setup_also_leaves_the_access_rules_alone(engine, monkeypatch):
    monkeypatch.setattr(db_credentials, "admin_password", lambda: "secret")
    monkeypatch.setattr(engine, "_can_authenticate", lambda: True)
    monkeypatch.setattr(engine, "_ensure_slate_database", lambda: None)
    monkeypatch.setattr(engine, "_ensure_application_role", lambda: False)

    hardened = []
    monkeypatch.setattr(engine, "_harden_access", lambda: hardened.append(True))

    assert engine._bootstrap(lambda _m: None) is False
    assert hardened == []


def test_a_healthy_first_run_still_hardens(engine, monkeypatch):
    """The protection above must not turn into never securing anything."""
    monkeypatch.setattr(db_credentials, "admin_password", lambda: "secret")
    monkeypatch.setattr(engine, "_can_authenticate", lambda: True)
    monkeypatch.setattr(engine, "_ensure_slate_database", lambda: None)
    monkeypatch.setattr(engine, "_ensure_application_role", lambda: True)

    hardened = []
    monkeypatch.setattr(engine, "_harden_access", lambda: hardened.append(True))

    assert engine._bootstrap(lambda _m: None) is True
    assert hardened == [True]


# ------------------------------------------------------------- the recovery

def test_the_temporary_rules_are_loopback_only(engine):
    rules = engine._trust_rules()
    assert engine.HBA_SIGNATURE in rules
    assert "127.0.0.1/32" in rules
    assert "0.0.0.0/0" not in rules
    for network in DatabaseEngine.STUDIO_NETWORKS:
        assert network not in rules, \
            "a repair must not open the cluster to the studio network"


def test_a_cluster_secured_by_hand_is_not_touched(engine, monkeypatch):
    """
    The repair resets the passwords to whatever the settings say. Doing that to
    an administrator's own configuration is not a repair.
    """
    engine.data_dir.mkdir(parents=True, exist_ok=True)
    path = engine.data_dir / "pg_hba.conf"
    original = "# Written by the studio's sysadmin, do not touch\nhost all all ::1/128 cert\n"
    path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(engine, "_can_authenticate", lambda: False)

    assert engine._recover_locked_out_cluster(lambda _m: None) is False
    assert path.read_text(encoding="utf-8") == original


def test_a_locked_out_cluster_is_let_back_in(engine, monkeypatch):
    path = _hardened(engine)
    locked = path.read_text(encoding="utf-8")

    def can_authenticate():
        # What the cluster would actually do: refuse while the hardened file is
        # in place, accept once the temporary trust rules are.
        return "trust" in path.read_text(encoding="utf-8")

    monkeypatch.setattr(engine, "_can_authenticate", can_authenticate)
    monkeypatch.setattr(engine, "_reload_configuration", lambda: True)

    assert engine._recover_locked_out_cluster(lambda _m: None) is True
    assert "trust" in path.read_text(encoding="utf-8"), \
        "recovery leaves the window open; _bootstrap closes it by hardening"
    assert (engine.data_dir / "pg_hba.conf.locked-out").read_text(
        encoding="utf-8") == locked, "the file it replaced is kept"


def test_a_failed_repair_puts_the_rules_back(engine, monkeypatch):
    """
    A repair that does not work must not be the thing that leaves a studio
    database open to the network.
    """
    path = _hardened(engine)
    locked = path.read_text(encoding="utf-8")

    monkeypatch.setattr(engine, "_can_authenticate", lambda: False)
    monkeypatch.setattr(engine, "_reload_configuration", lambda: True)

    assert engine._recover_locked_out_cluster(lambda _m: None) is False
    assert path.read_text(encoding="utf-8") == locked
    assert "trust" not in path.read_text(encoding="utf-8")


def test_a_repair_that_cannot_reload_puts_the_rules_back(engine, monkeypatch):
    path = _hardened(engine)
    locked = path.read_text(encoding="utf-8")

    monkeypatch.setattr(engine, "_can_authenticate", lambda: False)
    monkeypatch.setattr(engine, "_reload_configuration", lambda: False)

    assert engine._recover_locked_out_cluster(lambda _m: None) is False
    assert path.read_text(encoding="utf-8") == locked


def test_the_hardened_file_carries_the_signature_the_repair_looks_for(engine,
                                                                     monkeypatch):
    """
    The repair only acts on a file this software wrote. If hardening ever stops
    writing that first line, every locked-out cluster becomes unrecoverable and
    nothing else would notice.
    """
    engine.data_dir.mkdir(parents=True, exist_ok=True)
    path = engine.data_dir / "pg_hba.conf"
    path.write_text("local all all trust\n", encoding="utf-8")
    monkeypatch.setattr(engine, "_reload_configuration", lambda: True)

    assert engine._harden_access() is True
    assert path.read_text(encoding="utf-8").startswith(engine.HBA_SIGNATURE)


# ------------------------------------------------- the statement that failed

def test_the_ownership_transfer_is_composed_not_parameterised():
    """
    psycopg2 substitutes only when it is given parameters, and when it does,
    every % in the statement is a placeholder. This block is PL/pgSQL calling
    format(), so it is full of %I - and psycopg2 stopped at the first one, on
    every server, every time, with "IndexError: tuple index out of range".

    The account was therefore never created. Until hardening was made
    conditional, the cluster was then locked against credentials that did not
    exist; after that, the server said the account could not be set up and
    nobody could see why.

    Proved on a throwaway cluster: as the server sent it, IndexError; with the
    role composed in and no parameters, it runs and ownership transfers.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "slate_server" / "core"
              / "db_engine.py").read_text(encoding="utf-8")

    start = source.index("DO $do$")
    end = source.index("$do$;", start)
    block = source[start:end]

    assert "%I" in block, "this is the block being guarded"
    assert "{owner}" in block, "the role has to be composed in, not passed"
    assert "%s" not in block, \
        "a %s here means parameters are passed, and then every %I is a placeholder"

    after = source[end:end + 200]
    assert "sql.Literal(role)" in after
    assert "(role, role)" not in after, "no parameters may be passed with this"


def test_a_refused_account_says_what_the_database_said(engine, monkeypatch):
    """
    The reason was only ever in a log file, and it is the one thing standing
    between a fresh install and a working studio.
    """
    monkeypatch.setattr(db_credentials, "admin_password", lambda: "secret")
    monkeypatch.setattr(engine, "_can_authenticate", lambda: True)
    monkeypatch.setattr(engine, "_ensure_slate_database", lambda: None)
    monkeypatch.setattr(engine, "_ensure_application_role", lambda: False)
    monkeypatch.setattr(engine, "_harden_access", lambda: None)
    engine._last_role_error = "IndexError: tuple index out of range"

    said = []
    engine._bootstrap(said.append)

    assert any("tuple index out of range" in line for line in said), \
        "the screen has to carry the reason, not just the refusal"
