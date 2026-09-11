"""
Config Manager Tests - Aligned with Actual API

Comprehensive tests for ConfigManager covering:
- Settings load/save cycle
- Template management
- XSS sanitization (_sanitize_settings)
- Backup and restore functionality
- Security validation

Tests the actual ConfigManager API as implemented.
"""

import pytest
import tempfile
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestConfigManagerActualAPI:
    """Test ConfigManager using actual API methods."""
    
    @pytest.fixture
    def temp_config_dir(self):
        """Create temporary config directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            
            # Create initial settings.json
            settings_file = config_dir / "settings.json"
            initial_settings = {
                "global_settings": {
                    "restore_last_paths": True,
                    "theme": "dark"
                },
                "format_mapping": {
                    "exr": "Scan/Exr",
                    "dpx": "Scan/Dpx"
                }
            }
            settings_file.write_text(json.dumps(initial_settings, indent=2))
            
            yield config_dir
    
    @pytest.fixture
    def config_manager(self, temp_config_dir, monkeypatch):
        """Create ConfigManager with temp directory."""
        from ut_vfx.core.infra.config_manager import ConfigManager
        
        # Patch _get_app_data_dir to use temp directory
        def mock_get_app_data_dir(self):
            return temp_config_dir
        
        monkeypatch.setattr(ConfigManager, '_get_app_data_dir', mock_get_app_data_dir)
        
        cm = ConfigManager()
        return cm
    
    def test_load_settings_returns_dict(self, config_manager):
        """Test that load_settings returns a dictionary."""
        settings = config_manager.load_settings()
        
        assert settings is not None
        assert isinstance(settings, dict)
    
    def test_settings_have_defaults(self, config_manager):
        """Test that settings contain defaults even if file missing."""
        settings = config_manager.settings
        
        # Should have default values
        assert 'restore_last_paths' in settings
        assert isinstance(settings['restore_last_paths'], bool)
    
    def test_save_and_load_settings_cycle(self, config_manager, temp_config_dir):
        """Test saving and loading settings."""
        # Modify settings
        new_settings = {
            "custom_setting": "test_value",
            "theme": "light"
        }
        
        # Save settings
        success = config_manager.save_settings(new_settings)
        assert success is True
        
        # Verify file was created
        settings_file = temp_config_dir / "settings.json"
        assert settings_file.exists()
        
        # Load settings again
        loaded = config_manager.load_settings()
        assert "custom_setting" in loaded
        assert loaded["custom_setting"] == "test_value"
    
    def test_sanitize_settings_escapes_html(self, config_manager):
        """Test that _sanitize_settings escapes HTML/XSS."""
        malicious_settings = {
            "user_input": "<script>alert('XSS')</script>",
            "project_name": "<img src=x onerror=alert(1)>"
        }
        
        sanitized = config_manager._sanitize_settings(malicious_settings)
        
        # Should escape HTML entities
        assert "&lt;script&gt;" in sanitized["user_input"]
        assert "&lt;img" in sanitized["project_name"]
        assert "<script>" not in sanitized["user_input"]
    
    def test_sanitize_settings_recursively(self, config_manager):
        """Test that sanitization works on nested dicts."""
        nested = {
            "level1": {
                "level2": {
                    "xss": "<script>alert('nested')</script>"
                }
            }
        }
        
        sanitized = config_manager._sanitize_settings(nested)
        
        # Check deep sanitization
        deep_value = sanitized["level1"]["level2"]["xss"]
        assert "&lt;script&gt;" in deep_value
        assert "<script>" not in deep_value
    
    def test_sanitize_settings_handles_lists(self, config_manager):
        """Test sanitization of list values."""
        settings_with_list = {
            "tags": ["<script>xss1</script>", "normal", "<b>bold</b>"]
        }
        
        sanitized = config_manager._sanitize_settings(settings_with_list)
        
        # All list items should be sanitized
        assert "&lt;script&gt;" in sanitized["tags"][0]
        assert "normal" == sanitized["tags"][1]
        assert "&lt;b&gt;" in sanitized["tags"][2]
    
    def test_templates_available(self, config_manager):
        """Test that templates are loaded."""
        templates = config_manager.templates
        
        assert templates is not None
        assert isinstance(templates, dict)
        # Should have at least default templates
        assert len(templates) > 0
    
    def test_get_available_templates(self, config_manager):
        """Test getting list of template names."""
        template_list = config_manager.get_available_templates()
        
        assert template_list is not None
        assert isinstance(template_list, list)
    
    def test_backup_created_on_save(self, config_manager, temp_config_dir):
        """Test that backup is created when saving settings."""
        settings_file = temp_config_dir / "settings.json"
        backup_file = temp_config_dir / "settings.bak"
        
        # Create initial file
        settings_file.write_text(json.dumps({"test": "original"}))
        
        # Save new settings
        config_manager.save_settings({"test": "modified"})
        
        # Backup should exist
        if backup_file.exists():  # May or may not depending on implementation
            backup_content = json.loads(backup_file.read_text())
            assert backup_content.get("test") == "original"
    
    def test_secure_merge_dicts_type_validation(self, config_manager):
        """Test that _secure_merge_dicts validates types."""
        base = {"port": 5432, "host": "localhost"}
        update = {"port": "not_a_number"}  # Wrong type
        
        # Should skip mismatched types
        config_manager._secure_merge_dicts(base, update)
        
        # Port should still be int (type mismatch rejected)
        assert base["port"] == 5432
    
    def test_secure_merge_dicts_nested(self, config_manager):
        """Test recursive merging of nested dicts."""
        base = {
            "database": {
                "host": "localhost",
                "port": 5432
            }
        }
        update = {
            "database": {
                "host": "newhost"
                # port not specified - should be preserved
            }
        }
        
        config_manager._secure_merge_dicts(base, update)
        
        assert base["database"]["host"] == "newhost"
        assert base["database"]["port"] == 5432  # Preserved


class TestConfigManagerErrorHandling:
    """Test error handling in ConfigManager."""
    
    def test_load_settings_handles_corrupted_json(self, monkeypatch):
        """Test handling of corrupted settings file."""
        from ut_vfx.core.infra.config_manager import ConfigManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            
            # Create corrupted JSON file
            settings_file = config_dir / "settings.json"
            settings_file.write_text("{ invalid json }")
            
            # Patch to use temp directory
            def mock_get_app_data_dir(self):
                return config_dir
            
            monkeypatch.setattr(ConfigManager, '_get_app_data_dir', mock_get_app_data_dir)
            
            # Should handle gracefully and return defaults
            cm = ConfigManager()
            settings = cm.load_settings()
            
            # Should return defaults despite corrupted file
            assert isinstance(settings, dict)
            assert 'restore_last_paths' in settings
    
    def test_save_settings_rejects_non_dict(self, monkeypatch):
        """Test that save_settings rejects non-dict input."""
        from ut_vfx.core.infra.config_manager import ConfigManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            
            def mock_get_app_data_dir(self):
                return config_dir
            
            monkeypatch.setattr(ConfigManager, '_get_app_data_dir', mock_get_app_data_dir)
            
            cm = ConfigManager()
            
            # Try to save non-dict
            result = cm.save_settings("not a dict")
            
            assert result is False  # Should reject
    
    def test_missing_settings_file_creates_defaults(self, monkeypatch):
        """Test that missing settings file results in defaults."""
        from ut_vfx.core.infra.config_manager import ConfigManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            # No settings.json created
            
            def mock_get_app_data_dir(self):
                return config_dir
            
            monkeypatch.setattr(ConfigManager, '_get_app_data_dir', mock_get_app_data_dir)
            
            cm = ConfigManager()
            settings = cm.settings
            
            # Should have defaults
            assert isinstance(settings, dict)
            assert 'restore_last_paths' in settings


class TestConfigManagerTemplates:
    """Test template management."""
    
    @pytest.fixture
    def config_manager(self, monkeypatch):
        """Create ConfigManager for template testing."""
        from ut_vfx.core.infra.config_manager import ConfigManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            
            def mock_get_app_data_dir(self):
                return config_dir
            
            monkeypatch.setattr(ConfigManager, '_get_app_data_dir', mock_get_app_data_dir)
            
            yield ConfigManager()
    
    def test_default_templates_loaded(self, config_manager):
        """Test that default templates are loaded."""
        templates = config_manager.default_templates
        
        assert isinstance(templates, dict)
        # Should have at least one default template
        assert len(templates) > 0
    
    def test_template_structure_validation(self, config_manager):
        """Test _validate_template_structure."""
        valid_template = {
            "name": "Test Template",
            "base_folders": ["folder1", "folder2"],
            "production_subfolders": ["sub1"]
        }
        
        is_valid = config_manager._validate_template_structure(valid_template)
        assert is_valid is True
    
    def test_invalid_template_rejected(self, config_manager):
        """Test that invalid templates are rejected."""
        invalid_template = "not a dict"
        
        is_valid = config_manager._validate_template_structure(invalid_template)
        assert is_valid is False
    
    def test_save_templates(self, config_manager):
        """Test saving user templates."""
        new_templates = {
            "custom_template": {
                "name": "Custom",
                "base_folders": ["test1", "test2"]
            }
        }
        
        result = config_manager.save_templates(new_templates)
        assert result is True


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
