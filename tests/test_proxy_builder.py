"""
Making review proxies on demand.

Olive and RV both play EXR sequences slowly, so an MP4 goes beside each one.
This runs when someone presses the button after checking an ingest, never
during the ingest itself - a delivery must not wait behind ffmpeg.

ffmpeg is never actually invoked here; what these tests pin down is which files
get a proxy, where it lands, and what happens when things go wrong.
"""

import pytest

from ut_vfx.core.domain.proxy_builder import (
    ProxyBuildResult, ProxyJob, build, first_frame_file, needs_proxy, plan,
    proxy_path_for,
)
from ut_vfx.core.domain.shot_media import MediaClip
from ut_vfx.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot


def _frames(folder, basename="SH010", frames=(1001, 1002)):
    folder.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        (folder / f"{basename}.{frame:04d}.exr").write_bytes(b"x")


def _movie(folder, name="SH010_comp.mov"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(b"x")


def _shot(tmp_path, name="SH010", reel="ReelA", scan=True, comp=False):
    root = tmp_path / "05_Reels" / reel / name
    if scan:
        _frames(root / "01_Scan" / "v001" / "EXR", name)
    if comp:
        _movie(root / "07_Comp" / "Output", f"{name}_comp.mov")
    return Shot(shot_name=name, reel_episode=reel, folder_paths={
        "scan": f"05_Reels/{reel}/{name}/01_Scan",
        "comp": f"05_Reels/{reel}/{name}/07_Comp",
    })


class FakeManager:
    """Stands in for ffmpeg."""

    def __init__(self, ffmpeg_path="ffmpeg.exe", succeed=True):
        self.ffmpeg_path = ffmpeg_path
        self.succeed = succeed
        self.calls = []

    def generate_proxy(self, input_path=None, is_seq=False, proxy_path=None,
                       **kwargs):
        self.calls.append((input_path, is_seq, proxy_path))
        if self.succeed:
            proxy_path.write_bytes(b"mp4")
            return True, proxy_path
        return False, None


class TestWhatNeedsAProxy:

    def test_a_sequence_does(self, tmp_path):
        clip = MediaClip(path=tmp_path / "SH010.%04d.exr", is_sequence=True)

        assert needs_proxy(clip)

    def test_a_movie_does_not(self, tmp_path):
        """Making an MP4 out of an MP4 costs time and quality for nothing."""
        assert not needs_proxy(MediaClip(path=tmp_path / "SH010_comp.mov"))

    def test_a_plan_covers_the_plate_and_the_renders(self, tmp_path):
        shot = _shot(tmp_path, comp=True)

        jobs = plan([shot], tmp_path)

        # The comp is already a movie, so only the plate needs one.
        assert [job.department for job in jobs] == ["scan"]

    def test_a_shot_with_nothing_on_disk_produces_no_work(self, tmp_path):
        assert plan([_shot(tmp_path, scan=False)], tmp_path) == []

    def test_every_shot_in_the_project_is_covered(self, tmp_path):
        shots = [_shot(tmp_path, "SH010"), _shot(tmp_path, "SH020")]

        jobs = plan(shots, tmp_path)

        assert {job.shot_name for job in jobs} == {"SH010", "SH020"}


class TestWhereProxiesGo:

    def test_beside_the_media_in_a_proxy_folder(self, tmp_path):
        """This is the folder the Olive bridge already searches."""
        clip = MediaClip(path=tmp_path / "EXR" / "SH010.%04d.exr",
                         department="scan", is_sequence=True)

        target = proxy_path_for(clip, "SH010")

        assert target.parent.name == "proxy"
        assert target.parent.parent.name == "EXR"

    def test_the_name_says_the_shot_and_the_department(self, tmp_path):
        clip = MediaClip(path=tmp_path / "SH010.%04d.exr", department="comp")

        assert proxy_path_for(clip, "SH010").name == "SH010_comp_proxy.mp4"

    def test_a_sequence_is_handed_to_ffmpeg_as_a_real_frame(self, tmp_path):
        """A printf pattern is not a file; ffmpeg needs a frame to start at."""
        clip = MediaClip(path=tmp_path / "SH010.%04d.exr", is_sequence=True,
                         first_frame=1001, last_frame=1010)

        assert first_frame_file(clip).name == "SH010.1001.exr"

    def test_a_movie_is_handed_over_unchanged(self, tmp_path):
        clip = MediaClip(path=tmp_path / "SH010.mov")

        assert first_frame_file(clip) == tmp_path / "SH010.mov"


class TestBuilding:

    def test_a_proxy_is_made_for_each_job(self, tmp_path):
        manager = FakeManager()
        jobs = plan([_shot(tmp_path)], tmp_path)

        result = build(jobs, manager=manager)

        assert len(result.built) == 1
        assert jobs[0].target.exists()

    def test_a_sequence_is_flagged_as_one(self, tmp_path):
        manager = FakeManager()

        build(plan([_shot(tmp_path)], tmp_path), manager=manager)

        assert manager.calls[0][1] is True

    def test_a_proxy_that_is_already_there_is_not_remade(self, tmp_path):
        manager = FakeManager()
        jobs = plan([_shot(tmp_path)], tmp_path)
        jobs[0].target.parent.mkdir(parents=True)
        jobs[0].target.write_bytes(b"already")

        result = build(jobs, manager=manager)

        assert result.already_there
        assert not manager.calls

    def test_overwrite_remakes_it(self, tmp_path):
        manager = FakeManager()
        jobs = plan([_shot(tmp_path)], tmp_path)
        jobs[0].target.parent.mkdir(parents=True)
        jobs[0].target.write_bytes(b"already")

        result = build(jobs, manager=manager, overwrite=True)

        assert result.built
        assert manager.calls

    def test_a_failure_is_reported_by_name(self, tmp_path):
        manager = FakeManager(succeed=False)

        result = build(plan([_shot(tmp_path)], tmp_path), manager=manager)

        assert result.failed
        assert "SH010" in result.failed[0]
        assert not result.ok

    def test_no_ffmpeg_says_so_plainly(self, tmp_path):
        manager = FakeManager(ffmpeg_path="")

        result = build(plan([_shot(tmp_path)], tmp_path), manager=manager)

        assert "ffmpeg" in result.error
        assert not result.ok

    def test_progress_is_reported_for_every_job(self, tmp_path):
        seen = []
        shots = [_shot(tmp_path, "SH010"), _shot(tmp_path, "SH020")]

        build(plan(shots, tmp_path), manager=FakeManager(),
              progress=lambda done, total, label: seen.append((done, total)))

        assert seen == [(1, 2), (2, 2)]

    def test_stopping_leaves_the_rest_alone(self, tmp_path):
        """
        Cancelling is checked between jobs.

        Stopping ffmpeg mid-file would leave a half-written MP4 that looks
        like a finished proxy.
        """
        manager = FakeManager()
        shots = [_shot(tmp_path, "SH010"), _shot(tmp_path, "SH020")]

        result = build(plan(shots, tmp_path), manager=manager,
                       should_stop=lambda: len(manager.calls) >= 1)

        assert result.cancelled
        assert len(manager.calls) == 1

    def test_nothing_to_do_is_not_a_failure(self):
        result = build([], manager=FakeManager())

        assert result.ok
        assert "Nothing needed a proxy" in result.summary()

    def test_the_summary_says_what_happened(self, tmp_path):
        result = ProxyBuildResult(built=["a"], already_there=["b"], failed=["c"])

        text = result.summary()

        assert "1 proxy(s) made" in text
        assert "1 already there" in text
        assert "1 failed" in text
