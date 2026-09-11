"""
The dialog that asks a coordinator to confirm a stitch.

Merging is a guess, so what matters here is that the person's answer is what
reaches the ingest: ticked groups merge, unticked ones do not, cancelling stops
the run, and a corrected shot name is the one used.
"""

import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ut_vfx.core.domain.stitch_detect import StitchGroup
from ut_vfx.gui.dialogs.stitch_confirm_dialog import StitchConfirmDialog


@pytest.fixture(scope="session")
def qapp_stitch():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def _groups():
    return [
        StitchGroup(shot_name="SH010", parts=["SH010_A", "SH010_B"], reel="ReelA"),
        StitchGroup(shot_name="SH020", parts=["SH020_L", "SH020_R"], reel="ReelA"),
    ]


class TestWhatTheAnswerMeans:

    def test_everything_is_offered_as_a_merge_by_default(self, qapp_stitch, qtbot):
        dialog = StitchConfirmDialog(_groups())
        qtbot.addWidget(dialog)

        assert dialog.mapping() == {
            ("ReelA", "SH010_A"): "SH010",
            ("ReelA", "SH010_B"): "SH010",
            ("ReelA", "SH020_L"): "SH020",
            ("ReelA", "SH020_R"): "SH020",
        }

    def test_unticking_a_group_leaves_those_folders_alone(self, qapp_stitch, qtbot):
        dialog = StitchConfirmDialog(_groups())
        qtbot.addWidget(dialog)

        dialog.tree.topLevelItem(1).setCheckState(0, Qt.Unchecked)

        assert dialog.mapping() == {
            ("ReelA", "SH010_A"): "SH010",
            ("ReelA", "SH010_B"): "SH010",
        }

    def test_keep_all_separate_merges_nothing(self, qapp_stitch, qtbot):
        dialog = StitchConfirmDialog(_groups())
        qtbot.addWidget(dialog)

        dialog._untick_all()

        assert dialog.mapping() == {}

    def test_a_corrected_shot_name_is_the_one_used(self, qapp_stitch, qtbot):
        dialog = StitchConfirmDialog(_groups())
        qtbot.addWidget(dialog)

        _, _, name_edit = dialog._rows[0]
        name_edit.setText("EP01_SH010")

        assert dialog.mapping()[("ReelA", "SH010_A")] == "EP01_SH010"

    def test_a_blank_name_falls_back_to_the_suggestion(self, qapp_stitch, qtbot):
        dialog = StitchConfirmDialog(_groups())
        qtbot.addWidget(dialog)

        _, _, name_edit = dialog._rows[0]
        name_edit.setText("   ")

        assert dialog.mapping()[("ReelA", "SH010_A")] == "SH010"


class TestWhatIsShown:

    def test_each_group_gets_a_row_naming_its_parts(self, qapp_stitch, qtbot):
        dialog = StitchConfirmDialog(_groups())
        qtbot.addWidget(dialog)

        assert dialog.tree.topLevelItemCount() == 2
        first = dialog.tree.topLevelItem(0)
        assert first.text(1) == "ReelA"
        assert "SH010_A" in first.text(2) and "SH010_B" in first.text(2)

    def test_the_differing_tail_is_spelled_out(self, qapp_stitch, qtbot):
        dialog = StitchConfirmDialog(
            [StitchGroup(shot_name="SH010", parts=["SH010_left", "SH010_right"],
                         reel="ReelA")]
        )
        qtbot.addWidget(dialog)

        text = dialog.tree.topLevelItem(0).text(2)
        assert "(left)" in text and "(right)" in text
