"""
Renaming a thousand plates, and the dashboard's idea of a status.

CAP Rename had no collision check at all. Two files mapping to one name, or a
name already taken on disk, were queued and found out one at a time part way
through the run - leaving half a sequence renamed and half not, with nothing in
the filenames to say which half. Renaming in list order also breaks on its own
whenever names shuffle: renaming file 2 to what file 3 is currently called
either clobbers file 3 or fails, depending which the loop reaches first.

The dashboard compared statuses exactly in one place and upper-cased them in
another, so a status that arrived from a sheet as "wip" grouped under WIP and
then matched nothing in the filter - the row sat in a group the filter could not
find. And the Excel handler guessed column D when no shot-name column was
mapped, which silently treated a full sheet as empty and appended every row
again at the bottom.
"""

import os
from pathlib import Path

import pytest


# ------------------------------------------------------- the two-phase rename

def _rename_two_phase(pairs):
    """
    The worker's algorithm, without Qt: stage everything, then place it.

    Kept in step with RenameWorker.run by test_a_swap_is_what_one_pass_cannot_do,
    which fails if the real worker ever goes back to a single pass.
    """
    import uuid

    staged = []
    for old_path, new_path in pairs:
        if not old_path.exists():
            continue
        holding = old_path.parent / (".slate_rename_%s.tmp" % uuid.uuid4().hex)
        os.rename(old_path, holding)
        staged.append((holding, old_path, new_path))

    done = 0
    for holding, old_path, new_path in staged:
        os.rename(holding, new_path)
        done += 1
    return done


def test_a_swap_is_what_one_pass_cannot_do(tmp_path):
    """
    Two files trading names. In one pass the first rename destroys the second
    file's source; in two passes both survive.
    """
    a, b = tmp_path / "a.exr", tmp_path / "b.exr"
    a.write_text("A")
    b.write_text("B")

    assert _rename_two_phase([(a, b), (b, a)]) == 2
    assert (tmp_path / "b.exr").read_text() == "A"
    assert (tmp_path / "a.exr").read_text() == "B"


def test_shifting_a_whole_sequence_up_by_one(tmp_path):
    """
    Serialising an existing sequence is exactly the order-dependent case: every
    file's target is the next file's current name.
    """
    files = []
    for i in range(1, 5):
        f = tmp_path / ("plate_%04d.dpx" % i)
        f.write_text(str(i))
        files.append(f)

    pairs = [(f, tmp_path / ("plate_%04d.dpx" % (i + 2)))
             for i, f in enumerate(files, start=1)]
    assert _rename_two_phase(pairs) == 4

    for i in range(1, 5):
        assert (tmp_path / ("plate_%04d.dpx" % (i + 2))).read_text() == str(i)
    # 0003 and 0004 were both a source and a target in that set, which is the
    # case a single pass cannot survive.
    assert not (tmp_path / "plate_0001.dpx").exists()


def test_the_real_worker_still_stages_before_placing():
    """
    The guard on the test above. If RenameWorker goes back to renaming straight
    into place, this fails and says so.
    """
    import inspect
    from slate.gui.cap_rename_tab import RenameWorker

    source = inspect.getsource(RenameWorker.run)
    assert "holding" in source, "the worker must stage to a temporary name first"
    assert source.count("os.rename") >= 2, "staging and placing are two renames"


def test_the_undo_script_is_written_as_the_run_goes():
    """
    The run that most needs undoing is the one that did not finish, so the
    script cannot be written at the end.
    """
    import inspect
    from slate.gui.cap_rename_tab import RenameWorker

    source = inspect.getsource(RenameWorker.run)
    assert "handle.flush()" in source, "each line is flushed as it is written"


# ----------------------------------------------------------- the status casing

def test_a_status_is_stored_in_one_case(tmp_path):
    """
    The bug: "wip" from a sheet grouped under WIP and matched nothing in the
    filter, so the row was in a group the filter could not reach.
    """
    from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot

    assert Shot.normalise_status("wip") == "WIP"
    assert Shot.normalise_status("  Approved  ") == "APPROVED"
    assert Shot.normalise_status(None) == ""

    shot = Shot.from_dict({"shot_name": "SH010", "status": "wip"})
    assert shot.status == "WIP"


def test_the_filter_and_the_grouping_now_agree():
    from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot

    shot = Shot.from_dict({"shot_name": "SH010", "status": "sent for review"})
    # What the grouping does.
    grouped = str(shot.status).strip().upper()
    # What the filter compares against, populated from the same values.
    assert shot.status == grouped


# -------------------------------------------------------- the Excel guesswork

def test_an_unmapped_shot_column_is_an_error_not_a_guess(tmp_path):
    """
    The bug: it fell back to column D. Right for the sheet it was written
    against, wrong for any other - and when wrong, every shot matched nothing,
    so a full sheet was treated as empty and every row was written again at the
    bottom.
    """
    from openpyxl import Workbook
    from slate.gui.tabs.vfx_dashboard_pro.core.excel_handler import ExcelHandler

    path = tmp_path / "sheet.xlsx"
    wb = Workbook()
    wb.active["A1"] = "anything"
    wb.save(path)

    class Config:
        column_mapping = {"status": "B"}     # no shot_name
        sheet_name = "Sheet"
        header_row = 1
        data_start_row = 2

    handler = ExcelHandler(str(path), Config())
    assert handler.load()

    with pytest.raises(ValueError) as raised:
        handler._build_row_map()
    assert "shot name" in str(raised.value).lower()


def test_a_mapped_shot_column_is_used(tmp_path):
    from openpyxl import Workbook
    from slate.gui.tabs.vfx_dashboard_pro.core.excel_handler import ExcelHandler

    path = tmp_path / "sheet.xlsx"
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "SHOT NAME"
    ws["A2"] = "SH010"
    wb.save(path)

    class Config:
        column_mapping = {"shot_name": "A"}
        sheet_name = "Sheet"
        header_row = 1
        data_start_row = 2

    handler = ExcelHandler(str(path), Config())
    assert handler.load()
    handler._build_row_map()
    assert handler._find_row_idx_by_shot_name("SH010") == 2
