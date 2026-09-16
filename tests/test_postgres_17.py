"""
PostgreSQL 17 replaces 14, and a data directory from the wrong major is refused
with a sentence rather than a failed start.

14 leaves community support in November 2026. The studio has no data yet, so
this is the moment to move. What the move must never do is open a 14 data
directory with 17 binaries: PostgreSQL itself refuses, deep inside pg_ctl,
and all the window used to say was "Failed to start server".
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


class TestTheDataDirectoryVersionIsChecked:

    @pytest.fixture
    def engine(self, tmp_path, monkeypatch):
        from slate_server.core.db_engine import DatabaseEngine
        data = tmp_path / "LocalDatabase"
        data.mkdir()
        (data / "PG_VERSION").write_text("14\n", encoding="utf-8")
        eng = DatabaseEngine(str(data), port=5499)
        monkeypatch.setattr(DatabaseEngine, "binary_major", lambda self: 17)
        return eng

    def test_a_14_directory_is_refused_by_a_17_server(self, engine):
        from slate_server.core.db_engine import DataDirectoryFromAnotherVersion
        with pytest.raises(DataDirectoryFromAnotherVersion) as caught:
            engine.check_data_version()
        text = str(caught.value)
        assert "PostgreSQL 14" in text and "PostgreSQL 17" in text
        assert "pg_upgrade" in text and "backup" in text
        assert caught.value.have == 14 and caught.value.want == 17

    def test_a_matching_directory_passes(self, engine, monkeypatch):
        from slate_server.core.db_engine import DatabaseEngine
        monkeypatch.setattr(DatabaseEngine, "binary_major", lambda self: 14)
        engine.check_data_version()          # no exception

    def test_an_unreadable_version_is_not_a_refusal(self, engine, monkeypatch):
        from slate_server.core.db_engine import DatabaseEngine
        monkeypatch.setattr(DatabaseEngine, "binary_major", lambda self: 0)
        engine.check_data_version()

    def test_start_checks_before_touching_the_cluster(self, engine, monkeypatch):
        """The refusal comes before pg_ctl, so nothing is written or launched."""
        from slate_server.core.db_engine import DatabaseEngine, DataDirectoryFromAnotherVersion
        monkeypatch.setattr(DatabaseEngine, "is_installed", lambda self: True)
        monkeypatch.setattr(DatabaseEngine, "is_ready", lambda self: False)
        touched = []
        monkeypatch.setattr(DatabaseEngine, "_ensure_pg_directories", lambda self: touched.append("dirs"))
        monkeypatch.setattr(DatabaseEngine, "_update_port_in_conf", lambda self: touched.append("conf"))
        with pytest.raises(DataDirectoryFromAnotherVersion):
            engine.start()
        assert touched == []

    def test_the_bundled_version_is_read_from_the_binary(self):
        """On this checkout the real binaries answer; elsewhere 0 is allowed."""
        from slate_server.core.db_engine import DatabaseEngine
        eng = DatabaseEngine(str(ROOT / "LocalDatabase"), port=5440)
        major = eng.binary_major()
        assert major == 0 or major >= 14
        if eng.is_installed():
            assert major == 17


class TestEverythingAgreesOnSeventeen:

    def test_setup_downloads_17(self):
        import json
        manifest = json.loads((ROOT / "setup" / "components.json").read_text(encoding="utf-8"))
        postgres = next(c for c in manifest["components"] if c["name"] == "postgresql")
        assert "postgresql-17." in postgres["url"]
        assert "PostgreSQL 17" in postgres["title"]

    def test_the_notes_tell_a_studio_what_to_do_with_a_14_folder(self):
        notes = (ROOT / "deployment" / "release_notes" / "BETA_2.0.28.md").read_text(encoding="utf-8")
        assert "PostgreSQL 17" in notes
        assert "pg_upgrade" in notes and "Restore" in notes

    def test_the_doctor_flags_a_mismatched_directory(self):
        source = (ROOT / "slate" / "doctor.py").read_text(encoding="utf-8")
        assert "_bundled_postgres_major" in source
        assert "pg_upgrade" in source
