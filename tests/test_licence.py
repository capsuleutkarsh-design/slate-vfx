"""The licence credits (UT Community Licence 2.0, section 5) and the startup check that enforces them."""
import os
import shutil

import pytest
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from slate import licence

ROOT = licence.program_dir()


def test_the_real_files_pass():
    assert licence.file_problems() == []


def test_line_endings_do_not_matter(tmp_path):
    with open(os.path.join(ROOT, "LICENSE.md"), encoding="utf-8") as f:
        text = f.read()
    with open(tmp_path / "LICENSE.md", "w", encoding="utf-8", newline="\r\n") as f:
        f.write(text)
    shutil.copy(os.path.join(ROOT, "THIRD_PARTY_NOTICES.md"), tmp_path)
    assert licence.file_problems(str(tmp_path)) == []


def test_changed_or_missing_files_are_caught(tmp_path):
    assert len(licence.file_problems(str(tmp_path))) == 2
    with open(os.path.join(ROOT, "LICENSE.md"), encoding="utf-8") as f:
        text = f.read()
    (tmp_path / "LICENSE.md").write_text(text.replace("No selling", "Selling"), encoding="utf-8")
    notices = open(os.path.join(ROOT, "THIRD_PARTY_NOTICES.md"), encoding="utf-8").read()
    (tmp_path / "THIRD_PARTY_NOTICES.md").write_text(notices.replace(licence.ICONS8_CREDIT, ""), encoding="utf-8")
    assert licence.file_problems(str(tmp_path)) == ["LICENSE.md has been changed",
                                                    "THIRD_PARTY_NOTICES.md has been changed"]


def test_the_licence_names_the_same_credit_line():
    with open(os.path.join(ROOT, "LICENSE.md"), encoding="utf-8") as f:
        assert licence.CREDIT_LINE in f.read()


def test_plain_text_licence_for_the_installer():
    text = licence.plain_text(os.path.join(ROOT, "LICENSE.md"))
    assert "<div" not in text and "**" not in text
    assert "No selling" in text and licence.CREDIT_LINE in text


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _window(label):
    w = QWidget()
    QVBoxLayout(w).addWidget(label)
    return w


def test_credit_label_passes_and_hidden_or_changed_fails(app):
    assert licence.window_problems(_window(licence.credit_label())) == []
    hidden = licence.credit_label()
    w = _window(hidden)
    hidden.hide()
    assert licence.window_problems(w)
    assert licence.window_problems(_window(QLabel(licence.CREDIT_LINE.replace("Utkarsh Tripathi", "X"))))


def test_a_theme_that_sizes_text_in_pixels_still_passes(app):
    w = _window(licence.credit_label())
    w.setStyleSheet("QLabel { font-size: 11px; }")
    assert licence.window_problems(w) == []
    w.setStyleSheet("QLabel { font-size: 2px; }")      # shrunk until unreadable
    assert licence.window_problems(w)


def test_studio_and_ops_footer_carries_the_credit_line(app):
    from slate.gui.components.main_window_builder import MainWindowBuilderMixin
    footer = MainWindowBuilderMixin.create_footer(None)
    assert licence.window_problems(footer) == []
