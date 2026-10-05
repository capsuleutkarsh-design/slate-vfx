"""
The editors' EDLs (lineup.generate_timelines), written to disk from real
plates made with ffmpeg and read back as bytes, the way Resolve, Premiere and
Avid read a CMX 3600 file. There is no working OTIO here (OpenRV's is broken),
so the CMX layout is checked field by field.
"""

import re
from pathlib import Path

import pytest

from slate.core.domain.lineup import LineupShot, generate_timelines
from slate.core.domain.shot_media import resolve_scan
from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot
from tests.realtools.conftest import make_movie, make_sequence, needs_ffmpeg

pytestmark = [pytest.mark.realtools, needs_ffmpeg]

TC = r"(\d{2}):(\d{2}):(\d{2}):(\d{2})"
# The fixed columns every editor writes and reads: event, reel (8 wide), track, cut.
EVENT = re.compile(rf"^(\d{{3}})  (AX      ) V     C        {TC} {TC} {TC} {TC}$")


def frames(hh, mm, ss, ff, rate):
    return ((int(hh) * 60 + int(mm)) * 60 + int(ss)) * rate + int(ff)


def parse(path: Path, rate: int):
    """(title, fcm, events) from the file on disk; every event line must match the layout."""
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "a BOM before TITLE: breaks older EDL readers"
    text = raw.decode("utf-8")
    lines = text.split("\r\n")
    assert "\n" not in text.replace("\r\n", ""), "mixed line endings"
    assert text.endswith("\r\n"), "the last event's comment must end its line"
    title, fcm = lines[0], lines[1]
    events, current = [], None
    for line in lines[2:]:
        if not line:
            continue
        if line[:3].isdigit():
            m = EVENT.match(line)
            assert m, f"event line is not CMX 3600 layout: {line!r}"
            g = m.groups()
            tcs = [frames(*g[i:i + 4], rate) for i in (2, 6, 10, 14)]
            for i in (2, 6, 10, 14):
                assert int(g[i + 3]) < rate, f"frame field {g[i + 3]} >= {rate} in {line!r}"
                assert int(g[i + 1]) < 60 and int(g[i + 2]) < 60
            current = {"num": int(g[0]), "src_in": tcs[0], "src_out": tcs[1],
                       "rec_in": tcs[2], "rec_out": tcs[3], "comments": []}
            events.append(current)
        else:
            assert line.startswith("* "), f"stray line in EDL: {line!r}"
            current["comments"].append(line)
    return title, fcm, events


def check_contiguous(events, rate):
    assert [e["num"] for e in events] == list(range(1, len(events) + 1))
    assert events[0]["rec_in"] == 3600 * rate, "record must start at 01:00:00:00"
    for prev, nxt in zip(events, events[1:]):
        assert nxt["rec_in"] == prev["rec_out"], "gap or overlap on the record side"
    for e in events:
        assert e["src_out"] - e["src_in"] == e["rec_out"] - e["rec_in"] > 0


def comment(event, key):
    found = [c.split(": ", 1)[1] for c in event["comments"] if c.startswith(f"* {key}:")]
    assert len(found) == 1, event["comments"]
    return found[0]


def test_sequence_and_23976_movie_on_a_24_timebase(tmp_path):
    """Two real plates: an EXR sequence from 1001 and a 23.976 movie with spaces and accents."""
    root = tmp_path / "Show é"
    make_sequence(root / "05_Reels/R1/SH010/01_Scan/v001/EXR", "SH010_plate", "exr", 1001, 12)
    movie = make_movie(root / "05_Reels/R1/SH020 café/01_Scan/v001/SH020 plate é.mov",
                       frames=30, rate="24000/1001")
    shots = [Shot(shot_name="SH020", reel_episode="R1",
                  folder_paths={"scan": "05_Reels/R1/SH020 café/01_Scan"}),
             Shot(shot_name="SH010", reel_episode="R1",
                  folder_paths={"scan": "05_Reels/R1/SH010/01_Scan"})]

    result = generate_timelines(shots, root / "out", "PRJ", root)
    assert result.ok and not result.error and result.shot_count == 2

    for path in (result.combined, result.per_reel["R1"]):
        title, fcm, events = parse(path, 24)
        assert title.startswith("TITLE: PRJ ")
        assert fcm == "FCM: NON-DROP FRAME"
        assert len(events) == 2
        check_contiguous(events, 24)
        seq, mov = events                       # edit order: SH010 then SH020
        assert seq["src_in"] == 1001 and seq["src_out"] == 1013   # out is exclusive
        assert comment(seq, "FROM CLIP NAME") == "SH010_plate.%04d.exr"
        assert mov["src_in"] == 0 and mov["src_out"] == 30
        assert comment(mov, "FROM CLIP NAME") == "SH020 plate é.mov"
        assert Path(comment(mov, "SOURCE FILE")) == movie
        assert Path(comment(mov, "SOURCE FILE")).is_file()


@pytest.mark.parametrize("fps, rate", [(24.0, 24), (25.0, 25), (23.976, 24)])
def test_sequence_from_1001_timecodes(tmp_path, fps, rate):
    """A plate starting at 1001: its own frame numbers as source TC, record from one hour."""
    folder = tmp_path / "a long folder name with spaces" / ("x" * 60) / "01_Scan"
    make_sequence(folder / "v001", "SH010_plate", "exr", 1001, 10)
    make_sequence(tmp_path / "SH020/01_Scan/v001", "SH020_plate", "exr", 1001, 7)
    entries = []
    for name, scan in (("SH010", folder), ("SH020", tmp_path / "SH020/01_Scan")):
        clip = resolve_scan(scan)
        entries.append(LineupShot(name=name, reel="R1", fps=fps,
                                  frame_range=(clip.first_frame, clip.last_frame),
                                  paths={"scan": clip.path}, clips={"scan": clip}))
    result = generate_timelines([], tmp_path / "out", "Show", lineup=entries)
    _title, _fcm, events = parse(result.per_reel["R1"], rate)
    check_contiguous(events, rate)
    assert [(e["src_in"], e["src_out"]) for e in events] == [(1001, 1011), (1001, 1008)]
    assert events[-1]["rec_out"] == 3600 * rate + 17
    assert Path(comment(events[0], "SOURCE FILE")).parent.is_dir()
