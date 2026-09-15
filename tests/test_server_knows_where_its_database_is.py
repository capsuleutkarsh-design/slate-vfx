"""
The server never guesses where its database is, and never builds one unasked.

Four PostgreSQL clusters were found on one machine in a single day. Each was
created when the server, having no saved setting, derived a data directory
from something that changes - a drive letter, SERVER_ROOT, LOCALAPPDATA - found
an empty folder there, and ran initdb without being asked. Every one of them
then showed a correct IP, a correct port and "0 tables". The studio's real
database was a fifth folder that nothing was pointing at, and the server had
not opened it in a day.

The dev server and the installed one also shared a settings folder, so
uninstalling the installed build - with "delete its data" - deleted the
settings the dev server was using, and it went and built cluster number four.

The rules these tests hold:
  1. from a checkout, the server's home is the checkout - unreachable by any
     installer
  2. the default data directory is one fixed answer, derived from nothing
  3. the engine does not initdb unless told to, explicitly, by a person
  4. the server does not write to the workstation's config file
  5. the dashboard names another cluster on the machine that has data in it
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------- home & default

def test_from_a_checkout_the_server_lives_in_the_checkout(monkeypatch):
    from slate_server.gui import app_window

    monkeypatch.setattr(app_window.sys, "frozen", False, raising=False)
    assert Path(app_window._server_home()) == ROOT


def test_installed_the_server_lives_in_the_per_user_folder(monkeypatch, tmp_path):
    from slate_server.gui import app_window

    monkeypatch.setattr(app_window.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert Path(app_window._server_home()) == tmp_path / "Slate_Central"


def test_the_default_data_dir_is_one_answer_and_derived_from_nothing(monkeypatch, tmp_path):
    """
    SERVER_ROOT, drive letters and folder existence are all things that change,
    and each change used to move the database and build a new empty one.
    """
    from slate_server.gui import app_window

    monkeypatch.delenv("SLATE_DB_PATH", raising=False)
    home = str(tmp_path / "home")

    # Whatever the client settings say, the answer is the same.
    import slate.core.infra.local_secrets as ls
    monkeypatch.setattr(ls, "local_config", lambda: {"SERVER_ROOT": str(tmp_path)})
    (tmp_path / "Database").mkdir()

    assert app_window._default_data_dir(home) == os.path.join(home, "LocalDatabase")


def test_an_explicit_environment_path_still_wins(monkeypatch, tmp_path):
    from slate_server.gui import app_window

    monkeypatch.setenv("SLATE_DB_PATH", str(tmp_path / "somewhere"))
    assert app_window._default_data_dir("ignored") == str(tmp_path / "somewhere")


def test_there_is_no_drive_letter_fallback_any_more():
    """
    A server whose drive was not mapped yet used to start cleanly onto a new
    empty database, and every figure on its dashboard was correct.
    """
    source = (ROOT / "slate_server" / "gui" / "app_window.py").read_text(encoding="utf-8")
    live = [l for l in source.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert not any("Falling back to" in l for l in live)
    assert not any("splitdrive(db_path)" in l for l in live)


# ------------------------------------------------------ never initdb unasked

def test_the_engine_refuses_to_build_a_database_it_was_not_told_to(tmp_path):
    from slate_server.core.db_engine import DatabaseEngine, NoDatabaseHere

    engine = DatabaseEngine(str(tmp_path / "LocalDatabase"), port=59990)
    engine.is_installed = lambda: True       # binaries are not the question
    engine.is_ready = lambda: False

    with pytest.raises(NoDatabaseHere) as caught:
        engine.start()
    assert str(tmp_path / "LocalDatabase") in str(caught.value)
    assert not (tmp_path / "LocalDatabase" / "PG_VERSION").exists(), \
        "refusing means nothing was created"


def test_the_engine_builds_one_when_a_person_said_so(tmp_path, monkeypatch):
    from slate_server.core.db_engine import DatabaseEngine

    engine = DatabaseEngine(str(tmp_path / "LocalDatabase"), port=59990,
                            allow_create=True)
    engine.is_installed = lambda: True
    engine.is_ready = lambda: False

    made = []
    monkeypatch.setattr(engine, "initialize_database",
                        lambda progress_callback=None: made.append(True) or True)
    # Stop before pg_ctl: the point is whether initdb was reached.
    monkeypatch.setattr(engine, "_update_port_in_conf", lambda: None)
    monkeypatch.setattr(engine, "_ensure_pg_directories", lambda: None)
    monkeypatch.setattr(engine, "_clean_stale_pid_file", lambda: None)
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(engine, "_bootstrap", lambda *a, **k: True)

    try:
        engine.start()
    except Exception:
        pass        # readiness polling fails without a real cluster
    assert made == [True]


def test_allow_create_is_off_by_default(tmp_path):
    from slate_server.core.db_engine import DatabaseEngine
    assert DatabaseEngine(str(tmp_path), port=1).allow_create is False


def test_the_window_asks_before_the_engine_can_build_anything():
    """The choice is a dialog with a button, not a default."""
    source = (ROOT / "slate_server" / "gui" / "app_window.py").read_text(encoding="utf-8")
    assert "def _database_chosen" in source
    assert "Create a new empty database here" in source
    assert "Use an existing database folder" in source
    assert "if state and not self._database_chosen():" in source
    assert "engine.allow_create = True" in source, \
        "only the create button may switch it on"


# -------------------------------------------- hands off the workstation file

def test_the_server_does_not_write_the_workstation_config():
    """
    GlobalConfig.set() saves the entire client configuration back to the
    per-machine file. The server was doing that once a day to store a date.
    """
    source = (ROOT / "slate_server" / "gui" / "app_window.py").read_text(encoding="utf-8")
    live = [l for l in source.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert not any("GlobalConfig.set(" in l for l in live)


# --------------------------------------------------- the other clusters

def _fake_cluster(path: Path, data_kb: int):
    """A cluster on disk with one empty database and one holding data_kb."""
    (path / "base" / "13753").mkdir(parents=True)
    (path / "base" / "16384").mkdir(parents=True)
    (path / "PG_VERSION").write_text("14")
    (path / "base" / "13753" / "1259").write_bytes(b"\\0" * 8192)
    (path / "base" / "16384" / "1259").write_bytes(b"\\0" * 8192)
    if data_kb:
        (path / "base" / "16384" / "16500").write_bytes(b"\\0" * data_kb * 1024)


def test_a_cluster_with_data_is_told_from_an_empty_one(tmp_path):
    from slate_server.core import server_facts

    empty = tmp_path / "empty"
    _fake_cluster(empty, 0)
    full = tmp_path / "full"
    _fake_cluster(full, 4096)

    assert server_facts.looks_populated(empty) is False
    assert server_facts.looks_populated(full) is True
    assert server_facts.data_bytes(full) >= 4096 * 1024


def test_thirty_empty_tables_are_not_mistaken_for_data(tmp_path):
    """The exact thing that must not count: schema, no rows."""
    from slate_server.core import server_facts

    path = tmp_path / "schema_only"
    _fake_cluster(path, 0)
    for i in range(30):
        (path / "base" / "16384" / ("2%04d" % i)).write_bytes(b"\\0" * 8192)

    assert server_facts.looks_populated(path) is False


def test_the_served_cluster_is_not_listed_as_another_one(tmp_path, monkeypatch):
    from slate_server.core import server_facts

    here = tmp_path / "here"
    there = tmp_path / "there"
    _fake_cluster(here, 0)
    _fake_cluster(there, 2048)
    monkeypatch.setattr(server_facts, "known_cluster_locations",
                        lambda: [here, there])

    others = server_facts.other_clusters(here)
    assert [o["path"] for o in others] == [str(there)]
    assert others[0]["populated"] is True


def test_the_dashboard_points_at_the_cluster_that_has_the_data():
    """The line that would have ended this on the first morning."""
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from slate_server.gui.views.dashboard_view import DashboardView

    view = DashboardView()
    view.set_cluster_facts(
        {"data_dir": "d", "running_data_dir": "d", "size": "50.0 MB",
         "tables": 0, "database": "ut_vfx", "matches_config": True, "error": "",
         "others": [{"path": r"D:\Studio\LocalDatabase", "size": "124.6 MB",
                     "data": "2.6 MB", "data_bytes": 2700000, "populated": True}]},
        {"installed": True, "running": True, "listen_port": 6432,
         "publishes": "ut_vfx", "expects": "ut_vfx", "agrees": True})

    text = view.lbl_warning.text()
    assert r"D:\Studio\LocalDatabase" in text
    assert "2.6 MB" in text
    assert "Settings" in text


def test_a_new_studio_is_not_accused_of_a_mistake():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from slate_server.gui.views.dashboard_view import DashboardView

    view = DashboardView()
    view.set_cluster_facts(
        {"data_dir": "d", "running_data_dir": "d", "size": "50.0 MB",
         "tables": 0, "database": "ut_vfx", "matches_config": True, "error": "",
         "others": []},
        {"installed": True, "running": True, "listen_port": 6432,
         "publishes": "ut_vfx", "expects": "ut_vfx", "agrees": True})

    assert "normal for a brand new studio" in view.lbl_warning.text()
