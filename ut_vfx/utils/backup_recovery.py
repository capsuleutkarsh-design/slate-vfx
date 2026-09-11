import json
import shutil
import zipfile
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import logging
from datetime import datetime
import tempfile
import os
import sys
from cryptography.fernet import Fernet
from .security import SecretManager


class BackupManager:
    """Manage automated backups and recovery operations."""
    
    def __init__(self, backup_directory: Optional[Path] = None):
        self.backup_directory = backup_directory or self._get_default_backup_directory()
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        
        # Encryption key for secure backups
        self.encryption_key = self._get_or_create_encryption_key()
        self.cipher_suite = Fernet(self.encryption_key)
        
        logging.info(f"Backup manager initialized with directory: {self.backup_directory}")
    
    def _get_default_backup_directory(self) -> Path:
        """Get the default backup directory."""
        try:
            if sys.platform == "win32":
                return Path(os.environ.get('LOCALAPPDATA', Path.home() / "AppData" / "Local")) / "UTVFX" / "backups"
            else:
                return Path.home() / ".ut_vfx" / "backups"
        except Exception as exc:
            fallback = Path.cwd() / "backups"
            logging.warning(
                "BackupManager: default backup directory resolution failed (%s). Using fallback %s",
                exc,
                fallback,
            )
            return fallback
    
    def _get_or_create_encryption_key(self) -> bytes:
        """Get existing encryption key via SecretManager or create a new one."""
        key_name = "ut_backup_key"
        key_file = self.backup_directory / "backup.key"
        
        # 1. Try Fetching
        secret = SecretManager.get_secret(key_name)
        
        # 1.5 Fallback to reading the raw bytes file (Legacy behavior fix)
        # SecretManager reads as string (utf-8), but Fernet keys are bytes.
        # If SecretManager failed or returned a string that might be base64?
        
        if secret:
            # If came from Env/Keyring, it's likely a string.
            # Fernet keys are base64 url-safe strings, so encoding to bytes is usually fine.
            return secret.encode('utf-8')
            
        if key_file.exists():
             with open(key_file, 'rb') as f:
                return f.read()

        # 2. Generate New
        key = Fernet.generate_key()
        
        # 3. Store (Prefer Keyring, fallback to file)
        try:
            SecretManager.set_secret(key_name, key.decode('utf-8'))
        except Exception as exc:
            logging.warning("Failed to persist backup key in secret manager, using file fallback: %s", exc)
            
        # Always write file as fallback for now (User Expectation)
        # In a strict environment, we would disable this.
        with open(key_file, 'wb') as f:
            f.write(key)
            
        return key
    
    def create_backup(self, source_directories: List[Path], backup_name: Optional[str] = None) -> Tuple[bool, str, Optional[Path]]:
        """
        Create a backup of specified directories.
        
        Args:
            source_directories: List of directories to backup
            backup_name: Optional custom backup name
            
        Returns:
            (success, message, backup_path)
        """
        try:
            if not backup_name:
                backup_name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            # Create temporary directory for backup preparation
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Copy all source directories to temp location
                for i, source_dir in enumerate(source_directories):
                    if not source_dir.exists():
                        return False, f"Source directory does not exist: {source_dir}", None
                    
                    # Create a subdirectory in temp for each source
                    temp_subdir = temp_path / f"source_{i}_{source_dir.name}"
                    shutil.copytree(source_dir, temp_subdir)
                
                # Create backup manifest
                manifest = {
                    'backup_name': backup_name,
                    'created_at': datetime.now().isoformat(),
                    'source_directories': [str(d) for d in source_directories],
                    'backup_size': self._get_directory_size(temp_path)
                }
                
                # Save manifest
                manifest_path = temp_path / "manifest.json"
                with open(manifest_path, 'w', encoding='utf-8') as f:
                    json.dump(manifest, f, indent=2)
                
                # Create encrypted zip archive
                backup_path = self.backup_directory / f"{backup_name}.zip"
                
                with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files in os.walk(temp_path):
                        for file in files:
                            file_path = Path(root) / file
                            arc_name = file_path.relative_to(temp_path)
                            zipf.write(file_path, arc_name)
                
                # Encrypt the backup file
                backup_path = self._encrypt_file(backup_path)
                
                message = f"Backup created successfully: {backup_path}"
                logging.info(message)
                return True, message, backup_path
                
        except Exception as e:
            error_msg = f"Error creating backup: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False, error_msg, None
    
    def restore_backup(self, backup_path: Path, restore_directory: Path) -> Tuple[bool, str]:
        """
        Restore a backup to specified directory.
        
        Args:
            backup_path: Path to backup file
            restore_directory: Directory to restore to
            
        Returns:
            (success, message)
        """
        try:
            if not backup_path.exists():
                return False, f"Backup file does not exist: {backup_path}"
            
            if not restore_directory.exists():
                restore_directory.mkdir(parents=True, exist_ok=True)
            
            # Decrypt backup file if it's encrypted
            is_encrypted = backup_path.suffix == '.enc'
            backup_to_extract = backup_path
            decrypted_path = None
            if is_encrypted:
                decrypted_path = backup_path.with_suffix('')
                self._decrypt_file(backup_path, decrypted_path)
                backup_to_extract = decrypted_path
            
            # Extract the backup
            with zipfile.ZipFile(backup_to_extract, 'r') as zipf:
                zipf.extractall(restore_directory)
            
            # Clean up decrypted file if it was temporary
            if decrypted_path and decrypted_path.exists():
                decrypted_path.unlink()
            
            # Verify manifest
            manifest_path = restore_directory / "manifest.json"
            if manifest_path.exists():
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    manifest = json.load(f)
                
                logging.info(f"Restored backup: {manifest['backup_name']} from {manifest['created_at']}")
            else:
                logging.warning("No manifest found in backup")
            
            message = f"Backup restored successfully to: {restore_directory}"
            logging.info(message)
            return True, message
            
        except Exception as e:
            error_msg = f"Error restoring backup: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            # Best-effort cleanup for temporary decrypted payload.
            try:
                if 'decrypted_path' in locals() and decrypted_path and decrypted_path.exists():
                    decrypted_path.unlink()
            except OSError as cleanup_err:
                logging.debug("Backup restore cleanup warning for %s (%s)", decrypted_path, cleanup_err)
            return False, error_msg
    
    def list_backups(self) -> List[Dict[str, Any]]:
        """List all available backups."""
        backups = []
        
        for backup_file in self.backup_directory.glob("*.zip*"):  # Include both .zip and .zip.enc
            try:
                # For encrypted files, we need to decrypt temporarily to read manifest
                is_encrypted = backup_file.suffix == '.enc'
                temp_manifest = None
                
                if is_encrypted:
                    # Try to decrypt manifest temporarily
                    temp_dir = Path(tempfile.mkdtemp())
                    temp_backup = temp_dir / "temp.zip"
                    try:
                        self._decrypt_file(backup_file, temp_backup)
                        
                        with zipfile.ZipFile(temp_backup, 'r') as zipf:
                            if 'manifest.json' in zipf.namelist():
                                manifest_content = zipf.read('manifest.json')
                                temp_manifest = json.loads(manifest_content.decode())
                    finally:
                        # Clean up temp files
                        try:
                            if temp_backup.exists():
                                temp_backup.unlink()
                        except OSError as cleanup_err:
                            logging.debug("BackupManager: temp backup cleanup warning for %s (%s)", temp_backup, cleanup_err)
                        try:
                            shutil.rmtree(temp_dir, ignore_errors=True)
                        except OSError as cleanup_err:
                            logging.debug("BackupManager: temp directory cleanup warning for %s (%s)", temp_dir, cleanup_err)
                else:
                    # For unencrypted files, extract manifest directly
                    with zipfile.ZipFile(backup_file, 'r') as zipf:
                        if 'manifest.json' in zipf.namelist():
                            manifest_content = zipf.read('manifest.json')
                            temp_manifest = json.loads(manifest_content.decode())
                
                backup_info = {
                    'name': backup_file.stem,
                    'path': str(backup_file),
                    'size': backup_file.stat().st_size,
                    'created_at': datetime.fromtimestamp(backup_file.stat().st_mtime).isoformat(),
                    'is_encrypted': is_encrypted
                }
                
                if temp_manifest:
                    backup_info.update({
                        'backup_name': temp_manifest.get('backup_name', backup_file.stem),
                        'created': temp_manifest.get('created_at', 'Unknown'),
                        'source_directories': temp_manifest.get('source_directories', []),
                        'backup_size': temp_manifest.get('backup_size', 0)
                    })
                else:
                    backup_info['backup_name'] = backup_file.stem
                
                backups.append(backup_info)
                
            except Exception as e:
                logging.exception(f"Error reading backup info for {backup_file}: {e}")
        
        # Sort by creation time (newest first)
        backups.sort(key=lambda x: x['created_at'], reverse=True)
        return backups
    
    def delete_backup(self, backup_path: Path) -> Tuple[bool, str]:
        """Delete a backup file."""
        try:
            if backup_path.exists():
                backup_path.unlink()
                message = f"Backup deleted: {backup_path}"
                logging.info(message)
                return True, message
            else:
                return False, f"Backup file does not exist: {backup_path}"
        except Exception as e:
            error_msg = f"Error deleting backup: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False, error_msg
    
    def _get_directory_size(self, directory: Path) -> int:
        """Get total size of directory in bytes."""
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(directory):
            for filename in filenames:
                file_path = Path(dirpath) / filename
                total_size += file_path.stat().st_size
        return total_size
    
    def _encrypt_file(self, file_path: Path) -> Path:
        """Encrypt a file using Fernet encryption."""
        try:
            with open(file_path, 'rb') as f:
                file_data = f.read()
            
            encrypted_data = self.cipher_suite.encrypt(file_data)
            
            # Rename file to add .enc extension
            encrypted_path = file_path.with_suffix(file_path.suffix + '.enc')
            
            with open(encrypted_path, 'wb') as f:
                f.write(encrypted_data)
            
            # Remove original file
            file_path.unlink()
            return encrypted_path
            
        except Exception as e:
            logging.exception(f"Error encrypting file {file_path}: {e}")
            raise
    
    def _decrypt_file(self, encrypted_path: Path, output_path: Path):
        """Decrypt a file using Fernet encryption."""
        try:
            with open(encrypted_path, 'rb') as f:
                encrypted_data = f.read()
            
            decrypted_data = self.cipher_suite.decrypt(encrypted_data)
            
            with open(output_path, 'wb') as f:
                f.write(decrypted_data)
                
        except Exception as e:
            logging.exception(f"Error decrypting file {encrypted_path}: {e}")
            raise


