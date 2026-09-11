"""
Two ingests colliding, and picking up after one fails.

2.3  Two people pointing Build & Ingest at the same project both allocate scan
     v002, and two deliveries end up mixed in one folder.
2.4  A run that dies at minute thirty-five of forty had to be repeated in full.
"""

import json

import pytest

from ut_vfx.core.domain.ingest_lock import (
    IngestLock, IngestLocked, current_holder,
)
from ut_vfx.core.domain.ingest_retry import (
    latest_manifest, load_failures, retry_failures,
)


class TestIngestLock:

    def test_a_lock_is_taken_and_released(self, tmp_path):
        project = tmp_path / "PRJ"
        project.mkdir()

        with IngestLock(project, holder="coord1") as lock:
            assert lock.acquired is True
            assert current_holder(project) is not None

        assert current_holder(project) is None

    def test_a_second_ingest_is_refused(self, tmp_path):
        project = tmp_path / "PRJ"
        project.mkdir()

        with IngestLock(project, holder="coord1"):
            with pytest.raises(IngestLocked):
                IngestLock(project, holder="coord2").acquire()

    def test_the_refusal_names_who_holds_it(self, tmp_path):
        project = tmp_path / "PRJ"
        project.mkdir()

        with IngestLock(project, holder="coord1"):
            try:
                IngestLock(project, holder="coord2").acquire()
                pytest.fail("the second ingest was allowed")
            except IngestLocked as exc:
                assert "coord1" in str(exc)
                assert exc.info.machine

    def test_the_lock_is_released_even_when_the_run_raises(self, tmp_path):
        project = tmp_path / "PRJ"
        project.mkdir()

        with pytest.raises(ValueError):
            with IngestLock(project, holder="coord1"):
                raise ValueError("the ingest blew up")

        assert current_holder(project) is None

    def test_an_abandoned_lock_is_cleared(self, tmp_path, monkeypatch):
        """A crashed machine must not lock a project out forever."""
        import os
        import time
        from ut_vfx.core.domain import ingest_lock as module

        project = tmp_path / "PRJ"
        project.mkdir()

        stale = IngestLock(project, holder="ghost").acquire()
        # Age the lock past the abandonment window.
        old = time.time() - (module.STALE_AFTER.total_seconds() + 60)
        os.utime(stale.path, (old, old))

        assert current_holder(project) is None      # reported as not held
        assert IngestLock(project, holder="coord2").acquire().acquired is True

    def test_touch_keeps_a_long_run_alive(self, tmp_path):
        project = tmp_path / "PRJ"
        project.mkdir()

        lock = IngestLock(project, holder="coord1").acquire()
        lock.touch()        # must not raise, must not release
        assert current_holder(project) is not None
        lock.release()

    def test_releasing_a_lock_never_taken_is_harmless(self, tmp_path):
        project = tmp_path / "PRJ"
        project.mkdir()
        IngestLock(project).release()      # must not raise


