"""
signed_fleet_commands and signed_updates without a database: the share is a
temp folder, the switch and the studio's public key are given.

    a command file -> the workstation's poller, off / log_only / on    test_fleet_commands_*
    the Admin Panel gets the key with the admin's password            test_the_admin_panel_*
    a manifest -> UpdateChecker and SidecarEngine, off / log_only / on test_updates_*
    the owner's key is never made inside the repository               test_a_release_key_*

The database side (who gets the fleet key, the switch turned on in Recover
Slate) is in test_security_secdb.py, on real clusters.
"""

import hashlib
import importlib.util
import json
import shutil
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from slate.core.security import signing, switches

ROOT = Path(__file__).resolve().parent.parent


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


# ================================================================== updates

def _release(tmp_path, version="999.0"):
    """A package and its manifest, as tools/build_update_package.py makes them."""
    from slate.core.updater.manifest import build
    package = tmp_path / "Slate_Client_Update.zip"
    package.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    return package, build(version, package.name, digest, "client")


def _key_file(tmp_path):
    private, public = signing.new_keypair()
    path = tmp_path / "owner" / "release.key"
    path.parent.mkdir()
    path.write_text(private + "\n", encoding="utf-8")
    return path, public


def test_updates_signature_follows_the_switch(tmp_path, mode, monkeypatch, caplog):
    from slate.core.updater import manifest as m, release_key
    key, public = _key_file(tmp_path)
    monkeypatch.setattr(release_key, "PUBLIC_KEY", public)
    _, plain = _release(tmp_path)
    signed = m.sign(plain, key)
    tampered = dict(signed, hash_sha256="0" * 64)

    assert not any(m.signature_refusal(x) for x in (plain, signed, tampered)), "off"
    mode["signed_updates"] = switches.LOG_ONLY
    with caplog.at_level("WARNING"):
        assert not any(m.signature_refusal(x) for x in (plain, signed, tampered))
    assert caplog.text.count("would be refused") == 2
    mode["signed_updates"] = switches.ON
    assert m.signature_refusal(signed) == ""
    assert "not signed" in m.signature_refusal(plain)
    assert "not valid" in m.signature_refusal(tampered)
    monkeypatch.setattr(release_key, "PUBLIC_KEY", "")
    assert "no release key" in m.signature_refusal(signed)


def test_updates_unsigned_are_neither_offered_nor_staged_when_on(tmp_path, share, mode,
                                                                 monkeypatch):
    from slate.core.updater import manifest as m, release_key
    from slate.core.updater.sidecar_engine import SidecarEngine
    from slate.core.updater.update_checker import UpdateChecker
    key, public = _key_file(tmp_path)
    monkeypatch.setattr(release_key, "PUBLIC_KEY", public)
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    package, plain = _release(tmp_path)
    releases = share / "Updates" / "releases"
    releases.mkdir(parents=True)
    shutil.copy2(package, releases / package.name)

    def check(manifest):
        (releases / "manifest_client.json").write_text(json.dumps(manifest))
        checker, offered = UpdateChecker(manual_mode=True), []
        checker.update_available.connect(offered.append)
        checker.run()
        engine = SidecarEngine(manifest)
        staged = engine.stage_update()
        return bool(offered), staged, (engine.download_dir / package.name).exists()

    mode["signed_updates"] = switches.ON
    assert check(plain) == (False, False, False), "refused before anything is copied"
    assert check(m.sign(plain, key)) == (True, True, True)
    (tmp_path / "temp" / "SlateUpdate" / package.name).unlink()
    mode["signed_updates"] = switches.LOG_ONLY
    assert check(plain) == (True, True, True), "log_only: as before"


def _publisher():
    spec = importlib.util.spec_from_file_location("publisher_under_test",
                                                  ROOT / "tools" / "release_publisher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_release_key_is_never_made_inside_the_repository(tmp_path, monkeypatch):
    tool = _publisher()
    public_file = tmp_path / "release_key.py"
    public_file.write_text((ROOT / "slate" / "core" / "updater" / "release_key.py")
                           .read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(tool, "PUBLIC_KEY_FILE", public_file)
    with pytest.raises(SystemExit):
        tool.make_key(ROOT / "release.key")
    assert not (ROOT / "release.key").exists()

    tool.make_key(tmp_path / "usb" / "release.key")
    private = (tmp_path / "usb" / "release.key").read_text(encoding="utf-8").strip()
    namespace = {}
    exec(public_file.read_text(encoding="utf-8"), namespace)
    signed = signing.sign({"version": "1"}, private)
    assert signing.verify(signed, namespace["PUBLIC_KEY"])
    with pytest.raises(SystemExit):
        tool.make_key(tmp_path / "usb" / "release.key")          # never overwritten
