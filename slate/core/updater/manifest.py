"""
What a release manifest has to contain, where it goes, and how Slate Server
publishes one.

An update is the installer itself (``setup_Slate_Studio_vBETA 2.2.0.exe``),
copied into ``SERVER_ROOT/Updates/releases`` beside ``manifest_<target>.json``,
which names it and carries its SHA-256. Workstations read the manifest
(``update_checker``), copy the installer and refuse it when the hash differs.

A missing hash is a refusal, never a waiver: the old updater verified the
package only when the key happened to be present.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

# Every field the application actually reads.
REQUIRED = ("version", "package_name", "hash_sha256")

# The two workstation apps. The server is updated by hand from its own window.
TARGETS = ("studio", "ops")

# What the installers are called (OutputBaseFilename in the .iss files).
INSTALLER = re.compile(r"setup_Slate_(Studio|Ops)_v(.+)\.exe", re.IGNORECASE)


def manifest_name(target: str) -> str:
    """The manifest's file name: one per target, so two releases never share a pointer."""
    if target not in TARGETS:
        raise ValueError(
            "unknown update target %r - expected one of %s"
            % (target, ", ".join(TARGETS)))
    return "manifest_%s.json" % target


def releases_dir(updates_root) -> Path:
    """Where installers and manifests live, given the ``Updates`` folder. Flat."""
    return Path(updates_root) / "releases"


def build(version: str, package_name: str, hash_sha256: str,
          target: str, **extra) -> dict:
    """A manifest the application can act on; ``extra`` (required, published_at...) is carried."""
    if target not in TARGETS:
        raise ValueError(
            "unknown update target %r - expected one of %s"
            % (target, ", ".join(TARGETS)))
    if not version:
        raise ValueError("a manifest with no version is never offered as an update")
    if not package_name:
        raise ValueError("a manifest with no package_name has nothing to download")
    if not hash_sha256:
        raise ValueError(
            "refusing to write a manifest with no hash: the package would be "
            "installed without being verified")

    manifest = dict(extra)
    manifest.update({
        "version": version,
        "target": target,
        "package_name": package_name,
        "hash_sha256": hash_sha256,
    })
    return manifest


def problems(manifest) -> list:
    """Why this manifest cannot be used, as plain sentences. Empty means usable."""
    found = []
    if not isinstance(manifest, dict):
        return ["the manifest is %s, not an object" % type(manifest).__name__]

    for field in REQUIRED:
        if not manifest.get(field):
            found.append("no %s" % field)

    if not manifest.get("hash_sha256") and manifest.get("sha256"):
        found.append(
            "the hash is under 'sha256'; the application reads 'hash_sha256'")

    digest = manifest.get("hash_sha256") or ""
    if digest and len(digest) != 64:
        found.append("hash_sha256 is %d characters, not the 64 of a SHA-256"
                     % len(digest))

    # The package is opened as <releases>/<package_name>, never anywhere else.
    name = str(manifest.get("package_name") or "")
    if name and Path(name).name != name:
        found.append("package_name %r is not a plain file name" % name)

    return found


# ------------------------------------------------------------ publishing

def installer_target(name: str):
    """(target, version) from an installer's file name; ValueError for any other name."""
    name = Path(str(name)).name
    match = INSTALLER.fullmatch(name)
    if not match:
        raise ValueError(
            "%s is not a Slate workstation installer. Choose a file named "
            "setup_Slate_Studio_v<version>.exe or setup_Slate_Ops_v<version>.exe." % name)
    return match.group(1).lower(), match.group(2).strip()


def sha256_of(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(path, manifest: dict) -> None:
    """Whole or not at all: a workstation never reads half a manifest."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def publish(installer, releases, required: bool = False, progress=lambda _text: None) -> dict:
    """
    Offer this installer to every workstation of its target: copy it into the
    releases folder, hash the copy, only then write the manifest, then remove
    that target's older installers. Raises with a plain reason when it cannot.
    """
    installer = Path(installer)
    target, version = installer_target(installer.name)
    releases = Path(releases)
    releases.mkdir(parents=True, exist_ok=True)
    dest = releases / installer.name

    progress("Copying %s to the studio folder..." % installer.name)
    part = dest.with_name(dest.name + ".part")
    with open(installer, "rb") as src, open(part, "wb") as out:
        for block in iter(lambda: src.read(1 << 20), b""):
            out.write(block)
    os.replace(part, dest)

    progress("Checking the copy of %s..." % installer.name)
    manifest = build(version, dest.name, sha256_of(dest), target,
                     required=bool(required),
                     published_at=datetime.now().isoformat(timespec="seconds"))
    write_manifest(releases / manifest_name(target), manifest)
    remove_older(releases, dest.name)
    return manifest


def remove_older(folder, keep: str) -> None:
    """Delete the other installers of keep's target in folder (they are hundreds of MB each)."""
    target = installer_target(keep)[0]
    for old in Path(folder).glob("setup_Slate_*.exe"):
        try:
            same = installer_target(old.name)[0] == target
        except ValueError:
            continue
        if same and old.name.lower() != keep.lower():
            try:
                old.unlink()
            except OSError as exc:      # in use, e.g. a workstation is copying it
                logging.getLogger(__name__).warning(
                    "%s was not removed (%s); it goes next time.", old.name, exc)


def require_now(releases) -> list:
    """Mark every published manifest required. Returns labels like 'studio BETA 2.2.0'."""
    done = []
    for target in TARGETS:
        path = Path(releases) / manifest_name(target)
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if problems(manifest):
            continue
        manifest["required"] = True
        write_manifest(path, manifest)
        done.append("%s %s" % (target, manifest["version"]))
    return done
