"""
CAP Rename's rules, run for real (ING-080 ... ING-123).

The old tests read the worker's source text, so a worker that crashed on its
first file shipped with every test green. These run the renames.
"""

import json
from pathlib import Path

import pytest

from slate.core.domain import batch_rename as br
from slate.core.domain.naming import name_problem, normalise_shot_name


def _files(folder: Path, names, content=None):
    out = []
    for name in names:
        path = folder / name
        path.write_text(content or name, encoding="utf-8")
        out.append(path)
    return out


def _plan(files, **rules):
    return br.plan(files, br.RenameRules(**rules))


# --------------------------------------------------------------- the worker
def test_the_worker_renames_and_reports_success(qapp, tmp_path):
    from slate.gui.cap_rename_tab import RenameWorker

    a = _files(tmp_path, ["a.exr"])[0]
    worker = RenameWorker([(a, tmp_path / "b.exr")], user="priya", journal_dir=tmp_path / "app")
    seen = []
    worker.finished_signal.connect(lambda ok, msg, n: seen.append((ok, msg, n)))
    worker.run()

    assert (tmp_path / "b.exr").read_text(encoding="utf-8") == "a.exr"
    assert not a.exists()
    assert seen == [(True, "Renamed 1 file(s).", 1)]


def test_stopping_before_the_run_renames_nothing_and_leaves_no_journal(qapp, tmp_path):
    from slate.gui.cap_rename_tab import RenameWorker

    a = _files(tmp_path, ["a.exr"])[0]
    worker = RenameWorker([(a, tmp_path / "b.exr")], journal_dir=tmp_path / "app")
    worker.stop()
    worker.run()

    assert a.exists() and not (tmp_path / "b.exr").exists()
    assert br.latest_journal(tmp_path / "app") is None
    assert worker.outcome.cancelled


def test_a_run_where_every_rename_fails_leaves_no_undo_file(qapp, tmp_path):
    from slate.gui.cap_rename_tab import RenameWorker

    gone = tmp_path / "gone.exr"
    worker = RenameWorker([(gone, tmp_path / "x.exr")], journal_dir=tmp_path / "app")
    messages = []
    worker.finished_signal.connect(lambda ok, msg, n: messages.append(msg))
    worker.run()

    assert br.latest_journal(tmp_path / "app") is None
    assert not list(tmp_path.glob("*.bat")) and not list(tmp_path.glob("*.json"))
    # Friendly, not a doubled prefix with a Python exception in it (ING-105).
    assert "Rename failed: Rename failed" not in messages[0]
    assert "Error" not in messages[0] and "object has no attribute" not in messages[0]


def test_stopping_during_the_first_pass_puts_every_file_back(tmp_path):
    files = _files(tmp_path, [f"f_{i}.exr" for i in range(6)])
    pairs = [(f, tmp_path / f"g_{i}.exr") for i, f in enumerate(files)]
    calls = {"n": 0}

    def stop_after_three():
        calls["n"] += 1
        return calls["n"] > 3

    outcome = br.rename_files(pairs, should_stop=stop_after_three)
    assert outcome.cancelled and outcome.count == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(f.name for f in files)


def test_progress_says_how_far_it_got(tmp_path):
    files = _files(tmp_path, ["a.exr", "b.exr"])
    seen = []
    br.rename_files([(f, tmp_path / ("x" + f.name)) for f in files],
                    progress=lambda done, total, name: seen.append((done, total)))
    assert (2, 2) in seen


# --------------------------------------------------------------- undo
def test_undo_restores_a_swap_exactly(tmp_path):
    """The .bat replayed moves one by one and destroyed the first file."""
    app = tmp_path / "app"
    plates = tmp_path / "plates"
    plates.mkdir()
    a = plates / "a_0001.exr"
    b = plates / "a_0002.exr"
    a.write_text("ONE")
    b.write_text("TWO")

    outcome = br.rename_files([(a, b), (b, a)])
    journal = br.write_journal(outcome.renamed, user="priya", app_dir=app)
    assert a.read_text() == "TWO" and b.read_text() == "ONE"

    result = br.undo(journal)
    assert result.ok and result.restored == 2
    assert a.read_text() == "ONE" and b.read_text() == "TWO"
    # Nothing was written next to the plates; the journal is in Slate's folder.
    assert sorted(p.name for p in plates.iterdir()) == ["a_0001.exr", "a_0002.exr"]
    assert journal.parent == br.journal_dir(app)
    assert json.loads(journal.read_text(encoding="utf-8"))["undone"] is True
    assert br.latest_journal(app) is None


def test_undo_copes_with_percent_signs(tmp_path):
    """cmd expanded '%' in the old .bat (ING-108)."""
    f = _files(tmp_path, ["100%_plate.exr"])[0]
    outcome = br.rename_files([(f, tmp_path / "plate.exr")])
    journal = br.write_journal(outcome.renamed, app_dir=tmp_path / "app")
    assert br.undo(journal).ok
    assert (tmp_path / "100%_plate.exr").exists()


def test_undo_refuses_when_the_files_changed_since(tmp_path):
    f = _files(tmp_path, ["a.exr"])[0]
    outcome = br.rename_files([(f, tmp_path / "b.exr")])
    journal = br.write_journal(outcome.renamed, app_dir=tmp_path / "app")
    (tmp_path / "b.exr").rename(tmp_path / "c.exr")

    result = br.undo(journal)
    assert result.refused and "no longer there" in result.refused
    assert (tmp_path / "c.exr").exists()


# --------------------------------------------------------------- the plan
def test_sanitize_keeps_the_extension(tmp_path):
    files = _files(tmp_path, ["IMG_1.JPG", "clip_000 take.1001.dpx"])
    rows = _plan(files, sanitize=True).rows
    assert rows[0].new_name == "IMG_1.JPG" and rows[0].status == br.UNCHANGED
    assert rows[1].new_name == "clip_000_take_1001.dpx"


