
import pytest
from PySide6.QtWidgets import QLabel
from ut_vfx.gui.main_window import VFXFolderCreatorApp

@pytest.fixture
def app(qtbot, mock_db):
    """Fixture to create the app with mocked DB and User Manager."""
    # Mocking UserManager to return a specific user with a profile pic
    # Note: We can't easily mock the internal UserManager of the App class 
    # without dependency injection or patching, but we can check the default state.
    widget = VFXFolderCreatorApp()
    widget.show()
    qtbot.addWidget(widget)
    return widget

def test_header_structure(app, qtbot):
    """Header shows branding. The workflow mode selector was removed along with
    the Standard / Incoming Delivery modes - Auto-Scan is the only workflow."""
    app.findChild(QLabel, "appLogo").parent() # Get the header GroupBox

    logo = app.findChild(QLabel, "appLogo")
    assert logo is not None
    # The wordmark. The product was renamed from UT_VFX, whose "UT" carried the
    # owner's initials; those now live inside the mark's geometry instead.
    assert logo.text() == "SLATE"

    assert not hasattr(app, "mode_selector")

def test_avatar_rendering(app, qtbot):
    """Test that the avatar label is present and has a pixmap."""
    # Find by ObjectName we just added
    avatar_label = app.findChild(QLabel, "userAvatar")
            
    assert avatar_label is not None
    assert avatar_label.pixmap() is not None
    assert not avatar_label.pixmap().isNull()
