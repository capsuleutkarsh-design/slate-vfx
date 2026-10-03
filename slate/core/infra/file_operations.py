"""
PRODUCTION-READY File operations for Slate Production tool.
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
import os
# Read and hashed in 8 MB pieces: big enough to stream a ProRes file quickly,
# small enough to keep memory flat.
CHECKSUM_CHUNK = 8 * 1024 * 1024

_LONG_PREFIX = "\\\\?\\"           # \\?\
_UNC_PREFIX = "\\\\?\\UNC\\"       # \\?\UNC\


def long_path(path) -> str:
    """
    The path as a string Windows accepts past 260 characters.

    Every file-system call goes through this, not only the copy: the copy used
    the long form while the existence, size and checksum checks used the short
    one, so any destination past 260 characters "failed verification" and the
    file could never be brought in (ING-007). A UNC share takes the UNC form -
    putting the long prefix in front of a share name names nothing.
    """
    text = os.path.abspath(str(path))
    if sys.platform != "win32" or text.startswith(_LONG_PREFIX):
        return text
    if text.startswith("\\\\"):
        return _UNC_PREFIX + text[2:]
    return _LONG_PREFIX + text


def same_volume(a, b) -> bool:
    """Whether a rename can move a file from a to b (same drive, same volume)."""
    try:
        a, b = Path(a), Path(b)
        if a.drive.lower() != b.drive.lower():
            return False
        probe = b
        while not os.path.exists(long_path(probe)) and probe.parent != probe:
            probe = probe.parent
        return os.stat(long_path(a)).st_dev == os.stat(long_path(probe)).st_dev
    except OSError:
        return False


class SafeFileOperations:
    """
    ENTERPRISE-GRADE file operations for sensitive VFX production data.
    Includes verification, rollback, and performance optimization.
    """

    @staticmethod
    def _to_long_path(path: Path) -> str:
        """Kept for callers outside the ingest: see long_path()."""
        return long_path(path)

    @staticmethod
    def exists(path) -> bool:
        return os.path.exists(long_path(path))

    @staticmethod
    def safe_create_directory(directory_path: Path) -> Tuple[bool, str]:
        """
        Safely create directory with verification and proper permissions.
        Returns (success, message)
        """
        try:
            target = long_path(directory_path)
            if os.path.isdir(target):
                return True, f"Directory already exists: {directory_path}"

            os.makedirs(target, exist_ok=True)

            if not os.path.isdir(target):
                return False, f"Failed to create directory: {directory_path}"

            try:
                os.chmod(target, 0o755)  # rwxr-xr-x
            except Exception as perm_error:
                logging.warning(f"Could not set permissions on {directory_path}: {perm_error}")

            logging.debug(f"[OK] Created directory: {directory_path}")
            return True, f"Successfully created directory: {directory_path}"

        except Exception as e:
            error_msg = f"Failed to create directory {directory_path}: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False, error_msg

    @staticmethod
    def safe_move_with_verification(source: Path, destination: Path,
                                  verify_checksum: bool = True,
                                  allow_rename: bool = False) -> Tuple[bool, str, int]:
        """
        Move a file: copy, verify, then delete the source.

        With allow_rename and both ends on one volume the file is simply
        renamed into place - nothing is copied, so there is nothing to verify
        beyond the file arriving whole. Returns (success, message, bytes_moved).
        """
        try:
            source, destination = Path(source), Path(destination)
            if (allow_rename and not SafeFileOperations.exists(destination)
                    and same_volume(source, destination)):
                size = SafeFileOperations._get_path_size(source)
                parent_ok, message = SafeFileOperations.safe_create_directory(destination.parent)
                if not parent_ok:
                    return False, message, 0
                os.rename(long_path(source), long_path(destination))
                if SafeFileOperations._get_path_size(destination) != size:
                    return False, f"Size changed while moving {source.name}", 0
                return True, f"Successfully moved {source.name}", size

            success, message, size = SafeFileOperations.safe_copy_with_verification(
                source, destination, verify_checksum
            )

            if not success:
                return False, f"Move failed during copy phase: {message}", 0

            if not SafeFileOperations.exists(destination):
                return False, "Critical: Copy reported success but destination missing!", 0

            if not SafeFileOperations._safe_delete(source):
                return False, f"Copied {source.name} but could not remove it from the source.", size

            logging.debug(f"[OK] Verified move (Copy+Delete): {source} -> {destination}")
            return True, f"Successfully moved {source.name}", size

        except Exception as e:
            error_msg = f"Could not move {source} to {destination}: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False, error_msg, 0

    @staticmethod
    def safe_copy_with_verification(source: Path, destination: Path,
                                  verify_checksum: bool = True) -> Tuple[bool, str, int]:
        """
        Copy a file and check the copy: always its size, and with
        verify_checksum an MD5 of both ends, whatever the file's size (large
        MOV/ProRes files used to get a size check only). Returns
        (success, message, bytes_copied).
        """
        landing = None
        try:
            source, destination = Path(source), Path(destination)
            if not SafeFileOperations.exists(source):
                return False, f"Source does not exist: {source}", 0

            source_size = SafeFileOperations._get_path_size(source)
            source_checksum = None

            if verify_checksum:
                source_checksum = SafeFileOperations._calculate_checksum(source)

            dest_parent = destination.parent
            if not SafeFileOperations.exists(dest_parent):
                success, message = SafeFileOperations.safe_create_directory(dest_parent)
                if not success:
                    return False, f"Failed to create destination directory: {message}", 0

            disk_ok, disk_msg = SafeFileOperations._check_disk_space(source_size, dest_parent)
            if not disk_ok:
                return False, disk_msg, 0

            long_source = long_path(source)
            if os.path.isdir(long_source):
                shutil.copytree(long_source, long_path(destination))
                landing = destination
            else:
                # Copied under a '.partial' name and put in place only once it
                # checks out: a copy that dies half-way (share dropped, disk
                # full) never leaves a truncated plate under the real name.
                landing = destination.with_name(destination.name + ".partial")
                shutil.copy2(long_source, long_path(landing))

            verification = SafeFileOperations._verify_copy_result(
                source, landing, source_checksum, source_size
            )

            if not verification[0]:
                SafeFileOperations._safe_delete(landing)
                return False, f"Copy verification failed: {verification[1]}", 0
            if landing != destination:
                os.replace(long_path(landing), long_path(destination))

            logging.debug(f"[OK] Verified copy: {source} -> {destination}")
            return True, f"Successfully copied {source.name}", source_size

        except Exception as e:
            if landing is not None and landing != destination and SafeFileOperations.exists(landing):
                SafeFileOperations._safe_delete(landing)
            error_msg = f"Could not copy {source} to {destination}: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False, error_msg, 0

    @staticmethod
    def _verify_copy_result(source: Path, destination: Path,
                          original_checksum: Optional[str], original_size: int) -> Tuple[bool, str]:
        """Verify that copy operation completed successfully."""
        try:
            if not SafeFileOperations.exists(destination):
                return False, f"Destination not created: {destination}"

            if not SafeFileOperations.exists(source):
                return False, f"Source missing after copy: {source}"

            dest_size = SafeFileOperations._get_path_size(destination)
            if dest_size != original_size:
                return False, f"Size mismatch: source={original_size}, dest={dest_size}"

            if original_checksum:
                dest_checksum = SafeFileOperations._calculate_checksum(destination)
                if dest_checksum != original_checksum:
                    return False, f"Checksum mismatch: {destination.name}"

            return True, "Copy verification passed"

        except Exception as e:
            return False, f"Verification error: {str(e)}"

    @staticmethod
    def _calculate_checksum(path: Path) -> str:
        """MD5 of a file (streamed), or of a folder's names and sizes."""
        hash_md5 = hashlib.md5()
        target = long_path(path)

        if os.path.isfile(target):
            with open(target, "rb") as f:
                for chunk in iter(lambda: f.read(CHECKSUM_CHUNK), b""):
                    hash_md5.update(chunk)
        else:
            file_info = []
            for file_path in sorted(Path(path).rglob('*')):
                if file_path.is_file():
                    file_info.append(f"{file_path.name}:{file_path.stat().st_size}")
            hash_md5.update(str(sorted(file_info)).encode())

        return hash_md5.hexdigest()

    @staticmethod
    def _get_path_size(path: Path) -> int:
        """Get total size of file or directory in bytes."""
        target = long_path(path)
        if os.path.isfile(target):
            return os.stat(target).st_size
        try:
            return sum(f.stat().st_size for f in Path(path).rglob('*') if f.is_file())
        except Exception as e:
            logging.warning(f"Could not calculate size for {path}: {e}")
            return 0

    @staticmethod
    def _check_disk_space(required_size: int, location: Path) -> Tuple[bool, str]:
        """Check if there's sufficient disk space with safety buffer."""
        try:
            # The volume's root: a deep destination may not exist yet, or be
            # past 260 characters, and the free space is the volume's anyway.
            probe = Path(location)
            available_space = psutil.disk_usage(probe.anchor or str(probe)).free
            
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
            target = long_path(path)
            if os.path.isdir(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
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