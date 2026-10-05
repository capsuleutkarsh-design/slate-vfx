"""
Frame numbering, real files: the pattern Slate builds for a folder of frames
(shot_media / sequence_utils.group_frames) must be one that RV and ffmpeg both
read in full.
"""

from pathlib import Path

import pytest

from slate.core.domain.shot_media import _best_clip_in_folder
from tests.realtools.conftest import ffmpeg, make_sequence, needs_ffmpeg, needs_rvio, probe, rvio

pytestmark = [pytest.mark.realtools, needs_ffmpeg]


def _frames(folder: Path, name: str, numbers, ext="png"):
    folder.mkdir(parents=True, exist_ok=True)
    for n in numbers:
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=64x64", "-frames:v", 1, folder / f"{name}.{n}.{ext}")


CASES = {
    "padded_4": (lambda d: make_sequence(d, ext="png", count=8, size="64x64"), "%04d", 1001, 8),
    "padded_6": (lambda d: make_sequence(d, ext="png", first=100001, count=8, size="64x64"),
                 "%06d", 100001, 8),
    "unpadded_same_width": (lambda d: _frames(d, "shot", range(1001, 1007)), "%04d", 1001, 6),
    "unpadded_crossing_width": (lambda d: _frames(d, "shot", range(997, 1003)), "%d", 997, 6),
    "crossing_0999": (lambda d: _frames(d, "shot", ["0998", "0999", "1000", "1001"]), "%04d", 998, 4),
    "beside_a_shorter_one": (lambda d: (make_sequence(d, ext="png", count=8, size="64x64"),
                                        make_sequence(d, name="SH010_plate_alpha", ext="png",
                                                      count=3, size="64x64")), "%04d", 1001, 8),
}


@pytest.mark.parametrize("case", list(CASES))
def test_pattern_reads_every_frame_in_ffmpeg_and_rv(isolated_proxy_manager, tmp_path, case):
    make, printf, first, count = CASES[case]
    folder = tmp_path / "plate"
    make(folder)
    clip = _best_clip_in_folder(folder)
    assert clip.is_sequence and printf in clip.path.name
    assert (clip.first_frame, clip.frame_count) == (first, count)

    # ffmpeg, through the proxy Slate makes of it.
    ok, proxy = isolated_proxy_manager.generate_proxy(Path(str(clip.path) % first), is_seq=True,
                                                      proxy_path=tmp_path / "p.mp4")
    assert ok and int(probe(proxy)["nb_read_frames"]) == count


@needs_rvio
@pytest.mark.parametrize("case", list(CASES))
def test_pattern_reads_every_frame_in_rv(tmp_path, case):
    make, _printf, _first, count = CASES[case]
    folder = tmp_path / "plate"
    make(folder)
    clip = _best_clip_in_folder(folder)
    out = tmp_path / "rv"
    out.mkdir()
    code, log = rvio(clip.path, "-o", out / "f.#.jpg")
    assert code == 0, log
    assert len(list(out.glob("*.jpg"))) == count, log
