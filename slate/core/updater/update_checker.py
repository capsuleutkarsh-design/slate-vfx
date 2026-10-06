"""
Workstation updates, for Slate Studio and Slate Operations.

The app reads ``manifest_<target>.json`` on the share (at start, then every 5
minutes). A version different from this one is an update - newer or older, so
publishing an older installer is a rollback. The installer is copied to
%TEMP%\\SlateUpdate and checked against the manifest's hash; the app then asks
the artist (gui/dialogs/update_available_dialog.py), and ``launch`` runs the
installer silently from a small .cmd once Slate has quit.
"""

import hashlib
import json
import logging
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ... import __version__ as CURRENT_VERSION
from ..infra.global_config import GlobalConfig
from .manifest import (installer_target, manifest_name, problems, releases_dir,
                       remove_older, sha256_of)

JITTER_SECONDS = 600


def target_for(app_mode) -> str:
    """Slate Operations updates as "ops"; Slate Studio (and the all-in-one) as "studio"."""
    return "ops" if app_mode == "ops" else "studio"


def download_dir() -> Path:
    return Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "SlateUpdate"


def _releases(root=None) -> Path:
    return releases_dir(Path(root or GlobalConfig.server_root()) / "Updates")


def published(target, root=None):
    """(manifest, "") when a usable one is on the share, else (None, why not)."""
    path = _releases(root) / manifest_name(target)
    if not path.exists():
        return None, "manifest_missing"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logging.warning("The update manifest %s could not be read: %s", path, exc)
        return None, "invalid_manifest"
    faults = problems(manifest)
    try:
        if not faults and installer_target(manifest["package_name"])[0] != target:
            faults.append("%s is not a %s installer" % (manifest["package_name"], target))
    except ValueError as exc:
        faults.append(str(exc))
    if faults:
        logging.warning("The update manifest %s is not usable: %s", path, "; ".join(faults))
        return None, "invalid_manifest"
    return manifest, ""


def offered(manifest, current=CURRENT_VERSION) -> bool:
    """Any version but this one: an older installer published on purpose is a rollback."""
    return bool(manifest) and \
        str(manifest["version"]).strip().lower() != str(current).strip().lower()


def wait_before_download(manifest) -> float:
    """Seconds to wait before copying the installer; none for a required update."""
    # ponytail: a random 0-10 minute wait so 150 PCs do not pull a 500 MB installer
    # at the same moment; a download queue on the server if the share still chokes.
    return 0.0 if manifest.get("required") else random.uniform(0, JITTER_SECONDS)


def download(manifest, root=None, should_stop=lambda: False) -> Path:
    """The installer, copied to %TEMP%\\SlateUpdate and checked against its hash."""
    name = manifest["package_name"]
    dest = download_dir() / name
    expected = str(manifest["hash_sha256"]).lower()
    if dest.is_file() and sha256_of(dest) == expected:
        return dest                                     # copied at an earlier check
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    try:
        with open(_releases(root) / name, "rb") as src, open(dest, "wb") as out:
            for block in iter(lambda: src.read(1 << 20), b""):
                if should_stop():
                    raise InterruptedError("Slate is closing")
                digest.update(block)
                out.write(block)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    if digest.hexdigest() != expected:
        dest.unlink(missing_ok=True)
        raise ValueError(
            "The update %s is not the file that was published (its checksum does not "
            "match), so it was deleted and not installed. Ask whoever publishes Slate "
            "updates to publish it again." % name)
    remove_older(dest.parent, dest.name)
    return dest


def launch(installer, relaunch: bool, popen=subprocess.Popen) -> Path:
    """
    Run the installer silently from a .cmd started detached and without a
    window; with relaunch, the .cmd starts this app again afterwards. The
    caller quits Slate straight after. Refused outside an installed Slate.
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError(
            "This copy of Slate runs from source, not from an installation, so it "
            "cannot install updates. Run the installer by hand instead.")
    installer = Path(installer)
    target = installer_target(installer.name)[0]

    def cmd_text(value):
        return str(value).replace("%", "%%")       # % would expand inside a .cmd

    lines = [
        "@echo off",
        "chcp 65001 >nul",
        # The installers fill in the studio folder from this when run silently.
        'set "SLATE_UPDATE_ROOT=%s"' % cmd_text(GlobalConfig.server_root()),
        # Let Slate finish closing (saving, signing out) before the installer replaces it.
        ":wait",
        'tasklist /FI "PID eq %d" /NH | find " %d " >nul && (ping -n 2 127.0.0.1 >nul & goto wait)'
        % (os.getpid(), os.getpid()),
        'start "" /wait "%s" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS'
        % cmd_text(installer),
    ]
    if relaunch:
        lines.append('start "" "%s"' % cmd_text(sys.executable))
    script = installer.with_name("install_%s.cmd" % target)
    script.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    popen(["cmd.exe", "/c", str(script)], close_fds=True,
          creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    logging.info("Started the update %s (relaunch: %s).", installer.name, relaunch)
    return script


class UpdateChecker(QThread):
    """One check: read the manifest, wait (unless required), copy and verify."""
    ready = Signal(dict, str)        # the manifest, the verified installer
    downloading = Signal(str)        # the version being copied
    nothing = Signal(str)            # manifest_missing, invalid_manifest, up_to_date, put_off
    failed = Signal(str)

    def __init__(self, target, parent=None, put_off="", jitter=True):
        super().__init__(parent)
        self.target = target
        self.put_off = put_off          # the version the artist chose to install at close
        self.jitter = jitter

    def stop(self):
        self.requestInterruption()

    def run(self):
        try:
            manifest, why = published(self.target)
            if not offered(manifest):
                if manifest:            # installed: its installer need not sit in %TEMP%
                    try:
                        (download_dir() / manifest["package_name"]).unlink(missing_ok=True)
                    except OSError:
                        pass
                self.nothing.emit(why or "up_to_date")
                return
            if manifest["version"] == self.put_off and not manifest.get("required"):
                self.nothing.emit("put_off")
                return
            until = time.monotonic() + (wait_before_download(manifest) if self.jitter else 0)
            while time.monotonic() < until:
                if self.isInterruptionRequested():
                    return
                self.msleep(500)
            self.downloading.emit(str(manifest["version"]))
            path = download(manifest, should_stop=self.isInterruptionRequested)
            self.ready.emit(manifest, str(path))
        except InterruptedError:
            return
        except Exception as exc:
            logging.warning("The update check failed: %s", exc)
            self.failed.emit(str(exc))
