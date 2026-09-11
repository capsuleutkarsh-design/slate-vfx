"""End-to-end verification of Phase 4 against real objects, not fakes."""
import pytest
from ut_vfx.core.domain.deliveries import DeliveryStore
from ut_vfx.core.domain.versions import VersionStore


class TestDeliveriesAgainstARealDatabase:
    def test_full_cycle(self, mock_db):
        vs = VersionStore(db=mock_db)
        v1 = vs.add_version("PRJ", "SH010", artist="Rahul", department="comp")
        v2 = vs.add_version("PRJ", "SH020", artist="Priya", department="roto")

        ds = DeliveryStore(db=mock_db)
        d = ds.create_delivery("PRJ", "Delivery 07", [v1.id, v2.id],
                               recipient="Client", notes="first batch")
        assert d is not None
        assert len(d.items) == 2
        assert {i.shot_name for i in d.items} == {"SH010", "SH020"}

        assert [x.name for x in ds.list_deliveries("PRJ")] == ["Delivery 07"]

        note = ds.generate_delivery_note(d.id)
        assert "SH010" in note and "Delivery 07" in note and "Client" in note

    def test_deleting_a_delivery_keeps_the_versions(self, mock_db):
        vs = VersionStore(db=mock_db)
        v1 = vs.add_version("PRJ", "SH010")
        ds = DeliveryStore(db=mock_db)
        d = ds.create_delivery("PRJ", "D1", [v1.id])

        assert ds.delete_delivery(d.id) is True
        assert ds.get_delivery(d.id) is None
        assert len(vs.list_for_shot("PRJ", "SH010")) == 1, "the version was deleted with the batch"

    def test_status_at_delivery_is_captured(self, mock_db):
        vs = VersionStore(db=mock_db)
        v1 = vs.add_version("PRJ", "SH010")
        vs.update_version(v1.id, status="Approved")

        ds = DeliveryStore(db=mock_db)
        d = ds.create_delivery("PRJ", "D1", [v1.id])
        assert d.items[0].status_at_delivery == "Approved"

        vs.update_version(v1.id, status="Retake")
        again = ds.get_delivery(d.id)
        assert again.items[0].status_at_delivery == "Approved", \
            "what was sent should not change when the version moves on"

    def test_a_delivery_with_no_versions_is_refused(self, mock_db):
        assert DeliveryStore(db=mock_db).create_delivery("PRJ", "Empty", []) is None

    def test_an_unnamed_delivery_is_refused(self, mock_db):
        vs = VersionStore(db=mock_db)
        v1 = vs.add_version("PRJ", "SH010")
        assert DeliveryStore(db=mock_db).create_delivery("PRJ", "", [v1.id]) is None


class TestColumnLayoutsAgainstARealDatabase:
    def test_a_published_default_does_not_destroy_project_config(self, qtbot, mock_db):
        from PySide6.QtWidgets import QTableView
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.components.column_layout_manager import (
            ColumnLayoutManager,
        )
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.shot_table_model import ShotTableModel
        import json

        mock_db.save_tracking_project("PRJ", "Test Project", json.dumps({
            "code": "PRJ", "name": "Test Project",
            "folder_template": {"comp": "05_Reels/{reel}/{shot}/07_Comp"},
            "column_mapping": {"shot_name": "D"},
        }))

        view = QTableView()
        qtbot.addWidget(view)
        model = ShotTableModel(user_role="supervisor")
        view.setModel(model)

        mgr = ColumnLayoutManager(view, model=model, user_id="sup",
                                  project_code="PRJ", db_manager=mock_db)
        assert mgr.save_project_default() is True

        config = mock_db.get_tracking_project("PRJ")
        assert "default_columns" in config
        assert config["folder_template"] == {"comp": "05_Reels/{reel}/{shot}/07_Comp"}, \
            "publishing a layout destroyed the project's folder template"
        assert config["column_mapping"] == {"shot_name": "D"}


class TestReviewPlayerBuilds:
    def test_the_player_dialog_opens_for_a_version(self, qtbot, mock_db):
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.review_player_dialog import (
            ReviewPlayerDialog,
        )
        store = VersionStore(db=mock_db)
        v = store.add_version("PRJ", "SH010", artist="Rahul")

        dlg = ReviewPlayerDialog(version=v, queue_versions=[v], current_index=0,
                                 store=store)
        qtbot.addWidget(dlg)
        assert "SH010" in dlg.title_label.text()

    def test_a_missing_media_path_is_reported_not_crashed_on(self, qtbot, mock_db):
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.review_player_dialog import (
            ReviewPlayerDialog,
        )
        store = VersionStore(db=mock_db)
        v = store.add_version("PRJ", "SH010", media_path="/nowhere/missing.mov")

        dlg = ReviewPlayerDialog(version=v, queue_versions=[v], current_index=0,
                                 store=store)
        qtbot.addWidget(dlg)
        assert "not found" in dlg.media_path_label.text().lower()

    def test_a_verdict_is_written_to_the_version(self, qtbot, mock_db):
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.review_player_dialog import (
            ReviewPlayerDialog,
        )
        store = VersionStore(db=mock_db)
        v = store.add_version("PRJ", "SH010")

        dlg = ReviewPlayerDialog(version=v, queue_versions=[v], current_index=0,
                                 store=store)
        qtbot.addWidget(dlg)
        dlg._submit_verdict("Approved", "looks good")

        stored = store.list_for_shot("PRJ", "SH010")[0]
        assert stored.status == "Approved"
        assert any("looks good" in n.text for n in stored.notes)


class TestDeliveryEdgeCases:
    """Things a real production week will do to this."""

    def test_attaching_a_version_that_does_not_exist(self, mock_db):
        """A stale selection should not create a delivery full of blanks."""
        ds = DeliveryStore(db=mock_db)
        d = ds.create_delivery("PRJ", "D1", [999999])

        if d is not None:
            blanks = [i for i in d.items if not i.shot_name]
            assert not blanks, (
                "a delivery was created referencing a version that does not "
                "exist; the note would list a blank row"
            )

    def test_two_deliveries_can_share_a_name(self, mock_db):
        """If names repeat, the second must not be mistaken for the first."""
        vs = VersionStore(db=mock_db)
        v1 = vs.add_version("PRJ", "SH010")
        v2 = vs.add_version("PRJ", "SH020")

        ds = DeliveryStore(db=mock_db)
        first = ds.create_delivery("PRJ", "Delivery 07", [v1.id])
        second = ds.create_delivery("PRJ", "Delivery 07", [v2.id])

        assert first is not None and second is not None
        assert first.id != second.id, "the second delivery reused the first's id"
        assert [i.shot_name for i in second.items] == ["SH020"]
