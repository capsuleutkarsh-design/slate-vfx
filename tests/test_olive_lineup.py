"""
Building the Olive timeline from dashboard data.

The lineup is the edit: plates in reel order on layer one, and each
department's render on its own layer directly beneath the plate it came from.
What matters is that a render lands at the same point on the timeline as its
plate - a comp one slot to the left of its scan is worse than no comp at all.
"""

import xml.etree.ElementTree as ET

import pytest

from slate.core.domain.olive_lineup import (
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


def _clip_labels(path):
    """Every clip label in a generated timeline."""
    tree = ET.parse(path)
    labels = []
    for node in tree.iter("node"):
        if node.attrib.get("id", "").endswith(".footage"):
            label = node.find("label")
            if label is not None and label.text:
                labels.append(label.text)
    return labels


def _clip_starts(path):
    """
    Where each clip sits on the timeline, keyed by the media it plays.

    A clip node carries the timing but no name; the name is on the footage node
    it reaches through the transform, so the chain has to be followed.
    """
    tree = ET.parse(path)
    by_ptr = {n.attrib.get("ptr"): n for n in tree.iter("node")}

    def label_behind(node, depth=0):
        """The footage label this node ultimately plays."""
        if depth > 5:
            return None
        label = node.find("label")
        if node.attrib.get("id", "").endswith(".footage") and label is not None:
            return label.text
        for conn in node.iter("connection"):
            output = conn.find("output")
            if output is None or output.text not in by_ptr:
                continue
            found = label_behind(by_ptr[output.text], depth + 1)
            if found:
                return found
        return None

    starts = {}
    for node in tree.iter("node"):
        if not node.attrib.get("id", "").endswith(".clip"):
            continue
        name = label_behind(node)
        if not name:
            continue
        for inp in node.iter("input"):
            if inp.attrib.get("id") != "timeline_in":
                continue
            track = inp.find(".//track")
            if track is not None:
                starts[name] = track.text
    return starts


class TestOneShotBecomesATimelineEntry:

    def test_a_shot_with_only_a_plate_fills_layer_one(self, tmp_path):
        entry = build_lineup_shot(_shot(tmp_path), tmp_path)

        assert entry.layers == ["scan"]
        assert entry.scan_path is not None

    def test_renders_fill_their_own_layers(self, tmp_path):
        shot = _shot(tmp_path, departments=("comp", "prep"))

        entry = build_lineup_shot(shot, tmp_path)

        assert entry.layers == ["scan", "comp", "prep"]

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

    def test_a_department_the_layout_does_not_use_is_left_out(self, tmp_path):
        shot = _shot(tmp_path)
        root = tmp_path / "05_Reels" / "ReelA" / "SH010"
        _movie(root / "04_Roto" / "Output", "SH010_roto.mov")
        shot.folder_paths["roto"] = "05_Reels/ReelA/SH010/04_Roto"

        assert "roto" not in build_lineup_shot(shot, tmp_path).layers


class TestEditOrder:

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


class TestGeneratedTimelines:

    def test_a_timeline_per_reel_and_one_holding_everything(self, tmp_path):
        shots = [
            _shot(tmp_path, "SH010", reel="ReelA"),
            _shot(tmp_path, "SH020", reel="ReelB"),
        ]

        result = generate_timelines(shots, tmp_path / "out", "PRJ", tmp_path)

        assert set(result.per_reel) == {"ReelA", "ReelB"}
        assert result.combined.exists()
        assert all(path.exists() for path in result.per_reel.values())

    def test_the_combined_timeline_holds_every_shot(self, tmp_path):
        shots = [
            _shot(tmp_path, "SH010", reel="ReelA"),
            _shot(tmp_path, "SH020", reel="ReelB"),
        ]

        result = generate_timelines(shots, tmp_path / "out", "PRJ", tmp_path)
        labels = _clip_labels(result.combined)

        assert "SH010_scan" in labels
        assert "SH020_scan" in labels

    def test_a_reel_timeline_holds_only_that_reel(self, tmp_path):
        shots = [
            _shot(tmp_path, "SH010", reel="ReelA"),
            _shot(tmp_path, "SH020", reel="ReelB"),
        ]

        result = generate_timelines(shots, tmp_path / "out", "PRJ", tmp_path)
        labels = _clip_labels(result.per_reel["ReelA"])

        assert labels == ["SH010_scan"]

    def test_a_render_appears_on_its_own_layer(self, tmp_path):
        shots = [_shot(tmp_path, "SH010", departments=("comp", "deage"))]

        result = generate_timelines(shots, tmp_path / "out", "PRJ", tmp_path,
                                    prefer_proxy_media=False)
        labels = _clip_labels(result.combined)

        assert set(labels) == {"SH010_scan", "SH010_comp", "SH010_deage"}

    def test_the_timeline_has_a_track_for_every_layer(self, tmp_path):
        shots = [_shot(tmp_path, "SH010", departments=("comp",))]

        result = generate_timelines(shots, tmp_path / "out", "PRJ", tmp_path)
        tree = ET.parse(result.combined)
        tracks = [n for n in tree.iter("node")
                  if n.attrib.get("id", "").endswith(".track")]

        assert len(tracks) == len(TRACK_LAYOUT)

    def test_a_render_sits_at_the_same_time_as_its_plate(self, tmp_path):
        """The whole point of the stack: comp directly beneath its scan."""
        shots = [
            _shot(tmp_path, "SH010", frames=(1, 10), departments=("comp",)),
            _shot(tmp_path, "SH020", frames=(1, 10), departments=("comp",)),
        ]

        result = generate_timelines(shots, tmp_path / "out", "PRJ", tmp_path,
                                    prefer_proxy_media=False)
        starts = _clip_starts(result.combined)

        assert starts["SH010_comp"] == starts["SH010_scan"]
        assert starts["SH020_comp"] == starts["SH020_scan"]
        # And the second shot really does come after the first.
        assert starts["SH020_scan"] != starts["SH010_scan"]

    def test_each_shot_starts_where_the_last_one_ended(self, tmp_path):
        shots = [
            _shot(tmp_path, "SH010", frames=(1, 10)),
            _shot(tmp_path, "SH020", frames=(1, 10)),
        ]

        result = generate_timelines(shots, tmp_path / "out", "PRJ", tmp_path)
        starts = _clip_starts(result.combined)

        assert starts["SH010_scan"] == "0/1"
        # Ten frames at 24fps, as a reduced fraction of a second.
        assert starts["SH020_scan"] == "5/12"

    def test_shots_with_no_plate_are_reported_not_silently_dropped(self, tmp_path):
        shots = [
            _shot(tmp_path, "SH010"),
            _shot(tmp_path, "SH020", scan=False),
        ]

        result = generate_timelines(shots, tmp_path / "out", "PRJ", tmp_path)

        assert result.skipped == ["SH020"]
        assert "1 shot(s) skipped" in result.summary()

    def test_nothing_to_build_says_so(self, tmp_path):
        result = generate_timelines([], tmp_path / "out", "PRJ", tmp_path)

        assert not result.ok
        assert "no shot has a scan" in result.summary()

    def test_a_bridge_that_fails_is_reported_not_raised(self, tmp_path):
        class BrokenBridge:
            def generate_project(self, *args, **kwargs):
                return False

        result = generate_timelines([_shot(tmp_path)], tmp_path / "out", "PRJ",
                                    tmp_path, bridge=BrokenBridge())

        assert not result.ok


class TestNodeIdentifiersAreUnique:
    """
    Olive wires nodes together by their pointer, so two nodes sharing one means
    a clip pointing at the wrong footage. The old generator added a random
    number under ten thousand to a millisecond timestamp, which collides
    readily - a draw a millisecond later with a smaller random part lands on
    exactly the same value.
    """

    def test_a_long_run_never_repeats(self):
        from slate.core.domain.olive_bridge import OliveBridge

        bridge = OliveBridge()
        pointers = [bridge._generate_ptr() for _ in range(20000)]

        assert len(set(pointers)) == len(pointers), "pointers collided"

    def test_a_generated_project_has_no_duplicate_pointers(self, tmp_path):
        shots = [_shot(tmp_path, f"SH{n:03d}", departments=("comp", "prep"))
                 for n in range(10, 130, 10)]

        result = generate_timelines(shots, tmp_path / "out", "PRJ", tmp_path,
                                    prefer_proxy_media=False)
        tree = ET.parse(result.combined)
        pointers = [n.attrib.get("ptr") for n in tree.iter("node")
                    if n.attrib.get("ptr")]

        assert len(set(pointers)) == len(pointers), (
            "two nodes share a pointer; clips would play the wrong media"
        )
