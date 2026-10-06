"""
signed_fleet_commands without a database: the share is a temp folder, the
switch and the studio's public key are given.

    a command file -> the workstation's poller, off / log_only / on    test_fleet_commands_*
    the Admin Panel gets the key with the admin's password            test_the_admin_panel_*

The database side (who gets the fleet key, the switch turned on in Recover
Slate) is in test_security_secdb.py, on real clusters.
"""

import json
import socket
from types import SimpleNamespace

import pytest

from slate.core.security import signing, switches


@pytest.fixture
def share(tmp_path, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    if GlobalConfig._instance is None:
        GlobalConfig._instance = GlobalConfig()
    root = tmp_path / "share"
    root.mkdir()
    monkeypatch.setitem(GlobalConfig._instance.data, "SERVER_ROOT", str(root))
    return root


@pytest.fixture
def mode(monkeypatch):
    """The switches, as the test sets them: {name: mode}."""
    modes = {}
    monkeypatch.setattr(switches, "mode", lambda name, db=None, override_path=None:
                        modes.get(name, switches.OFF))
    return modes


@pytest.fixture
def fleet_key(monkeypatch):
    private, public = signing.new_keypair()
    monkeypatch.setattr(signing, "fleet_public_key", lambda db=None: public)
    return private


def _received(hub):
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
    return sorted(c["message"] for c in got)


def test_fleet_commands_follow_the_switch(share, mode, fleet_key, monkeypatch):
    from slate.core.infra.server_hub import ServerHub
    me = socket.gethostname()
    hub = ServerHub()
    hub.signing_key = fleet_key
    hub.post_command("message", me, "signed")
    ServerHub().post_command("message", me, "unsigned")       # an old Admin Panel, or anyone
    signed = next(json.loads(p.read_text()) for p in (share / "Commands").glob("*.json")
                  if "signed" == json.loads(p.read_text())["message"])
    forged = dict(signed, message="forged", timestamp=signed["timestamp"] + 1)   # edited
    (share / "Commands" / "cmd_forged.json").write_text(json.dumps(forged))

    assert _received(ServerHub()) == ["forged", "signed", "unsigned"], "off: as before"
    mode["signed_fleet_commands"] = switches.LOG_ONLY
    logged = []
    monkeypatch.setattr(signing.logger, "warning", lambda text, *a: logged.append(text % a))
    assert _received(ServerHub()) == ["forged", "signed", "unsigned"]
    assert sorted(line for line in logged if "would be refused" in line) == [
        "signed_fleet_commands (log_only): the 'message' command for %s would be refused, "
        "because %s." % (me, why) for why in ("it is not signed", "its signature is not valid")]
    mode["signed_fleet_commands"] = switches.ON
    assert _received(ServerHub()) == ["signed"]


def test_fleet_commands_on_with_no_key_readable_refuses_and_never_raises(share, mode,
                                                                          monkeypatch):
    from slate.core.infra.server_hub import ServerHub
    monkeypatch.setattr(signing, "fleet_public_key", lambda db=None: "")    # database down
    ServerHub().post_command("message", socket.gethostname(), "unsigned")
    mode["signed_fleet_commands"] = switches.ON
    assert _received(ServerHub()) == []


def test_the_admin_panel_signs_only_with_a_key_from_the_admins_password(share, mode,
                                                                       monkeypatch):
    from slate.core.infra.server_hub import ServerHub
    from slate.gui import admin_panel
    asked = []
    monkeypatch.setattr(signing, "fleet_private_key",
                        lambda user, password, db=None: asked.append((user, password)) or None)
    monkeypatch.setattr(admin_panel.QMessageBox, "warning", lambda *a, **k: None)
    panel = SimpleNamespace(hub=ServerHub(), current_username="admin", db=None)
    unlock = admin_panel.AdminPanelTab._unlock_signing

    assert unlock(panel, "pw") and asked == [], "off: nothing is fetched"
    mode["signed_fleet_commands"] = switches.LOG_ONLY
    assert unlock(panel, "pw") and asked == [("admin", "pw")], "log_only: sent unsigned"
    mode["signed_fleet_commands"] = switches.ON
    assert not unlock(panel, "pw"), "on: no key, nothing sent"

    private, _ = signing.new_keypair()
    monkeypatch.setattr(signing, "fleet_private_key", lambda user, password, db=None: private)
    assert unlock(panel, "pw") and panel.hub.signing_key == private
    # The broadcast box asks for the password first when there is no key yet.
    panel = SimpleNamespace(hub=ServerHub(), verify_admin_action=lambda: False,
                            log_action=lambda m: None,
                            inp_broadcast=SimpleNamespace(text=lambda: "Lunch", clear=lambda: None))
    admin_panel.AdminPanelTab.send_broadcast(panel)
    assert not list((share / "Commands").glob("*.json")), "a refused password sends nothing"
