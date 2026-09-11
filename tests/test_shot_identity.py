"""
A shot is identified by its reel and its name together.

SH010 in ReelA and SH010 in ReelB are different shots. They used to collapse
into one database row: the folders for both were built on disk while only one
appeared in the dashboard, and nothing was logged. On an episodic where SH010
exists in EP01 and EP02, half the show went missing.
"""

import json
import sqlite3

import pytest

from ut_vfx.core.domain.shot_registry import register_ingested_shots
from ut_vfx.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler
from ut_vfx.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot


PROJECT = "IDENT_PRJ"


@pytest.fixture
def handler(mock_db):
    return SQLiteHandler(project_code=PROJECT, db_manager=mock_db,
                         user_id=1, user_role="supervisor")


def _shot(name, reel, **kw):
    shot = Shot(shot_name=name, reel_episode=reel, status="YTS")
    for key, value in kw.items():
        setattr(shot, key, value)
    return shot


class TestTwoReelsOneShotName:

    def test_both_shots_exist(self, handler):
        handler.write_shots([_shot("SH010", "ReelA"), _shot("SH010", "ReelB")])

        found = sorted((s.reel_episode, s.shot_name) for s in handler.read_shots())
        assert found == [("ReelA", "SH010"), ("ReelB", "SH010")]

    def test_they_keep_separate_work(self, handler):
        handler.write_shots([_shot("SH010", "ReelA"), _shot("SH010", "ReelB")])

        shots = {s.reel_episode: s for s in handler.read_shots()}
        shots["ReelA"].dept("comp").artist = "Rahul"
        shots["ReelA"].dept("comp").bid_days = 3.0
        shots["ReelB"].dept("comp").artist = "Priya"
        shots["ReelB"].dept("comp").bid_days = 5.0
        handler.write_shots(list(shots.values()))

        after = {s.reel_episode: s for s in handler.read_shots()}
        assert after["ReelA"].dept("comp").artist == "Rahul"
        assert after["ReelA"].dept("comp").bid_days == 3.0
        assert after["ReelB"].dept("comp").artist == "Priya"
        assert after["ReelB"].dept("comp").bid_days == 5.0

    def test_editing_one_does_not_move_the_other(self, handler):
        handler.write_shots([_shot("SH010", "ReelA"), _shot("SH010", "ReelB")])

        shots = {s.reel_episode: s for s in handler.read_shots()}
        target = shots["ReelB"]
        target.status = "APPROVED"
        handler.write_shots([target])

        after = {s.reel_episode: s for s in handler.read_shots()}
        assert after["ReelB"].status == "APPROVED"
        assert after["ReelA"].status == "YTS", "editing ReelB changed ReelA"

    def test_an_ingest_creates_both(self, mock_db, handler):
        delivery = [
            {"reel": "ReelA", "shot": "SH010", "scan_version": "v001"},
            {"reel": "ReelB", "shot": "SH010", "scan_version": "v001"},
        ]
        result = register_ingested_shots(PROJECT, delivery, db=mock_db)

        assert len(result.created) == 2
        assert len(handler.read_shots()) == 2

    def test_re_ingesting_creates_neither_again(self, mock_db, handler):
        delivery = [
            {"reel": "ReelA", "shot": "SH010", "scan_version": "v001"},
            {"reel": "ReelB", "shot": "SH010", "scan_version": "v001"},
        ]
        register_ingested_shots(PROJECT, delivery, db=mock_db)
        second = register_ingested_shots(PROJECT, delivery, db=mock_db)

        assert second.created == []
        assert len(second.already_present) == 2
        assert len(handler.read_shots()) == 2

    def test_the_same_shot_twice_in_one_delivery_is_still_one_shot(
            self, mock_db, handler):
        delivery = [
            {"reel": "ReelA", "shot": "SH010"},
            {"reel": "ReelA", "shot": "SH010"},
        ]
        result = register_ingested_shots(PROJECT, delivery, db=mock_db)

        assert result.created == ["SH010"]
        assert len(handler.read_shots()) == 1


class TestVersionsAreScopedToTheRightShot:

    def test_versions_do_not_leak_between_reels(self, mock_db, handler):
        from ut_vfx.core.domain.versions import VersionStore

        handler.write_shots([_shot("SH010", "ReelA"), _shot("SH010", "ReelB")])
        store = VersionStore(db=mock_db)

        store.add_version(PROJECT, "SH010", artist="Rahul")

        # Versions are still addressed by name today; this records that a
        # version lands once, not once per reel.
        assert len(store.list_for_shot(PROJECT, "SH010")) == 1


