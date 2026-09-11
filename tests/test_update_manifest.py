"""
The contract between whoever publishes an update and whoever installs it.

Everything that went wrong with the updater went wrong in the gap between those
two: the publishers each invented a manifest shape, and the reader quietly
tolerated the difference instead of rejecting it. Nothing failed, so nothing was
noticed - the client update path had simply never been reachable, and one
publisher's packages installed without being verified at all.

These tests assert the contract from both ends: that the reader refuses anything
it cannot act on, and that the names the publishers write are the names the
reader opens.
"""

import json

import pytest

from slate.core.updater.manifest import (
    REQUIRED,
    TARGETS,
    build,
    manifest_name,
    problems,
    releases_dir,
)


GOOD_HASH = "a" * 64


def a_good_manifest(**overrides):
    manifest = build(version="1.4.0", package_name="Slate_Client_Update.zip",
                     hash_sha256=GOOD_HASH, target="client")
    manifest.update(overrides)
    return manifest


class TestWhatTheReaderRequires:

    def test_a_complete_manifest_is_accepted(self):
        assert problems(a_good_manifest()) == []

    @pytest.mark.parametrize("field", REQUIRED)
    def test_every_required_field_is_actually_required(self, field):
        manifest = a_good_manifest()
        del manifest[field]
        assert problems(manifest), "%s can go missing without complaint" % field

    def test_a_missing_hash_is_refused_rather_than_waived(self):
        """
        The specific defect. The sidecar used to verify only `if expected_hash`,
        so a manifest without one skipped the check instead of failing it.
        """
        manifest = a_good_manifest()
        del manifest["hash_sha256"]
        assert any("hash_sha256" in p for p in problems(manifest))

    def test_the_wrong_hash_key_is_named_explicitly(self):
        """
        release_publisher wrote "sha256". Saying so is the difference between a
        five minute fix and an afternoon.
        """
        manifest = a_good_manifest()
        del manifest["hash_sha256"]
        manifest["sha256"] = GOOD_HASH
        assert any("hash_sha256" in p and "sha256" in p for p in problems(manifest))

    def test_a_truncated_digest_is_caught(self):
        assert problems(a_good_manifest(hash_sha256="abc123"))

    def test_something_that_is_not_a_manifest_at_all(self):
        assert problems(["not", "a", "manifest"])
        assert problems(None)


class TestWhatThePublishersMayProduce:

    def test_a_manifest_cannot_be_built_without_a_hash(self):
        with pytest.raises(ValueError, match="without being verified"):
            build(version="1.0.0", package_name="x.zip",
                  hash_sha256="", target="client")

    def test_a_manifest_cannot_be_built_for_an_unknown_target(self):
        """
        The build tool knows vfx, ops and server; the application knows client
        and server. Publishing "manifest_vfx.json" is how the client update path
        stayed invisible.
        """
        with pytest.raises(ValueError, match="unknown update target"):
            build(version="1.0.0", package_name="x.zip",
                  hash_sha256=GOOD_HASH, target="vfx")

    def test_extra_fields_are_carried_but_do_not_break_the_contract(self):
        manifest = build(version="1.0.0", package_name="x.zip",
                         hash_sha256=GOOD_HASH, target="server",
                         notes="Fixes the thing", critical=True)
        assert manifest["notes"] == "Fixes the thing"
        assert problems(manifest) == []

    def test_what_is_built_survives_a_round_trip_through_json(self, tmp_path):
        """A manifest is read back off disk, not handed over in memory."""
        path = tmp_path / manifest_name("client")
        path.write_text(json.dumps(a_good_manifest()), encoding="utf-8")
        assert problems(json.loads(path.read_text(encoding="utf-8"))) == []


class TestTheNamesMatchTheReader:

    @pytest.mark.parametrize("target", TARGETS)
    def test_the_filename_is_the_one_update_checker_opens(self, target):
        """
        update_checker builds its path as
            server_root / "Updates" / "releases" / f"manifest_{self.target}.json"
        so this is that expression, written out.
        """
        assert manifest_name(target) == "manifest_%s.json" % target

    def test_the_targets_are_the_ones_the_application_asks_for(self):
        """
        settings_tab passes target="client"; the server window passes "server".
        Anything else names a file nobody opens.
        """
        assert set(TARGETS) == {"client", "server"}

    def test_releases_is_flat(self, tmp_path):
        """
        The sidecar resolves a package as <releases>/<package_name>, so a
        manifest published into a per-version subfolder points at a file the
        downloader cannot reach.
        """
        assert releases_dir(tmp_path / "Updates") == tmp_path / "Updates" / "releases"

    def test_an_unknown_target_has_no_filename(self):
        with pytest.raises(ValueError):
            manifest_name("vfx")
