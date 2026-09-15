"""
The same work, done against a real PostgreSQL.

Every other test in this suite runs on SQLite. That is fast, and it is not what
the studio runs - and three separate bugs reached the live server behind a
green suite because of it:

  * the server's own monitoring lost its password and went blank;
  * the client's constructor was cut in half, so no PostgreSQL client could
    start at all;
  * every dashboard save raised a TypeError, because the PostgreSQL wrapper
    dropped an argument SQLite accepted.

Not one of those was visible on SQLite. These tests put the real thing
underneath the same code. They skip themselves when no PostgreSQL is running,
so the suite still passes on a laptop with nothing installed.
"""

import pytest

from slate.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import (
    SQLiteHandler, StaleDataError,
)
from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot


PROJECT = "PGTEST"


def _handler(pg_db, role="supervisor"):
    return SQLiteHandler(PROJECT, db_manager=pg_db, user_role=role)


def _shot(name="SH010", reel="ReelA", **kwargs):
    return Shot(shot_name=name, reel_episode=reel, status="WIP", **kwargs)


@pytest.fixture
def handler(pg_db):
    return _handler(pg_db)


class TestTheBasicsActuallyWork:
    """If these fail, nothing else about PostgreSQL matters."""

    def test_the_client_starts_at_all(self, pg_db):
        """
        The constructor was once split in two by a bad edit, leaving the
        password field unassigned. Every PostgreSQL client failed on startup
        and the whole suite stayed green.
        """
        from slate.core.infra.postgres_manager import PostgresManager

        manager = PostgresManager()

        for attribute in ("host", "port", "dbname", "user", "password",
                          "minconn", "maxconn", "pooler_port"):
            assert hasattr(manager, attribute), (
                f"PostgresManager has no {attribute} - its constructor did not "
                "finish"
            )

    def test_it_is_really_postgres_and_not_a_quiet_fallback(self, pg_db):
        status = pg_db.get_runtime_status()

        assert status.get("active_mode") == "postgres"
        assert status.get("fallback_used") is False

    def test_a_query_returns_rows(self, pg_db):
        assert pg_db.execute_query("SELECT 1 AS v", fetch="one")["v"] == 1


class TestSavingAShot:
    """
    The bug that mattered most: the dashboard could not save. At all.

    SQLite's update_tracking_shot_safe took a `reel`; PostgreSQL's wrapper did
    not, and the dashboard always passes one.
    """

    def test_a_shot_can_be_created(self, handler):
        assert handler.write_shots([_shot()])
        assert [s.shot_name for s in handler.read_shots()] == ["SH010"]

    def test_a_shot_can_be_edited_and_the_change_sticks(self, handler, pg_db):
        handler.write_shots([_shot()])

        shot = handler.read_shots()[0]
        shot.sow = "remove the wires"
        assert handler.write_shots([shot]), "the save was rejected"

        assert _handler(pg_db).read_shots()[0].sow == "remove the wires"

    def test_department_work_survives_the_round_trip(self, handler, pg_db):
        handler.write_shots([_shot()])
        shot = handler.read_shots()[0]
        shot.dept("comp").artist = "priya"
        shot.dept("comp").status = "WIP"
        shot.dept("roto").bid_days = 2.5
        handler.write_shots([shot])

        after = _handler(pg_db).read_shots()[0]

        assert after.dept("comp").artist == "priya"
        assert after.dept("comp").status == "WIP"
        assert after.dept("roto").bid_days == 2.5

    def test_two_reels_may_hold_a_shot_of_the_same_name(self, handler, pg_db):
        """
        This is why the reel has to reach the database at all. Saving one must
        not touch the other.
        """
        handler.write_shots([_shot("SH010", "ReelA"), _shot("SH010", "ReelB")])

        shots = sorted(handler.read_shots(), key=lambda s: s.reel_episode)
        assert len(shots) == 2

        shots[0].sow = "reel A only"
        handler.write_shots([shots[0]])

        after = sorted(_handler(pg_db).read_shots(), key=lambda s: s.reel_episode)
        assert after[0].sow == "reel A only"
        assert after[1].sow == "", "editing one reel changed the other"

    def test_a_batch_save_works(self, handler):
        handler.write_shots([_shot("SH010"), _shot("SH020"), _shot("SH030")])

        assert len(handler.read_shots()) == 3


class TestTwoPeopleAtOnce:
    """Optimistic locking has to behave the same on both backends."""

    def test_the_second_writer_is_rejected(self, handler, pg_db):
        handler.write_shots([_shot()])

        first = handler.read_shots()[0]
        second = _handler(pg_db).read_shots()[0]

        first.sow = "first writer"
        assert handler.write_shots([first])

        second.sow = "second writer"
        with pytest.raises(StaleDataError):
            handler.write_shots([second])

    def test_the_first_writer_s_work_survives(self, handler, pg_db):
        handler.write_shots([_shot()])
        first = handler.read_shots()[0]
        second = _handler(pg_db).read_shots()[0]

        first.sow = "first writer"
        handler.write_shots([first])
        second.sow = "second writer"
        try:
            handler.write_shots([second])
        except StaleDataError:
            pass

        assert _handler(pg_db).read_shots()[0].sow == "first writer"


