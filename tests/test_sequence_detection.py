"""
One answer to "is this a frame of a sequence, or a still?" (MED-002).

The stock viewer sent ordinary photos (rain_heavy_i.jpg, IMG_2045.jpg, a
Hindi-named jpg) to the sequence player, because find_sequence returned the
first same-extension sequence in the folder for any file without a frame
number, matched names by substring, and accepted one-frame "sequences".
"""

from pathlib import Path

import pytest

from slate.utils import sequence_utils as su


def _touch(folder: Path, *names):
    for name in names:
        (folder / name).write_bytes(b"x")


@pytest.fixture
def library(tmp_path):
    _touch(tmp_path,
           "shot.1001.exr", "shot.1002.exr", "shot.1003.exr",
           "plate_ref.0001.jpg", "plate_ref.0002.jpg",
           "rain_heavy_i.jpg", "portrait_4x5_ref.jpg", "IMG_2045.jpg",
           "बारिश.jpg", "emoji_\U0001F3AC.png",
           "plate.0001.jpg",           # one frame of 'plate' - a still
           "clip_0001.mov")
    return tmp_path


def test_stills_and_sequences_are_told_apart(library):
    sequences, stills = su.group_frames(library.iterdir())
    patterns = sorted(s.filename_pattern for s in sequences)
    assert patterns == ["plate_ref.%04d.jpg", "shot.%d.exr"]
    still_names = {p.name for p in stills}
    for name in ("rain_heavy_i.jpg", "IMG_2045.jpg", "plate.0001.jpg", "clip_0001.mov",
                 "बारिश.jpg", "emoji_\U0001F3AC.png"):
        assert name in still_names


def test_a_still_has_no_sequence(library):
    for name in ("rain_heavy_i.jpg", "portrait_4x5_ref.jpg", "IMG_2045.jpg",
                 "बारिश.jpg", "plate.0001.jpg"):
        assert su.sequence_for(library / name) is None, name
        assert su.SequenceDetector.find_sequence(library / name) is None, name


def test_a_frame_finds_its_own_sequence_not_a_namesake(library):
    seq = su.sequence_for(library / "plate_ref.0002.jpg")
    assert seq is not None and (seq.start, seq.end) == (1, 2)
    assert seq.filename_pattern == "plate_ref.%04d.jpg"
    if su.HAS_FILESEQ:
        found = su.SequenceDetector.find_sequence(library / "plate_ref.0002.jpg")
        assert found is not None and len(found) == 2
        assert su.SequenceDetector.find_sequence(library / "shot.1002.exr") is not None


def test_padding_carries_past_the_width(tmp_path):
    """0998, 0999, 1000: one sequence padded to 4, not two."""
    _touch(tmp_path, "s.0998.dpx", "s.0999.dpx", "s.1000.dpx", "s.5.dpx")
    sequences, stills = su.group_frames(tmp_path.iterdir())
    assert len(sequences) == 1
    seq = sequences[0]
    assert (seq.padding, seq.start, seq.end) == (4, 998, 1000)
    assert seq.frame_path(1000).name == "s.1000.dpx"
    assert [p.name for p in stills] == ["s.5.dpx"]


def test_gaps_are_reported(tmp_path):
    _touch(tmp_path, "g_0001.exr", "g_0002.exr", "g_0005.exr")
    seq = su.group_frames(tmp_path.iterdir())[0][0]
    assert seq.missing_frames == [3, 4]
    info = seq.info()
    assert info["first_frame"] == 1 and info["last_frame"] == 5 and info["has_missing"]


def test_the_legacy_entry_points_agree(library):
    from slate.utils.sequence_detector import detect_sequence
    info = detect_sequence(library, "plate*.jpg")
    assert info["pattern"] == "plate_ref.%04d.jpg" and info["frame_count"] == 2
    assert detect_sequence(library, "rain*.jpg") is None
    assert su.SequenceFallback.detect_sequence_pattern(library / "IMG_2045.jpg") is None
    assert su.get_sequence_info(library, ["shot.*.exr"])["frame_count"] == 3


def test_the_ingest_scanner_uses_the_same_rules(library):
    from slate.core.domain.ingest.sequence import SequenceDetector as IngestDetector
    sequences, singles = IngestDetector.scan_directory(library)
    assert sorted(s.name for s in sequences) == ["plate_ref", "shot"]
    assert "plate.0001.jpg" in {p.name for p in singles}
