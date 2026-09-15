"""
Where the server gets its password, and what it does when it has none.

This is the failure that reached a studio machine. An installed server looked
for its settings next to its own source code, which in a frozen build is a
temporary folder holding nothing. So it read no settings, had no password,
created no accounts with it - and then replaced pg_hba.conf with rules
demanding a password anyway. The result is a cluster that nothing can log in
to, including the server that built it, with the studio's database inside it.

Everything on the dashboard was still correct. The IP, the port, the connection
count and the cluster size all agreed with each other.
"""

import json

import pytest

from slate_server.core import db_credentials, server_facts


@pytest.fixture(autouse=True)
def forget_cached_settings():
    """The settings are read once per process, which would leak between tests."""
    db_credentials._cache = None
    yield
    db_credentials._cache = None


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ------------------------------------------------------------- the layering

def test_a_later_file_wins(tmp_path, monkeypatch):
    shipped = _write(tmp_path / "default_config.json",
                     {"db_password": "shipped", "db_name": "ut_vfx"})
    machine = _write(tmp_path / "machine.json", {"db_password": "corrected"})

    monkeypatch.setattr(db_credentials, "_config_layers",
                        lambda: [shipped, machine])

    assert db_credentials.admin_password() == "corrected"
    assert db_credentials.database_name() == "ut_vfx", \
        "a file that says nothing about a setting must not erase it"


def test_a_blank_value_does_not_erase_a_real_one(tmp_path, monkeypatch):
    """
    The settings the server writes carry keys it does not set. Treating an
    empty string as a correction is how the password disappears from a machine
    that had one, and the save that did it looks like it worked.
    """
    shipped = _write(tmp_path / "default_config.json", {"db_password": "real"})
    machine = _write(tmp_path / "machine.json", {"db_password": "", "db_port": 5440})

    monkeypatch.setattr(db_credentials, "_config_layers",
                        lambda: [shipped, machine])

    assert db_credentials.admin_password() == "real"


def test_a_file_that_is_not_there_is_not_an_error(tmp_path, monkeypatch):
    real = _write(tmp_path / "config.json", {"db_password": "real"})
    monkeypatch.setattr(db_credentials, "_config_layers",
                        lambda: [tmp_path / "nowhere.json", real])

    assert db_credentials.admin_password() == "real"


def test_unreadable_settings_do_not_stop_the_readable_ones(tmp_path, monkeypatch):
    broken = tmp_path / "broken.json"
    broken.write_text("this is not json", encoding="utf-8")
    good = _write(tmp_path / "good.json", {"db_password": "real"})

    monkeypatch.setattr(db_credentials, "_config_layers", lambda: [broken, good])
    assert db_credentials.admin_password() == "real"


def test_the_files_that_were_read_can_be_listed(tmp_path, monkeypatch):
    """
    An installed build read none of them, and nothing on any screen said so.
    The Settings panel shows this list for that reason.
    """
    good = _write(tmp_path / "good.json", {"db_password": "real"})
    monkeypatch.setattr(db_credentials, "_config_layers",
                        lambda: [tmp_path / "nowhere.json", good])

    assert db_credentials.settings_sources() == [str(good)]


def test_an_installed_build_looks_beside_its_executable(monkeypatch, tmp_path):
    """
    The whole bug in one assertion. A frozen build's code lives in a temporary
    folder with no settings in it; the settings are beside the .exe.
    """
    monkeypatch.setattr(db_credentials.sys, "frozen", True, raising=False)
    monkeypatch.setattr(db_credentials.sys, "executable",
                        str(tmp_path / "Slate_Server.exe"))

    layers = [str(p) for p in db_credentials._config_layers()]
    assert any(p == str(tmp_path / "slate" / "default_config.json") for p in layers)
    assert any(p == str(tmp_path / "config.json") for p in layers)


