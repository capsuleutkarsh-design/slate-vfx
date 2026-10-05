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
