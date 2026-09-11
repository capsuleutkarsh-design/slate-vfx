"""
Version history and per-version notes.

A shot used to carry only "current version" as a string, so submission history
existed nowhere in the software. These tests cover the record that replaces it.
"""

from datetime import date

import pytest

from ut_vfx.core.domain.versions import (
    STATUS_APPROVED, STATUS_PENDING, STATUS_RETAKE, SENT_CLIENT, SENT_INTERNAL,
    Version, VersionStore, next_version_name,
)


PROJECT = "VER_PRJ"


@pytest.fixture
def store(mock_db):
    return VersionStore(db=mock_db)


class TestVersionNaming:

    def test_first_version_is_v001(self):
        assert next_version_name([]) == "v001"

    def test_next_follows_the_highest_used(self):
        assert next_version_name(["v001", "v002"]) == "v003"

    def test_gaps_do_not_reuse_a_number(self):
        assert next_version_name(["v001", "v007"]) == "v008"

    def test_unnumbered_names_are_ignored(self):
        assert next_version_name(["final", "v002"]) == "v003"

    def test_version_number_is_parsed_for_ordering(self):
        assert Version(version_name="v012").number == 12
        assert Version(version_name="comp_v7").number == 7
        assert Version(version_name="final").number == 0


class TestRecordingVersions:

    def test_add_and_read_back(self, store):
        version = store.add_version(
            PROJECT, "SH010", department="comp", artist="Rahul",
            media_path="/renders/SH010_v001.mov", created_by="coord",
        )
        assert version is not None
        assert version.version_name == "v001"
        assert version.artist == "Rahul"
        assert version.status == STATUS_PENDING

        stored = store.list_for_shot(PROJECT, "SH010")
        assert len(stored) == 1
        assert stored[0].media_path == "/renders/SH010_v001.mov"

    def test_versions_auto_number(self, store):
        store.add_version(PROJECT, "SH010")
        store.add_version(PROJECT, "SH010")
        third = store.add_version(PROJECT, "SH010")

        assert third.version_name == "v003"
        assert len(store.list_for_shot(PROJECT, "SH010")) == 3

    def test_newest_version_comes_first(self, store):
        store.add_version(PROJECT, "SH010")
        store.add_version(PROJECT, "SH010")

        names = [v.version_name for v in store.list_for_shot(PROJECT, "SH010")]
        assert names == ["v002", "v001"]

    def test_latest_for_shot(self, store):
        store.add_version(PROJECT, "SH010")
        store.add_version(PROJECT, "SH010")

        assert store.latest_for_shot(PROJECT, "SH010").version_name == "v002"

    def test_latest_is_none_when_there_are_no_versions(self, store):
        assert store.latest_for_shot(PROJECT, "SH999") is None

    def test_versions_are_scoped_to_their_shot(self, store):
        store.add_version(PROJECT, "SH010")
        store.add_version(PROJECT, "SH020")

        assert len(store.list_for_shot(PROJECT, "SH010")) == 1
        assert len(store.list_for_shot(PROJECT, "SH020")) == 1

    def test_a_shot_with_no_project_is_rejected(self, store):
        assert store.add_version("", "SH010") is None
        assert store.add_version(PROJECT, "") is None