class RecoveryManager:
    """Manage recovery operations and recovery points."""
    
    def __init__(self, backup_manager: BackupManager):
        self.backup_manager = backup_manager
        self.recovery_points = self._load_recovery_points()
    
    def _load_recovery_points(self) -> Dict[str, Any]:
        """Load existing recovery points from storage."""
        recovery_points_file = self.backup_manager.backup_directory / "recovery_points.json"
        
        if recovery_points_file.exists():
            try:
                with open(recovery_points_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logging.exception(f"Error loading recovery points: {e}")
                return {}
        else:
            return {}
    
    def _save_recovery_points(self):
        """Save recovery points to storage."""
        recovery_points_file = self.backup_manager.backup_directory / "recovery_points.json"
        
        try:
            with open(recovery_points_file, 'w', encoding='utf-8') as f:
                json.dump(self.recovery_points, f, indent=2)
        except Exception as e:
            logging.exception(f"Error saving recovery points: {e}")
    
    def create_recovery_point(self, name: str, directories: List[Path]) -> Tuple[bool, str]:
        """Create a recovery point with specified directories."""
        try:
            # Create backup with the recovery point name
            success, message, backup_path = self.backup_manager.create_backup(directories, f"recovery_{name}")
            
            if success and backup_path:
                # Record recovery point
                self.recovery_points[name] = {
                    'created_at': datetime.now().isoformat(),
                    'backup_path': str(backup_path),
                    'directories': [str(d) for d in directories],
                    'status': 'active'
                }
                
                self._save_recovery_points()
                
                logging.info(f"Created recovery point: {name}")
                return True, f"Recovery point '{name}' created successfully"
            else:
                return False, message
                
        except Exception as e:
            error_msg = f"Error creating recovery point: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False, error_msg
    
    def restore_from_recovery_point(self, name: str, restore_directory: Path) -> Tuple[bool, str]:
        """Restore from a specific recovery point."""
        try:
            if name not in self.recovery_points:
                return False, f"Recovery point not found: {name}"
            
            recovery_point = self.recovery_points[name]
            backup_path = Path(recovery_point['backup_path'])
            
            if not backup_path.exists():
                return False, f"Backup file for recovery point does not exist: {backup_path}"
            
            # Update recovery point status
            self.recovery_points[name]['status'] = 'restoring'
            self._save_recovery_points()
            
            # Perform restoration
            success, message = self.backup_manager.restore_backup(backup_path, restore_directory)
            
            if success:
                self.recovery_points[name]['status'] = 'restored'
                self.recovery_points[name]['last_restored'] = datetime.now().isoformat()
                self._save_recovery_points()
                
                logging.info(f"Restored from recovery point: {name}")
                return True, f"Successfully restored from recovery point '{name}'"
            else:
                self.recovery_points[name]['status'] = 'failed'
                self._save_recovery_points()
                return False, message
                
        except Exception as e:
            error_msg = f"Error restoring from recovery point: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            
            if name in self.recovery_points:
                self.recovery_points[name]['status'] = 'error'
                self._save_recovery_points()
            
            return False, error_msg
    
    def list_recovery_points(self) -> List[Dict[str, Any]]:
        """List all available recovery points."""
        recovery_list = []
        
        for name, details in self.recovery_points.items():
            recovery_list.append({
                'name': name,
                'created_at': details['created_at'],
                'status': details['status'],
                'directories': details['directories'],
                'backup_exists': Path(details['backup_path']).exists()
            })
        
        # Sort by creation time (newest first)
        recovery_list.sort(key=lambda x: x['created_at'], reverse=True)
        return recovery_list
    
    def delete_recovery_point(self, name: str) -> Tuple[bool, str]:
        """Delete a recovery point (both record and backup file)."""
        try:
            if name not in self.recovery_points:
                return False, f"Recovery point not found: {name}"
            
            # Delete the backup file
            backup_path = Path(self.recovery_points[name]['backup_path'])
            if backup_path.exists():
                backup_path.unlink()
            
            # Remove from recovery points
            del self.recovery_points[name]
            self._save_recovery_points()
            
            logging.info(f"Deleted recovery point: {name}")
            return True, f"Recovery point '{name}' deleted successfully"
            
        except Exception as e:
            error_msg = f"Error deleting recovery point: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False, error_msg


# Global instances
backup_manager = BackupManager()
recovery_manager = RecoveryManager(backup_manager)
