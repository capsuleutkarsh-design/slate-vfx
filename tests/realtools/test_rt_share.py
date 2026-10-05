"""
The studio share (SERVER_ROOT) on a real temp folder:

    Commands      ServerHub.post_command  ->  gatekeeper's CommandCheckWorker
    Updates       a real release zip + manifest  ->  UpdateChecker, SidecarEngine,
                  and the updater's swap into a sandbox install folder
    Logs/Audit    AuditLogger and the Admin Panel log  ->  the Audit Logs screen
    Cache         where thumbnails and proxies go
"""

import ctypes
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.realtools


# ================================================================ commands

def _workstation_receives(hub):
    """One poll of the workstation's command worker, as its thread does it."""
    from slate.gatekeeper_main import CommandCheckWorker
    worker = CommandCheckWorker(hub, [])
    got = []
    worker.command_received.connect(got.append)
    real = hub.get_active_commands

    def once():
        worker.running = False
        return real()
    hub.get_active_commands = once
    worker.run()
    hub.get_active_commands = real
    return got


def _handled(commands, monkeypatch):
    """What the workstation's main thread did with each command."""
    import slate.gatekeeper_main as gatekeeper
    done = []

    class Shown:
        def __init__(self, message, parent=None, sender=""):
            done.append(("shown", message))

        def show(self):
            pass
    monkeypatch.setattr(gatekeeper, "BroadcastWindow", Shown)
    app = SimpleNamespace(_handle_system_command=lambda cmd: done.append(("asked", cmd["command"])))
    for cmd in commands:
        gatekeeper.ApplicationEntry._process_command_main_thread(app, cmd)
    return done


