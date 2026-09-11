"""
Headless integration tests for the Auto-Scan ingest flow.

Auto-Scan (header mode "Auto-Build & Move") is the single supported ingest path:
FolderCreationWorker builds the project structure and moves the client scans
into it in one pass.

These tests run the worker directly against a temp filesystem - no GUI, no
event loop - and assert on what actually lands on disk.
"""

from pathlib import Path

from ut_vfx.core.workers.structure import FolderCreationWorker


TEMPLATE_DATA = (
    ["01_Scan", "05_Reels"],   # base_folders
    [],                        # production_subfolders
    [],                        # outsource_subfolders
    ["01_Scan", "07_Comp", "08_Output"],   # shot_folders
)


def _build_client_drive(root: Path, with_root_level_media=False, with_junk=False):
    """Create a realistic client delivery: ReelA/{SH010,SH020}, ReelB/SH030."""
    for reel, shots in (("ReelA", ("SH010", "SH020")), ("ReelB", ("SH030",))):
        for shot in shots:
            shot_dir = root / reel / shot
            shot_dir.mkdir(parents=True)
            for frame in (1, 2):
                (shot_dir / f"{shot}.{frame:04d}.exr").write_bytes(b"exr-data")

    if with_junk:
        junk_dir = root / "ReelA" / "SH010"
        (junk_dir / "Thumbs.db").write_bytes(b"junk")
        (junk_dir / "scratch.tmp").write_bytes(b"junk")
        (junk_dir / "notes.bak").write_bytes(b"junk")

    if with_root_level_media:
        (root / "stray_plate.exr").write_bytes(b"exr-data")

    return root


def _run_autoscan(source, target, mock_db, project_name="PRJ"):
    """Run FolderCreationWorker synchronously in Auto-Scan configuration."""
    import ut_vfx.core.workers.structure as structure_module

    structure_module.database_manager = mock_db

    worker = FolderCreationWorker(
        target_dir=target,
        source_scan_path=source,
        project_name=project_name,
        template_data=TEMPLATE_DATA,
        template_type="standard",
        overwrite=False,
        dry_run=False,
        format_mapping={},
        fast_mode=True,
    )

    logs = []
    worker.log_signal.connect(logs.append)
    worker.run()   # synchronous - no QThread.start()
    return worker, logs


def _relative_files(root: Path):
    """Every file under root, as posix-style relative paths."""
    if not root.exists():
        return set()
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


class TestAutoScanIngest:
    """Auto-Build & Move: structure creation plus scan ingest in one pass."""

    def test_reel_shot_hierarchy_is_preserved(self, temp_vfx_root, mock_db):
        """Reel/shot layout on the client drive is mirrored into 05_Reels."""
        source = _build_client_drive(temp_vfx_root / "ClientDrive")
        target = temp_vfx_root / "Projects"
        target.mkdir()

        worker, _ = _run_autoscan(source, target, mock_db)

        reels = target / "PRJ" / "05_Reels"
        # Each delivery lands in its own scan version folder.
        assert _relative_files(reels) == {
            "ReelA/SH010/01_Scan/v001/EXR/SH010.0001.exr",
            "ReelA/SH010/01_Scan/v001/EXR/SH010.0002.exr",
            "ReelA/SH020/01_Scan/v001/EXR/SH020.0001.exr",
            "ReelA/SH020/01_Scan/v001/EXR/SH020.0002.exr",
            "ReelB/SH030/01_Scan/v001/EXR/SH030.0001.exr",
            "ReelB/SH030/01_Scan/v001/EXR/SH030.0002.exr",
        }
        assert worker.files_moved == 6
        assert worker.errors == 0

    def test_root_level_files_are_not_left_behind(self, temp_vfx_root, mock_db):
        """
        Regression: a loose file at the root of the client drive used to be
        silently dropped whenever reel/shot folders were also present - the
        worker reported success while leaving media on the source drive.
        """
        source = _build_client_drive(temp_vfx_root / "ClientDrive",
                                     with_root_level_media=True)
        target = temp_vfx_root / "Projects"
        target.mkdir()

        worker, _ = _run_autoscan(source, target, mock_db)

        moved = _relative_files(target / "PRJ" / "05_Reels")

        # The stray file made it across...
        assert any(name.endswith("stray_plate.exr") for name in moved), (
            f"root-level file was dropped; destination holds: {sorted(moved)}"
        )
        # ...and the reel/shot hierarchy is untouched by its presence.
        assert "ReelA/SH010/01_Scan/v001/EXR/SH010.0001.exr" in moved
        assert "ReelB/SH030/01_Scan/v001/EXR/SH030.0002.exr" in moved

        # Nothing media-bearing is still sitting on the client drive.
        assert _relative_files(source) == set()
        assert worker.files_moved == 7

    def test_junk_files_are_not_ingested(self, temp_vfx_root, mock_db):
        """
        Regression: Thumbs.db / *.tmp / *.bak reached the project because the
        sequence-detection path bypassed the junk filter (fileseq reports every
        standalone file as a one-frame sequence).
        """
        source = _build_client_drive(temp_vfx_root / "ClientDrive", with_junk=True)
        target = temp_vfx_root / "Projects"
        target.mkdir()

        worker, _ = _run_autoscan(source, target, mock_db)

        moved = _relative_files(target / "PRJ" / "05_Reels")
        offenders = [n for n in moved
                     if n.lower().endswith((".db", ".tmp", ".bak"))]
        assert not offenders, f"junk files were ingested: {offenders}"

        assert worker.files_moved == 6
        assert worker.errors == 0

    def test_failed_moves_are_reported_as_errors(self, temp_vfx_root, mock_db,
                                                 monkeypatch):
        """A move that fails must be counted, not reported as a success."""
        import ut_vfx.core.workers.structure as structure_module

        def always_fail(src, dst, verify_checksum=True):
            return (False, "SIMULATED: destination unreachable", 0)

        monkeypatch.setattr(
            structure_module.SafeFileOperations,
            "safe_move_with_verification",
            staticmethod(always_fail),
        )

        source = _build_client_drive(temp_vfx_root / "ClientDrive")
        target = temp_vfx_root / "Projects"
        target.mkdir()

        worker, _ = _run_autoscan(source, target, mock_db)

        assert worker.files_moved == 0
        assert worker.errors == 6

    def test_accepts_string_target_dir(self, temp_vfx_root, mock_db):
        """Callers passing a plain string path must not blow up."""
        source = _build_client_drive(temp_vfx_root / "ClientDrive")
        target = temp_vfx_root / "Projects"
        target.mkdir()

        worker, _ = _run_autoscan(source, str(target), mock_db)

        assert worker.errors == 0
        assert (target / "PRJ" / "05_Reels").exists()


