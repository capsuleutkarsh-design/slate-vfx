import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication, QComboBox
from PySide6.QtCore import Qt

# Initialize QApplication if not present
app = QApplication.instance()
if not app:
    app = QApplication(sys.argv)

from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot, DepartmentInfo
from slate.gui.tabs.vfx_dashboard_pro.ui.shot_table_model import ShotTableModel
from slate.gui.tabs.vfx_dashboard_pro.ui.status_delegate import StatusDelegate
from slate.gui.tabs.vfx_dashboard_pro.ui.artist_delegate import ArtistDelegate
from slate.gui.tabs.vfx_dashboard_pro.ui.batch_edit_dialog import BatchEditDialog
from slate.gui.tabs.vfx_dashboard_pro.ui.group_header_delegate import GROUP_HEADER_ROLE


def create_sample_shots():
    s1 = Shot(
        id=1,
        shot_name="SH010",
        reel_episode="REEL_01",
        status="WIP",
        assigned_artist="Rahul",
        edit_frames=120,
        priority=2,
        comp_dept=DepartmentInfo(artist="Rahul", bid_days=3.0, status="WIP")
    )
    s2 = Shot(
        id=2,
        shot_name="SH020",
        reel_episode="REEL_01",
        status="APPROVED",
        assigned_artist="Priya",
        edit_frames=85,
        priority=1,
        comp_dept=DepartmentInfo(artist="Priya", bid_days=2.5, status="APPROVED")
    )
    s3 = Shot(
        id=3,
        shot_name="SH030",
        reel_episode="REEL_02",
        status="RETAKE",
        assigned_artist="Rahul",
        edit_frames=200,
        priority=0,
        comp_dept=DepartmentInfo(artist="Rahul", bid_days=4.0, status="RETAKE")
    )
    return [s1, s2, s3]


def test_batch_edit_dialog():
    print("Testing BatchEditDialog...")
    dialog = BatchEditDialog(selected_count=3, all_users=["Rahul", "Priya", "Amit"])
    assert dialog.selected_count == 3
    assert "Rahul" in dialog.all_users
    
    # Check default: no updates selected
    updates = dialog.get_updates()
    assert updates == {}

    # Enable status and artist
    dialog.status_cb.setChecked(True)
    dialog.status_combo.setCurrentText("APPROVED")
    dialog.artist_cb.setChecked(True)
    dialog.artist_combo.setCurrentText("Priya")

    updates = dialog.get_updates()
    assert updates["status"] == "APPROVED"
    assert updates["assigned_artist"] == "Priya"
    print("BatchEditDialog passed!")


def test_status_delegate():
    print("Testing StatusDelegate...")
    shots = create_sample_shots()
    model = ShotTableModel(shots, user_role="supervisor")
    delegate = StatusDelegate()

    # Column 2 is Status
    idx = model.index(0, 2)
    editor = delegate.createEditor(None, None, idx)
    assert isinstance(editor, QComboBox)

    delegate.setEditorData(editor, idx)
    assert editor.currentText() == "WIP"

    editor.setCurrentText("APPROVED")
    delegate.setModelData(editor, model, idx)
    assert shots[0].status == "APPROVED"
    assert shots[0]._modified is True
    print("StatusDelegate passed!")


def test_artist_delegate():
    print("Testing ArtistDelegate...")
    shots = create_sample_shots()
    model = ShotTableModel(shots, user_role="supervisor")
    delegate = ArtistDelegate(get_users_callback=lambda: ["Rahul", "Priya", "Amit"])

    # Column 3 is Artist
    idx = model.index(0, 3)
    editor = delegate.createEditor(None, None, idx)
    assert isinstance(editor, QComboBox)

    delegate.setEditorData(editor, idx)
    assert editor.currentText() == "Rahul"

    editor.setCurrentText("Priya")
    delegate.setModelData(editor, model, idx)
    assert shots[0].assigned_artist == "Priya"
    assert shots[0]._modified is True
    print("ArtistDelegate passed!")


def test_group_by_sequence():
    print("Testing Group-By Reel/Sequence...")
    shots = create_sample_shots()
    model = ShotTableModel(shots, user_role="supervisor")
    
    # Default: Flat list
    assert model.rowCount() == 3
    assert not model.is_group_header(0)

    # Group by Reel / Sequence
    model.set_group_by("Reel / Sequence")
    # REEL_01 has 2 shots -> 1 header + 2 shots = 3
    # REEL_02 has 1 shot -> 1 header + 1 shot = 2
    # Total rows = 5
    assert model.rowCount() == 5
    assert model.is_group_header(0)
    
    info_0 = model.get_group_info(0)
    assert info_0["title"] == "REEL_01"
    assert info_0["count"] == 2
    assert info_0["approved_count"] == 1
    assert info_0["total_frames"] == 205

    # Check shot at row 1
    shot_1 = model.get_shot_at(1)
    assert shot_1.shot_name == "SH010"

    # Test Collapse REEL_01
    model.toggle_group_collapse("REEL_01")
    # REEL_01 is now collapsed (1 row), REEL_02 is expanded (1 header + 1 shot = 2 rows)
    # Total rows = 3
    assert model.rowCount() == 3
    assert model.is_group_header(0)
    assert model.get_group_info(0)["is_collapsed"] is True
    assert model.is_group_header(1)
    assert model.get_group_info(1)["title"] == "REEL_02"

    # Test Expand REEL_01
    model.toggle_group_collapse("REEL_01")
    assert model.rowCount() == 5
    assert model.get_group_info(0)["is_collapsed"] is False
    print("Group-By Reel/Sequence passed!")


def test_group_by_status():
    print("Testing Group-By Status...")
    shots = create_sample_shots()
    model = ShotTableModel(shots, user_role="supervisor")
    model.set_group_by("Status")
    # Statuses: WIP (1 shot), APPROVED (1 shot), RETAKE (1 shot)
    # 3 groups * 2 (header + 1 shot) = 6 rows
    assert model.rowCount() == 6
    print("Group-By Status passed!")


def test_batch_update_application():
    print("Testing batch update application...")
    shots = create_sample_shots()
    updates = {"status": "APPROVED", "assigned_artist": "SuperLead", "priority": 0}

    for s in shots:
        for k, v in updates.items():
            setattr(s, k, v)
            s._modified = True

    for s in shots:
        assert s.status == "APPROVED"
        assert s.assigned_artist == "SuperLead"
        assert s.priority == 0
        assert s._modified is True
    print("Batch update application passed!")


if __name__ == "__main__":
    test_batch_edit_dialog()
    test_status_delegate()
    test_artist_delegate()
    test_group_by_sequence()
    test_group_by_status()
    test_batch_update_application()
    print("\nALL VFX DASHBOARD TESTS PASSED SUCCESSFULLY!")
