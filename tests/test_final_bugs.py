"""
The last bugs before rollout (done_final_bugs.md). One check per item.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_reconfigure_writes_the_file_setup_bat_wrote(monkeypatch, tmp_path):
    """
    Item 1. Reconfigure saved the server with GlobalConfig.set (LOCALAPPDATA),
    and slate/config.json from setup.bat, read last, put the old one back at
    the next start.
    """
    from slate.core.infra import local_secrets
    from slate.core.infra.global_config import GlobalConfig

    package = tmp_path / "slate"
    package.mkdir()
    setup_bat = package / "config.json"
    setup_bat.write_text(json.dumps({"db_host": "old-server", "db_password": "kept"}))
    monkeypatch.setattr(local_secrets, "_PACKAGE", package)
    monkeypatch.setattr(local_secrets, "_ROOT", tmp_path)

    config = object.__new__(GlobalConfig)
    config.data = {}
    config.config_path = tmp_path / "appdata" / "config.json"
    monkeypatch.setattr(GlobalConfig, "_instance", config)

    GlobalConfig.save_connection({"SERVER_ROOT": str(tmp_path), "db_host": "new-server",
                                  "db_port": 5544, "db_name": "slate", "db_user": "ut_vfx_app",
                                  "db_password": ""})

    written = json.loads(setup_bat.read_text())
    assert written["db_host"] == "new-server" and written["db_port"] == 5544
    assert written["db_password"] == "kept", "a blank password box must not wipe the password"
    assert GlobalConfig.get("db_host") == "new-server"
    assert GlobalConfig.get("SERVER_ROOT") == str(tmp_path)


def test_roles_editor_shows_what_access_json_gives_by_name(mock_db, qapp):
    """
    Item 3. Abilities access.json gives a role by its name showed unticked,
    and unticking one did nothing. They show ticked, locked, and say why;
    saving does not copy them into the role's own ticks.
    """
    from slate.core.domain import access
    from slate.core.domain.user_manager import UserManager
    from slate.gui.role_editor import RoleEditor
    from slate.gui.tester_panel import TesterPanel

    access.reset_cache()
    users = UserManager(db=mock_db)
    users.update_role_permissions("Lead", ["Settings"])
    editor = RoleEditor(users)
    editor.refresh_roles(select="Lead")
    box = editor.ability_boxes["dashboard_write"]
    assert box.isChecked() and not box.isEnabled()
    assert "access.json" in box.toolTip()

    editor.tab_boxes["Bidding"].setChecked(True)
    assert editor.save_changes()
    assert "can:dashboard_write" not in users.role_permissions("Lead")

    (row,) = [r for r in TesterPanel.permission_rows({"Lead": ["Settings"]})]
    assert "Edit the dashboard" in row[2]
    access.reset_cache()


def test_server_comp_off_job_runs_on_the_studio_database(monkeypatch, tmp_path):
    """
    Item 4. Slate Server's daily comp-off job never loaded the studio policy
    (comp off always looked switched off) and used the client database
    manager. It now reads the studio's rules from the studio database,
    credits there, and lapses expired comp off in the same run.
    """
    from slate.core.domain import leave_policy as lp
    from slate_server.core.maintenance import Maintenance

    asked = []

    class StudioDb:
        conn = type("Conn", (), {"close": lambda self: asked.append("closed")})()

        def execute_query(self, sql, params=None, fetch="all", strict=False):
            asked.append(sql)
            if "studio_settings" in sql:
                return [{"key": "attendance_policy", "value": '{"comp_off_enabled": true}'}]
            return []

        def execute_update(self, sql, params=None):
            return True

    monkeypatch.setattr(lp, "_OVERRIDES", {})
    jobs = Maintenance(tmp_path / "bin", tmp_path / "data", port=55999, dbname="studio")
    monkeypatch.setattr(jobs, "_studio_db", StudioDb)
    result = jobs.credit_comp_off()
    assert result["ok"] and not result.get("skipped"), result
    assert any("FROM attendance_log" in a for a in asked), "the attendance record was never reviewed"
    assert any("expires_on IS NOT NULL" in a for a in asked), "expired comp off was never lapsed"
    assert asked[-1] == "closed"


def test_clear_temporary_files_keeps_the_caches(monkeypatch, tmp_path):
    """
    Item 5. The sweeper (every 5 minutes, and "Clear temporary files")
    emptied Slate\Cache of anything a day old: thumbnails, local-only
    proxies, the RV playlist. Only temporary files go now.
    """
    import os
    import time
    from slate.core.services.sweepers import temp_sweeper

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(temp_sweeper.tempfile, "gettempdir", lambda: str(temp))
    cache = tmp_path / "appdata" / "Slate" / "Cache" / "Proxies"
    cache.mkdir(parents=True)
    kept = [cache / "sh010_v001.jpg", cache / "sh010_v001.mp4", cache.parent / "rv_temp.rv",
            cache.parent / "Library_Cache.caplib", cache / "sh020.partial-notes.txt"]
    gone = [cache / "sh010_v002.part4120-7788.mp4", temp / "slate-frames-4120-99.txt"]
    fresh = cache / "sh010_v003.part4120-7788.jpg"
    day_old = time.time() - 2 * 86400
    for path in kept + gone + [fresh]:
        path.write_bytes(b"x")
        if path is not fresh:
            os.utime(path, (day_old, day_old))

    result = temp_sweeper.TempFileSweeper(max_age_days=1).run()
    assert result["files_deleted"] == 2 and not result["errors"]
    assert all(p.exists() for p in kept + [fresh]) and not any(p.exists() for p in gone)


def test_users_and_roles_is_offered_only_to_who_can_use_it(mock_db):
    """
    Item 6. The sidebar offered Users & Roles to a role holding only the HRMS
    tab key; it opened onto "You do not have permission".
    """
    from slate.core.domain import access
    from slate.core.domain.user_manager import UserManager

    access.reset_cache()
    UserManager(db=mock_db).update_role_permissions("Payroll", ["HRMS"])
    assert not access.opens_users_and_roles(["Payroll"])
    assert access.opens_users_and_roles(["HR"]) and access.opens_users_and_roles(["IT"])
    access.reset_cache()


def test_export_to_excel_on_an_excel_only_project_says_so(monkeypatch):
    """Item 6. It said "Exported N shot(s)" having written nothing."""
    from types import SimpleNamespace
    from slate.gui.tabs.vfx_dashboard_pro.core.excel_handler import ExcelHandler
    from slate.gui.tabs.vfx_dashboard_pro.ui.components.dashboard_actions_mixin import (
        DashboardActionsMixin)

    said, mirrored = [], []
    widget = SimpleNamespace(
        _excel_allowed=lambda: True, current_project=object(), all_shots=[object()],
        data_handler=object.__new__(ExcelHandler),
        _notify=lambda text, *a, **k: said.append(text),
        _mirror_shots_to_excel=lambda *a, **k: mirrored.append(a))
    DashboardActionsMixin.export_to_excel_click(widget)
    assert not mirrored and "kept in its Excel file" in said[-1]


def test_the_studio_logo_is_one_for_the_studio(monkeypatch):
    """
    Item 6. The logo sat on a studio card but was saved per workstation. It is
    a studio setting now; a PC's old value shows until the studio saves one.
    """
    from slate.core.infra import database_manager, studio_settings

    monkeypatch.setattr(database_manager, "is_connected", lambda: True)
    monkeypatch.setattr(studio_settings, "get_setting",
                        lambda key, default=None, db=None: "\\server\brand\logo.png")
    assert studio_settings.studio_logo("C:/old/logo.png") == "\\server\brand\logo.png"
    monkeypatch.setattr(studio_settings, "get_setting", lambda key, default=None, db=None: "")
    assert studio_settings.studio_logo("C:/old/logo.png") == "C:/old/logo.png"
    assert studio_settings.VALIDATORS["branding_logo_path"]("  x.png ") == "x.png"


def test_a_sidebar_heading_with_nothing_under_it_is_hidden(qtbot):
    """Item 6. Ops users with neither Users & Roles nor Admin Panel saw an empty ADMINISTRATION."""
    from PySide6.QtWidgets import QListWidget, QStackedWidget, QWidget
    from slate.gui.components.tab_coordinator import TabCoordinator

    window = QWidget()
    qtbot.addWidget(window)
    nav, stack = QListWidget(window), QStackedWidget(window)
    tabs = TabCoordinator(window, nav, stack)
    tabs.add_category_header("PEOPLE")
    tabs.register_tab_factory("Leave", QWidget, permission_key=None)
    tabs.add_category_header("ADMINISTRATION")
    tabs.register_tab_factory("Admin Panel", QWidget, permission_key="Admin Panel",
                              user_role="HR", allowed_tabs=["HRMS"])
    people, admin = (nav.item(g["header_row"]) for g in tabs.groups)
    assert not people.isHidden() and admin.isHidden()


def test_a_dashboard_thumbnail_cut_short_leaves_no_stump(monkeypatch, tmp_path):
    """
    Item 7. ffmpeg wrote the dashboard thumbnail straight to its final name,
    so a run stopped by the 30 s timeout left a stump taken as made for ever.
    """
    import subprocess
    from slate.gui.tabs.vfx_dashboard_pro.utils import thumbnail

    gen = object.__new__(thumbnail.ThumbnailGenerator)
    gen.ffmpeg_path = "ffmpeg"
    gen._is_gui_thread = lambda: False          # it runs on a worker in Slate
    final = tmp_path / "thumb.jpg"

    def cut_short(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"\xff\xd8 half a jpeg")
        raise subprocess.TimeoutExpired(cmd, 30)
    monkeypatch.setattr(thumbnail.subprocess, "run", cut_short)
    assert not gen.generate_with_ffmpeg("plate.exr", str(final))
    assert list(tmp_path.iterdir()) == []

    monkeypatch.setattr(thumbnail.subprocess, "run",
                        lambda cmd, **kw: Path(cmd[-1]).write_bytes(b"\xff\xd8 whole"))
    assert gen.generate_with_ffmpeg("plate.exr", str(final))
    assert list(tmp_path.iterdir()) == [final]


def test_signing_in_on_approved_leave_is_not_a_punch_in(mock_db, monkeypatch):
    """Item 12 (owner's decision). The punch-in at sign-in skips a day of approved full-day leave."""
    from slate.core.domain.central_attendance import CentralAttendance

    att = CentralAttendance(mock_db)
    monkeypatch.setattr(CentralAttendance, "leave_today", lambda self, user: {"type": "Sick", "half": ""})
    assert att.log_action("asha", "in", automatic=True) is None
    assert att.today_state("asha")["state"] == "out"
    monkeypatch.setattr(CentralAttendance, "leave_today", lambda self, user: None)
    assert att.log_action("asha", "in", automatic=True)["session"] == 1


def test_a_leads_department_comes_from_their_own_record(mock_db):
    """
    Item 12 (owner's decision). The shot handler confined a lead to whatever
    department the screen passed in. It reads the person's own ut_users
    record now, so a screen cannot widen it.
    """
    import pytest
    from slate.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler
    from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot
    from tests.dashboard_util import open_project, person

    open_project(mock_db, "PRJ")
    shot = Shot(shot_name="SH010", reel_episode="R1", status="WIP")
    assert SQLiteHandler("PRJ", db_manager=mock_db, user_role="supervisor").write_shots([shot])
    lead = SQLiteHandler("PRJ", db_manager=mock_db, user_role="lead",
                         username=person(mock_db, "rl", "Roto Lead"))
    mine = lead.read_shots()[0]
    mine.dept("comp").status = "WIP"
    with pytest.raises(PermissionError):
        lead.write_shots([mine])
    mine = lead.read_shots()[0]
    mine.dept("roto").status = "WIP"
    assert lead.write_shots([mine])


def test_a_projects_frame_rate_reaches_the_lineup_and_the_edl(tmp_path):
    """
    Item 10. Image sequences were 24 fps everywhere. The project's rate
    (ProjectConfig.fps, default 24) now sets the lineup entry and the EDL
    timebase; a movie keeps its own.
    """
    from slate.core.domain import lineup
    from slate.gui.tabs.vfx_dashboard_pro.core.project_manager import ProjectConfig

    assert ProjectConfig(code="P", name="P").fps == 24.0
    assert lineup.project_fps(ProjectConfig(code="P", name="P", fps=25)) == 25.0
    assert lineup.project_fps({"fps": None}) == lineup.project_fps(None) == 24.0

    from slate.core.domain.shot_media import MediaClip
    plate = MediaClip(path=tmp_path / "SH010.%04d.exr", is_sequence=True,
                      first_frame=1001, last_frame=1048)
    assert lineup.plate_facts(plate, 25.0) == (25.0, 0)
    entry = lineup.LineupShot(name="SH010", fps=lineup.plate_facts(plate, 25.0)[0],
                              frame_range=(1001, 1048), clips={"scan": plate})
    assert lineup.lineup_fps([entry]) == 25.0
    # Frame 1001 is 40 s 1 frame at 25 fps (it was written as 41 s 17 frames, at 24).
    assert "00:00:40:01" in lineup.edl_text([entry], "P")
