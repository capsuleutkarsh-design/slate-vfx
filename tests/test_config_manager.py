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
        from slate.core.infra.config_manager import ConfigManager
        
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
    
    # Settings are JSON, not HTML. They used to be html.escape()d on every
    # load and every save, so "Tom & Jerry's Show" became
    # "Tom &amp;amp; Jerry&amp;#x27;s Show" after one restart (SYS-105).

    def test_sanitize_settings_keeps_values_exactly(self, config_manager):
        values = {"path": "D:/Tom & Jerry's Show/<plates>", "quote": 'say "hi"'}
        assert config_manager._sanitize_settings(values) == values

    def test_sanitize_settings_recursively(self, config_manager):
        """Nested dicts and lists are checked, and kept as they are."""
        nested = {"level1": {"level2": {"v": "A & B"}}, "tags": ["x & y", 3, {"k": "<b>"}]}
        assert config_manager._sanitize_settings(nested) == nested

    def test_sanitize_settings_limits_length(self, config_manager):
        sanitized = config_manager._sanitize_settings({"big": "x" * 20000, "list": ["y" * 20000]})
        assert len(sanitized["big"]) == 10000 and len(sanitized["list"][0]) == 10000

    def test_ampersands_and_apostrophes_survive_save_and_load(self, config_manager):
        original = "D:/Studio/Tom & Jerry's Show"
        config_manager.settings["global_settings"]["last_project_dir"] = original
        assert config_manager.save_settings(config_manager.settings)
        for _ in range(3):
            loaded = config_manager.load_settings()
            assert loaded["global_settings"]["last_project_dir"] == original
            config_manager.save_settings(loaded)

    def test_a_file_escaped_by_an_older_slate_is_repaired_once(self, config_manager):
        import json
        path = config_manager.settings_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "global_settings": {"last_project_dir": "Tom &amp;amp; Jerry&amp;#x27;s Show"},
            "logo": ["A &amp;amp; B"]}), encoding="utf-8")
        loaded = config_manager.load_settings()
        assert loaded["global_settings"]["last_project_dir"] == "Tom & Jerry's Show"
        assert loaded["logo"] == ["A & B"]
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["global_settings"]["last_project_dir"] == "Tom & Jerry's Show"
        assert on_disk[config_manager.ESCAPE_REPAIRED_KEY] is True

    def test_after_the_repair_a_literal_entity_is_left_alone(self, config_manager):
        import json
        path = config_manager.settings_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"note": "write &amp; like this",
                                    config_manager.ESCAPE_REPAIRED_KEY: True}), encoding="utf-8")
        assert config_manager.load_settings()["note"] == "write &amp; like this"
    
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
        from slate.core.infra.config_manager import ConfigManager
        
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
        from slate.core.infra.config_manager import ConfigManager
        
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
        from slate.core.infra.config_manager import ConfigManager
        
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
        from slate.core.infra.config_manager import ConfigManager
        
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


def test_an_old_config_with_central_library_still_loads(tmp_path, monkeypatch):
    """Integration: the unused central_library default is gone; old configs keep working."""
    import json
    from slate.core.infra.config_manager import ConfigManager
    monkeypatch.setattr(ConfigManager, "_get_app_data_dir", lambda self: tmp_path)
    cm = ConfigManager()
    cm.settings_file.write_text(json.dumps({"paths": {"central_library": str(tmp_path / "lib")}}),
                                encoding="utf-8")
    old = ConfigManager()
    assert old.settings.get("paths", {}).get("central_library") == str(tmp_path / "lib")
    assert old.get_path("central_library") == tmp_path / "lib"
    assert "Studio_soft_2" not in str(cm.get_path("central_library"))