def test_find_and_replace_leaves_the_extension_by_default(tmp_path):
    f = _files(tmp_path, ["IMG_1.JPG"])
    assert _plan(f, search="jpg", replace="exr").rows[0].status == br.UNCHANGED
    row = _plan(f, search="jpg", replace="exr", keep_extension=False).rows[0]
    assert row.new_name == "IMG_1.exr" and "extension changes" in row.warning


def test_repad_works_with_the_default_options(tmp_path):
    f = _files(tmp_path, ["IMG_1.JPG"])
    assert _plan(f, repad=True, repad_digits=4).rows[0].new_name == "IMG_0001.JPG"


def test_sanitize_keeps_letters_in_any_script(tmp_path):
    files = _files(tmp_path, ["प्लेट 01.exr", "shot 🎬 final.exr"])
    names = [r.new_name for r in _plan(files, sanitize=True).rows]
    assert names == ["प्लेट_01.exr", "shot_final.exr"]


def test_a_name_containing_error_is_just_a_name(tmp_path):
    f = _files(tmp_path, ["IMG_1.JPG", "TERROR_plate.exr"])
    rows = _plan(f, search="IMG", replace="ERROR").rows
    assert rows[0].new_name == "ERROR_1.JPG" and rows[0].status == br.WILL_RENAME
    assert rows[1].status == br.UNCHANGED


def test_a_bad_pattern_is_reported_with_its_reason(tmp_path):
    f = _files(tmp_path, ["IMG_1.JPG"])
    plan = _plan(f, search="(", use_regex=True)
    assert "missing )" in plan.pattern_error
    assert plan.rows[0].status == br.UNCHANGED and not plan.can_run
    plan = _plan(f, search=r"_(\d+)", replace=r"_\2", use_regex=True)
    assert "invalid group reference" in plan.pattern_error


def test_a_folder_in_a_new_name_is_a_conflict(tmp_path):
    f = _files(tmp_path, ["IMG_1.JPG"])
    row = _plan(f, search="IMG_", replace="sub/").rows[0]
    assert row.status == br.CONFLICT and "folder" in row.reason
    row = _plan(f, search="IMG_", replace="..\\").rows[0]
    assert row.status == br.CONFLICT


def test_the_worker_refuses_to_move_a_file_to_another_folder(tmp_path):
    f = _files(tmp_path, ["a.exr"])[0]
    (tmp_path / "sub").mkdir()
    outcome = br.rename_files([(f, tmp_path / "sub" / "a.exr")])
    assert outcome.count == 0 and f.exists()


def test_every_file_in_a_collision_is_a_conflict(tmp_path):
    files = _files(tmp_path, [f"IMG_{i}.JPG" for i in range(1, 13)])
    plan = _plan(files, search=r"IMG_\d+", replace="IMG_X", use_regex=True)
    assert [r.status for r in plan.rows] == [br.CONFLICT] * 12
    assert plan.counts["rename"] == 0 and plan.counts["conflict"] == 12


def test_a_name_that_is_too_long_says_so(tmp_path):
    f = _files(tmp_path, ["a.exr"])
    row = _plan(f, search="a", replace="b" * 212).rows[0]
    assert row.status == br.CONFLICT and "too long" in row.reason and "216" in row.reason


def test_natural_order(tmp_path):
    names = [f"IMG_{i}.JPG" for i in (10, 2, 1, 12, 11, 3)]
    files = br.natural_sorted(_files(tmp_path, names))
    numbered = _plan(files, mode=br.MODE_SEQUENCE, base_name="s_").rows
    order = [r.source.name for r in numbered]
    assert order.index("IMG_2.JPG") < order.index("IMG_10.JPG")


def test_an_empty_base_name_blocks_the_sequence(tmp_path):
    plan = _plan(_files(tmp_path, ["a.exr"]), mode=br.MODE_SEQUENCE)
    assert plan.blocked == "Enter a base name." and not plan.can_run


def test_numbers_that_outgrow_the_padding_are_flagged(tmp_path):
    files = _files(tmp_path, ["a.exr", "b.exr", "c.exr"])
    plan = _plan(files, mode=br.MODE_SEQUENCE, base_name="s_", start=9998, padding=4)
    assert plan.padding_needed == 5
    # 9998 and 9999 fit; 10000 grows past the padding and is flagged.
    assert [bool(r.warning) for r in plan.rows] == [False, False, True]


def test_why_the_button_is_off(tmp_path):
    assert br.plan([], br.RenameRules()).why_not() == "Load files first."
    f = _files(tmp_path, ["a.exr"])
    assert _plan(f).why_not() == "No names change with these rules."


# --------------------------------------------------------------- shared names
@pytest.mark.parametrize("name", ["../x", "a/b", "a\\b", "SH:01", "", "   ", "CON", "x."])
def test_name_problem_refuses(name):
    assert name_problem(name, "A shot name")


def test_name_problem_accepts_ordinary_names():
    assert name_problem("SH_010", "A shot name") is None
    assert name_problem("प्लेट_01", "A shot name") is None


def test_normalise_shot_name():
    assert normalise_shot_name("sh 060 (client)") == "SH_060"
    assert normalise_shot_name("SH010") == "SH010"
    assert normalise_shot_name("sh_010_bg") == "sh_010_bg"


def test_help_page_describes_the_real_undo():
    data = json.loads((Path(__file__).parent.parent / "slate" / "core" / "help_content.json")
                      .read_text(encoding="utf-8"))
    page = data["rename_tool"]["content"]
    assert "Undo last rename" in page
    assert "undo_rename" not in page and ".bat" not in page
