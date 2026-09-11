"""
Reviewing in OpenRV, and getting the verdict back.

Two halves. Going out: what a shot offers to review, and what actually gets
handed to RV. Coming back: putting the verdict on the right shot - which never
worked before, because the watcher matched against attributes a dashboard shot
does not have.
"""

import json
from pathlib import Path

import pytest

from slate.core.domain.rv_feedback import (
    RVFeedback, apply_feedback, department_from_path, file_annotation,
    match_shot, note_text, read_feedback,
)
from slate.core.domain.rv_review import build_request, launch
from slate.core.rv_integration import RVLauncher
from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot


def _frames(folder, basename="SH010", frames=(1001, 1002)):
    folder.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        (folder / f"{basename}.{frame:04d}.exr").write_bytes(b"x")


def _movie(folder, name="SH010_comp.mov"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(b"x")


class FakeShot:
    def __init__(self, name="SH010", **folders):
        self.shot_name = name
        self.folder_paths = folders


class FakeLauncher:
    def __init__(self, ok=True):
        self.ok = ok
        self.single = []
        self.playlists = []

    def launch_media(self, path):
        self.single.append(path)
        return self.ok

    def launch_playlist(self, paths):
        self.playlists.append(list(paths))
        return self.ok


class TestWhatAShotOffers:

    def _shot(self):
        return FakeShot(
            scan="01_Scan", comp="07_Comp", prep="05_Prep", deage="09_Deage",
        )

    def test_only_departments_with_media_are_offered(self, tmp_path):
        _frames(tmp_path / "01_Scan" / "v001" / "EXR")
        _movie(tmp_path / "07_Comp" / "Output")
        (tmp_path / "05_Prep" / "Output").mkdir(parents=True)

        request = build_request(self._shot(), tmp_path)

        assert [opt.key for opt in request.options] == ["scan", "comp"]

    def test_the_plate_is_offered_first(self, tmp_path):
        """A review normally starts from the plate."""
        _movie(tmp_path / "07_Comp" / "Output")
        _frames(tmp_path / "01_Scan" / "v001" / "EXR")
        _movie(tmp_path / "09_Deage" / "Output", "SH010_deage.mov")

        request = build_request(self._shot(), tmp_path)

        assert request.options[0].key == "scan"

    def test_a_shot_with_nothing_rendered_offers_nothing(self, tmp_path):
        assert build_request(self._shot(), tmp_path).options == []

    def test_the_scan_option_says_which_version_it_is(self, tmp_path):
        _frames(tmp_path / "01_Scan" / "v001" / "EXR")
        _frames(tmp_path / "01_Scan" / "v002" / "EXR")

        request = build_request(self._shot(), tmp_path)

        assert "v002" in request.options[0].detail

    def test_media_paths_come_back_in_the_order_asked_for(self, tmp_path):
        _frames(tmp_path / "01_Scan" / "v001" / "EXR")
        _movie(tmp_path / "07_Comp" / "Output")

        request = build_request(self._shot(), tmp_path)
        paths = request.media_paths(["comp", "scan"])

        assert paths[0].endswith("SH010_comp.mov")
        assert "01_Scan" in paths[1]

    def test_asking_for_something_that_is_not_there_is_skipped(self, tmp_path):
        _frames(tmp_path / "01_Scan" / "v001" / "EXR")

        request = build_request(self._shot(), tmp_path)

        assert request.media_paths(["scan", "cg"]) == request.media_paths(["scan"])


class TestHandingMediaToRV:

    def test_one_thing_plays_on_its_own(self):
        launcher = FakeLauncher()

        assert launch(["a.mov"], launcher=launcher)
        assert launcher.single == ["a.mov"]
        assert launcher.playlists == []

    def test_several_things_become_a_playlist_to_compare(self):
        launcher = FakeLauncher()

        assert launch(["a.mov", "b.mov"], launcher=launcher)
        assert launcher.playlists == [["a.mov", "b.mov"]]

    def test_nothing_to_play_is_not_an_error(self):
        launcher = FakeLauncher()

        assert launch([], launcher=launcher) is False

    def test_a_launcher_that_throws_is_reported_not_raised(self):
        class Broken:
            def launch_media(self, path):
                raise OSError("rv is not installed")

        assert launch(["a.mov"], launcher=Broken()) is False


class TestRVAcceptsSequences:
    """Most of the project is EXR sequences; the old check rejected them all."""

    def test_a_sequence_pattern_counts_as_present(self, tmp_path):
        _frames(tmp_path / "EXR")

        assert RVLauncher.media_exists(str(tmp_path / "EXR" / "SH010.%04d.exr"))

    def test_a_pattern_in_a_missing_folder_does_not(self, tmp_path):
        assert not RVLauncher.media_exists(str(tmp_path / "gone" / "x.%04d.exr"))

    def test_an_ordinary_file_still_has_to_exist(self, tmp_path):
        movie = tmp_path / "a.mov"
        movie.write_bytes(b"x")

        assert RVLauncher.media_exists(str(movie))
        assert not RVLauncher.media_exists(str(tmp_path / "b.mov"))


class TestReadingWhatRVWrote:

    def _write(self, tmp_path, **data):
        path = tmp_path / "rv_feedback.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_a_verdict_is_read(self, tmp_path):
        path = self._write(tmp_path, status="approved",
                           media_path="/x/SH010/07_Comp/a.mov", frame=1042)

        feedback = read_feedback(path)

        assert feedback.dashboard_status == "Approved"
        assert feedback.frame == 1042

    def test_rejected_means_retake(self, tmp_path):
        path = self._write(tmp_path, status="rejected", media_path="/x/a.mov")

        assert read_feedback(path).dashboard_status == "Retake"

    def test_an_empty_file_is_ignored(self, tmp_path):
        path = tmp_path / "rv_feedback.json"
        path.write_text("{}", encoding="utf-8")

        assert read_feedback(path) is None

    def test_a_corrupt_file_is_ignored(self, tmp_path):
        path = tmp_path / "rv_feedback.json"
        path.write_text("not json at all", encoding="utf-8")

        assert read_feedback(path) is None

    def test_a_missing_file_is_ignored(self, tmp_path):
        assert read_feedback(tmp_path / "nope.json") is None

    def test_a_note_with_no_verdict_is_still_worth_keeping(self, tmp_path):
        path = self._write(tmp_path, status="note", media_path="/x/a.mov",
                           note="left edge flickers")

        feedback = read_feedback(path)

        assert feedback is not None
        assert feedback.dashboard_status == ""

    def test_a_nonsense_frame_number_does_not_break_it(self, tmp_path):
        path = self._write(tmp_path, status="approved", media_path="/x/a.mov",
                           frame="not a number")

        assert read_feedback(path).frame is None


class TestFindingTheShot:

    def test_the_shot_whose_folder_the_media_sits_in(self):
        shots = [Shot(shot_name="SH010"), Shot(shot_name="SH020")]

        found = match_shot(shots, r"D:\Proj\05_Reels\ReelA\SH020\07_Comp\a.mov")

        assert found.shot_name == "SH020"

    def test_a_longer_shot_number_is_not_a_match(self):
        """SH010 and SH0100 are different shots; a substring match confuses them."""
        shots = [Shot(shot_name="SH010")]

        assert match_shot(shots, r"D:\Proj\05_Reels\ReelA\SH0100\07_Comp\a.mov") is None

    def test_the_reel_decides_when_two_reels_share_a_shot_name(self):
        shots = [
            Shot(shot_name="SH010", reel_episode="ReelA"),
            Shot(shot_name="SH010", reel_episode="ReelB"),
        ]

        found = match_shot(shots, r"D:\Proj\05_Reels\ReelB\SH010\07_Comp\a.mov")

        assert found.reel_episode == "ReelB"

    def test_an_ambiguous_match_is_refused_rather_than_guessed(self):
        shots = [
            Shot(shot_name="SH010", reel_episode="ReelA"),
            Shot(shot_name="SH010", reel_episode="ReelB"),
        ]

        assert match_shot(shots, r"D:\Elsewhere\SH010\a.mov") is None

    def test_no_shots_loaded_matches_nothing(self):
        assert match_shot([], "/x/SH010/a.mov") is None

    def test_an_empty_path_matches_nothing(self):
        assert match_shot([Shot(shot_name="SH010")], "") is None


class TestWhichDepartment:

    def test_a_comp_render_is_comp(self):
        assert department_from_path(r"D:\P\SH010\07_Comp\Output\a.mov") == "comp"

    def test_a_plate_is_the_scan(self):
        assert department_from_path(r"D:\P\SH010\01_Scan\v002\EXR\a.exr") == "scan"

    def test_something_outside_the_structure_is_nothing_in_particular(self):
        assert department_from_path(r"D:\Desktop\random.mov") == ""


class TestApplyingTheVerdict:

    def test_approving_sets_the_shot_and_its_department(self):
        shot = Shot(shot_name="SH010")
        feedback = RVFeedback(status="approved",
                              media_path=r"D:\P\SH010\07_Comp\Output\a.mov")

        assert apply_feedback(shot, feedback)
        assert shot.status == "Approved"
        assert shot.dept("comp").status == "Approved"

    def test_a_retake_on_the_comp_marks_the_comp(self):
        """A retake nobody's department owns is a retake nobody picks up."""
        shot = Shot(shot_name="SH010")
        feedback = RVFeedback(status="rejected",
                              media_path=r"D:\P\SH010\07_Comp\Output\a.mov")

        apply_feedback(shot, feedback)

        assert shot.status == "Retake"
        assert shot.dept("comp").status == "Retake"

    def test_a_verdict_on_the_plate_does_not_mark_a_department(self):
        shot = Shot(shot_name="SH010")
        feedback = RVFeedback(status="rejected",
                              media_path=r"D:\P\SH010\01_Scan\v001\EXR\a.exr")

        apply_feedback(shot, feedback)

        assert shot.status == "Retake"
        assert shot.dept("comp").status == ""

    def test_the_note_records_the_frame_it_was_given_on(self):
        shot = Shot(shot_name="SH010")
        feedback = RVFeedback(status="rejected", frame=1042,
                              note="left edge flickers",
                              media_path=r"D:\P\SH010\07_Comp\a.mov")

        apply_feedback(shot, feedback)

        text = shot.feedback_internal[0].text
        assert "frame 1042" in text
        assert "left edge flickers" in text

    def test_the_annotation_image_is_named_in_the_note(self):
        feedback = RVFeedback(status="rejected", frame=7, note="see this",
                              annotation_path=r"C:\Users\me\.slate\annotations\SH010_x_7.jpg",
                              media_path=r"D:\P\SH010\07_Comp\a.mov")

        assert "SH010_x_7.jpg" in note_text(feedback)

    def test_a_note_without_a_verdict_leaves_the_status_alone(self):
        shot = Shot(shot_name="SH010", status="WIP")
        feedback = RVFeedback(status="note", note="check the edge",
                              media_path=r"D:\P\SH010\07_Comp\a.mov")

        assert apply_feedback(shot, feedback)
        assert shot.status == "WIP"
        assert "check the edge" in shot.feedback_internal[0].text

    def test_notes_accumulate_rather_than_replace(self):
        shot = Shot(shot_name="SH010")
        media = r"D:\P\SH010\07_Comp\a.mov"

        apply_feedback(shot, RVFeedback(status="note", note="one", media_path=media))
        apply_feedback(shot, RVFeedback(status="note", note="two", media_path=media))

        assert len(shot.feedback_internal) == 2

    def test_the_note_says_it_came_from_rv(self):
        shot = Shot(shot_name="SH010")
        apply_feedback(shot, RVFeedback(status="approved",
                                        media_path=r"D:\P\SH010\07_Comp\a.mov"))

        assert shot.feedback_internal[0].source == "OpenRV"


class TestFilingTheAnnotation:
    """
    An annotated frame has to end up with the shot.

    RV writes it to the reviewer's own machine. Left there, an artist reads
    "see the annotation" and has nothing to open, because the picture is on
    somebody else's laptop.
    """

    def _reviewed(self, tmp_path, name="SH010_note_1042.jpg"):
        """A shot on disk and an annotation sitting in the reviewer's cache."""
        shot_root = tmp_path / "05_Reels" / "ReelA" / "SH010"
        (shot_root / "01_Scan" / "v001" / "EXR").mkdir(parents=True)

        cache = tmp_path / "reviewer_cache"
        cache.mkdir()
        image = cache / name
        image.write_bytes(b"annotated frame")

        shot = Shot(shot_name="SH010", reel_episode="ReelA", folder_paths={
            "scan": "05_Reels/ReelA/SH010/01_Scan",
            "annotation": "05_Reels/ReelA/SH010/00_Annotation",
        })
        feedback = RVFeedback(
            status="rejected", frame=1042, note="left edge flickers",
            annotation_path=str(image),
            media_path=str(shot_root / "07_Comp" / "Output" / "a.mov"),
        )
        return shot, feedback, shot_root

    def test_the_image_is_copied_into_the_shots_annotation_folder(self, tmp_path):
        shot, feedback, shot_root = self._reviewed(tmp_path)

        apply_feedback(shot, feedback, project_root=tmp_path)

        filed = shot_root / "00_Annotation" / "SH010_note_1042.jpg"
        assert filed.exists()
        assert filed.read_bytes() == b"annotated frame"

    def test_the_note_points_at_where_it_was_filed(self, tmp_path):
        """A bare filename tells an artist a picture exists and nothing more."""
        shot, feedback, shot_root = self._reviewed(tmp_path)

        apply_feedback(shot, feedback, project_root=tmp_path)

        text = shot.feedback_internal[0].text
        assert str(shot_root / "00_Annotation") in text

    def test_the_reviewers_own_copy_is_left_alone(self, tmp_path):
        """Copied, not moved - RV may still have the file open."""
        shot, feedback, _ = self._reviewed(tmp_path)
        original = Path(feedback.annotation_path)

        apply_feedback(shot, feedback, project_root=tmp_path)

        assert original.exists()

    def test_a_project_with_no_annotation_folder_configured_still_works(
            self, tmp_path):
        """
        Older projects have no "annotation" entry in their folder template.

        The folder is worked out from the shot root instead, so no migration is
        needed for a project that already exists.
        """
        shot, feedback, shot_root = self._reviewed(tmp_path)
        del shot.folder_paths["annotation"]

        apply_feedback(shot, feedback, project_root=tmp_path)

        assert (shot_root / "00_Annotation" / "SH010_note_1042.jpg").exists()

    def test_a_verdict_with_no_annotation_is_unaffected(self, tmp_path):
        shot = Shot(shot_name="SH010")
        feedback = RVFeedback(status="approved",
                              media_path=r"D:\P\SH010\07_Comp\a.mov")

        assert apply_feedback(shot, feedback, project_root=tmp_path)
        assert shot.status == "Approved"

    def test_an_annotation_that_has_gone_missing_does_not_break_the_verdict(
            self, tmp_path):
        shot, feedback, _ = self._reviewed(tmp_path)
        Path(feedback.annotation_path).unlink()

        assert apply_feedback(shot, feedback, project_root=tmp_path)
        assert shot.status == "Retake"

    def test_two_notes_on_the_same_frame_do_not_overwrite_each_other(
            self, tmp_path):
        shot, feedback, shot_root = self._reviewed(tmp_path)
        apply_feedback(shot, feedback, project_root=tmp_path)

        second = tmp_path / "reviewer_cache" / "second"
        second.mkdir()
        image2 = second / "SH010_note_1042.jpg"
        image2.write_bytes(b"a different drawing entirely")
        feedback2 = RVFeedback(
            status="rejected", frame=1042, note="and this too",
            annotation_path=str(image2),
            media_path=feedback.media_path,
        )
        apply_feedback(shot, feedback2, project_root=tmp_path)

        filed = sorted(p.name for p in (shot_root / "00_Annotation").iterdir())
        assert len(filed) == 2

    def test_the_same_image_filed_twice_is_not_duplicated(self, tmp_path):
        shot, feedback, shot_root = self._reviewed(tmp_path)

        apply_feedback(shot, feedback, project_root=tmp_path)
        feedback.annotation_path = str(tmp_path / "reviewer_cache"
                                       / "SH010_note_1042.jpg")
        apply_feedback(shot, feedback, project_root=tmp_path)

        assert len(list((shot_root / "00_Annotation").iterdir())) == 1