class TestMigrationFromAnOlderDatabase:
    """An install created before the reel was part of the key."""

    @pytest.fixture
    def legacy_db(self, tmp_path):
        """A database with the old schema and one shot already in it."""
        path = tmp_path / "legacy.db"
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE tracking_shots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT NOT NULL,
                shot_name TEXT NOT NULL,
                status TEXT DEFAULT '',
                priority INTEGER DEFAULT 0,
                data_json TEXT DEFAULT '{}',
                last_updated TEXT,
                version INTEGER DEFAULT 0,
                UNIQUE(project_code, shot_name)
            )
        """)
        payload = json.dumps({
            "shot_name": "SH010", "reel_episode": "ReelA", "status": "WIP",
            "assigned_artist": "Rahul",
        })
        conn.execute(
            "INSERT INTO tracking_shots (project_code, shot_name, status, "
            "priority, data_json, version) VALUES (?, ?, ?, ?, ?, ?)",
            (PROJECT, "SH010", "WIP", 2, payload, 3),
        )
        conn.commit()
        conn.close()
        return path

    @staticmethod
    def _manager(path):
        from ut_vfx.core.infra.sqlite_manager import SQLiteManager
        SQLiteManager._instance = None
        return SQLiteManager(db_path=str(path))

    def test_dry_run_reports_before_changing_anything(self, legacy_db):
        from ut_vfx.core.infra.migrations.shot_identity import report_shot_identity

        db = self._manager(legacy_db)
        report = report_shot_identity(db)

        assert report["error"] is None
        assert report["reel_column_present"] is False
        assert report["key_is_current"] is False

    def test_migration_adds_the_column_and_backfills_it(self, legacy_db):
        from ut_vfx.core.infra.migrations.shot_identity import (
            ensure_shot_identity, report_shot_identity,
        )

        db = self._manager(legacy_db)
        assert ensure_shot_identity(db) is True

        report = report_shot_identity(db)
        assert report["reel_column_present"] is True
        assert report["key_is_current"] is True
        assert report["shots_needing_backfill"] == 0

        row = db.execute_query(
            "SELECT reel, shot_name, status, version FROM tracking_shots",
            fetch="one",
        )
        assert row["reel"] == "ReelA"
        assert row["shot_name"] == "SH010"

    def test_existing_work_is_preserved(self, legacy_db):
        """The migration must not disturb a status, an artist or a version."""
        from ut_vfx.core.infra.migrations.shot_identity import ensure_shot_identity

        db = self._manager(legacy_db)
        ensure_shot_identity(db)

        row = db.execute_query(
            "SELECT status, priority, data_json, version FROM tracking_shots",
            fetch="one",
        )
        assert row["status"] == "WIP"
        assert row["priority"] == 2
        assert row["version"] == 3
        assert json.loads(row["data_json"])["assigned_artist"] == "Rahul"

    def test_running_it_twice_changes_nothing(self, legacy_db):
        from ut_vfx.core.infra.migrations.shot_identity import (
            ensure_shot_identity, report_shot_identity,
        )

        db = self._manager(legacy_db)
        ensure_shot_identity(db)
        before = db.execute_query("SELECT * FROM tracking_shots", fetch="all")

        assert ensure_shot_identity(db) is True
        after = db.execute_query("SELECT * FROM tracking_shots", fetch="all")

        assert len(before) == len(after) == 1
        assert report_shot_identity(db)["key_is_current"] is True

    def test_a_migrated_database_accepts_the_second_reel(self, legacy_db):
        """The whole point: after migrating, ReelB/SH010 can be added."""
        from ut_vfx.core.infra.migrations.shot_identity import ensure_shot_identity

        db = self._manager(legacy_db)
        ensure_shot_identity(db)

        payload = json.dumps({"shot_name": "SH010", "reel_episode": "ReelB"})
        db.save_tracking_shots(PROJECT, [("SH010", "YTS", 3, payload)])

        rows = db.execute_query(
            "SELECT reel FROM tracking_shots WHERE shot_name='SH010'", fetch="all"
        )
        assert sorted(r["reel"] for r in rows) == ["ReelA", "ReelB"]
