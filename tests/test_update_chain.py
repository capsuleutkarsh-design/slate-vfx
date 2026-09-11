"""
A package published the way the tools publish it, found and verified the way a
workstation finds and verifies it.

The unit tests next door assert the manifest contract in isolation. This one
walks the whole path against a real folder, because every defect in the updater
lived in the joins rather than in any one part: each piece worked, and together
they addressed different files. A test of the pieces would have passed
throughout.

Nothing is mocked except SERVER_ROOT, which has to point somewhere disposable.
"""

import hashlib
import json
import zipfile

import pytest

from slate.core.infra.global_config import GlobalConfig
from slate.core.updater.manifest import build as build_manifest, manifest_name


@pytest.fixture
def central(tmp_path, monkeypatch):
    """A central server folder with nothing published in it yet."""
    (tmp_path / "Updates" / "releases").mkdir(parents=True)
    monkeypatch.setattr(GlobalConfig, "server_root", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def published(central):
    """One client package, published the way the release tools publish one."""
    releases = central / "Updates" / "releases"

    zip_name = "Slate_Client_Update.zip"
    zip_path = releases / zip_name
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("Slate.exe", b"pretend this is the new build")

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    manifest = build_manifest(version="9.9.9", package_name=zip_name,
                              hash_sha256=digest, target="client")
    (releases / manifest_name("client")).write_text(
        json.dumps(manifest, indent=4), encoding="utf-8")
    return manifest


def _check(target="client"):
    from slate.core.updater.update_checker import UpdateChecker

    checker = UpdateChecker(None, manual_mode=True, target=target)
    offered = {}
    checker.update_available.connect(lambda m: offered.update(m))
    checker.run()
    return checker, offered


class TestFindingIt:

    def test_a_published_package_is_offered(self, published):
        checker, offered = _check()
        assert checker.last_result_reason == "update_available"
        assert offered["package_name"] == published["package_name"]

    def test_the_client_looks_for_the_name_the_tools_write(self, published, central):
        """
        The build tool used to write manifest_vfx.json while the client asked
        for manifest_client.json, so nothing was ever found. Renaming the file
        must break this test.
        """
        releases = central / "Updates" / "releases"
        (releases / manifest_name("client")).rename(releases / "manifest_vfx.json")
        checker, _ = _check()
        assert checker.last_result_reason == "manifest_missing"

    def test_an_empty_central_folder_is_not_an_error(self, central):
        checker, _ = _check()
        assert checker.last_result_reason == "manifest_missing"


class TestVerifyingIt:

    def test_a_good_package_stages(self, published):
        from slate.core.updater.sidecar_engine import SidecarEngine

        engine = SidecarEngine(published)
        assert engine.stage_update() is True
        assert engine.local_zip.exists()

    def test_a_tampered_package_is_refused(self, published):
        from slate.core.updater.sidecar_engine import SidecarEngine

        wrong = dict(published, hash_sha256="b" * 64)
        assert SidecarEngine(wrong).stage_update() is False

    def test_a_manifest_with_the_old_hash_key_is_refused(self, published):
        """
        The defect worth a test of its own: the sidecar verified only when
        hash_sha256 was present, so this manifest used to install without being
        checked at all. It must now be refused, not waved through.
        """
        from slate.core.updater.sidecar_engine import SidecarEngine

        legacy = {k: v for k, v in published.items() if k != "hash_sha256"}
        legacy["sha256"] = published["hash_sha256"]
        assert SidecarEngine(legacy).stage_update() is False

    def test_nothing_is_downloaded_when_the_manifest_is_unusable(self, published, tmp_path):
        """
        A broken manifest is rejected before the package is copied anywhere -
        there is no reason to move bytes for an update that cannot be installed.
        """
        from slate.core.updater.sidecar_engine import SidecarEngine

        engine = SidecarEngine({"version": "9.9.9"})
        assert engine.stage_update() is False
        assert not hasattr(engine, "local_zip")
