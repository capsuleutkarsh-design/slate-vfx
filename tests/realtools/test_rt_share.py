"""
The studio share (SERVER_ROOT) on a real temp folder:

    Commands      ServerHub.post_command  ->  gatekeeper's CommandCheckWorker
    Logs/Audit    AuditLogger and the Admin Panel log  ->  the Audit Logs screen
    Cache         where thumbnails and proxies go
"""

import json
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest

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

    # The Admin Panel's broadcast box, and a PC
    # card's Restart and Shut down - each through the real button handler.
    panel = SimpleNamespace(hub=hub, log_action=lambda m: None, current_username="boss",
                            verify_admin_action=lambda: True,
                            inp_broadcast=SimpleNamespace(text=lambda: "Lunch is here",
                                                          clear=lambda: None))
    admin_panel.AdminPanelTab.send_broadcast(panel)
    monkeypatch.setattr(admin_widgets, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(admin_widgets, "toast", lambda *a, **k: None)
    monkeypatch.setattr(admin_widgets, "ask_reason", lambda parent, verb, pc: f"{verb} for updates")
    card = SimpleNamespace(read_only=False, window=lambda: None, verify_callback=None,
                           hub=hub, pc_name=me, log_action=None, requested=None,
                           current_data={}, admin_user="boss")
    admin_widgets.PCCard.request_power(card, "restart")
    admin_widgets.PCCard.request_power(card, "shutdown")

    files = list((share / "Commands").glob("*.json"))
    assert len(files) == 3, "three commands sent within a second must be three files"

    received = _workstation_receives(hub)
    assert sorted(c["command"] for c in received) == ["message", "restart", "shutdown"]
    # Who asked and why reach the PC (it said "No reason provided" every time).
    restart = next(c for c in received if c["command"] == "restart")
    assert (restart["admin_user"], restart["reason"]) == ("boss", "Restart for updates")
    done = _handled(received, monkeypatch)
    assert ("shown", "Lunch is here") in done
    assert ("asked", "restart") in done and ("asked", "shutdown") in done


def test_old_and_foreign_commands_are_not_acted_on(share):
    from slate.core.infra.server_hub import ServerHub
    hub = ServerHub()
    commands = share / "Commands"
    now = time.time()
    (commands / "expired.json").write_text(json.dumps(
        {"command": "message", "target": "all", "message": "old", "timestamp": now - 90,
         "expires": now - 30}))
    # Its age is its time on the share (the admin PC's clock is not this PC's).
    import os
    os.utime(commands / "expired.json", (now - 90, now - 90))
    (commands / "other_pc.json").write_text(json.dumps(
        {"command": "message", "target": "SOME-OTHER-PC", "message": "x", "timestamp": now,
         "expires": now + 60}))
    (commands / "half_written.json").write_text('{"command": "mess')
    (commands / "unknown.json").write_text(json.dumps(
        {"command": "format_c", "target": "all", "timestamp": now, "expires": now + 60}))
    assert _workstation_receives(hub) == []


def test_a_workstation_whose_clock_runs_ahead_still_gets_commands(share, monkeypatch):
    """
    Commands expired by the admin PC's clock: a workstation more than 60 s
    ahead of it dropped every one. Their age is now their time on the share.
    """
    from slate.core.infra import server_hub
    hub = server_hub.ServerHub()
    hub.post_command("message", "all", "Fire drill at 3")
    ahead = time.time() + 300
    monkeypatch.setattr(server_hub.time, "time", lambda: ahead)
    assert [c["message"] for c in _workstation_receives(server_hub.ServerHub())] == ["Fire drill at 3"]


def test_a_missing_share_does_not_break_the_command_poll(share, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    from slate.core.infra.server_hub import ServerHub
    hub = ServerHub()
    shutil.rmtree(share)
    assert _workstation_receives(hub) == []


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
