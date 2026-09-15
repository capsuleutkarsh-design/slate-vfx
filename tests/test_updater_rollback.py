"""
A failed update must put the previous build back without touching the
studio's own files.

The backup is made with the persistent items excluded - the database, the
logs, the settings files - so a rollback that cleared the install folder and
copied the backup over the gap deleted exactly what the exclusion list existed
to protect. A failed update then cost the studio its server settings on the
way back.
"""

import pytest

from slate.core.updater import updater_script as updater


@pytest.fixture
def install(tmp_path):
    install_dir = tmp_path / "Slate Server"
    install_dir.mkdir()
    (install_dir / "Slate_Server.exe").write_bytes(b"new build, half applied")
    (install_dir / "_internal").mkdir()
    (install_dir / "_internal" / "new.dll").write_bytes(b"new")
    (install_dir / "leftover_from_new_build.dll").write_bytes(b"new")

    (install_dir / "LocalDatabase").mkdir()
    (install_dir / "LocalDatabase" / "PG_VERSION").write_text("14")
    (install_dir / "slate_server_config.json").write_text('{"db_path": "here"}')
    (install_dir / "logs").mkdir()
    (install_dir / "logs" / "server.log").write_text("kept")
    (install_dir / "Cache").mkdir()
    (install_dir / "Cache" / "thumb.jpg").write_bytes(b"kept")

    backup_dir = tmp_path / "Backups" / "Backup_1"
    backup_dir.mkdir(parents=True)
    (backup_dir / "Slate_Server.exe").write_bytes(b"previous build")
    (backup_dir / "_internal").mkdir()
    (backup_dir / "_internal" / "old.dll").write_bytes(b"old")
    return install_dir, backup_dir


def test_rollback_restores_the_build_and_keeps_the_data(install, monkeypatch):
    install_dir, backup_dir = install
    monkeypatch.setattr(updater, "log", lambda msg: None)

    assert updater.restore_backup(backup_dir, install_dir) is True

    assert (install_dir / "Slate_Server.exe").read_bytes() == b"previous build"
    assert (install_dir / "_internal" / "old.dll").exists()
    assert not (install_dir / "_internal" / "new.dll").exists()
    assert not (install_dir / "leftover_from_new_build.dll").exists()

    assert (install_dir / "LocalDatabase" / "PG_VERSION").read_text() == "14"
    assert (install_dir / "slate_server_config.json").exists()
    assert (install_dir / "logs" / "server.log").read_text() == "kept"
    assert (install_dir / "Cache" / "thumb.jpg").exists()


def test_the_persistent_list_still_knows_the_old_names():
    for name in (".capsule_vfx", "ut_server_config.json", "slate_server_config.json",
                 "LocalDatabase", "client_config.json"):
        assert updater.is_persistent(name), name
    assert updater.is_persistent("server.log")
    assert not updater.is_persistent("_internal")


def test_a_missing_backup_is_reported_not_faked(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "log", lambda msg: None)
    assert updater.restore_backup(tmp_path / "nope", tmp_path) is False
