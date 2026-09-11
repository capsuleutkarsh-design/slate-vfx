import pytest


pytestmark = pytest.mark.skip(
    reason="VideoEditorWidget was removed from the product; Olive is used instead."
)


def test_video_editor_widget_removed_placeholder():
    assert True