class TestSubmissionHistory:
    """The question this feature exists to answer: what did we send, and when?"""

    def test_marking_a_version_sent_stamps_the_date(self, store):
        version = store.add_version(PROJECT, "SH010")
        assert store.update_version(version.id, sent_to=SENT_CLIENT) is True

        sent = store.list_for_shot(PROJECT, "SH010")[0]
        assert sent.sent_to == SENT_CLIENT
        assert sent.sent_date == date.today().isoformat()

    def test_an_explicit_send_date_is_kept(self, store):
        version = store.add_version(PROJECT, "SH010")
        store.update_version(version.id, sent_to=SENT_CLIENT,
                             sent_date="2026-09-01")

        assert store.list_for_shot(PROJECT, "SH010")[0].sent_date == "2026-09-01"

    def test_full_submission_cycle_is_preserved(self, store):
        """v001 sent, rejected with notes; v002 sent and approved."""
        v1 = store.add_version(PROJECT, "SH010", artist="Rahul")
        store.update_version(v1.id, sent_to=SENT_CLIENT, sent_date="2026-09-01")
        store.add_note(v1.id, "Too green in the sky", source=SENT_CLIENT,
                       author="Client", note_date="2026-09-03")
        store.update_version(v1.id, status=STATUS_RETAKE)

        v2 = store.add_version(PROJECT, "SH010", artist="Rahul")
        store.update_version(v2.id, sent_to=SENT_CLIENT, sent_date="2026-09-08",
                             status=STATUS_APPROVED)

        history = store.list_for_shot(PROJECT, "SH010")
        assert [v.version_name for v in history] == ["v002", "v001"]

        latest, first = history
        assert latest.status == STATUS_APPROVED
        assert latest.sent_date == "2026-09-08"

        assert first.status == STATUS_RETAKE
        assert first.sent_date == "2026-09-01"
        assert len(first.notes) == 1
        assert first.notes[0].text == "Too green in the sky"

    def test_update_rejects_unknown_fields(self, store):
        version = store.add_version(PROJECT, "SH010")
        assert store.update_version(version.id, shot_name="HACKED") is False
        assert store.list_for_shot(PROJECT, "SH010")[0].shot_name == "SH010"


class TestNotes:

    def test_notes_attach_to_the_version_they_were_given_on(self, store):
        v1 = store.add_version(PROJECT, "SH010")
        v2 = store.add_version(PROJECT, "SH010")

        store.add_note(v1.id, "fix the edge", source=SENT_INTERNAL)
        store.add_note(v2.id, "looks good", source=SENT_CLIENT)

        versions = {v.version_name: v for v in store.list_for_shot(PROJECT, "SH010")}
        assert [n.text for n in versions["v001"].notes] == ["fix the edge"]
        assert [n.text for n in versions["v002"].notes] == ["looks good"]

    def test_notes_are_ordered_by_date(self, store):
        version = store.add_version(PROJECT, "SH010")
        store.add_note(version.id, "second", note_date="2026-09-05")
        store.add_note(version.id, "first", note_date="2026-09-01")

        notes = store.notes_for_version(version.id)
        assert [n.text for n in notes] == ["first", "second"]

    def test_an_empty_note_is_rejected(self, store):
        version = store.add_version(PROJECT, "SH010")
        assert store.add_note(version.id, "   ") is False
        assert store.notes_for_version(version.id) == []

    def test_a_note_without_a_date_gets_today(self, store):
        version = store.add_version(PROJECT, "SH010")
        store.add_note(version.id, "note")
        assert store.notes_for_version(version.id)[0].note_date == \
            date.today().isoformat()


class TestReviewQueue:
    """What still needs looking at."""

    def test_pending_versions_are_listed(self, store):
        pending = store.add_version(PROJECT, "SH010")
        store.update_version(pending.id, sent_to=SENT_CLIENT,
                             sent_date="2026-09-01")

        done = store.add_version(PROJECT, "SH020")
        store.update_version(done.id, status=STATUS_APPROVED)

        queue = store.awaiting_review(PROJECT)
        assert [v.shot_name for v in queue] == ["SH010"]

    def test_queue_is_oldest_submission_first(self, store):
        newer = store.add_version(PROJECT, "SH010")
        store.update_version(newer.id, sent_date="2026-09-08")
        older = store.add_version(PROJECT, "SH020")
        store.update_version(older.id, sent_date="2026-09-01")

        queue = store.awaiting_review(PROJECT)
        assert [v.shot_name for v in queue] == ["SH020", "SH010"]

    def test_approved_and_retake_are_not_awaiting_review(self, store):
        a = store.add_version(PROJECT, "SH010")
        store.update_version(a.id, status=STATUS_APPROVED)
        b = store.add_version(PROJECT, "SH020")
        store.update_version(b.id, status=STATUS_RETAKE)

        assert store.awaiting_review(PROJECT) == []


