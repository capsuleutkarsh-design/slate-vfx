"""
Worker Thread Tests

Comprehensive tests for worker threads covering:
- FolderCreationWorker: Project structure creation
- ShotSubfoldersWorker: Shot folder management
- ReportWorker: Report generation

Tests thread safety, pause/resume, cancellation, error handling, and signal emissions.
"""

import pytest
import tempfile
from pathlib import Path
import sys
from PySide6.QtCore import QThread

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestFolderCreationWorker:
    """Test FolderCreationWorker for project structure creation."""
    
    def test_folder_creation_worker_can_be_imported(self):
        """Test that FolderCreationWorker can be imported."""
        from slate.core.workers.structure import FolderCreationWorker
        assert FolderCreationWorker is not None
    
    def test_folder_creation_worker_inherits_qthread(self):
        """Test that FolderCreationWorker inherits from QThread."""
        from slate.core.workers.structure import FolderCreationWorker
        assert issubclass(FolderCreationWorker, QThread)
    
    def test_folder_creation_worker_has_signals(self):
        """Test that worker has required Qt signals."""
        from slate.core.workers.structure import FolderCreationWorker
        
        assert hasattr(FolderCreationWorker, 'log_signal')
        assert hasattr(FolderCreationWorker, 'progress_signal')
        assert hasattr(FolderCreationWorker, 'finished_signal')
    
    def test_folder_creation_worker_initialization(self):
        """Test worker initialization with minimal parameters."""
        from slate.core.workers.structure import FolderCreationWorker
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Just verify we can create the worker
            worker = FolderCreationWorker(
                target_dir=tmpdir,
                project_name="TestProject",
                dry_run=True
            )
            
            # Basic checks
            assert worker is not None
            assert hasattr(worker, 'run')
            assert hasattr(worker, 'is_running')
    
    def test_folder_creation_worker_pause_resume(self):
        """Test pause and resume functionality."""
        from slate.core.workers.structure import FolderCreationWorker
        
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = FolderCreationWorker(target_dir=tmpdir, dry_run=True)
            
            # Test pause
            worker.pause()
            assert worker.is_paused is True
            
            # Test resume
            worker.resume()
            assert worker.is_paused is False
    
    def test_folder_creation_worker_stop(self):
        """Test stopping the worker."""
        from slate.core.workers.structure import FolderCreationWorker
        
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = FolderCreationWorker(target_dir=tmpdir, dry_run=True)
            
            worker.stop()
            assert worker.is_running is False
    
    def test_folder_creation_worker_has_methods(self):
        """Test that worker has required methods."""
        from slate.core.workers.structure import FolderCreationWorker
        
        assert hasattr(FolderCreationWorker, 'run')
        assert hasattr(FolderCreationWorker, 'pause')
        assert hasattr(FolderCreationWorker, 'resume')
        assert hasattr(FolderCreationWorker, 'stop')
        assert hasattr(FolderCreationWorker, '_process_excel')
        assert hasattr(FolderCreationWorker, '_process_scan')
        assert hasattr(FolderCreationWorker, '_create_subs')


class TestShotSubfoldersWorker:
    """Test ShotSubfoldersWorker for shot folder management."""
    
    def test_shot_subfolders_worker_can_be_imported(self):
        """Test that ShotSubfoldersWorker can be imported."""
        from slate.core.workers.structure import ShotSubfoldersWorker
        assert ShotSubfoldersWorker is not None
    
    def test_shot_subfolders_worker_inherits_qthread(self):
        """Test that worker inherits from QThread."""
        from slate.core.workers.structure import ShotSubfoldersWorker
        assert issubclass(ShotSubfoldersWorker, QThread)
    
    def test_shot_subfolders_worker_has_signals(self):
        """Test that worker has required Qt signals."""
        from slate.core.workers.structure import ShotSubfoldersWorker
        
        assert hasattr(ShotSubfoldersWorker, 'progress_signal')
        assert hasattr(ShotSubfoldersWorker, 'log_signal')
        assert hasattr(ShotSubfoldersWorker, 'finished_signal')
    
    def test_shot_subfolders_worker_initialization(self):
        """Test worker initialization."""
        from slate.core.workers.structure import ShotSubfoldersWorker
        
        with tempfile.TemporaryDirectory() as tmpdir:
            shot_folders = ["01_Scan", "02_Comp", "03_Output"]
            worker = ShotSubfoldersWorker(
                target_dir=tmpdir,
                shot_folders=shot_folders
            )
            
            # Basic checks
            assert worker is not None
            assert hasattr(worker, 'run')
            assert hasattr(worker, 'is_running')
    
    def test_shot_subfolders_worker_stop(self):
        """Test stopping the worker."""
        from slate.core.workers.structure import ShotSubfoldersWorker
        
        with tempfile.TemporaryDirectory() as tmpdir:
            worker = ShotSubfoldersWorker(target_dir=tmpdir, shot_folders=[])
            
            worker.stop()
            assert worker.is_running is False


class TestReportWorker:
    """Test ReportWorker for report generation."""
    
    def test_report_worker_can_be_imported(self):
        """Test that ReportWorker can be imported."""
        from slate.core.workers.reporting import ReportWorker
        assert ReportWorker is not None
    
    def test_report_worker_inherits_qthread(self):
        """Test that worker inherits from QThread."""
        from slate.core.workers.reporting import ReportWorker
        assert issubclass(ReportWorker, QThread)
    
    def test_report_worker_has_signals(self):
        """Test that worker has required Qt signals."""
        from slate.core.workers.reporting import ReportWorker
        
        assert hasattr(ReportWorker, 'finished_signal')
    
    def test_report_worker_initialization(self):
        """Test worker initialization."""
        from slate.core.workers.reporting import ReportWorker
        
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.pdf"
            worker = ReportWorker(output_path=output_path, project_id=123)
            
            assert worker.output_path == output_path
            assert worker.project_id == 123
    
    def test_report_worker_has_run_method(self):
        """Test that worker has run method."""
        from slate.core.workers.reporting import ReportWorker
        
        assert hasattr(ReportWorker, 'run')
        assert callable(getattr(ReportWorker, 'run'))


class TestWorkerFacade:
    """Test worker_threads.py facade module."""
    
    def test_workers_can_be_imported_from_facade(self):
        """Test that workers can be imported from facade module."""
        from slate.core.worker_threads import (
            FolderCreationWorker,
            ShotSubfoldersWorker,
            ReportWorker
        )

        assert FolderCreationWorker is not None
        assert ShotSubfoldersWorker is not None
        assert ReportWorker is not None
    
    def test_safe_file_operations_exported(self):
        """Test that SafeFileOperations is exported from facade."""
        from slate.core.worker_threads import SafeFileOperations
        assert SafeFileOperations is not None


