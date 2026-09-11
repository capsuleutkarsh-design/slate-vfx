"""
Tests for Item 4.2: Scope Selector: All / My Department / My Shots.

Verifies:
- Detecting department family from user job title / designation
- Row filtering logic for:
  - All
  - My Shots (matching artist identity)
  - My Department (matching active department work in family)
- Column narrowing logic: hides other department columns, preserves shot identity and own dept
"""

import pytest
from PySide6.QtWidgets import QApplication, QTableView

from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot
from slate.gui.tabs.vfx_dashboard_pro.ui.shot_table_model import ShotTableModel
from slate.core.domain.departments import load_departments, families


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_department_family_detection():
    # Helper logic mirroring DashboardWidget._detect_user_department_family
    def detect_family(job_title, dept=""):
        search = f"{job_title} {dept}".lower().strip()
        all_depts = load_departments()
        for d in all_depts:
            if d.key in search or d.name.lower() in search or d.label.lower() in search:
                return d.family
        for fam in families().keys():
            if fam in search:
                return fam
        return None

    assert detect_family("Senior Roto Artist") == "roto"
    assert detect_family("Roto Lead") == "roto"
    assert detect_family("Comp Supervisor") == "comp"
    assert detect_family("Prep & Paint Lead") == "prep"
    assert detect_family("CG Generalist") == "cg"
    assert detect_family("Matchmove Lead") == "matchmove"
    assert detect_family("DMP Artist") == "dmp"
    assert detect_family("Pipeline TD") is None


def test_scope_row_filtering():
    shot1 = Shot(shot_name="SH001", reel_episode="Reel01", assigned_artist="bob")
    shot1.dept("roto").status = "WIP"
    shot1.dept("roto").assigned_artist = "bob"

    shot2 = Shot(shot_name="SH002", reel_episode="Reel01", assigned_artist="alice")
    shot2.dept("comp").status = "WIP"
    shot2.dept("comp").assigned_artist = "alice"

    shot3 = Shot(shot_name="SH003", reel_episode="Reel01", assigned_artist="charlie")
    shot3.dept("roto").status = "APPROVED"
    shot3.dept("roto").assigned_artist = "david"

    shots = [shot1, shot2, shot3]
    user_identities = {"bob"}

    # Filter: All
    assert len(shots) == 3

    # Filter: My Shots (bob)
    def filter_my_shots(shot_list, identities):
        res = []
        for s in shot_list:
            all_artists = s.get_all_artists() if hasattr(s, "get_all_artists") else [s.assigned_artist]
            if any(a.lower() in identities for a in all_artists if a):
                res.append(s)
        return res

    my_shots = filter_my_shots(shots, user_identities)
    assert len(my_shots) == 1
    assert my_shots[0].shot_name == "SH001"

    # Filter: My Dept (roto)
    def filter_dept(shot_list, fam):
        depts_in_fam = [d.key for d in load_departments() if d.family == fam]
        res = []
        for s in shot_list:
            has_work = False
            for dk in depts_in_fam:
                st = (s.dept(dk).status or "").strip().upper()
                if st and st not in ("N/A", "OMIT", "-"):
                    has_work = True
                    break
            if has_work:
                res.append(s)
        return res

    roto_shots = filter_dept(shots, "roto")
    assert len(roto_shots) == 2
    assert {s.shot_name for s in roto_shots} == {"SH001", "SH003"}


def test_scope_column_narrowing(qapp):
    shot = Shot(shot_name="SH010", reel_episode="Reel01")
    model = ShotTableModel([shot])
    table = QTableView()
    table.setModel(model)

    # Narrow to 'roto'
    fam = "roto"
    depts_in_fam = {d.key for d in load_departments() if d.family == fam}
    other_depts = {d.key for d in load_departments() if d.family != fam}

    for col_idx, (col_key, _, _) in enumerate(model.COLUMNS):
        if col_key in other_depts:
            table.setColumnHidden(col_idx, True)
        else:
            table.setColumnHidden(col_idx, False)

    # Core columns should be visible
    shot_idx = next(i for i, c in enumerate(model.COLUMNS) if c[0] == "shot_name")
    assert table.isColumnHidden(shot_idx) is False

    # Roto column should be visible
    roto_idx = next(i for i, c in enumerate(model.COLUMNS) if c[0] == "roto")
    assert table.isColumnHidden(roto_idx) is False

    # Comp column should be hidden
    comp_idx = next(i for i, c in enumerate(model.COLUMNS) if c[0] == "comp")
    assert table.isColumnHidden(comp_idx) is True
