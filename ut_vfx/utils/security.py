import re
import html
from pathlib import Path
from typing import Tuple, Optional, List
import logging
import tempfile
import os
import mimetypes
from datetime import datetime


class SecurityValidator:
    """Security validation for file operations and user inputs."""
    
    # Blocked patterns for path traversal
    TRAVERSAL_PATTERNS = [
        r'\.\.', r'%2e%2e', r'%2e%2e%2f', r'%2e%2e%5c',
        r'\.\./', r'\.\.\\', r'\\\.\.', r'//\.\.',
        r'\.\.%00', r'\.\.%0a', r'\.\.%0d', r'\.\./\.\.'
    ]
    
    # Blocked characters for filenames
    FORBIDDEN_CHARS = ['<', '>', ':', '"', '|', '?', '*', '%00', '%0a', '%0d', '\0', '\n', '\r']
    # Note: Removed '/' and '\' from forbidden chars to allow paths, checked separately
    
    # Restricted directory names (Windows/Linux)
    RESTRICTED_NAMES = {
        'CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 
        'COM5', 'COM6', 'COM7', 'COM8', 'COM9', 'LPT1', 'LPT2', 'LPT3', 
        'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
        'CLOCK$', 'CONFIG$', 'CONIN$', 'CONOUT$'
    }
    
    # Dangerous file extensions
    # Note: .zip removed to allow updates
    DANGEROUS_EXTENSIONS = {
        '.exe', '.bat', '.cmd', '.com', '.pif', '.scr', '.vbs', '.vbe', 
        '.js', '.jse', '.wsf', '.wsh', '.msi', '.msp', '.dll', '.sys',
        '.bin', '.dat', '.inf', '.lnk', '.hta', '.cpl', '.msc', '.reg',
        '.pif', '.scf', '.shs', '.url', '.vb', '.ws', '.wsf'
    }
    
    @staticmethod
    def validate_file_path(user_input: str, base_directory: Path = None) -> Tuple[bool, Optional[Path], str]:
        """
        Securely validate and sanitize file paths.
        Returns (is_valid, safe_path, error_message)
        """
        try:
            # Check for empty input
            if not user_input or not user_input.strip():
                return False, None, "Path cannot be empty"
            
            # Convert to Path object
            input_path = Path(user_input.strip())
            
            # Check for path traversal attacks
            if SecurityValidator._contains_traversal(user_input):
                return False, None, "Path traversal attempts are blocked"
            
            # Check for restricted names (Windows reserved)
            if SecurityValidator._is_restricted_name(input_path.name):
                return False, None, f"Restricted filename: {input_path.name}"
            
            # ALLOW ABSOLUTE PATHS (Safe for local IO tools)
            if input_path.is_absolute():
                # Just ensure it's not a root system path like C:\ or /
                if str(input_path.resolve()) == str(input_path.anchor):
                     return False, None, "Cannot operate directly on drive root"
                return True, input_path.resolve(), "Absolute path accepted"
            
            # If relative and base_directory provided, resolve it
            if base_directory:
                safe_path = (base_directory / input_path).resolve()
                # Ensure the path stays within base directory
                try:
                    safe_path.relative_to(base_directory.resolve())
                except ValueError:
                    return False, None, "Path attempts to escape base directory"
                return True, safe_path, "Relative path secure"
            
            return True, input_path, "Path structure valid"
            
        except (TypeError, ValueError, OSError) as e:
            logging.error(f"Path validation error: {e}")
            return False, None, f"Path validation failed: {str(e)}"
    
    @staticmethod
    def _contains_traversal(path: str) -> bool:
        """Check if path contains traversal patterns."""
        path_lower = path.lower()
        
        # Check for basic traversal patterns
        for pattern in SecurityValidator.TRAVERSAL_PATTERNS:
            if re.search(pattern, path_lower):
                return True
        
        return False
    
    @staticmethod
    def _contains_forbidden_chars(text: str) -> bool:
        """Check if text contains forbidden characters."""
        return any(char in text for char in SecurityValidator.FORBIDDEN_CHARS)
    
    @staticmethod
    def _is_restricted_name(filename: str) -> bool:
        """Check if filename is a restricted system name."""
        name_without_ext = filename.split('.')[0].upper()
        return name_without_ext in SecurityValidator.RESTRICTED_NAMES
    
    @staticmethod
    def sanitize_filename(filename: str) -> Tuple[bool, str, str]:
        """
        Sanitize filename for safe filesystem use.
        Returns (is_valid, sanitized_name, error_message)
        """
        try:
            if not filename or not filename.strip():
                return False, "", "Filename cannot be empty"
            
            original_name = filename.strip()
            
            # Check for traversal
            if SecurityValidator._contains_traversal(original_name):
                return False, "", "Filename contains path traversal attempts"
            
            # Check for restricted names
            if SecurityValidator._is_restricted_name(original_name):
                return False, "", "Filename is a restricted system name"
            
            # Remove forbidden characters
            sanitized = original_name
            for char in SecurityValidator.FORBIDDEN_CHARS:
                sanitized = sanitized.replace(char, '_')
            
            # Remove leading/trailing spaces and dots
            sanitized = sanitized.strip().strip('.')
            
            # Ensure not empty after sanitization
            if not sanitized:
                return False, "", "Filename is empty after sanitization"
            
            # Limit length
            if len(sanitized) > 200:
                sanitized = sanitized[:200]
            
            return True, sanitized, "Filename sanitized successfully"
            
        except (TypeError, ValueError) as e:
            logging.error(f"Filename sanitization error: {e}")
            return False, "", f"Filename sanitization failed: {str(e)}"
    
    @staticmethod
    def validate_excel_file(file_path: Path) -> Tuple[bool, str]:
        """
        Security validation for Excel files.
        Returns (is_valid, error_message)
        """
        try:
            if not file_path.exists():
                return False, "File does not exist"
            
            # Check file size (max 100MB)
            max_size = 100 * 1024 * 1024  # 100MB
            file_size = file_path.stat().st_size
            if file_size > max_size:
                return False, f"File too large ({file_size//1024//1024}MB > {max_size//1024//1024}MB)"
            
            if file_size == 0:
                return False, "File is empty"
            
            # Check file extension
            valid_extensions = {'.xlsx', '.xls', '.xlsm'}
            file_suffix = file_path.suffix.lower()
            if file_suffix not in valid_extensions:
                return False, f"Invalid file extension '{file_suffix}'. Allowed: {', '.join(valid_extensions)}"
            
            # Check file signature (magic bytes)
            if not SecurityValidator._verify_excel_signature(file_path):
                return False, "Invalid Excel file signature (file may be corrupted or malicious)"
            
            # Additional check: ensure file is not a directory
            if file_path.is_dir():
                return False, "Path is a directory, not a file"
            
            # Check for dangerous content
            if SecurityValidator._check_for_dangerous_content(file_path):
                return False, "Excel file contains potentially dangerous content"
            
            return True, "Excel file validation passed"
            
        except (OSError, TypeError, ValueError) as e:
            return False, f"Excel file validation error: {str(e)}"
    
    @staticmethod
    def _verify_excel_signature(file_path: Path) -> bool:
        """Verify Excel file signature to prevent file type spoofing."""
        try:
            with open(file_path, 'rb') as f:
                header = f.read(8)
            
            # Excel file signatures
            xlsx_sig = b'PK\x03\x04'  # ZIP-based format
            xls_sig = b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1'  # OLE2 format
            
            return header.startswith(xlsx_sig) or header.startswith(xls_sig)
            
        except OSError as e:
            logging.error(f"Excel signature verification failed: {e}")
            return False
    
    @staticmethod
    def _check_for_dangerous_content(file_path: Path) -> bool:
        """Check Excel file for potentially dangerous content."""
        try:
            # For security, we'll check the file's MIME type and magic bytes
            # This is a basic check - in production, use a more robust library
            mime_type, _ = mimetypes.guess_type(str(file_path))
            if mime_type and 'executable' in mime_type.lower():
                return True
            
            # Check for executable file extensions in the filename
            file_ext = file_path.suffix.lower()
            if file_ext in SecurityValidator.DANGEROUS_EXTENSIONS:
                return True
            
            return False
            
        except (OSError, TypeError, ValueError) as e:
            logging.error(f"Dangerous content check failed: {e}")
            return True  # Fail safe - assume dangerous if we can't check
    
    @staticmethod
    def sanitize_user_input(text: str, max_length: int = 1000) -> Tuple[bool, str, str]:
        """
        Sanitize general user input to prevent injection attacks.
        Returns (is_valid, sanitized_text, error_message)
        """
        try:
            if not text or not text.strip():
                return False, "", "Input cannot be empty"
            
            cleaned = text.strip()
            
            # Check length
            if len(cleaned) > max_length:
                return False, "", f"Input too long (max {max_length} characters)"
            
            # Remove potential HTML/XML tags
            cleaned = re.sub(r'<[^>]*>', '', cleaned)
            
            # Remove potential script fragments
            cleaned = re.sub(r'javascript:', '', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'vbscript:', '', cleaned, flags=re.IGNORECASE)
            
            # Escape HTML characters
            cleaned = html.escape(cleaned)
            
            # Check for SQL injection patterns (basic)
            sql_patterns = [
                r'(\bSELECT\b|\bINSERT\b|\bUPDATE\b|\bDELETE\b|\bDROP\b|\bUNION\b)',
                r'(\bOR\b.*\b1=1\b|\bAND\b.*\b1=1\b)',
                r'(\bEXEC\b|\bEXECUTE\b|\bXP_CMDSHELL\b)'
            ]
            
            for pattern in sql_patterns:
                if re.search(pattern, cleaned, re.IGNORECASE):
                    return False, "", "Input contains potentially dangerous patterns"
            
            return True, cleaned, "Input sanitized successfully"
            
        except (TypeError, ValueError, re.error) as e:
            logging.error(f"User input sanitization error: {e}")
            return False, "", f"Input sanitization failed: {str(e)}"
    
    @staticmethod
    def validate_directory_path(directory_path: Path, must_exist: bool = True) -> Tuple[bool, str]:
        """
        Validate directory path for security.
        Returns (is_valid, error_message)
        """
        try:
            # Check if path contains traversal
            path_str = str(directory_path)
            if SecurityValidator._contains_traversal(path_str):
                return False, "Path contains traversal attempts"
            
            # Check if directory is a symlink (potential security risk)
            if directory_path.is_symlink():
                return False, "Symbolic links are not allowed for security reasons"
            
            # Resolve the path to get absolute path
            resolved_path = directory_path.resolve()
            
            # --- CRITICAL FIX FOR NETWORK DRIVES ---
            # Instead of guessing "/" as a system root (which blocks X:\), 
            # we explicitly target only the C:\ drive and Windows system folders.
            
            system_roots = []
            
            # 1. The Real System Drive (Usually C:\)
            system_root_env = os.environ.get('SystemRoot', 'C:\\Windows')
            system_drive = os.path.splitdrive(system_root_env)[0] + '\\'
            if Path(system_drive).exists():
                system_roots.append(Path(system_drive).resolve())
            else:
                # Fallback
                system_roots.append(Path('C:\\').resolve())

            # 2. Critical Windows Folders (C:\Windows, C:\Program Files)
            try:
                system_roots.append(Path(os.environ.get('SystemRoot')).resolve())
                system_roots.append(Path(os.environ.get('ProgramFiles')).resolve())
            except (TypeError, ValueError, OSError):
                pass

            # Only check if the user is trying to write EXACTLY to a system root (like C:\)
            # Writing to C:\Project is fine. Writing to C:\ is blocked.
            for system_root in system_roots:
                if resolved_path == system_root:
                    return False, f"Access to system directory {system_root} is not allowed"
            
            # Check if directory exists if must_exist is True
            if must_exist and not resolved_path.exists():
                return False, f"Directory does not exist: {resolved_path}"
            
            # Check if path is actually a directory
            if must_exist and not resolved_path.is_dir():
                return False, f"Path is not a directory: {resolved_path}"
            
            return True, "Directory path validated"
            
        except (TypeError, ValueError, OSError) as e:
            return False, f"Directory validation error: {str(e)}"
    
    @staticmethod
    def validate_file_extension(file_path: Path) -> Tuple[bool, str]:
        """
        Validate file extension against dangerous types.
        Returns (is_valid, error_message)
        """
        try:
            file_ext = file_path.suffix.lower()
            
            if file_ext in SecurityValidator.DANGEROUS_EXTENSIONS:
                return False, f"Potentially dangerous file extension: {file_ext}"
            
            return True, "File extension is safe"
            
        except (TypeError, ValueError) as e:
            return False, f"Extension validation failed: {str(e)}"
    
    @staticmethod
    def is_valid_update_file(file_path: Path) -> bool:
        """Check if a file is a valid update candidate (ZIP)."""
        return file_path.suffix.lower() == '.zip' and file_path.exists()

    @staticmethod
    def check_file_integrity(file_path: Path) -> Tuple[bool, str]:
        """
        Perform basic file integrity check.
        Returns (is_valid, error_message)
        """
        try:
            if not file_path.exists():
                return False, "File does not exist"
            
            if file_path.is_dir():
                return False, "Path is a directory, not a file"
            
            # Check file size is reasonable
            file_size = file_path.stat().st_size
            if file_size == 0:
                return False, "File is empty"
            
            # Check if file is accessible
            try:
                with open(file_path, 'rb') as f:
                    f.read(1)  # Try to read first byte
            except (PermissionError, OSError) as e:
                return False, f"File access denied: {str(e)}"
            
            return True, "File integrity check passed"
            
        except OSError as e:
            return False, f"Integrity check failed: {str(e)}"


