"""
The dashboard's Excel passbook (ExcelHandler): written by the software, copied
as a backup, opened with the real openpyxl, then read back the way an
Excel-only project is opened. Every field has to survive - unicode, dates,
numbers, empty cells, and text that looks like a formula, which must stay text.
"""

from datetime import date, datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.realtools
openpyxl = pytest.importorskip("openpyxl")

from openpyxl.utils import get_column_letter  # noqa: E402

from slate.gui.tabs.vfx_dashboard_pro.core.excel_handler import ExcelHandler  # noqa: E402
from slate.gui.tabs.vfx_dashboard_pro.core.project_manager import (  # noqa: E402
    ProjectConfig, default_column_mapping,
)
from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import FeedbackEntry, Shot  # noqa: E402

EXTRA = ("notes", "description", "target", "prev_version", "edit_status", "in_os",
         "exr_date", "mov_date", "comp_actual")


@pytest.fixture(autouse=True)
def offline_settings(monkeypatch):
    """Studio settings at their defaults: the priority names are read from the database."""
    import copy
    from slate.core.infra import studio_settings
    monkeypatch.setattr(studio_settings, "get_setting",
                        lambda key, default=None, db=None:
                        copy.deepcopy(studio_settings.DEFAULTS.get(key, default)))


def project(path: Path) -> ProjectConfig:
    mapping = default_column_mapping()
    last = max(openpyxl.utils.column_index_from_string(c) for c in mapping.values())
    for offset, key in enumerate(EXTRA, start=1):
        mapping.setdefault(key, get_column_letter(last + offset))
    return ProjectConfig(code="PRJ", name="Passbook é", excel_path=str(path),
                         header_row=2, data_start_row=3, column_mapping=mapping)


def shots():
    busy = Shot(shot_name="SH010", reel_episode="R01 – ünï", status="WIP", edit_frames=48,
                sow="=1+1", description='Line one\nLine two, "quoted"; ₹ 5,000',
                notes="@SUM(A1:A9)", assigned_artist="राहुल", target="2026-10-05",
                wip_date="2026-09-01", submission_date="2026-10-01", priority=1,
                shot_type="-5", curr_version="v003", prev_version="v002",
                scan_status="+cmd|' /C calc'!A0", edit_status="'quoted already",
                in_os="Yes 🎬")
    busy.dept("comp").artist = "Zoë"
    busy.dept("comp").status = "APPROVED"
    busy.dept("comp").bid_days = 2.5
    busy.dept("comp").eta = "2026-10-10"
    busy.dept("comp").actual_days = 3.0
    busy.dept("roto").status = "=HYPERLINK(\"http://x\",\"y\")"
    busy.dept("roto").bid_days = 1.25
    busy.feedback_client.append(FeedbackEntry(date="2026-09-30", source="Client",
                                              text="-fix the edge\nand the grain", logged_by="Ana"))
    busy.feedback_internal.append(FeedbackEntry(date="2026-09-29", source="Internal",
                                                text="Looks good 👍", logged_by="Raj"))
    empty = Shot(shot_name="SH020", reel_episode="R01 – ünï")
    return [busy, empty]


def test_passbook_backup_opens_in_openpyxl_and_restores_every_field(tmp_path):
    path = tmp_path / "Tracking" / "PRJ_tracking.xlsx"
    config = project(path)
    handler = ExcelHandler(str(path), config)
    assert handler.create_workbook()
    original = shots()
    assert handler.write_shots(original)
    assert not handler.skipped_cells

    backup = Path(ExcelHandler(str(path), config).create_backup())
    assert backup.is_file() and backup.parent == path.parent / "backups"

    # --- the backup as Excel sees it
    book = openpyxl.load_workbook(backup)
    for ws in book.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                assert cell.data_type != "f", f"{ws.title}!{cell.coordinate} is a formula: {cell.value!r}"
    ws = book["MASTER"]
    col = {key: openpyxl.utils.column_index_from_string(letter)
           for key, letter in config.column_mapping.items()}
    headers = {ws.cell(row=2, column=c).value for c in col.values()}
    assert "SHOT NAME" in headers and "NOTES" in headers
    row = {r[col["shot_name"] - 1].value: r for r in ws.iter_rows(min_row=3)}
    sh010 = row["SH010"]
    assert sh010[col["frames"] - 1].value == 48
    assert sh010[col["sow"] - 1].value == "'=1+1"
    assert isinstance(sh010[col["target"] - 1].value, datetime)
    assert sh010[col["target"] - 1].value.date() == date(2026, 10, 5)
    assert sh010[col["comp_bid"] - 1].value == 2.5
    assert sh010[col["assigned_artist"] - 1].value == "राहुल"
    assert book["FEEDBACK_LOG"].max_row == 3
    assert book["Slate_DATA"].sheet_state == "hidden"

    # --- restored the way an Excel-only project is opened
    reader = ExcelHandler(str(backup), config)
    back = {s.shot_name: s for s in reader.read_shots()}
    assert not reader.read_problems
    assert set(back) == {"SH010", "SH020"}

    fields = ("reel_episode", "status", "edit_frames", "sow", "description", "notes",
              "assigned_artist", "target", "wip_date", "submission_date", "priority",
              "shot_type", "curr_version", "prev_version", "scan_status", "edit_status", "in_os")
    for want in original:
        got = back[want.shot_name]
        for name in fields:
            assert (getattr(got, name) or "") == (getattr(want, name) or ""), \
                f"{want.shot_name}.{name}: wrote {getattr(want, name)!r}, read {getattr(got, name)!r}"
        for key in ("comp", "roto"):
            w, g = want.dept(key), got.dept(key)
            assert (g.artist, g.status, g.bid_days) == (w.artist, w.status, w.bid_days), key
        assert (got.dept("comp").eta or "") == (want.dept("comp").eta or "")
        assert got.dept("comp").actual_days == want.dept("comp").actual_days
        assert [(e.date, e.text, e.logged_by) for e in got.feedback_client] == \
            [(e.date, e.text, e.logged_by) for e in want.feedback_client]
        assert [e.text for e in got.feedback_internal] == [e.text for e in want.feedback_internal]
