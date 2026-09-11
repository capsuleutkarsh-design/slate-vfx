"""
PRODUCTION-READY File operations for UT_VFX Production tool.
Enhanced with data integrity, verification, and enterprise-level error handling.
"""
import shutil
import hashlib
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import logging
from datetime import datetime
import psutil
import sys
class SafeFileOperations:
    """
    ENTERPRISE-GRADE file operations for sensitive VFX production data.
    Includes verification, rollback, and performance optimization.
    """

    @staticmethod
    def _to_long_path(path: Path) -> str:
        """
        Convert path to Windows long path format (UNC) to bypass 260-char MAX_PATH limit.
        Only applies on Windows.
        """
        path_str = str(path.resolve())
        if sys.platform == 'win32' and not path_str.startswith('\\\\?\\'):
            return f'\\\\?\\{path_str}'
        return path_str
    
    @staticmethod
    def safe_create_directory(directory_path: Path) -> Tuple[bool, str]:
        """
        Safely create directory with verification and proper permissions.
        Returns (success, message)
        """
        try:
            if directory_path.exists():
                return True, f"Directory already exists: {directory_path}"
            
            # Create with parents
            directory_path.mkdir(parents=True, exist_ok=True)
            
            # Verify creation
            if not directory_path.exists():
                return False, f"Failed to create directory: {directory_path}"
            
            # Set appropriate permissions (read/write for user, read for others)
            try:
                directory_path.chmod(0o755)  # rwxr-xr-x
            except Exception as perm_error:
                logging.warning(f"Could not set permissions on {directory_path}: {perm_error}")
            
            logging.info(f"[OK] Created directory: {directory_path}")
            return True, f"Successfully created directory: {directory_path}"
            
        except Exception as e:
            error_msg = f"Failed to create directory {directory_path}: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False, error_msg
    
    @staticmethod
    def safe_move_with_verification(source: Path, destination: Path, 
                                  verify_checksum: bool = True) -> Tuple[bool, str, int]:
        """
        PRODUCTION: Move files with comprehensive verification.
        Uses Copy -> Verify -> Delete strategy to ensure source data safety.
        Returns (success, message, bytes_moved)
        """
        try:
            # 1. Attempt Safe Copy
            # This handles checks, copy, verification, and cleanup details
            success, message, size = SafeFileOperations.safe_copy_with_verification(
                source, destination, verify_checksum
            )
            
            if not success:
                return False, f"Move failed during copy phase: {message}", 0
            
            # 2. If Copy Succeeded, Delete Source
            # We verify again that destination exists before deleting source, just to be paranoid
            if not destination.exists():
                 return False, "Critical: Copy reported success but destination missing!", 0

            # Safe delete source
            if not SafeFileOperations._safe_delete(source):
                # Warning: Duplicate data exists
                return False, f"Moved {source.name} successfully but failed to delete source file.", size
            
            logging.info(f"[OK] Verified move (Copy+Delete): {source} -> {destination}")
            return True, f"Successfully moved {source.name}", size
            
        except Exception as e:
            error_msg = f"Safe move failed: {source} -> {destination}. Error: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False, error_msg, 0
    
    @staticmethod
    def safe_copy_with_verification(source: Path, destination: Path,
                                  verify_checksum: bool = True) -> Tuple[bool, str, int]:
        """
        PRODUCTION: Copy files with verification for critical data.
        Returns (success, message, bytes_copied)
        """
        try:
            if not source.exists():
                return False, f"Source does not exist: {source}", 0
            
            source_size = SafeFileOperations._get_path_size(source)
            source_checksum = None
            
            if verify_checksum and source_size < 1024**3:
                source_checksum = SafeFileOperations._calculate_checksum(source)
            
            # Ensure destination directory
            dest_parent = destination.parent
            if not dest_parent.exists():
                success, message = SafeFileOperations.safe_create_directory(dest_parent)
                if not success:
                    return False, f"Failed to create destination directory: {message}", 0
            
            # Check disk space
            disk_ok, disk_msg = SafeFileOperations._check_disk_space(source_size, dest_parent)
            if not disk_ok:
                return False, disk_msg, 0
            
            # Perform copy operation
            long_source = SafeFileOperations._to_long_path(source)
            long_dest = SafeFileOperations._to_long_path(destination)
            if source.is_dir():
                shutil.copytree(long_source, long_dest)
            else:
                shutil.copy2(long_source, long_dest)
            
            # Verify copy
            verification = SafeFileOperations._verify_copy_result(
                source, destination, source_checksum, source_size
            )
            
            if not verification[0]:
                # Clean up failed copy
                SafeFileOperations._safe_delete(destination)
                return False, f"Copy verification failed: {verification[1]}", 0
            
            logging.info(f"[OK] Verified copy: {source} -> {destination}")
            return True, f"Successfully copied {source.name}", source_size
            
        except Exception as e:
            error_msg = f"Safe copy failed: {source} -> {destination}. Error: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False, error_msg, 0
    

    
    @staticmethod
    def _verify_copy_result(source: Path, destination: Path,
                          original_checksum: Optional[str], original_size: int) -> Tuple[bool, str]:
        """Verify that copy operation completed successfully."""
        try:
            if not destination.exists():
                return False, f"Destination not created: {destination}"
            
            # Verify both source and destination exist (for copy)
            if not source.exists():
                return False, f"Source missing after copy: {source}"
            
            # Verify size
            dest_size = SafeFileOperations._get_path_size(destination)
            if dest_size != original_size:
                return False, f"Size mismatch: source={original_size}, dest={dest_size}"
            
            # Verify checksum if available
            if original_checksum:
                dest_checksum = SafeFileOperations._calculate_checksum(destination)
                if dest_checksum != original_checksum:
                    return False, f"Checksum mismatch: {destination.name}"
            
            return True, "Copy verification passed"
            
        except Exception as e:
            return False, f"Verification error: {str(e)}"
    
    @staticmethod
    def _calculate_checksum(path: Path) -> str:
        """Calculate MD5 checksum for file or directory."""
        hash_md5 = hashlib.md5()
        
        if path.is_file():
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hash_md5.update(chunk)
        else:
            # For directories, create a consistent hash based on file structure
            file_info = []
            for file_path in sorted(path.rglob('*')):
                if file_path.is_file():
                    # Use filename and size for directory checksum (faster than hashing all content)
                    file_info.append(f"{file_path.name}:{file_path.stat().st_size}")
            hash_md5.update(str(sorted(file_info)).encode())
            
        return hash_md5.hexdigest()
    
    @staticmethod
    def _get_path_size(path: Path) -> int:
        """Get total size of file or directory in bytes."""
        if path.is_file():
            return path.stat().st_size
        else:
            try:
                return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
            except Exception as e:
                logging.warning(f"Could not calculate size for {path}: {e}")
                return 0
    
    @staticmethod
    def _check_disk_space(required_size: int, location: Path) -> Tuple[bool, str]:
        """Check if there's sufficient disk space with safety buffer."""
        try:
            available_space = psutil.disk_usage(str(location)).free
            
            # 20% safety buffer
            required_with_buffer = required_size * 1.2
            
            if required_with_buffer > available_space:
                return False, (
                    f"Insufficient disk space at {location}.\n"
                    f"Required: {SafeFileOperations._format_bytes(required_with_buffer)}\n"
                    f"Available: {SafeFileOperations._format_bytes(available_space)}"
                )
            
            return True, "Sufficient disk space available"
            
        except Exception as e:
            logging.warning(f"Disk space check failed: {e}")
            return True, "Disk space check skipped"  # Continue with warning
    

    

    
    @staticmethod
    def _safe_delete(path: Path) -> bool:
        """Safely delete file or directory."""
        try:
            long_path = SafeFileOperations._to_long_path(path)
            if path.is_dir():
                shutil.rmtree(long_path)
            else:
                import os
                os.remove(long_path)
            return True
        except Exception as e:
            logging.exception(f"Failed to delete {path}: {e}")
            return False
    
    @staticmethod
    def _format_bytes(size: int) -> str:
        """Format bytes to human readable format."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"
    
    @staticmethod
    def get_file_info(file_path: Path) -> Dict[str, Any]:
        """Get comprehensive file information for logging and verification."""
        try:
            stat = file_path.stat()
            return {
                'path': str(file_path),
                'size': stat.st_size,
                'created': datetime.fromtimestamp(stat.st_ctime),
                'modified': datetime.fromtimestamp(stat.st_mtime),
                'permissions': oct(stat.st_mode)[-3:],
                'is_file': file_path.is_file(),
                'is_dir': file_path.is_dir(),
                'exists': file_path.exists()
            }
        except Exception as e:
            return {
                'path': str(file_path),
                'error': str(e),
                'exists': False
            }
    
    @staticmethod
    def validate_vfx_file(file_path: Path) -> Tuple[bool, str]:
        """
        Validate VFX-specific file integrity.
        Returns (is_valid, message)
        """
        try:
            if not file_path.exists():
                return False, "File does not exist"
            
            if file_path.is_dir():
                return True, "Directory validation passed"
            
            # Check file extension against known VFX formats
            vfx_extensions = {'.exr', '.dpx', '.tif', '.tiff', '.mov', '.png', '.jpg', '.jpeg', '.ari', '.r3d'}
            if file_path.suffix.lower() not in vfx_extensions:
                return True, f"Non-VFX file type: {file_path.suffix}"
            
            # Basic file integrity check
            file_size = file_path.stat().st_size
            if file_size == 0:
                return False, "File is empty (0 bytes)"
            
            if file_size > 100 * 1024**3:  # 100GB
                return False, "File size exceeds reasonable limits (100GB)"
            
            return True, "VFX file validation passed"
            
        except Exception as e:
            return False, f"Validation error: {str(e)}"