class SecureTempManager:
    """Secure temporary file management with automatic cleanup."""
    
    def __init__(self):
        self.temp_files: List[Path] = []
        self.temp_dirs: List[Path] = []
        self.created_at = datetime.now()
    
    def create_temp_file(self, suffix: str = ".tmp", content: bytes = None) -> Path:
        """Create a secure temporary file."""
        try:
            temp_file = tempfile.NamedTemporaryFile(
                suffix=suffix, 
                delete=False,
                dir=tempfile.gettempdir()
            )
            temp_path = Path(temp_file.name)
            
            if content:
                temp_file.write(content)
            
            temp_file.close()
            self.temp_files.append(temp_path)
            
            # Set secure permissions (read/write for user only)
            try:
                os.chmod(temp_path, 0o600)
            except OSError as e:
                logging.warning(f"Could not set permissions on temp file {temp_path}: {e}")
            
            return temp_path
            
        except (OSError, ValueError, TypeError) as e:
            logging.error(f"Failed to create temp file: {e}")
            raise SecurityError(f"Failed to create secure temporary file: {e}")
    
    def create_temp_directory(self, prefix: str = "ut_vfx_") -> Path:
        """Create a secure temporary directory."""
        try:
            temp_dir = tempfile.mkdtemp(prefix=prefix)
            temp_path = Path(temp_dir)
            self.temp_dirs.append(temp_path)
            
            # Set secure permissions (read/write/execute for user only)
            try:
                os.chmod(temp_path, 0o700)
            except OSError as e:
                logging.warning(f"Could not set permissions on temp directory {temp_path}: {e}")
            
            return temp_path
            
        except (OSError, ValueError, TypeError) as e:
            logging.error(f"Failed to create temp directory: {e}")
            raise SecurityError(f"Failed to create secure temporary directory: {e}")
    
    def cleanup(self):
        """Securely cleanup all temporary files and directories."""
        errors = []
        
        # Cleanup files
        for temp_path in self.temp_files:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError as e:
                errors.append(f"File {temp_path}: {e}")
        
        # Cleanup directories
        for temp_path in self.temp_dirs:
            try:
                if temp_path.exists():
                    import shutil
                    shutil.rmtree(temp_path)
            except OSError as e:
                errors.append(f"Directory {temp_path}: {e}")
        
        self.temp_files.clear()
        self.temp_dirs.clear()
        
        if errors:
            logging.warning(f"Secure temp cleanup completed with errors: {errors}")
        else:
            logging.info("Secure temp cleanup completed successfully")
    
    def __del__(self):
        """Destructor to ensure cleanup."""
        self.cleanup()


