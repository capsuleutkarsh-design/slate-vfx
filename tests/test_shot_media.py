"""
Finding the right media for a shot.

Both the review player and the Olive timeline ask this module what to show.
The mistake that actually costs money is reviewing the wrong thing - an old
scan version after a re-delivery, or a department's work-in-progress instead of
its output - so that is what most of these tests pin down.
"""

import pytest

from ut_vfx.core.domain.shot_media import (
    MediaClip, available_media, department_label, resolve_department,
    resolve_scan, scan_versions, shot_folder,
)


def _frames(folder, basename, frames=(1001, 1002, 1003), ext="exr"):
    folder.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        (folder / f"{basename}.{frame:04d}.{ext}").write_bytes(b"x")
    return folder


def _movie(folder, name="review.mov"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"x")
    return path


class FakeShot:
    def __init__(self, folder_paths):
        self.folder_paths = folder_paths


class TestScanVersions:

    def test_the_newest_version_is_what_gets_reviewed(self, tmp_path):
        """A re-delivered plate must not be reviewed as the old one."""
        scan = tmp_path / "01_Scan"
        _frames(scan / "v001" / "EXR", "SH010")
        _frames(scan / "v003" / "EXR", "SH010")
        _frames(scan / "v002" / "EXR", "SH010")

        clip = resolve_scan(scan)

        assert clip.scan_version == "v003"
        assert clip.department == "scan"

    def test_versions_sort_by_number_not_by_text(self, tmp_path):
        scan = tmp_path / "01_Scan"
        for version in ("v001", "v002", "v010"):
            _frames(scan / version / "EXR", "SH010")

        assert scan_versions(scan) == ["v001", "v002", "v010"]
        assert resolve_scan(scan).scan_version == "v010"

    def test_an_older_version_can_be_asked_for_by_name(self, tmp_path):
        scan = tmp_path / "01_Scan"
        _frames(scan / "v001" / "EXR", "SH010")
        _frames(scan / "v002" / "EXR", "SH010")

        assert resolve_scan(scan, "v001").scan_version == "v001"

    def test_asking_for_a_version_that_is_not_there_falls_back(self, tmp_path):
        scan = tmp_path / "01_Scan"
        _frames(scan / "v002" / "EXR", "SH010")

        clip = resolve_scan(scan, "v009")

        # Better to show the plate that exists than nothing at all.
        assert clip is not None

    def test_an_empty_scan_folder_resolves_to_nothing(self, tmp_path):
        scan = tmp_path / "01_Scan"
        scan.mkdir()

        assert resolve_scan(scan) is None

    def test_a_missing_folder_is_not_an_error(self, tmp_path):
        assert resolve_scan(tmp_path / "nope") is None
        assert scan_versions(tmp_path / "nope") == []


class TestSequences:

    def test_frames_become_one_clip_with_a_frame_range(self, tmp_path):
        scan = tmp_path / "01_Scan"
        _frames(scan / "v001" / "EXR", "SH010", frames=(1001, 1002, 1003, 1004))

        clip = resolve_scan(scan)

        assert clip.is_sequence
        assert (clip.first_frame, clip.last_frame) == (1001, 1004)
        assert clip.frame_count == 4

    def test_the_padding_comes_from_the_files(self, tmp_path):
        """A show numbering past 9999 must not be forced into four digits."""
        scan = tmp_path / "01_Scan"
        _frames(scan / "v001" / "EXR", "SH010", frames=(100001, 100002))

        assert resolve_scan(scan).path.name == "SH010.%06d.exr"

    def test_a_movie_wins_over_a_sequence(self, tmp_path):
        folder = tmp_path / "07_Comp" / "Output"
        _frames(folder, "SH010")
        _movie(folder, "SH010_comp_v003.mov")

        clip = resolve_department(tmp_path / "07_Comp", "comp")

        assert clip.path.name == "SH010_comp_v003.mov"
        assert not clip.is_sequence

    def test_junk_is_not_mistaken_for_media(self, tmp_path):
        folder = tmp_path / "07_Comp" / "Output"
        folder.mkdir(parents=True)
        (folder / "._SH010.mov").write_bytes(b"x")

        assert resolve_department(tmp_path / "07_Comp", "comp") is None


