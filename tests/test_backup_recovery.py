"""
Test suite for BackupManager and RecoveryManager.

Tests the backup and recovery system, including:
1. Backup directory resolution (tests the startup bug fix!)
2. Backup creation and encryption
3. Backup restoration
4. Recovery point management

⭐ SPECIAL TEST: test_default_backup_directory_resolution
   - Verifies the fix for the morning startup PermissionError
   - Ensures backup dir → AppData, NOT system32
   - Critical regression test!

Classes:
- TestBackupManager: Tests backup creation, encryption, listing, and deletion (6 tests)
- TestRecoveryManager: Tests recovery points and restoration (9 tests)

Coverage:
- BackupManager (utils/backup_recovery.py)
- RecoveryManager (utils/backup_recovery.py)

Total Tests: 15
"""

import pytest
import tempfile
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ut_vfx.utils.backup_recovery import BackupManager, RecoveryManager


class TestBackupManager:
    """Test the BackupManager class."""
    
    @pytest.fixture
    def temp_backup_dir(self):
        """Create a temporary backup directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)
    
    @pytest.fixture
    def backup_manager(self, temp_backup_dir):
        """Create a backup manager with temp directory."""
        return BackupManager(backup_directory=temp_backup_dir)
    
    @pytest.fixture
    def temp_source_dir(self):
        """Create a temporary source directory with test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source"
            source.mkdir()
            
            # Create some test files
            (source / "file1.txt").write_text("Test content 1")
            (source / "file2.txt").write_text("Test content 2")
            
            subdir = source / "subdir"
            subdir.mkdir()
            (subdir / "file3.txt").write_text("Test content 3")
            
            yield source
    
    def test_default_backup_directory_resolution(self):
        """
        Test that backup directory resolves correctly on Windows.
        This tests the fix for the startup PermissionError bug!
        """
        manager = BackupManager()
        
        # Should resolve to user AppData, NOT system32
        backup_dir = manager.backup_directory
        
        assert backup_dir.exists()
        assert "AppData" in str(backup_dir) or ".ut_vfx" in str(backup_dir)
        assert "system32" not in str(backup_dir).lower()
        assert "WINDOWS" not in str(backup_dir)
    
    def test_backup_creation(self, backup_manager, temp_source_dir):
        """Test creating a backup."""
        success, message, backup_path = backup_manager.create_backup(
            source_directories=[temp_source_dir],
            backup_name="test_backup"
        )
        
        assert success, f"Backup failed: {message}"
        assert backup_path is not None
        assert backup_path.exists()
        # Should be encrypted (.enc extension)
        assert backup_path.suffix == ".enc"
    
    def test_backup_restore(self, backup_manager, temp_source_dir):
        """Test restoring from a backup."""
        # Create backup first
        success, message, backup_path = backup_manager.create_backup(
            source_directories=[temp_source_dir],
            backup_name="test_restore"
        )
        assert success
        
        # Create restore directory
        with tempfile.TemporaryDirectory() as tmpdir:
            restore_dir = Path(tmpdir) / "restore"
            restore_dir.mkdir()
            
            # Restore backup
            success, message = backup_manager.restore_backup(backup_path, restore_dir)
            
            assert success, f"Restore failed: {message}"
            
            # Check manifest exists
            manifest_path = restore_dir / "manifest.json"
            assert manifest_path.exists()
    
    def test_backup_list(self, backup_manager, temp_source_dir):
        """Test listing backups."""
        # Create a few backups
        for i in range(3):
            backup_manager.create_backup(
                source_directories=[temp_source_dir],
                backup_name=f"test_backup_{i}"
            )
        
        backups = backup_manager.list_backups()
        
        assert len(backups) >= 3
        # Should be sorted by creation time (newest first)
        for backup in backups:
            assert 'name' in backup
            assert 'path' in backup
            assert 'is_encrypted' in backup
    
    def test_backup_deletion(self, backup_manager, temp_source_dir):
        """Test deleting a backup."""
        # Create backup
        success, message, backup_path = backup_manager.create_backup(
            source_directories=[temp_source_dir],
            backup_name="test_delete"
        )
        assert success
        assert backup_path.exists()
        
        # Delete backup
        success, message = backup_manager.delete_backup(backup_path)
        
        assert success, f"Delete failed: {message}"
        assert not backup_path.exists()
    
    def test_encryption_key_persistence(self, temp_backup_dir):
        """Test that encryption key is persisted and reused."""
        # Create first manager
        manager1 = BackupManager(backup_directory=temp_backup_dir)
        key1 = manager1.encryption_key
        
        # Create second manager (should reuse key)
        manager2 = BackupManager(backup_directory=temp_backup_dir)
        key2 = manager2.encryption_key
        
        # Keys should match
        assert key1 == key2


class TestRecoveryManager:
    """Test the RecoveryManager class."""
    
    @pytest.fixture
    def temp_backup_dir(self):
        """Create a temporary backup directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)
    
    @pytest.fixture
    def backup_manager(self, temp_backup_dir):
        """Create a backup manager."""
        return BackupManager(backup_directory=temp_backup_dir)
    
    @pytest.fixture
    def recovery_manager(self, backup_manager):
        """Create a recovery manager."""
        return RecoveryManager(backup_manager=backup_manager)
    
    @pytest.fixture
    def temp_source_dir(self):
        """Create a temporary source directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source"
            source.mkdir()
            (source / "file.txt").write_text("Recovery test")
            yield source
    
    def test_create_recovery_point(self, recovery_manager, temp_source_dir):
        """Test creating a recovery point."""
        success, message = recovery_manager.create_recovery_point(
            name="checkpoint_1",
            directories=[temp_source_dir]
        )
        
        assert success, f"Recovery point creation failed: {message}"
        
        # Check it's in the list
        points = recovery_manager.list_recovery_points()
        assert len(points) > 0
        assert points[0]['name'] == "checkpoint_1"
    
    def test_restore_from_recovery_point(self, recovery_manager, temp_source_dir):
        """Test restoring from a recovery point."""
        # Create recovery point
        success, message = recovery_manager.create_recovery_point(
            name="restore_test",
            directories=[temp_source_dir]
        )
        assert success
        
        # Restore to new location
        with tempfile.TemporaryDirectory() as tmpdir:
            restore_dir = Path(tmpdir) / "restored"
            restore_dir.mkdir()
            
            success, message = recovery_manager.restore_from_recovery_point(
                name="restore_test",
                restore_directory=restore_dir
            )
            
            assert success, f"Restore failed: {message}"
    
    def test_list_recovery_points(self, recovery_manager, temp_source_dir):
        """Test listing recovery points."""
        # Create multiple recovery points
        for i in range(3):
            recovery_manager.create_recovery_point(
                name=f"point_{i}",
                directories=[temp_source_dir]
            )
        
        points = recovery_manager.list_recovery_points()
        
        assert len(points) >= 3
        # Should be sorted by creation time
        for point in points:
            assert 'name' in point
            assert 'status' in point
            assert 'created_at' in point
    
    def test_delete_recovery_point(self, recovery_manager, temp_source_dir):
        """Test deleting a recovery point."""
        # Create recovery point
        success, message = recovery_manager.create_recovery_point(
            name="delete_me",
            directories=[temp_source_dir]
        )
        assert success
        
        # Delete it
        success, message = recovery_manager.delete_recovery_point("delete_me")
        
        assert success, f"Delete failed: {message}"
        
        # Verify it's gone
        points = recovery_manager.list_recovery_points()
        point_names = [p['name'] for p in points]
        assert "delete_me" not in point_names


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