class SecurityError(Exception):
    """Security-related exception."""
    pass


# Global instance for convenience
security_validator = SecurityValidator()


class SecretManager:
    """
    Securely manage application secrets (API keys, encryption keys, db passwords).
    Priority:
    1. Environment Variables (Best for CI/CD)
    2. OS Keyring (Best for Local User)
    3. Flat File (Legacy/Fallback - Least Secure)
    """

    SERVICE_NAME = "UTVFX"
    
    @staticmethod
    def get_secret(key_name: str, fallback_file_path: Optional[Path] = None) -> Optional[str]:
        """
        Retrieve a secret by name.
        """
        # 1. Environment Variable
        env_val = os.environ.get(key_name.upper())
        if env_val:
            return env_val

        # 2. OS Keyring
        try:
            import keyring
            keyring_val = keyring.get_password(SecretManager.SERVICE_NAME, key_name)
            if keyring_val:
                return keyring_val
        except ImportError:
            logging.debug("Keyring not installed. Skipping OS secret store.")
        except Exception as e:
            logging.warning(f"Failed to access Keyring: {e}")

        # 3. Fallback File
        if fallback_file_path and fallback_file_path.exists():
            try:
                with open(fallback_file_path, 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except Exception as e:
                logging.exception(f"Failed to read fallback secret file {fallback_file_path}: {e}")

        return None

    @staticmethod
    def set_secret(key_name: str, secret_value: str, store_in_keyring: bool = True):
        """
        Store a secret.
        """
        if not secret_value:
            return

        # Always suggest Env Var usage to user in logs
        logging.info(f"Secret '{key_name}' set. Consider setting {key_name.upper()} env var for better security.")

        if store_in_keyring:
            try:
                import keyring
                keyring.set_password(SecretManager.SERVICE_NAME, key_name, secret_value)
                logging.info(f"Secret '{key_name}' stored in OS Keyring.")
            except ImportError:
                logging.warning("Keyring not installed. Secret NOT stored in OS Keyring.")
            except Exception as e:
                logging.exception(f"Failed to write to Keyring: {e}")

    @staticmethod
    def delete_secret(key_name: str):
        try:
            import keyring
            keyring.delete_password(SecretManager.SERVICE_NAME, key_name)
        except Exception as exc:
            logging.debug("Keyring secret cleanup skipped for %s: %s", key_name, exc)