def test_every_command_the_admin_screens_send_is_received_and_handled(share, monkeypatch):
    from slate.core.infra.server_hub import ServerHub
    from slate.gui import admin_panel, admin_widgets
    import socket

    hub = ServerHub()
    me = socket.gethostname()

    # The Admin Panel's broadcast box and its fleet cache wipe, and a PC
    # card's Restart and Shut down - each through the real button handler.
    panel = SimpleNamespace(hub=hub, log_action=lambda m: None, can_wipe_caches=True,
                            verify_admin_action=lambda: True,
                            inp_broadcast=SimpleNamespace(text=lambda: "Lunch is here",
                                                          clear=lambda: None))
    admin_panel.AdminPanelTab.send_broadcast(panel)
    monkeypatch.setattr(admin_widgets, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(admin_widgets, "toast", lambda *a, **k: None)
    card = SimpleNamespace(read_only=False, window=lambda: None, verify_callback=None,
                           hub=hub, pc_name=me, log_action=None, requested=None,
                           current_data={})
    admin_widgets.PCCard.request_power(card, "restart")
    admin_widgets.PCCard.request_power(card, "shutdown")

    files = list((share / "Commands").glob("*.json"))
    assert len(files) == 3, "three commands sent within a second must be three files"

    received = _workstation_receives(hub)
    assert sorted(c["command"] for c in received) == ["message", "restart", "shutdown"]
    done = _handled(received, monkeypatch)
    assert ("shown", "Lunch is here") in done
    assert ("asked", "restart") in done and ("asked", "shutdown") in done


def test_the_fleet_cache_wipe_reaches_no_workstation(share):
    """Documented, not fixed: no workstation has a handler for wipe_cache (see report)."""
    from slate.core.infra.server_hub import ServerHub
    from slate.gui import admin_panel
    hub = ServerHub()
    panel = SimpleNamespace(hub=hub, log_action=lambda m: None, can_wipe_caches=True,
                            verify_admin_action=lambda: True)
    admin_panel.AdminPanelTab.wipe_remote_caches(panel)
    assert [json.loads(p.read_text())["command"] for p in (share / "Commands").glob("*.json")] \
        == ["wipe_cache"]
    assert _workstation_receives(hub) == []


def test_old_and_foreign_commands_are_not_acted_on(share):
    from slate.core.infra.server_hub import ServerHub
    hub = ServerHub()
    commands = share / "Commands"
    now = time.time()
    (commands / "expired.json").write_text(json.dumps(
        {"command": "message", "target": "all", "message": "old", "timestamp": now - 90,
         "expires": now - 30}))
    (commands / "other_pc.json").write_text(json.dumps(
        {"command": "message", "target": "SOME-OTHER-PC", "message": "x", "timestamp": now,
         "expires": now + 60}))
    (commands / "half_written.json").write_text('{"command": "mess')
    (commands / "unknown.json").write_text(json.dumps(
        {"command": "format_c", "target": "all", "timestamp": now, "expires": now + 60}))
    assert _workstation_receives(hub) == []


def test_a_missing_share_does_not_break_the_command_poll(share, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    from slate.core.infra.server_hub import ServerHub
    hub = ServerHub()
    shutil.rmtree(share)
    assert _workstation_receives(hub) == []


# ================================================================= updates

def _load_tool(name):
    spec = importlib.util.spec_from_file_location("rt_" + name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _publish_release(share, tmp_path, version, files):
    """What tools/build_update_package.py does after PyInstaller, for real, into the share."""
    from slate.core.updater.manifest import build, manifest_name
    tool = _load_tool("build_update_package")
    dist = tmp_path / "dist" / "Slate"
    for rel, content in files.items():
        (dist / rel).parent.mkdir(parents=True, exist_ok=True)
        (dist / rel).write_bytes(content)
    releases = tmp_path / "releases"
    releases.mkdir(exist_ok=True)
    shutil.make_archive(str(releases / "Slate_Client_Update"), "zip", dist)
    zip_path = releases / "Slate_Client_Update.zip"
    manifest = build(version=version, package_name=zip_path.name,
                     hash_sha256=tool.generate_file_hash(zip_path), target="client",
                     built_from="vfx")
    manifest_path = releases / manifest_name("client")
    manifest_path.write_text(json.dumps(manifest, indent=4), encoding="utf-8")
    published = tool.publish(zip_path, manifest_path, tmp_path)
    assert published == share / "Updates" / "releases"
    return manifest


def _check(target="client"):
    from slate.core.updater.update_checker import UpdateChecker
    checker = UpdateChecker(manual_mode=True, target=target)
    offered, current = [], []
    checker.update_available.connect(offered.append)
    checker.update_not_found.connect(current.append)
    checker.run()
    return checker.last_result_reason, offered


NEW_BUILD = {"Slate.exe": b"new exe", "_internal/new.dll": b"new dll",
             "_internal/lib/core.pyd": b"new core"}


def test_a_published_release_is_found_verified_and_staged(share, tmp_path, monkeypatch):
    from slate.core.updater.sidecar_engine import SidecarEngine
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))

    assert _check()[0] == "manifest_missing"
    manifest = _publish_release(share, tmp_path, "99.0.0", NEW_BUILD)
    reason, offered = _check()
    assert reason == "update_available" and offered[0]["version"] == "99.0.0"

    engine = SidecarEngine(offered[0])
    errors = []
    engine.update_error.connect(errors.append)
    assert engine.stage_update(), errors
    staged = tmp_path / "temp" / "SlateUpdate" / manifest["package_name"]
    assert hashlib.sha256(staged.read_bytes()).hexdigest() == manifest["hash_sha256"]

    # The same version as this build is not offered again.
    from slate import __version__
    _publish_release(share, tmp_path, __version__.replace("BETA ", ""), NEW_BUILD)
    assert _check() == ("up_to_date", [])


def test_a_package_changed_after_publishing_is_refused(share, tmp_path, monkeypatch):
    from slate.core.updater.sidecar_engine import SidecarEngine
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    manifest = _publish_release(share, tmp_path, "99.0.0", NEW_BUILD)
    package = share / "Updates" / "releases" / manifest["package_name"]
    with open(package, "ab") as f:
        f.write(b"tampered")
    engine = SidecarEngine(manifest)
    errors = []
    engine.update_error.connect(errors.append)
    assert not engine.stage_update()
    assert "Hash mismatch" in errors[0]
    assert not (tmp_path / "temp" / "SlateUpdate" / manifest["package_name"]).exists()

    # A manifest with its hash under the wrong key is refused before copying.
    bad = dict(manifest)
    bad["sha256"] = bad.pop("hash_sha256")
    engine = SidecarEngine(bad)
    errors = []
    engine.update_error.connect(errors.append)
    assert not engine.stage_update() and "hash_sha256" in errors[0]


def _old_install(tmp_path):
    install = tmp_path / "Program" / "Slate"
    old = {"Slate.exe": b"old exe", "_internal/old_only.dll": b"old dll",
           "_internal/lib/core.pyd": b"old core", "slate_server_config.json": b"{}",
           "client_config.json": b'{"db_host": "10.0.0.5"}', "LocalDatabase/PG_VERSION": b"17",
           "logs/today.log": b"log"}
    for rel, content in old.items():
        (install / rel).parent.mkdir(parents=True, exist_ok=True)
        (install / rel).write_bytes(content)
    return install


def _run_updater(monkeypatch, zip_path, install, exe="Slate.exe"):
    """updater_script.main() as SlateUpdater.exe runs it, minus the message box and relaunch."""
    from slate.core.updater import updater_script
    boxes, launched = [], []
    monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW",
                        lambda *a: boxes.append(a[1]) or 1)
    monkeypatch.setattr(updater_script.subprocess, "Popen",
                        lambda cmd, **k: launched.append(cmd))
    monkeypatch.setattr(sys, "argv", ["SlateUpdater.exe", "0", str(zip_path), str(install), exe])
    with pytest.raises(SystemExit) as exit_info:
        updater_script.main()
    return exit_info.value.code, boxes, launched


