"""
Building the lineup and its EDLs from dashboard data.

The lineup is the edit: plates in reel order on layer one, and each
department's render on its own layer directly beneath the plate it came from.
What matters is that a render lands at the same point on the timeline as its
plate - a comp one slot to the left of its scan is worse than no comp at all.
"""

import pytest

from slate.core.domain.lineup import (
    TRACK_LAYOUT, build_lineup, build_lineup_shot, generate_timelines,
    group_by_reel,
)
from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot


def _frames(folder, basename="plate", frames=(1001, 1002, 1003)):
    folder.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        (folder / f"{basename}.{frame:04d}.exr").write_bytes(b"x")


def _movie(folder, name="render.mov"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(b"x")


def _shot(tmp_path, name="SH010", reel="ReelA", scan=True, departments=(),
          frames=(1001, 1003), **kwargs):
    """A dashboard shot with matching folders on disk."""
    root = tmp_path / "05_Reels" / reel / name
    folders = {
        "scan": f"05_Reels/{reel}/{name}/01_Scan",
        "comp": f"05_Reels/{reel}/{name}/07_Comp",
        "prep": f"05_Reels/{reel}/{name}/05_Prep",
        "deage": f"05_Reels/{reel}/{name}/09_Deage",
    }
    if scan:
        _frames(root / "01_Scan" / "v001" / "EXR", name,
                frames=tuple(range(frames[0], frames[1] + 1)))
    for dept in departments:
        folder = {"comp": "07_Comp", "prep": "05_Prep", "deage": "09_Deage"}[dept]
        _movie(root / folder / "Output", f"{name}_{dept}.mov")

    return Shot(shot_name=name, reel_episode=reel, folder_paths=folders, **kwargs)


def _events(path):
    """Each event line of an EDL, split into its fields."""
    return [line.split() for line in path.read_text(encoding="utf-8").splitlines()
            if line[:3].isdigit()]


def _clip_names(path):
    return [line.split(": ", 1)[1] for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("* FROM CLIP NAME:")]


class TestOneShotBecomesATimelineEntry:

    def test_a_shot_with_only_a_plate_fills_layer_one(self, tmp_path):
        entry = build_lineup_shot(_shot(tmp_path), tmp_path)

        assert entry.layers == ["scan"]
        assert entry.paths["scan"] is not None

    def test_renders_fill_their_own_layers(self, tmp_path):
        shot = _shot(tmp_path, departments=("comp", "prep"))

        entry = build_lineup_shot(shot, tmp_path)

        # Departments in their order in departments.json.
        assert entry.layers == ["scan", "prep", "comp"]

    def test_a_shot_with_no_plate_is_not_put_on_the_timeline(self, tmp_path):
        """A clip pointing at nothing is worse than a gap."""
        shot = _shot(tmp_path, scan=False, departments=("comp",))

        assert build_lineup_shot(shot, tmp_path) is None

    def test_the_length_comes_from_the_frames_on_disk(self, tmp_path):
        shot = _shot(tmp_path, frames=(1001, 1048))

        entry = build_lineup_shot(shot, tmp_path)

        assert entry.frame_range == (1001, 1048)
        assert entry.get_frame_count() == 48

    def test_a_typed_frame_count_is_only_a_fallback(self, tmp_path):
        """What is on disk beats what someone typed into the dashboard."""
        shot = _shot(tmp_path, frames=(1001, 1010), edit_frames=999)

        assert build_lineup_shot(shot, tmp_path).get_frame_count() == 10

    def test_every_department_that_renders_gets_a_layer(self, tmp_path):
        """DMP, CG, Roto... used to be left out of a fixed four-layer layout (MED-085)."""
        shot = _shot(tmp_path)
        root = tmp_path / "05_Reels" / "ReelA" / "SH010"
        _movie(root / "04_Roto" / "Output", "SH010_roto.mov")
        shot.folder_paths["roto"] = "05_Reels/ReelA/SH010/04_Roto"

        assert "roto" in build_lineup_shot(shot, tmp_path).layers
        keys = [key for _label, key in TRACK_LAYOUT]
        assert keys[0] == "scan" and {"dmp", "cg", "roto", "comp"} <= set(keys)


class TestEditOrder:

    def test_reels_then_every_number(self, tmp_path):
        """The audit's five shots came out interleaved by their last number (MED-079)."""
        names = [("R2", "SEQ030_SH005"), ("R1", "SEQ010_SH010"), ("R1", "SEQ020_SH010"),
                 ("R2", "SEQ030_SH015"), ("R1", "SEQ010_SH020")]
        shots = [_shot(tmp_path, name, reel=reel) for reel, name in names]
        order = [(e.reel, e.name) for e in build_lineup(shots, tmp_path)]
        assert order == [("R1", "SEQ010_SH010"), ("R1", "SEQ010_SH020"), ("R1", "SEQ020_SH010"),
                         ("R2", "SEQ030_SH005"), ("R2", "SEQ030_SH015")]

    def test_an_unknown_length_is_marked(self, tmp_path):
        from slate.core.domain.lineup import fps_mismatches
        shot = _shot(tmp_path, "SH010")
        root = tmp_path / "05_Reels" / "ReelA" / "SH010" / "01_Scan" / "v001" / "EXR"
        for f in root.iterdir():
            f.unlink()
        _movie(root, "plate.mov")
        entry = build_lineup_shot(shot, tmp_path)
        assert not entry.length_known and entry.get_frame_count() == 100        # MED-089
        assert "unknown" in entry.frames_text()
        # The rate is the plate's own (MED2-041); a 25 fps one stands out.
        other = build_lineup_shot(_shot(tmp_path, "SH020"), tmp_path)
        other.fps = 25.0
        assert fps_mismatches([entry, entry, other]) == ["SH020"]

    def test_lineups_go_into_the_project(self, tmp_path):
        from slate.core.domain.lineup import lineup_folder
        assert lineup_folder(tmp_path, "PRJ") == tmp_path / "editorial" / "lineups"   # MED-093

    def test_shots_run_in_shot_number_order(self, tmp_path):
        shots = [_shot(tmp_path, name) for name in ("SH030", "SH010", "SH020")]

        lineup = build_lineup(shots, tmp_path)

        assert [entry.name for entry in lineup] == ["SH010", "SH020", "SH030"]

    def test_numbers_sort_as_numbers_not_as_text(self, tmp_path):
        shots = [_shot(tmp_path, name) for name in ("SH0100", "SH0020")]

        lineup = build_lineup(shots, tmp_path)

        assert [entry.name for entry in lineup] == ["SH0020", "SH0100"]

    def test_an_unnumbered_shot_still_makes_it_in(self, tmp_path):
        shots = [_shot(tmp_path, "SH010"), _shot(tmp_path, "pickup")]

        assert len(build_lineup(shots, tmp_path)) == 2

    def test_reels_are_kept_apart(self, tmp_path):
        shots = [
            _shot(tmp_path, "SH010", reel="ReelA"),
            _shot(tmp_path, "SH020", reel="ReelB"),
        ]

        reels = group_by_reel(build_lineup(shots, tmp_path))

        assert set(reels) == {"ReelA", "ReelB"}


class TestGeneratedEdls:

    def test_an_edl_per_reel_and_one_holding_everything(self, tmp_path):
        shots = [
            _shot(tmp_path, "SH010", reel="ReelA"),
            _shot(tmp_path, "SH020", reel="ReelB"),
        ]

        result = generate_timelines(shots, tmp_path / "out", "PRJ", tmp_path)

        assert set(result.per_reel) == {"ReelA", "ReelB"}
        assert result.combined.name == "PRJ_All_Reels_scan.edl"
        assert all(path.exists() for path in result.per_reel.values())

    def test_the_combined_edl_holds_every_shot_in_order(self, tmp_path):
        shots = [
            _shot(tmp_path, "SH020", reel="ReelB"),
            _shot(tmp_path, "SH010", reel="ReelA"),
        ]

        result = generate_timelines(shots, tmp_path / "out", "PRJ", tmp_path)

        assert _clip_names(result.combined) == ["SH010.%04d.exr", "SH020.%04d.exr"]

    def test_a_reel_edl_holds_only_that_reel(self, tmp_path):
        shots = [
            _shot(tmp_path, "SH010", reel="ReelA"),
            _shot(tmp_path, "SH020", reel="ReelB"),
        ]

        result = generate_timelines(shots, tmp_path / "out", "PRJ", tmp_path)

        assert _clip_names(result.per_reel["ReelA"]) == ["SH010.%04d.exr"]

    def test_the_chosen_layer_with_the_plate_standing_in(self, tmp_path):
        shots = [_shot(tmp_path, "SH010", departments=("comp",)), _shot(tmp_path, "SH020")]

        result = generate_timelines(shots, tmp_path / "out", "PRJ", tmp_path, layer="comp")

        assert result.combined.name == "PRJ_All_Reels_comp.edl"
        assert _clip_names(result.combined) == ["SH010_comp.mov", "SH020.%04d.exr"]

    def test_timecodes_follow_on_from_one_hour(self, tmp_path):
        """A sequence keeps its frame numbers as source; each shot starts where the last ended."""
        shots = [_shot(tmp_path, "SH010", frames=(1001, 1010)),
                 _shot(tmp_path, "SH020", frames=(1001, 1048))]

        result = generate_timelines(shots, tmp_path / "out", "PRJ", tmp_path)
        events = _events(result.combined)

        # 1001 at 24 fps is 41 s 17 f; ten frames run to 41 s 27 f = 42 s 3 f.
        assert events[0] == ["001", "AX", "V", "C", "00:00:41:17", "00:00:42:03",
                             "01:00:00:00", "01:00:00:10"]
        assert events[1][6:] == ["01:00:00:10", "01:00:02:10"]

    def test_a_movie_starts_at_zero(self, tmp_path):
        result = generate_timelines([_shot(tmp_path, "SH010", departments=("comp",))],
                                    tmp_path / "out", "PRJ", tmp_path, layer="comp")

        assert _events(result.combined)[0][4] == "00:00:00:00"

    def test_shots_with_no_plate_are_reported_not_silently_dropped(self, tmp_path):
        shots = [
            _shot(tmp_path, "SH010"),
            _shot(tmp_path, "SH020", scan=False),
        ]

        result = generate_timelines(shots, tmp_path / "out", "PRJ", tmp_path)

        assert result.skipped == ["SH020"]
        assert "1 shot skipped" in result.summary()

    def test_nothing_to_build_says_so(self, tmp_path):
        result = generate_timelines([], tmp_path / "out", "PRJ", tmp_path)

        assert not result.ok
        assert "No shots loaded" in result.summary()

    def test_a_folder_that_cannot_be_written_is_reported_not_raised(self, tmp_path):
        blocker = tmp_path / "out"
        blocker.write_text("a file where the folder should be")

        result = generate_timelines([_shot(tmp_path)], blocker, "PRJ", tmp_path)

        assert not result.ok and "Could not write" in result.summary()


def test_timecode():
    from slate.core.domain.lineup import timecode
    assert timecode(0, 24) == "00:00:00:00"
    assert timecode(86400 + 23, 24) == "01:00:00:23"
    assert timecode(25 * 61, 25) == "00:01:01:00"