class TestDepartmentOutput:

    def test_output_is_preferred_over_the_department_root(self, tmp_path):
        """Work-in-progress at the root is not what gets reviewed."""
        comp = tmp_path / "07_Comp"
        _movie(comp, "scratch.mov")
        _movie(comp / "Output", "SH010_comp.mov")

        clip = resolve_department(comp, "comp")

        assert clip.path.name == "SH010_comp.mov"

    def test_a_split_output_folder_is_searched(self, tmp_path):
        """03_Cg/Output/Anim and friends still have to be found."""
        cg = tmp_path / "03_Cg"
        _movie(cg / "Output" / "Anim", "SH010_anim.mov")

        clip = resolve_department(cg, "cg")

        assert clip.path.name == "SH010_anim.mov"

    def test_media_dropped_straight_in_the_department_folder_works(self, tmp_path):
        comp = tmp_path / "07_Comp"
        _movie(comp, "SH010_comp.mov")

        assert resolve_department(comp, "comp").path.name == "SH010_comp.mov"

    def test_a_department_with_no_output_resolves_to_nothing(self, tmp_path):
        comp = tmp_path / "07_Comp" / "Output"
        comp.mkdir(parents=True)

        assert resolve_department(tmp_path / "07_Comp", "comp") is None

    def test_the_clip_remembers_which_department_it_came_from(self, tmp_path):
        _movie(tmp_path / "07_Comp" / "Output", "SH010.mov")

        assert resolve_department(tmp_path / "07_Comp", "comp").department == "comp"


class TestResolvingAShotsFolders:

    def test_relative_folder_paths_are_resolved_against_the_project(self, tmp_path):
        """Relative paths are what let a project move drive without breaking."""
        shot = FakeShot({"comp": "05_Reels/ReelA/SH010/07_Comp"})

        resolved = shot_folder(shot, tmp_path, "comp")

        assert resolved == tmp_path / "05_Reels/ReelA/SH010/07_Comp"

    def test_an_absolute_path_is_left_alone(self, tmp_path):
        shot = FakeShot({"comp": str(tmp_path / "elsewhere")})

        assert shot_folder(shot, tmp_path, "comp") == tmp_path / "elsewhere"

    def test_a_shot_without_that_folder_gives_nothing(self, tmp_path):
        assert shot_folder(FakeShot({}), tmp_path, "comp") is None

    def test_a_shot_with_no_folder_paths_at_all_gives_nothing(self, tmp_path):
        assert shot_folder(object(), tmp_path, "comp") is None


class TestWhatThereIsToWatch:

    def _shot(self):
        return FakeShot({
            "scan": "05_Reels/ReelA/SH010/01_Scan",
            "comp": "05_Reels/ReelA/SH010/07_Comp",
            "prep": "05_Reels/ReelA/SH010/05_Prep",
            "deage": "05_Reels/ReelA/SH010/09_Deage",
        })

    def test_only_departments_that_rendered_something_are_offered(self, tmp_path):
        shot_root = tmp_path / "05_Reels/ReelA/SH010"
        _frames(shot_root / "01_Scan/v001/EXR", "SH010")
        _movie(shot_root / "07_Comp/Output", "SH010_comp.mov")
        (shot_root / "05_Prep/Output").mkdir(parents=True)

        media = available_media(self._shot(), tmp_path)

        assert set(media) == {"scan", "comp"}

    def test_every_department_can_be_listed_when_asked(self, tmp_path):
        """The picker sometimes wants to show what is missing, greyed out."""
        shot_root = tmp_path / "05_Reels/ReelA/SH010"
        _frames(shot_root / "01_Scan/v001/EXR", "SH010")

        media = available_media(self._shot(), tmp_path, include_empty=True)

        assert "prep" in media
        assert not media["prep"].exists() or media["prep"].path.is_dir()

    def test_a_shot_with_nothing_on_disk_offers_nothing(self, tmp_path):
        assert available_media(self._shot(), tmp_path) == {}

    def test_the_scan_entry_carries_its_version(self, tmp_path):
        shot_root = tmp_path / "05_Reels/ReelA/SH010"
        _frames(shot_root / "01_Scan/v001/EXR", "SH010")
        _frames(shot_root / "01_Scan/v002/EXR", "SH010")

        assert available_media(self._shot(), tmp_path)["scan"].scan_version == "v002"


class TestLabels:

    def test_the_plate_is_called_scan(self):
        assert department_label("scan") == "Scan"

    def test_departments_use_the_name_from_the_registry(self):
        assert department_label("prep") == "Prep / Paint"

    def test_an_unknown_key_still_reads_sensibly(self):
        assert department_label("whatever") == "Whatever"


class TestMediaClip:

    def test_a_sequence_pattern_counts_as_present_when_its_folder_is(self, tmp_path):
        folder = _frames(tmp_path / "EXR", "SH010")
        clip = MediaClip(path=folder / "SH010.%04d.exr", is_sequence=True)

        assert clip.exists()

    def test_a_sequence_in_a_folder_that_is_gone_does_not(self, tmp_path):
        clip = MediaClip(path=tmp_path / "nope" / "SH010.%04d.exr", is_sequence=True)

        assert not clip.exists()
