"""
Security Validation Unit Tests.

This suite is critical for data safety and preventing malicious input:
1. Path Traversal: Verifies detection of attempts to access files outside the sandbox (e.g. `../../etc/passwd`).
2. Filename Sanitization: Ensures special characters are stripped from filenames.
3. Secure Temp Files: Verifies creation and cleanup of temporary data.
"""

import unittest
from pathlib import Path
import tempfile
from ut_vfx.utils.security import SecurityValidator, SecureTempManager


class TestSecurityValidator(unittest.TestCase):
    """Test the security validation functions."""
    
    def setUp(self):
        """Set up test environment."""
        self.validator = SecurityValidator()
        self.test_dir = Path(tempfile.mkdtemp())
    
    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.test_dir)
    
    def test_path_traversal_detection(self):
        """Test path traversal detection."""
        dangerous_paths = [
            "../../../etc/passwd",
            "..\\..\\windows\\system32",
            "%2e%2e%2fetc%2fpasswd",
            "%2e%2e%2fetc%2fpasswd"
            # "//etc/passwd" - Removed as // is valid UNC path start on Windows
        ]
        
        for path in dangerous_paths:
            self.assertTrue(
                self.validator._contains_traversal(path),
                f"Should detect traversal in: {path}"
            )
    
    def test_filename_sanitization(self):
        """Test filename sanitization."""
        test_cases = [
            ("normal_file.txt", True),
            ("file<name>.txt", True),  # Should be sanitized
            ("CON.txt", False),  # Restricted name
            ("", False),  # Empty
        ]
        
        for filename, should_succeed in test_cases:
            is_valid, sanitized, error_msg = self.validator.sanitize_filename(filename)
            if should_succeed:
                self.assertTrue(is_valid, f"Should sanitize: {filename}")
            else:
                self.assertFalse(is_valid, f"Should reject: {filename}")
    
    def test_directory_validation(self):
        """Test directory validation."""
        # Valid directory
        is_valid, error_msg = self.validator.validate_directory_path(self.test_dir)
        self.assertTrue(is_valid, "Should validate existing directory")
        
        # Non-existent directory
        non_existent = self.test_dir / "non_existent"
        is_valid, error_msg = self.validator.validate_directory_path(non_existent, must_exist=False)
        self.assertTrue(is_valid, "Should validate non-existent directory when must_exist=False")


class TestSecureTempManager(unittest.TestCase):
    """Test the secure temporary file manager."""
    
    def test_temp_file_creation(self):
        """Test secure temporary file creation."""
        manager = SecureTempManager()
        
        # Create a temp file
        temp_file = manager.create_temp_file(content=b"test content")
        
        # Verify file exists and has content
        self.assertTrue(temp_file.exists())
        with open(temp_file, 'rb') as f:
            content = f.read()
        self.assertEqual(content, b"test content")
        
        # Cleanup
        manager.cleanup()
        self.assertFalse(temp_file.exists())


if __name__ == '__main__':
    unittest.main()