class TestWhatAnArtistMayDo:
    """The permission rules must hold on the real backend, not just SQLite."""

    @pytest.fixture
    def assigned(self, handler):
        shot = _shot()
        shot.dept("comp").artist = "priya"
        shot.dept("comp").status = "WIP"
        handler.write_shots([shot])
        return handler.read_shots()[0]

    def test_an_artist_cannot_approve_their_own_work(self, pg_db, assigned):
        artist = _handler(pg_db, role="artist")

        with pytest.raises(PermissionError, match="verdict"):
            artist.update_department_status(
                "SH010", "ReelA", "comp", "APPROVED", assigned.version,
                actor_identities=["priya"])

    def test_an_artist_can_submit_for_review(self, pg_db, assigned):
        artist = _handler(pg_db, role="artist")

        artist.update_department_status(
            "SH010", "ReelA", "comp", "SENT FOR REVIEW", assigned.version,
            actor_identities=["priya"])

        assert _handler(pg_db).read_shots()[0].dept("comp").status == "SENT FOR REVIEW"

    def test_an_artist_cannot_touch_another_department(self, pg_db, assigned):
        artist = _handler(pg_db, role="artist")

        with pytest.raises(PermissionError):
            artist.update_department_status(
                "SH010", "ReelA", "roto", "WIP", assigned.version,
                actor_identities=["priya"])


class TestTheIngestReachesTheDashboard:
    """The whole chain, on the real backend."""

    def test_an_ingested_shot_appears_with_its_frame_range(self, pg_db, tmp_path):
        from slate.core.domain.shot_registry import register_ingested_shots

        result = register_ingested_shots(
            PROJECT,
            [{"reel": "ReelA", "shot": "SH010", "scan_version": "v001",
              "first_frame": 1001, "last_frame": 1048}],
            db=pg_db, project_name="Postgres Test", folder_base=str(tmp_path))

        assert result.ok
        assert result.created == ["SH010"]

        shot = _handler(pg_db).read_shots()[0]
        assert shot.frame_range_text == "1001-1048"
        assert shot.folder_paths, "the shot has no folder paths"

    def test_the_project_can_be_loaded_by_the_dashboard(self, pg_db, tmp_path):
        """
        A project the ingest creates must be loadable, not silently dropped for
        missing fields.
        """
        from slate.core.domain.shot_registry import register_ingested_shots
        from slate.gui.tabs.vfx_dashboard_pro.core.project_manager import ProjectManager

        register_ingested_shots(
            PROJECT, [{"reel": "ReelA", "shot": "SH010", "scan_version": "v001"}],
            db=pg_db, project_name="Postgres Test", folder_base=str(tmp_path))

        assert PROJECT in ProjectManager().projects


class TestTheOtherStores:
    """Everything else that keeps data, exercised once on the real backend."""

    def test_versions(self, pg_db):
        from slate.core.domain.versions import VersionStore

        store = VersionStore(db=pg_db)
        assert isinstance(store.awaiting_review(PROJECT), list)

    def test_delivery_batches(self, pg_db):
        from slate.core.domain.deliveries import DeliveryStore

        store = DeliveryStore(db=pg_db)
        assert isinstance(store.list_deliveries(PROJECT), list)

    def test_users_and_roles_tables_exist(self, pg_db):
        for table in ("ut_users", "ut_roles"):
            assert pg_db.execute_query(
                f"SELECT count(*) AS c FROM {table}", fetch="one") is not None


class TestLeaveRequestsOnPostgres:
    """
    A leave request has to reach the database, not only the log.

    On SQLite this always passed: SQLite takes 1 and 0 for a boolean. On
    PostgreSQL the half_day column is a real BOOLEAN, the insert was refused,
    and the repository reported success anyway - so nobody in the studio could
    file leave, and nothing said so.
    """

    def test_a_request_is_saved_and_read_back(self, pg_db):
        from datetime import date, timedelta
        from slate.core.infra.leave_repository import LeaveRepository

        repo = LeaveRepository(db=pg_db)
        start = date.today() + timedelta(days=40)
        assert repo.submit("EMP0090", "Casual", start, start, True, "dentist") is True

        rows = pg_db.execute_query(
            "SELECT id, half_day, days_charged FROM leave_requests WHERE user_id = %s",
            ("EMP0090",), fetch="all")
        assert len(rows) == 1, "the request must be in the table, not just reported"
        assert rows[0]["half_day"] is True

        again = repo.request(rows[0]["id"])
        assert again["user_id"] == "EMP0090"
        assert again["reason"] == "dentist"

    def test_a_refused_insert_is_not_reported_as_saved(self, pg_db, monkeypatch):
        from datetime import date, timedelta
        from slate.core.infra.leave_repository import LeaveRepository

        repo = LeaveRepository(db=pg_db)
        monkeypatch.setattr(pg_db, "execute_update", lambda *a, **k: False)
        start = date.today() + timedelta(days=50)
        assert repo.submit("EMP0091", "Casual", start, start, False, "x") is False

    def test_an_older_integer_column_is_converted_so_requests_still_save(self, pg_db):
        """
        Databases made by an earlier build have half_day as INTEGER. A fresh
        one has BOOLEAN. The migration makes them the same, and a request
        saves on both.
        """
        from datetime import date, timedelta
        from slate.core.infra.leave_repository import LeaveRepository
        from slate.core.infra.migrations import workplace_schema

        pg_db.execute_update(
            "ALTER TABLE leave_requests ALTER COLUMN half_day DROP DEFAULT, "
            "ALTER COLUMN half_day TYPE INTEGER USING (CASE WHEN half_day THEN 1 ELSE 0 END), "
            "ALTER COLUMN half_day SET DEFAULT 0")
        assert workplace_schema._column_type(pg_db, "leave_requests", "half_day") == "integer"

        assert workplace_schema.apply_migration(pg_db) is True
        assert workplace_schema._column_type(pg_db, "leave_requests", "half_day") == "boolean"

        start = date.today() + timedelta(days=60)
        assert LeaveRepository(db=pg_db).submit("EMP0092", "Casual", start, start, True, "x") is True
        row = pg_db.execute_query(
            "SELECT half_day FROM leave_requests WHERE user_id = %s", ("EMP0092",), fetch="one")
        assert row["half_day"] is True