class TestRetry:

    def _manifest(self, tmp_path, failures):
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps({"project": "PRJ", "failed": failures}),
                        encoding="utf-8")
        return path

    def test_a_failed_file_is_moved_on_the_second_attempt(self, tmp_path):
        source = tmp_path / "src" / "SH010.0001.exr"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"plate-data")
        destination = tmp_path / "dst" / "SH010.0001.exr"

        manifest = self._manifest(tmp_path, [{
            "file": source.name, "source": str(source),
            "destination": str(destination), "error": "network dropped",
        }])

        result = retry_failures(manifest)

        assert result.ok
        assert result.recovered == ["SH010.0001.exr"]
        assert result.still_failing == []
        assert destination.exists()
        assert destination.read_bytes() == b"plate-data"
        assert not source.exists()          # moved, not copied

    def test_a_file_no_longer_on_the_drive_is_reported_separately(self, tmp_path):
        """Somebody moving it by hand is not the same as a broken copy."""
        manifest = self._manifest(tmp_path, [{
            "file": "gone.exr",
            "source": str(tmp_path / "src" / "gone.exr"),
            "destination": str(tmp_path / "dst" / "gone.exr"),
            "error": "network dropped",
        }])

        result = retry_failures(manifest)

        assert result.missing == ["gone.exr"]
        assert result.recovered == []
        assert result.still_failing == []

    def test_a_file_that_arrived_anyway_counts_as_recovered(self, tmp_path):
        destination = tmp_path / "dst" / "SH010.0001.exr"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"plate-data")

        manifest = self._manifest(tmp_path, [{
            "file": destination.name,
            "source": str(tmp_path / "src" / "SH010.0001.exr"),
            "destination": str(destination),
            "error": "reported as failed but it landed",
        }])

        result = retry_failures(manifest)
        assert result.recovered == ["SH010.0001.exr"]

    def test_a_file_that_fails_again_is_still_reported(self, tmp_path, monkeypatch):
        from ut_vfx.core.domain import ingest_retry

        source = tmp_path / "src" / "SH010.0001.exr"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"x")

        monkeypatch.setattr(
            ingest_retry.SafeFileOperations, "safe_move_with_verification",
            staticmethod(lambda s, d, verify_checksum=True: (False, "still broken", 0)),
        )

        manifest = self._manifest(tmp_path, [{
            "file": source.name, "source": str(source),
            "destination": str(tmp_path / "dst" / source.name),
            "error": "network dropped",
        }])

        result = retry_failures(manifest)

        assert result.recovered == []
        assert len(result.still_failing) == 1
        assert result.still_failing[0]["error"] == "still broken"
        assert source.exists(), "a failed retry must not remove the source"

    def test_progress_is_reported(self, tmp_path):
        source = tmp_path / "src" / "a.exr"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"x")

        manifest = self._manifest(tmp_path, [{
            "file": "a.exr", "source": str(source),
            "destination": str(tmp_path / "dst" / "a.exr"), "error": "x",
        }])

        seen = []
        retry_failures(manifest, progress=lambda d, t, n: seen.append((d, t, n)))
        assert seen == [(1, 1, "a.exr")]

    def test_an_unreadable_manifest_is_not_fatal(self, tmp_path):
        broken = tmp_path / "broken.json"
        broken.write_text("{ not json", encoding="utf-8")

        assert load_failures(broken) == []
        assert retry_failures(broken).recovered == []

    def test_entries_without_a_destination_are_ignored(self, tmp_path):
        """An older manifest recorded no destination, so it cannot be retried."""
        manifest = self._manifest(tmp_path, [{"file": "a.exr", "source": "/x/a.exr"}])
        assert load_failures(manifest) == []

    def test_the_latest_manifest_is_found(self, tmp_path):
        from ut_vfx.core.domain.delivery_report import REPORT_DIRNAME

        project = tmp_path / "PRJ"
        reports = project / REPORT_DIRNAME
        reports.mkdir(parents=True)
        (reports / "manifest_20260101_000000.json").write_text("{}", encoding="utf-8")
        (reports / "manifest_20260909_120000.json").write_text("{}", encoding="utf-8")

        found = latest_manifest(project)
        assert found is not None
        assert "20260909" in found.name

    def test_no_manifest_is_not_an_error(self, tmp_path):
        assert latest_manifest(tmp_path / "empty") is None


class TestFastModeIsHonest:
    """
    Fast Mode does not turn verification off - every copy is still checked
    against the source size, which is what catches a truncated file. Only the
    MD5 comparison is skipped.
    """

    def test_a_size_mismatch_is_caught_without_a_checksum(self, tmp_path):
        from ut_vfx.core.infra.file_operations import SafeFileOperations

        source = tmp_path / "a.exr"
        source.write_bytes(b"1234567890")
        destination = tmp_path / "out" / "a.exr"

        ok, message, _ = SafeFileOperations.safe_copy_with_verification(
            source, destination, verify_checksum=False,
        )
        assert ok is True

        # A destination that does not match the source size is rejected.
        verified, reason = SafeFileOperations._verify_copy_result(
            source, destination, None, original_size=999,
        )
        assert verified is False
        assert "Size mismatch" in reason
