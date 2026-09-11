"""
What a release manifest has to contain, and where it goes.

The application reads a manifest in two halves. ``update_checker`` decides
whether there is anything worth offering, and ``sidecar_engine`` fetches the
package and verifies it. Until now each publishing tool decided the shape of
that file on its own, and they disagreed:

  - ``build_update_package.py`` wrote ``hash_sha256`` into
    ``releases/manifest_client.json`` - which is what the application reads.
  - ``release_publisher.py`` wrote ``sha256`` into ``releases/v1.2.3/manifest.json``
    - a different key, a different filename, one directory deeper, and with no
    notion of which half of the product it was for.

Neither tool was wrong about anything it could see. There was simply nowhere
that said what the right answer was, so the contract only existed in the reader.

The dangerous part was the hash. The sidecar verified the package only when the
key happened to be present, so the manifest that used the wrong key did not fail
the check - it skipped it, and the update installed unverified. A rule that is
enforced only when someone remembers to supply the input is not a rule.

So the shape lives here. The tools write through :func:`build`, the sidecar
validates with :func:`problems`, and a missing hash is now a refusal rather
than a waiver.
"""

from __future__ import annotations

# Every field the application actually reads. Keep this in step with
# update_checker.run() and SidecarEngine.stage_update().
REQUIRED = ("version", "package_name", "hash_sha256")

# The two halves of the product that can be updated independently.
TARGETS = ("client", "server")


def manifest_name(target: str) -> str:
    """
    The filename ``update_checker`` looks for.

    It asks for one target at a time, so the target is part of the name rather
    than a field inside a shared file - two independent releases must not
    overwrite each other's pointer.
    """
    if target not in TARGETS:
        raise ValueError(
            "unknown update target %r - expected one of %s"
            % (target, ", ".join(TARGETS)))
    return "manifest_%s.json" % target


def releases_dir(updates_root):
    """
    Where packages and manifests live, given the ``Updates`` folder.

    Flat, deliberately: the sidecar resolves a package as
    ``<releases>/<package_name>``, so a manifest in a per-version subfolder
    points at a file the downloader will never find.
    """
    from pathlib import Path
    return Path(updates_root) / "releases"


def build(version: str, package_name: str, hash_sha256: str,
          target: str, **extra) -> dict:
    """
    A manifest the application can actually act on.

    Anything else a publisher wants to record - release notes, a date, a
    "critical" flag - is carried through in ``extra`` and ignored by the reader.
    """
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
    """
    Why this manifest cannot be used, as plain sentences. Empty means usable.

    Returns a list rather than raising so the caller decides what a broken
    manifest means: for the sidecar it is fatal, for a build tool it is
    something to print before anybody publishes it.
    """
    found = []
    if not isinstance(manifest, dict):
        return ["the manifest is %s, not an object" % type(manifest).__name__]

    for field in REQUIRED:
        if not manifest.get(field):
            found.append("no %s" % field)

    # The commonest way to get here, and the one that used to pass silently.
    if not manifest.get("hash_sha256") and manifest.get("sha256"):
        found.append(
            "the hash is under 'sha256'; the application reads 'hash_sha256'")

    digest = manifest.get("hash_sha256") or ""
    if digest and len(digest) != 64:
        found.append("hash_sha256 is %d characters, not the 64 of a SHA-256"
                     % len(digest))

    return found
