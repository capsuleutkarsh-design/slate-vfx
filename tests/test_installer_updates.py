"""
Updates the Quillo way: Slate Server publishes the installer itself on the
share, workstations copy it, check its hash and run it silently.

    installer name -> target and version, other names refused   test_installer_names
    publish: copy, then manifest, then older installers go       test_publish_*
    a different version is offered, the same one is not          test_a_different_version_*
    required: no wait, no "later"                                test_a_required_update_*
    a changed installer is refused                               test_a_hash_mismatch_*
    the .cmd and a detached start (Popen is mocked)              test_launch_*

Nothing here runs an installer.
"""

import json
import os
import subprocess
import sys

import pytest

from slate.core.updater import manifest as m
from slate.core.updater import update_checker as uc


def _installer(folder, name, body=b"MZ fake installer"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(body)
    return path


def test_installer_names():
    assert m.installer_target("setup_Slate_Studio_vBETA 2.2.0.exe") == ("studio", "BETA 2.2.0")
    assert m.installer_target(r"C:\x\setup_Slate_Ops_v2.3.0.exe") == ("ops", "2.3.0")
    for bad in ("setup_Slate Server_vBETA 2.2.0.exe", "Slate_Studio_v2.exe",
                "setup_Slate_Studio_v2.2.0.zip", "setup_Slate_Studio_v.exe"):
        with pytest.raises(ValueError):
            m.installer_target(bad)


def test_publish_copies_then_writes_the_manifest_and_removes_older(tmp_path):
    releases = tmp_path / "share" / "Updates" / "releases"
    _installer(releases, "setup_Slate_Studio_vBETA 2.1.0.exe", b"old")
    _installer(releases, "setup_Slate_Ops_vBETA 2.1.0.exe", b"ops stays")
    new = _installer(tmp_path / "build", "setup_Slate_Studio_vBETA 2.2.0.exe")

    said = []
    manifest = m.publish(new, releases, required=False, progress=said.append)

    on_disk = json.loads((releases / "manifest_studio.json").read_text(encoding="utf-8"))
    assert on_disk == manifest and m.problems(on_disk) == []
    assert (on_disk["target"], on_disk["version"], on_disk["required"]) == ("studio", "BETA 2.2.0", False)
    assert on_disk["published_at"]
    assert on_disk["hash_sha256"] == m.sha256_of(releases / new.name) == m.sha256_of(new)
    assert sorted(p.name for p in releases.iterdir()) == [
        "manifest_studio.json", "setup_Slate_Ops_vBETA 2.1.0.exe",
        "setup_Slate_Studio_vBETA 2.2.0.exe"], "the older Studio installer goes, Ops stays"
    assert said and not list(releases.glob("*.part")) and not list(releases.glob("*.tmp"))

    with pytest.raises(ValueError):
        m.publish(_installer(tmp_path / "build", "Slate.exe"), releases)

    assert m.require_now(releases) == ["studio BETA 2.2.0"]
    assert json.loads((releases / "manifest_studio.json").read_text(encoding="utf-8"))["required"]


def test_a_different_version_is_offered_and_the_same_is_not(tmp_path):
    releases = tmp_path / "Updates" / "releases"
    m.publish(_installer(tmp_path / "b", "setup_Slate_Ops_vBETA 2.1.0.exe"), releases)
    assert uc.published("studio", root=tmp_path) == (None, "manifest_missing")
    manifest, why = uc.published("ops", root=tmp_path)
    assert why == "" and manifest["version"] == "BETA 2.1.0"
    assert uc.offered(manifest, current="BETA 2.2.0"), "an older installer is a rollback"
    assert uc.offered(manifest, current="BETA 2.0.32")
    assert not uc.offered(manifest, current="BETA 2.1.0")
    assert not uc.offered(None)


def test_a_required_update_skips_the_wait_and_has_no_later(qtbot, monkeypatch):
    monkeypatch.setattr(uc.random, "uniform", lambda a, b: b)
    assert uc.wait_before_download({"required": False}) == uc.JITTER_SECONDS
    assert uc.wait_before_download({"required": True}) == 0

    from slate.gui.dialogs.update_available_dialog import UpdateAvailableDialog
    later = UpdateAvailableDialog({"version": "2.3.0"})
    qtbot.addWidget(later)
    assert later.btn_later is not None and later.btn_later.text() == "When I close Slate"

    required = UpdateAvailableDialog({"version": "2.3.0", "required": True}, seconds=2)
    qtbot.addWidget(required)
    assert required.btn_later is None and "0:02" in required.message.text()
    accepted = []
    required.accepted.connect(lambda: accepted.append(True))
    required.show()
    required.reject()                                  # Esc: nothing happens
    assert required.isVisible() and not accepted
    qtbot.waitUntil(lambda: accepted == [True], timeout=5000)   # the countdown updates


def test_a_hash_mismatch_is_refused_and_deleted(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    releases = tmp_path / "Updates" / "releases"
    manifest = m.publish(_installer(tmp_path / "b", "setup_Slate_Studio_v9.0.exe"), releases)
    assert uc.download(manifest, root=tmp_path).read_bytes() == b"MZ fake installer"

    (releases / manifest["package_name"]).write_bytes(b"MZ changed after publishing")
    (tmp_path / "temp" / "SlateUpdate" / manifest["package_name"]).unlink()
    with pytest.raises(ValueError, match="checksum does not match"):
        uc.download(manifest, root=tmp_path)
    assert not (tmp_path / "temp" / "SlateUpdate" / manifest["package_name"]).exists()

    # A Studio manifest pointing at the Ops installer is not offered at all.
    manifest["package_name"] = "setup_Slate_Ops_v9.0.exe"
    m.write_manifest(releases / "manifest_studio.json", manifest)
    assert uc.published("studio", root=tmp_path) == (None, "invalid_manifest")


def test_launch_writes_the_cmd_and_starts_it_detached(tmp_path, monkeypatch):
    installer = _installer(tmp_path / "SlateUpdate", "setup_Slate_Studio_vBETA 2.3.0.exe")
    started = []
    with pytest.raises(RuntimeError, match="runs from source"):
        uc.launch(installer, relaunch=True, popen=lambda *a, **k: started.append(a))
    assert not started

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Programs\Slate Studio\Slate_Studio.exe")
    monkeypatch.setattr(uc.GlobalConfig, "server_root", classmethod(lambda cls: r"Z:\Slate_Central"))
    script = uc.launch(installer, relaunch=True, popen=lambda *a, **k: started.append((a, k)))
    lines = script.read_text(encoding="utf-8").splitlines()
    assert script == installer.with_name("install_studio.cmd")
    assert 'set "SLATE_UPDATE_ROOT=Z:\\Slate_Central"' in lines
    assert ('start "" /wait "%s" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS'
            % installer) in lines
    assert lines[-1] == r'start "" "C:\Programs\Slate Studio\Slate_Studio.exe"'
    wait = lines.index(":wait")
    assert '"PID eq %d"' % os.getpid() in lines[wait + 1], "the installer waits for Slate to exit"
    assert wait < lines.index(next(l for l in lines if "/VERYSILENT" in l))
    (args, kwargs), = started
    assert args == (["cmd.exe", "/c", str(script)],)
    assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW

    started.clear()
    lines = uc.launch(installer, relaunch=False, popen=lambda *a, **k: started.append(a)) \
        .read_text(encoding="utf-8").splitlines()
    assert not any("Slate_Studio.exe" in line for line in lines), "When I close Slate: no reopen"


def test_the_window_runs_it_now_or_at_close_but_not_at_sign_out(monkeypatch):
    from types import SimpleNamespace
    from slate.gui.main_window import VFXFolderCreatorApp as App
    launched = []
    monkeypatch.setattr(uc, "launch", lambda path, relaunch: launched.append((path, relaunch)))
    win = SimpleNamespace(show_feedback=lambda *a, **k: None, _update_on_close=None,
                          _update_put_off="", confirm_open_work=lambda action: False,
                          close=lambda: launched.append("closed"))
    win._install_update_at_close = lambda *a: App._install_update_at_close(win, *a)

    # Update now, with unsaved work the artist keeps: it waits for the close.
    assert not App._install_update_now(win, {"version": "2.3"}, "a.exe")
    assert (win._update_on_close, win._update_put_off, launched) == ("a.exe", "2.3", [])
    win._logout_requested = True
    App._install_update_on_quit(win)
    assert launched == [], "signing out is not closing Slate"
    win._logout_requested = False
    App._install_update_on_quit(win)
    assert launched == [("a.exe", False)]

    # Required: no question, start it, reopen afterwards, close.
    launched.clear()
    assert App._install_update_now(win, {"version": "2.4", "required": True}, "b.exe")
    assert launched == [("b.exe", True), "closed"]


def test_a_required_update_replaces_an_offer_left_open(qtbot):
    from PySide6.QtWidgets import QWidget
    from slate.gui.main_window import VFXFolderCreatorApp as App
    win = QWidget()                                    # the dialogs' parent
    qtbot.addWidget(win)
    win._update_dialog = None
    win._install_update_now = win._install_update_at_close = lambda *a: None
    win._required_update_showing = lambda: App._required_update_showing(win)
    offer = App._on_update_ready(win, {"version": "2.3"}, "a.exe")
    qtbot.addWidget(offer)
    assert App._on_update_ready(win, {"version": "2.3"}, "a.exe") is None, "same offer: kept"
    forced = App._on_update_ready(win, {"version": "2.3", "required": True}, "a.exe")
    qtbot.addWidget(forced)
    assert forced.required and not offer.isVisible() and win._update_dialog is forced
    assert App._on_update_ready(win, {"version": "2.4"}, "b.exe") is None, "required stays"