def test_the_updater_swaps_a_sandbox_install_and_keeps_the_studios_files(share, tmp_path,
                                                                          monkeypatch):
    from slate.core.updater.sidecar_engine import SidecarEngine
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    manifest = _publish_release(share, tmp_path, "99.0.0", NEW_BUILD)
    engine = SidecarEngine(manifest)
    assert engine.stage_update()
    install = _old_install(tmp_path)

    code, boxes, launched = _run_updater(monkeypatch, engine.local_zip, install)
    assert code == 0 and boxes == []
    assert launched == [[str(install / "Slate.exe")]]
    assert (install / "Slate.exe").read_bytes() == b"new exe"
    assert (install / "_internal" / "new.dll").exists()
    assert (install / "_internal" / "lib" / "core.pyd").read_bytes() == b"new core"
    assert not (install / "_internal" / "old_only.dll").exists(), "old build files go"
    for kept in ("slate_server_config.json", "client_config.json",
                 "LocalDatabase/PG_VERSION", "logs/today.log"):
        assert (install / kept).exists(), kept
    backups = list((install.parent / "Backups").iterdir())
    assert len(backups) == 1 and (backups[0] / "Slate.exe").read_bytes() == b"old exe"
    assert not (backups[0] / "client_config.json").exists()


def test_a_broken_package_is_rolled_back_and_the_studios_files_survive(tmp_path,
                                                                       monkeypatch):
    install = _old_install(tmp_path)
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"PK\x03\x04 this is not a zip")
    code, boxes, launched = _run_updater(monkeypatch, broken, install)
    assert code == 1 and "Rollback completed" in boxes[0]
    assert (install / "Slate.exe").read_bytes() == b"old exe"
    assert (install / "_internal" / "old_only.dll").exists()
    assert (install / "client_config.json").read_bytes() == b'{"db_host": "10.0.0.5"}'
    assert (install / "LocalDatabase" / "PG_VERSION").exists()