class TestProgressAndDryRun:
    """Progress reporting and dry-run honesty."""

    def test_progress_is_global_and_monotonic(self, temp_vfx_root, mock_db):
        """
        Progress used to be emitted per shot (resetting to 0 for each) and not
        at all on the sequence path, so the bar barely moved on a real delivery.
        """
        source = _build_client_drive(temp_vfx_root / "ClientDrive")
        target = temp_vfx_root / "Projects"
        target.mkdir()

        import ut_vfx.core.workers.structure as structure_module
        structure_module.database_manager = mock_db

        worker = FolderCreationWorker(
            target_dir=target, source_scan_path=source, project_name="PRJ",
            template_data=TEMPLATE_DATA, fast_mode=True, format_mapping={},
        )
        seen = []
        worker.progress_signal.connect(lambda value, text: seen.append(value))
        worker.run()

        assert worker._total_files == 6
        assert worker._processed_files == 6
        assert seen, "no progress was reported at all"
        assert all(b >= a for a, b in zip(seen, seen[1:])), f"progress went backwards: {seen}"
        assert max(seen) <= 99

    def test_dry_run_writes_nothing(self, temp_vfx_root, mock_db):
        """Dry run must plan the work without touching either side."""
        source = _build_client_drive(temp_vfx_root / "ClientDrive")
        target = temp_vfx_root / "Projects"
        target.mkdir()

        import ut_vfx.core.workers.structure as structure_module
        structure_module.database_manager = mock_db

        worker = FolderCreationWorker(
            target_dir=target, source_scan_path=source, project_name="PRJ",
            template_data=TEMPLATE_DATA, dry_run=True, fast_mode=True,
            format_mapping={},
        )
        logs = []
        worker.log_signal.connect(logs.append)
        worker.run()

        # Counts are reported...
        assert worker.files_moved == 6
        # ...but nothing was created or moved.
        assert _relative_files(target) == set()
        assert len(_relative_files(source)) == 6
        assert any(line.startswith("[DRY]") for line in logs)
        assert not any(line.startswith("[OK]") for line in logs)

    def test_database_outage_does_not_fail_the_ingest(self, temp_vfx_root, mock_db,
                                                     monkeypatch):
        """Bookkeeping is best-effort; files still move when the DB is down."""
        import ut_vfx.core.workers.structure as structure_module

        class DeadDB:
            def __getattr__(self, name):
                def boom(*a, **k):
                    raise RuntimeError("database unavailable")
                return boom

        monkeypatch.setattr(structure_module, "database_manager", DeadDB())

        source = _build_client_drive(temp_vfx_root / "ClientDrive")
        target = temp_vfx_root / "Projects"
        target.mkdir()

        worker = FolderCreationWorker(
            target_dir=target, source_scan_path=source, project_name="PRJ",
            template_data=TEMPLATE_DATA, fast_mode=True, format_mapping={},
        )
        result = {}
        worker.finished_signal.connect(lambda *a: result.update(args=a))
        worker.run()

        assert result["args"][0] is True, f"ingest reported failure: {result['args']}"
        assert worker.files_moved == 6
        assert worker.errors == 0
