"""
Unit tests for separated VFX Home and Operations Home tabs.
"""
import pytest
from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication

from slate.gui.tabs.home_tab import HomeTab, VfxHomeTab, OpsHomeTab


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


class TestSeparatedHomeTabs:
    """Tests verifying complete separation of VFX Home and Operations Home."""

    def test_vfx_home_tab_has_no_attendance(self, qapp, qtbot):
        """Verify VFX Home tab contains zero attendance widgets or punch actions."""
        user_data = {"username": "vfx_artist", "display_name": "VFX Artist"}
        tab = VfxHomeTab(user_data=user_data)
        qtbot.addWidget(tab)

        assert tab.mode == "vfx"
        assert tab.attendance is None
        assert not hasattr(tab, "attendance_panel")
        assert not hasattr(tab, "btn_punch_in")
        assert not hasattr(tab, "btn_punch_out")
        assert not hasattr(tab, "lbl_punch_status")

    def test_ops_home_tab_has_biometric_attendance(self, qapp, qtbot):
        """Verify Operations Home tab includes attendance panel and punch controls."""
        user_data = {"username": "hr_manager", "display_name": "HR Manager"}
        tab = OpsHomeTab(user_data=user_data)
        qtbot.addWidget(tab)

        assert tab.mode == "ops"
        assert hasattr(tab, "attendance_panel")
        assert hasattr(tab, "btn_punch_in")
        assert hasattr(tab, "btn_punch_out")
        assert hasattr(tab, "lbl_punch_status")
        assert tab.btn_punch_in.text() == "PUNCH IN"
        assert tab.btn_punch_out.text() == "PUNCH OUT"

    def test_home_tab_mode_parameter(self, qapp, qtbot):
        """Verify HomeTab defaults to vfx and switches cleanly based on mode parameter."""
        tab_vfx = HomeTab(mode="vfx")
        qtbot.addWidget(tab_vfx)
        assert tab_vfx.mode == "vfx"
        assert not hasattr(tab_vfx, "attendance_panel")

        tab_ops = HomeTab(mode="ops")
        qtbot.addWidget(tab_ops)
        assert tab_ops.mode == "ops"
        assert hasattr(tab_ops, "attendance_panel")

    @patch('slate.core.domain.central_attendance.CentralAttendance.log_action')
    def test_ops_home_punch_action(self, mock_log_action, qapp, qtbot):
        """Verify punch action logs attendance in Operations Home."""
        user_data = {"username": "test_ops", "display_name": "Test Ops"}
        tab = OpsHomeTab(user_data=user_data)
        qtbot.addWidget(tab)

        tab.do_punch("in")
        mock_log_action.assert_called_with("Test Ops", "in")

        tab.do_punch("out")
        mock_log_action.assert_called_with("Test Ops", "out")