class TestDeletion:

    def test_deleting_a_version_takes_its_notes_with_it(self, store):
        version = store.add_version(PROJECT, "SH010")
        store.add_note(version.id, "some feedback")

        assert store.delete_version(version.id) is True
        assert store.list_for_shot(PROJECT, "SH010") == []
        assert store.notes_for_version(version.id) == []


class TestResilience:

    def test_a_database_failure_never_raises(self):
        class DeadDB:
            def __getattr__(self, name):
                def boom(*a, **k):
                    raise RuntimeError("database unavailable")
                return boom

        store = VersionStore(db=DeadDB())
        assert store.list_for_shot(PROJECT, "SH010") == []
        assert store.add_version(PROJECT, "SH010") is None
        assert store.awaiting_review(PROJECT) == []
        assert store.notes_for_version(1) == []


class TestVersionsUI:
    """The panel and the review queue are how versions are actually used."""

    def test_panel_lists_a_shots_versions(self, qtbot, mock_db):
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.versions_panel import VersionsPanel

        store = VersionStore(db=mock_db)
        store.add_version(PROJECT, "SH010", artist="Rahul")
        store.add_version(PROJECT, "SH010", artist="Rahul")

        panel = VersionsPanel(project_code=PROJECT, shot_name="SH010",
                              store=store, can_edit=True)
        qtbot.addWidget(panel)
        panel.refresh()

        assert panel.table.rowCount() == 2
        assert panel.table.item(0, 0).text() == "v002"   # newest first

    def test_panel_does_not_touch_the_database_until_shown(self, qtbot):
        """Building a shot detail panel must never block on a query."""
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.versions_panel import VersionsPanel

        class ExplodingStore:
            def list_for_shot(self, *a, **k):
                raise AssertionError("queried during construction")

        panel = VersionsPanel(project_code=PROJECT, shot_name="SH010",
                              store=ExplodingStore())
        qtbot.addWidget(panel)   # constructed without a single query

    def test_panel_is_read_only_for_an_artist(self, qtbot, mock_db):
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.versions_panel import VersionsPanel

        panel = VersionsPanel(project_code=PROJECT, shot_name="SH010",
                              store=VersionStore(db=mock_db), can_edit=False)
        qtbot.addWidget(panel)

        assert panel.add_btn.isEnabled() is False
        assert panel.note_btn.isEnabled() is False

    def test_review_queue_shows_pending_versions(self, qtbot, mock_db):
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.review_queue_dialog import (
            ReviewQueueDialog,
        )

        store = VersionStore(db=mock_db)
        pending = store.add_version(PROJECT, "SH010")
        store.update_version(pending.id, sent_to=SENT_CLIENT,
                             sent_date="2026-09-01")
        done = store.add_version(PROJECT, "SH020")
        store.update_version(done.id, status=STATUS_APPROVED)

        dialog = ReviewQueueDialog(PROJECT, store=store)
        qtbot.addWidget(dialog)

        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 0).text() == "SH010"

    def test_review_queue_handles_an_empty_queue(self, qtbot, mock_db):
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.review_queue_dialog import (
            ReviewQueueDialog,
        )

        dialog = ReviewQueueDialog(PROJECT, store=VersionStore(db=mock_db))
        qtbot.addWidget(dialog)

        assert dialog.table.rowCount() == 0
        assert "Nothing" in dialog.heading.text()

    def test_shot_detail_shows_the_versions_section(self, qtbot, mock_db):
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.shot_detail import ShotDetailWidget
        from ut_vfx.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot

        panel = ShotDetailWidget(Shot(shot_name="SH010"), user_role="supervisor",
                                 current_project_code=PROJECT,
                                 all_users=["Rahul"])
        qtbot.addWidget(panel)

        assert hasattr(panel, "versions_group")
        assert panel.versions_group.shot_name == "SH010"
        assert panel.versions_group.project_code == PROJECT
