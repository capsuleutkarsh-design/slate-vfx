"""
Two failsafes that used to fail quietly.

1.2  When the central database is unreachable the app falls back to a local
     file. It used to keep accepting edits that would never reach anyone, with
     an eight-second status message as the only warning.

2.3  Excel is the studio's backup. The save path discarded whether the backup
     actually happened, so a locked sheet meant "All changes have been saved"
     over a backup that was silently weeks stale.
"""

import pytest

from ut_vfx.core.domain import access
from ut_vfx.core.domain.access import OfflineError, is_offline_fallback
from ut_vfx.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler
from ut_vfx.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot


PROJECT = "OFFLINE_PRJ"


class _Status:
    """Stands in for the database manager's runtime status."""

    def __init__(self, mode, fallback):
        self._status = {"active_mode": mode, "fallback_used": fallback}

    def get_runtime_status(self):
        return self._status


def _set_offline(monkeypatch, value: bool):
    """
    Force the offline check both places it is consumed.

    The handler binds the name at import time, so patching the access module
    alone would not reach it.
    """
    from ut_vfx.gui.tabs.vfx_dashboard_pro.core import sqlite_handler

    monkeypatch.setattr(access, "is_offline_fallback", lambda: value)
    monkeypatch.setattr(sqlite_handler, "is_offline_fallback", lambda: value)


@pytest.fixture
def offline(monkeypatch):
    """Pretend the central database went away."""
    _set_offline(monkeypatch, True)
    yield


@pytest.fixture
def online(monkeypatch):
    _set_offline(monkeypatch, False)
    yield


class TestDetectingAnOutage:
    """These exercise the real detection, so they stub the status it reads."""

    def test_fallback_is_detected(self, monkeypatch):
        import ut_vfx.core.infra.database_manager as dbm
        monkeypatch.setattr(dbm, "database_manager", _Status("sqlite", True))
        assert is_offline_fallback() is True

    def test_a_healthy_connection_is_not_an_outage(self, monkeypatch):
        import ut_vfx.core.infra.database_manager as dbm
        monkeypatch.setattr(dbm, "database_manager", _Status("postgres", False))
        assert is_offline_fallback() is False

    def test_a_studio_running_on_sqlite_on_purpose_is_not_offline(self, monkeypatch):
        """Choosing SQLite is not the same as losing the server."""
        import ut_vfx.core.infra.database_manager as dbm
        monkeypatch.setattr(dbm, "database_manager", _Status("sqlite", False))
        assert is_offline_fallback() is False

    def test_an_unreadable_status_is_not_treated_as_an_outage(self, monkeypatch):
        """A detection failure must not lock everybody out of the tool."""
        class Broken:
            def get_runtime_status(self):
                raise RuntimeError("no status")

        import ut_vfx.core.infra.database_manager as dbm
        monkeypatch.setattr(dbm, "database_manager", Broken())
        assert is_offline_fallback() is False


class TestWritesAreRefusedWhileOffline:
    """The write path refuses on its own, whatever the interface allows."""

    def test_saving_a_shot_is_refused(self, mock_db, offline):
        handler = SQLiteHandler(PROJECT, db_manager=mock_db, user_role="supervisor")
        with pytest.raises(OfflineError):
            handler.write_shots([Shot(shot_name="SH010", reel_episode="ReelA")])

    def test_editing_a_field_is_refused(self, mock_db, offline):
        handler = SQLiteHandler(PROJECT, db_manager=mock_db, user_role="supervisor")
        with pytest.raises(OfflineError):
            handler.update_shot_field("SH010", "status", "WIP", 1)

    def test_reading_still_works(self, mock_db, offline):
        """People can look at the board during an outage; they just cannot change it."""
        handler = SQLiteHandler(PROJECT, db_manager=mock_db, user_role="supervisor")
        assert handler.read_shots() == []

    def test_writing_resumes_when_the_connection_returns(self, mock_db, online):
        handler = SQLiteHandler(PROJECT, db_manager=mock_db, user_role="supervisor")
        assert handler.write_shots([Shot(shot_name="SH010", reel_episode="ReelA")]) is True


class TestTheDashboardShowsTheOutage:

    def _widget(self, qtbot):
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.dashboard_widget import DashboardWidget

        widget = DashboardWidget(user_data={
            "username": "coord", "display_name": "Coordinator",
            "roles": ["Supervisor"],
        })
        qtbot.addWidget(widget)
        return widget

    def test_banner_hidden_and_editing_allowed_when_healthy(self, qtbot, mock_db, online):
        widget = self._widget(qtbot)
        widget.refresh_connection_state()

        assert widget.offline_banner.isVisible() is False
        assert widget._user_can_edit() is True
        assert widget.save_btn.isEnabled() is True

    def test_banner_shown_and_save_disabled_during_an_outage(self, qtbot, mock_db, offline):
        widget = self._widget(qtbot)
        widget.show()
        widget.refresh_connection_state()

        assert widget.offline_banner.isVisible() is True
        assert widget._user_can_edit() is False
        assert widget.save_btn.isEnabled() is False
        assert "unreachable" in widget.save_btn.toolTip().lower()

    def test_the_banner_explains_why(self, qtbot, mock_db, offline):
        widget = self._widget(qtbot)
        text = widget.offline_label.text().lower()
        assert "read-only" in text
        assert "would not reach" in text

    def test_there_is_a_way_to_try_again(self, qtbot, mock_db, offline):
        widget = self._widget(qtbot)
        assert callable(widget.retry_connection)
        assert widget.offline_retry_btn is not None


class TestExcelBackupHealth:

    def _service(self):
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.dashboard_sync_service import (
            DashboardSyncService,
        )

        class _Projects:
            def get_excel_path(self, code):
                return ""      # no sheet configured

            def ensure_excel_path(self, code):
                return ""      # and nowhere to create one

        return DashboardSyncService(_Projects())

    def test_a_missing_sheet_is_reported_not_swallowed(self):
        """
        A project with no passbook normally gets one created. When there is
        nowhere to create it, that failure must be recorded, not swallowed.
        """
        service = self._service()

        class _Project:
            code = "PRJ"

        ok, _ = service.mirror_shots_to_excel(
            shots=[Shot(shot_name="SH010")], current_project=_Project(),
            data_handler=None, force=True,
        )
        assert ok is False
        assert service.last_backup_error, "the failure was not recorded"

    def test_a_fresh_service_has_no_successful_backup_yet(self):
        service = self._service()
        assert service.last_backup_at is None
        assert service.last_backup_error is None

    def test_the_indicator_reflects_a_failure(self, qtbot, mock_db, online):
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.dashboard_widget import DashboardWidget

        widget = DashboardWidget(user_data={
            "username": "coord", "roles": ["Supervisor"],
        })
        qtbot.addWidget(widget)

        widget.sync_service.last_backup_error = "The file is open in Excel."
        widget.update_backup_indicator()

        assert "FAILING" in widget.backup_label.text()
        assert "open in Excel" in widget.backup_label.toolTip()

    def test_the_indicator_reflects_a_success(self, qtbot, mock_db, online):
        from datetime import datetime
        from ut_vfx.gui.tabs.vfx_dashboard_pro.ui.dashboard_widget import DashboardWidget

        widget = DashboardWidget(user_data={
            "username": "coord", "roles": ["Supervisor"],
        })
        qtbot.addWidget(widget)

        widget.sync_service.last_backup_error = None
        widget.sync_service.last_backup_at = datetime.now()
        widget.update_backup_indicator()

        assert "FAILING" not in widget.backup_label.text()
        assert "Excel backup:" in widget.backup_label.text()
