"""
Spotting a stitch without fusing two real shots.

The suffix varies by project, so detection works on shape rather than a list of
known endings. The dangerous failure is a false positive - merging SH010 and
SH011 into one shot would be far worse than missing a stitch - so most of these
tests are about what must NOT be grouped.
"""

import pytest

from slate.core.domain.stitch_detect import (
    StitchGroup, find_stitch_groups, group_by_reel,
)


def _grouped(names):
    return {g.shot_name: g.parts for g in find_stitch_groups(names)}


class TestRealStitches:
    """Shapes a client actually delivers."""

    def test_letter_parts(self):
        assert _grouped(["SH010_A", "SH010_B"]) == {"SH010": ["SH010_A", "SH010_B"]}

    def test_left_and_right(self):
        assert _grouped(["SH010_left", "SH010_right"]) == {
            "SH010": ["SH010_left", "SH010_right"]
        }

    def test_numbered_parts(self):
        assert _grouped(["SH010_pt1", "SH010_pt2"]) == {
            "SH010": ["SH010_pt1", "SH010_pt2"]
        }

    def test_background_and_foreground(self):
        assert _grouped(["SH010_bg", "SH010_fg"]) == {
            "SH010": ["SH010_bg", "SH010_fg"]
        }

    def test_three_parts(self):
        result = _grouped(["SH010_A", "SH010_B", "SH010_C"])
        assert result == {"SH010": ["SH010_A", "SH010_B", "SH010_C"]}

    def test_whole_plus_a_part(self):
        """The shot delivered whole, with an extra element alongside."""
        assert _grouped(["SH010", "SH010_bg"]) == {"SH010": ["SH010", "SH010_bg"]}

    def test_hyphens_work_like_underscores(self):
        assert _grouped(["SH010-A", "SH010-B"]) == {"SH010": ["SH010-A", "SH010-B"]}

    def test_longer_shot_names(self):
        assert _grouped(["EP01_SH010_A", "EP01_SH010_B"]) == {
            "EP01_SH010": ["EP01_SH010_A", "EP01_SH010_B"]
        }


class TestMustNotGroup:
    """False positives are the expensive mistake."""

    def test_consecutive_shot_numbers(self):
        assert find_stitch_groups(["SH010", "SH011"]) == []

    def test_shots_ten_apart(self):
        assert find_stitch_groups(["SH010", "SH020", "SH030"]) == []

    def test_a_longer_shot_number_is_not_a_part(self):
        assert find_stitch_groups(["SH010", "SH0100"]) == []

    def test_names_without_a_number_are_left_alone(self):
        """'plate_one' and 'plate_two' are not obviously one shot."""
        assert find_stitch_groups(["plate_one", "plate_two"]) == []

    def test_a_long_tail_is_not_a_part_marker(self):
        assert find_stitch_groups(
            ["SH010_originalplate", "SH010_replacementplate"]
        ) == []

    def test_two_extra_tokens_is_too_uncertain(self):
        assert find_stitch_groups(["SH010_A_v2", "SH010_B"]) == []

    def test_a_single_folder_groups_with_nothing(self):
        assert find_stitch_groups(["SH010_A"]) == []

    def test_identical_names_are_not_a_stitch(self):
        assert find_stitch_groups(["SH010", "SH010"]) == []

    def test_empty_input(self):
        assert find_stitch_groups([]) == []
        assert find_stitch_groups(["", "  "]) == []


class TestMixedDeliveries:
    """A real drive has stitches and ordinary shots together."""

    def test_only_the_stitch_is_grouped(self):
        names = ["SH010_A", "SH010_B", "SH020", "SH030"]
        assert _grouped(names) == {"SH010": ["SH010_A", "SH010_B"]}

    def test_two_separate_stitches(self):
        names = ["SH010_A", "SH010_B", "SH020_L", "SH020_R"]
        assert _grouped(names) == {
            "SH010": ["SH010_A", "SH010_B"],
            "SH020": ["SH020_L", "SH020_R"],
        }

    def test_grouping_never_crosses_a_reel(self):
        """SH010 in ReelA and SH010 in ReelB are different shots."""
        groups = group_by_reel({
            "ReelA": ["SH010_A", "SH010_B"],
            "ReelB": ["SH010_A", "SH010_B"],
        })
        assert len(groups) == 2
        assert {g.reel for g in groups} == {"ReelA", "ReelB"}
        for group in groups:
            assert group.parts == ["SH010_A", "SH010_B"]


class TestWhatThePersonSees:
    """The coordinator has to be able to judge the suggestion at a glance."""

    def test_part_labels_show_the_differing_tail(self):
        group = find_stitch_groups(["SH010_left", "SH010_right"])[0]
        assert group.part_labels == ["left", "right"]

    def test_a_whole_plate_is_labelled_as_such(self):
        group = find_stitch_groups(["SH010", "SH010_bg"])[0]
        assert "(whole)" in group.part_labels

    def test_the_description_reads_plainly(self):
        group = find_stitch_groups(["SH010_A", "SH010_B"])[0]
        assert group.describe() == "SH010_A + SH010_B  ->  SH010"


class TestMapping:
    def test_every_part_maps_to_the_merged_shot(self):
        from slate.core.domain.stitch_detect import apply_groups

        groups = find_stitch_groups(["SH010_A", "SH010_B"])
        mapping = apply_groups([], groups)

        assert mapping == {"SH010_A": "SH010", "SH010_B": "SH010"}

    def test_no_groups_means_no_mapping(self):
        from slate.core.domain.stitch_detect import apply_groups

        assert apply_groups([], []) == {}