def test_a_file_whose_name_changed_case_survives_the_update(tmp_path, monkeypatch):
    """Windows names are case-blind: the cleanup must not delete what it just unpacked."""
    install = _old_install(tmp_path)
    (install / "_internal" / "Qt6Core.DLL").write_bytes(b"old qt")
    dist = tmp_path / "dist"
    for rel, content in {"Slate.exe": b"new exe", "_internal/Qt6Core.dll": b"new qt"}.items():
        (dist / rel).parent.mkdir(parents=True, exist_ok=True)
        (dist / rel).write_bytes(content)
    package = Path(shutil.make_archive(str(tmp_path / "pkg"), "zip", dist))
    code, boxes, _ = _run_updater(monkeypatch, package, install)
    assert code == 0, boxes
    survivors = [p.name for p in (install / "_internal").iterdir()]
    assert [n for n in survivors if n.lower() == "qt6core.dll"], survivors
    assert (install / "_internal" / "Qt6Core.dll").read_bytes() == b"new qt"


# ============================================================== audit logs

def test_what_auditlogger_writes_is_what_the_audit_logs_screen_reads(share, monkeypatch):
    from slate.core.infra.audit_logger import AuditLogger
    from slate.gui import admin_panel
    from slate.gui.advanced_log_viewer import read_audit_trail
    monkeypatch.setattr(AuditLogger, "_instance", None)

    AuditLogger().log_auth("aarav", success=False, reason="wrong password")
    AuditLogger().log_event("BACKUP", "SYSTEM", "Workstation backup written: Données", "SUCCESS")
    assert AuditLogger.log_directory() == share / "Logs" / "Audit"
    assert len(list((share / "Logs" / "Audit").glob("audit_*.log"))) == 1

    # The Admin Panel's own log, written by its real log_action.
    legacy = share / "Config" / "audit.log"
    legacy.parent.mkdir(parents=True, exist_ok=True)     # as AdminPanelTab.__init__ does
    panel = SimpleNamespace(current_username="admin", log_file=legacy)
    admin_panel.AdminPanelTab.log_action(panel, "Broadcast Alert: Lunch is here")

    trail = read_audit_trail(AuditLogger.log_directory(), legacy)
    by_type = {row["type"]: row for row in trail}
    assert by_type["AUTH"]["status"] == "FAILURE" and by_type["AUTH"]["user"] == "aarav"
    assert by_type["BACKUP"]["details"].endswith("Données")
    assert by_type["ADMIN"]["details"] == "Broadcast Alert: Lunch is here"
    assert by_type["ADMIN"]["user"] == "admin"


# =================================================================== cache

def test_thumbnails_go_to_the_share_cache(share):
    from slate.core.domain.proxy_manager import ProxyManager
    manager = ProxyManager()
    assert manager.cache_dir == share / "Cache" and manager.local_only is False


def test_a_missing_share_means_a_local_only_cache(share, tmp_path, monkeypatch):
    """The paths of pictures made on this PC alone must not go into the shared library."""
    from slate.core.domain.proxy_manager import ProxyManager
    from slate.core.infra.global_config import GlobalConfig
    monkeypatch.setitem(GlobalConfig._instance.data, "SERVER_ROOT",
                        str(tmp_path / "share_that_is_not_mounted"))
    manager = ProxyManager()
    assert not str(manager.cache_dir).startswith(str(tmp_path / "share_that_is_not_mounted"))
    assert manager.local_only is True


def test_a_read_only_share_cache_means_a_local_only_cache(share):
    import getpass
    from slate.core.domain.proxy_manager import ProxyManager
    cache = share / "Cache"
    cache.mkdir()
    user = getpass.getuser()
    deny = subprocess.run(["icacls", str(cache), "/deny", f"{user}:(W,AD)"],
                          capture_output=True, text=True)
    if deny.returncode != 0:
        pytest.skip("could not make a read-only folder here")
    try:
        manager = ProxyManager()
        assert manager.local_only is True
        assert manager.cache_dir != cache
    finally:
        subprocess.run(["icacls", str(cache), "/remove:d", user], capture_output=True)