def test_the_environment_still_wins_over_every_file(tmp_path, monkeypatch):
    shipped = _write(tmp_path / "default_config.json", {"db_password": "shipped"})
    monkeypatch.setattr(db_credentials, "_config_layers", lambda: [shipped])
    monkeypatch.setenv("SLATE_DB_PASSWORD", "from the environment")

    assert db_credentials.admin_password() == "from the environment"


def test_reload_picks_up_a_password_that_was_just_saved(tmp_path, monkeypatch):
    """Saved settings that the running process ignores look like a failed save."""
    machine = _write(tmp_path / "machine.json", {})
    monkeypatch.setattr(db_credentials, "_config_layers", lambda: [machine])
    assert db_credentials.admin_password() == ""

    _write(machine, {"db_password": "typed into Settings"})
    db_credentials.reload()
    assert db_credentials.admin_password() == "typed into Settings"


# ------------------------------------------------------ what the error means

def test_a_missing_password_is_not_reported_as_a_dead_database():
    """
    "no password supplied" is not a network problem and not a wrong password.
    Left as the driver writes it, it reads as a database that is down, and
    somebody spends an afternoon looking at ports.
    """
    message = server_facts.explain(
        Exception('connection to server at "127.0.0.1", port 5440 failed: '
                  'fe_sendauth: no password supplied'))

    assert "no database password is configured" in message
    assert "Settings" in message


def test_a_wrong_password_says_it_is_the_wrong_password():
    message = server_facts.explain(
        Exception('connection to server at "127.0.0.1", port 5440 failed: '
                  'FATAL:  password authentication failed for user "postgres"'))

    assert "rejected" in message
    assert "not the one the database has" in message


def test_a_missing_role_says_the_accounts_were_never_made():
    message = server_facts.explain(
        Exception('FATAL:  role "ut_vfx_app" does not exist'))
    assert "accounts were never created" in message


def test_an_ordinary_failure_is_passed_through_unchanged():
    message = server_facts.explain(Exception("Connection refused"))
    assert message == "Connection refused"


def test_an_empty_failure_still_says_something():
    assert server_facts.explain(Exception("")) == "the database did not answer"


def test_the_bundle_root_is_taken_from_pyinstaller_not_guessed(monkeypatch, tmp_path):
    """
    The settings live inside the bundle, and sys._MEIPASS is where PyInstaller
    says that is. Working it out from __file__ instead happens to agree today
    and is not a thing to rely on when being wrong means no password at all.
    """
    monkeypatch.setattr(db_credentials.sys, "frozen", True, raising=False)
    monkeypatch.setattr(db_credentials.sys, "_MEIPASS", str(tmp_path / "bundle"),
                        raising=False)
    monkeypatch.setattr(db_credentials.sys, "executable",
                        str(tmp_path / "Slate_Server.exe"))

    layers = [str(p) for p in db_credentials._config_layers()]
    assert layers[0] == str(tmp_path / "bundle" / "slate" / "default_config.json")


def test_the_same_file_is_never_listed_twice(monkeypatch, tmp_path):
    """A settings list that names one file twice reads as a problem."""
    monkeypatch.setattr(db_credentials.sys, "frozen", True, raising=False)
    monkeypatch.setattr(db_credentials.sys, "_MEIPASS", str(tmp_path),
                        raising=False)
    monkeypatch.setattr(db_credentials.sys, "executable",
                        str(tmp_path / "Slate_Server.exe"))

    layers = [str(p).lower() for p in db_credentials._config_layers()]
    assert len(layers) == len(set(layers))


def test_the_servers_own_settings_file_is_not_read_for_credentials(monkeypatch,
                                                                  tmp_path):
    """
    slate_server_config.json carries no credentials, and its "db_path" means the
    PostgreSQL data directory while the same key in the client settings means
    something else. Reading it here would add nothing but that collision.
    """
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    layers = [str(p) for p in db_credentials._config_layers()]
    assert not any("slate_server_config.json" in p for p in layers)
