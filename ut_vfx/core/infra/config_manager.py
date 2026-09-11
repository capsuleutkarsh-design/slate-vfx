"""
SECURE Configuration manager for UT_VFX Production tool.
Enhanced with security validation and data sanitization.
"""
import json
import html
from pathlib import Path
import shutil
import logging
from typing import Dict, Any, List
import sys

from ut_vfx.utils.security import SecurityValidator, SecurityError


class ConfigManager:
    """SECURE: Manages application settings and templates with enhanced security."""

    def __init__(self) -> None:
        self.app_data_dir: Path = self._get_app_data_dir()
        self.settings_file: Path = self._get_settings_path()
        self.user_templates_file: Path = self._get_user_templates_path()
        self.security_validator: SecurityValidator = SecurityValidator()

        # SECURITY: Validate app data directory
        try:
            dir_valid, dir_error = self.security_validator.validate_directory_path(
                self.app_data_dir, must_exist=False
            )
            if not dir_valid:
                logging.warning(f"App data directory security issue: {dir_error}")
                # Fallback to user home with security validation
                self.app_data_dir = Path.home() / ".ut_vfx_secure"
                self.app_data_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logging.exception(f"App data directory validation failed: {e}")
            self.app_data_dir = Path.home() / ".ut_vfx_secure_fallback"
            self.app_data_dir.mkdir(parents=True, exist_ok=True)

        # Load default templates from the package data
        self.default_templates: Dict[str, Any] = self._load_default_templates()

        # Load user templates (saved custom ones) from AppData
        self.user_templates: Dict[str, Any] = self._load_user_templates()

        # SECURITY: Validate and combine templates
        self.templates: Dict[str, Any] = self._secure_combine_templates()

        # Load settings
        self.settings: Dict[str, Any] = self.load_settings()
        self.global_settings: Dict[str, Any] = self.settings.get("global_settings", self.default_global_settings)

        # SECURITY: Validate format mapping
        self.format_mapping: Dict[str, str] = self._validate_format_mapping()
        
        # Load Ingest Rules
        self.ingest_rules: Dict[str, Any] = self.settings.get("ingest_rules", self.default_ingest_rules)

    @property
    def default_settings(self) -> Dict[str, Any]:
        """Get complete default settings structure"""
        return {
            "global_settings": self.default_global_settings,
            "ingest_rules": self.default_ingest_rules,
            "export_presets": {
                "client_review": {
                    "format": "mp4",
                    "codec": "libx264",
                    "resolution": [1920, 1080],
                    "bitrate": "10M",
                    "quality": "high"
                },
                "high_quality": {
                    "format": "mov",
                    "codec": "prores",
                    "resolution": "source",
                    "bitrate": None,
                    "quality": "highest"
                },
                "web_preview": {
                    "format": "mp4",
                    "codec": "libx264",
                    "resolution": [1280, 720],
                    "bitrate": "5M",
                    "quality": "medium"
                }
            }
        }
        
    def get_path(self, key: str) -> Path:
        """SECURE: Get a configured path with security validation."""
        # 1. Check user settings
        paths = self.settings.get('paths', {})
        if key in paths:
             # Basic validation
             p = Path(paths[key])
             return p
             
        # 2. Defaults
        if key == "central_library":
            return Path.home() / "RuntimeData" / "Studio_soft_2" / "Central_Library"
            
        return Path.home()

    def _secure_combine_templates(self) -> Dict[str, Any]:
        """SECURE: Combine default and user templates with validation."""
        combined = self.default_templates.copy()
        
        # SECURITY: Validate each user template before merging
        for key, template in self.user_templates.items():
            if not isinstance(template, dict):
                logging.warning(f"Skipping invalid user template {key}: not a dictionary")
                continue
                
            # SECURITY: Validate template key
            key_valid, sanitized_key, key_error = self.security_validator.sanitize_filename(key)
            if not key_valid:
                logging.warning(f"Skipping user template with invalid key {key}: {key_error}")
                continue
                
            # SECURITY: Validate template structure
            if self._validate_template_structure(template):
                combined[sanitized_key] = template
            else:
                logging.warning(f"Skipping user template {key}: invalid structure")
                
        return combined

    def _validate_template_structure(self, template: Dict[str, Any]) -> bool:
        """SECURE: Validate template structure and content."""
        try:
            # Check required fields
            if not isinstance(template, dict):
                return False
                
            # SECURITY: Validate template name
            name = template.get("name", "")
            if name:
                name_valid, _, name_error = self.security_validator.sanitize_filename(name)
                if not name_valid:
                    logging.warning(f"Template name validation failed: {name_error}")
                    return False

            # SECURITY: Validate folder lists
            list_fields = ["base_folders", "production_subfolders", "outsource_subfolders", "shot_folders"]
            for field in list_fields:
                if field in template:
                    folder_list = template[field]
                    if not isinstance(folder_list, list):
                        return False
                    # Validate each folder name in the list
                    for folder in folder_list:
                        if not isinstance(folder, str):
                            return False
                        folder_valid, _, folder_error = self.security_validator.sanitize_filename(folder)
                        if not folder_valid:
                            logging.warning(f"Template folder validation failed: {folder_error}")
                            return False

            return True
            
        except Exception as e:
            logging.exception(f"Template structure validation error: {e}")
            return False

    def _validate_format_mapping(self) -> Dict[str, str]:
        """SECURE: Validate and sanitize format mapping."""
        format_mapping = self.settings.get("format_mapping", self.default_format_mapping)
        validated_mapping = {}
        
        for ext, folder in format_mapping.items():
            if not isinstance(ext, str) or not isinstance(folder, str):
                logging.warning(f"Skipping invalid format mapping: {ext} -> {folder}")
                continue
                
            # SECURITY: Validate extension and folder
            ext_valid, sanitized_ext, ext_error = self.security_validator.sanitize_filename(ext)
            folder_valid, sanitized_folder, folder_error = self.security_validator.sanitize_filename(folder)
            
            if ext_valid and folder_valid:
                validated_mapping[sanitized_ext] = sanitized_folder
            else:
                logging.warning(f"Skipping format mapping {ext} -> {folder}: {ext_error or folder_error}")
                
        return validated_mapping

    def _get_app_data_dir(self) -> Path:
        """Get the application data directory with security validation."""
        try:
            import os
            if getattr(sys, 'frozen', False):
                # Running as compiled executable
                app_dir = Path(os.environ.get('LOCALAPPDATA', Path.home() / "AppData" / "Local")) / "UTVFX"
            else:
                # Running as script
                app_dir = Path(os.environ.get('LOCALAPPDATA', Path.home() / "AppData" / "Local")) / "UTVFX"
            
            # SECURITY: Create directory with validation
            app_dir.mkdir(parents=True, exist_ok=True)
            
            # SECURITY: Validate the directory was created
            if not app_dir.exists():
                raise SecurityError(f"Could not create app data directory: {app_dir}")
                
            return app_dir
        except Exception as e:
            logging.exception(f"Could not create/get app data directory: {e}")
            # SECURITY: Fallback with validation
            fallback_dir = Path.home() / ".ut_vfx_secure_fallback"
            fallback_dir.mkdir(parents=True, exist_ok=True)
            return fallback_dir

    def _get_settings_path(self) -> Path:
        """Get the path for settings file."""
        return self.app_data_dir / "settings.json"

    def _get_user_templates_path(self) -> Path:
        """Get the path for user-defined templates file."""
        return self.app_data_dir / "templates.json"

    def _load_default_templates(self) -> Dict[str, Any]:
        """SECURE: Load default templates with validation."""
        try:
            # Try to get the path to the default templates file within the package
            from importlib.resources import files
            # Assuming your package is named 'ut_vfx' and the file is in 'ut_vfx.data'
            template_path = files('ut_vfx.data').joinpath('templates.json')
            with template_path.open('r', encoding='utf-8') as f:
                templates = json.load(f)
                
            # SECURITY: Validate loaded templates
            if not isinstance(templates, dict):
                raise SecurityError("Default templates file does not contain a dictionary")
                
            return templates
            
        except (ImportError, FileNotFoundError, SecurityError) as e:
            logging.warning(f"Could not load default templates from package data: {e}")
            # Fallback: Load from a known relative path (less ideal but might work in some setups)
            try:
                fallback_path = Path(__file__).parent.parent / "data" / "templates.json"
                if fallback_path.exists():
                    with open(fallback_path, 'r', encoding='utf-8') as f:
                        templates = json.load(f)
                    # SECURITY: Validate fallback templates
                    if isinstance(templates, dict):
                        return templates
            except Exception as fallback_e:
                logging.exception(f"Fallback also failed: {fallback_e}")

            # Return an empty dict or a minimal standard template as a last resort
            return {
                "standard": {
                    "name": "Standard Pipeline (Studio)",
                    "description": "Default standard pipeline structure.",
                    "base_folders": ["01_Frm Client", "02_Edit", "03_References", "04_Production", "05_Reels", "06_Feedback", "07_Archive", "08_To Client"],
                    "production_subfolders": ["01_ARTIST_SOW_SHEET", "02_Contact_sheet"],
                    "outsource_subfolders": ["01_To_Outsource"],
                    "shot_folders": ["01_Scan", "02_Dmp", "03_Cg", "04_Roto", "05_Prep", "06_Cmm", "07_Comp", "08_Output"]
                }
            }
        except Exception as e:
            logging.exception(f"Unexpected error loading default templates: {e}")
            # Return a minimal standard template as a last resort
            return {
                "standard": {
                    "name": "Standard Pipeline (Studio)",
                    "description": "Default standard pipeline structure.",
                    "base_folders": ["01_Frm Client", "02_Edit", "03_References", "04_Production", "05_Reels", "06_Feedback", "07_Archive", "08_To Client"],
                    "production_subfolders": ["01_ARTIST_SOW_SHEET", "02_Contact_sheet"],
                    "outsource_subfolders": ["01_To_Outsource"],
                    "shot_folders": ["01_Scan", "02_Dmp", "03_Cg", "04_Roto", "05_Prep", "06_Cmm", "07_Comp", "08_Output"]
                }
            }

    def _load_user_templates(self) -> Dict[str, Any]:
        """SECURE: Load user-defined templates with validation."""
        if self.user_templates_file.exists():
            try:
                # SECURITY: Validate file before reading
                file_valid, file_error = self.security_validator.validate_directory_path(
                    self.user_templates_file.parent, must_exist=True
                )
                if not file_valid:
                    logging.warning(f"Cannot access user templates file: {file_error}")
                    return {}
                    
                with open(self.user_templates_file, 'r', encoding='utf-8') as f:
                    loaded_user_templates = json.load(f)
                    
                # SECURITY: Validate that loaded data is a dictionary
                if not isinstance(loaded_user_templates, dict):
                    logging.warning(f"Loaded user templates file {self.user_templates_file} is not a dictionary. Ignoring.")
                    return {}
                    
                logging.info(f"Loaded {len(loaded_user_templates)} user-defined templates from {self.user_templates_file}")
                return loaded_user_templates
                
            except (json.JSONDecodeError, FileNotFoundError) as e:
                logging.warning(f"Could not load user templates file {self.user_templates_file}: {e}")
                return {}
            except Exception as e:
                logging.exception(f"Unexpected error loading user templates: {e}")
                return {}
        else:
            logging.info(f"User templates file {self.user_templates_file} does not exist yet.")
            return {}

    def get_available_templates(self) -> List[str]:
        """SECURE: Get list of all available template keys with validation."""
        return [key for key in self.templates.keys() 
                if self.security_validator.sanitize_filename(key)[0]]

    def save_templates(self, templates_dict: Dict[str, Any]) -> bool:
        """SECURE: Save user-defined templates with security validation."""
        try:
            # SECURITY: Validate templates dictionary
            if not isinstance(templates_dict, dict):
                raise SecurityError("Templates data is not a dictionary")
                
            # Create backup of existing user templates file
            if self.user_templates_file.exists():
                # SECURITY: Validate backup path
                backup_path = self.user_templates_file.with_suffix('.bak')
                backup_valid, backup_error = self.security_validator.validate_directory_path(
                    backup_path.parent, must_exist=True
                )
                if backup_valid:
                    shutil.copy2(self.user_templates_file, backup_path)
                    logging.info(f"Backed up user templates to {backup_path}")
                else:
                    logging.warning(f"Cannot create backup: {backup_error}")

            # SECURITY: Prepare to save - only save templates that are *not* in the default set
            # and pass security validation
            user_defined_only = {}
            for key, value in templates_dict.items():
                if key not in self.default_templates:
                    # SECURITY: Validate template key and structure
                    key_valid, sanitized_key, key_error = self.security_validator.sanitize_filename(key)
                    if key_valid and self._validate_template_structure(value):
                        user_defined_only[sanitized_key] = value
                    else:
                        logging.warning(f"Skipping invalid user template {key}: {key_error}")

            # SECURITY: Ensure the directory exists with validation
            dir_valid, dir_error = self.security_validator.validate_directory_path(
                self.user_templates_file.parent, must_exist=False
            )
            if not dir_valid:
                raise SecurityError(f"Cannot access templates directory: {dir_error}")
                
            self.user_templates_file.parent.mkdir(parents=True, exist_ok=True)

            # SECURITY: Save the user-defined templates
            with open(self.user_templates_file, 'w', encoding='utf-8') as f:
                json.dump(user_defined_only, f, indent=4, ensure_ascii=False)

            logging.info(f"Saved {len(user_defined_only)} validated user-defined templates to {self.user_templates_file}")

            # SECURITY: Update the in-memory user_templates and combined templates
            self.user_templates = user_defined_only
            self.templates = self.default_templates.copy()
            
            # SECURITY: Re-validate combined templates
            for key, template in self.user_templates.items():
                if self._validate_template_structure(template):
                    self.templates[key] = template

            return True
            
        except SecurityError as e:
            logging.error(f"Security error saving user templates: {e}")
            # Attempt to restore backup if save failed
            self._restore_templates_backup()
            return False
        except Exception as e:
            logging.exception(f"Could not save user templates file {self.user_templates_file}: {e}")
            # Attempt to restore backup if save failed
            self._restore_templates_backup()
            return False

    def _restore_templates_backup(self) -> None:
        """SECURE: Attempt to restore templates from backup."""
        try:
            backup_path = self.user_templates_file.with_suffix('.bak')
            if backup_path.exists():
                # SECURITY: Validate backup before restoration
                backup_valid, backup_error = self.security_validator.validate_directory_path(
                    backup_path.parent, must_exist=True
                )
                if backup_valid:
                    shutil.copy2(backup_path, self.user_templates_file)
                    logging.info("Restored user templates from backup")
                    # Reload from backup
                    self.user_templates = self._load_user_templates()
                    # SECURITY: Re-validate combined templates
                    self.templates = self.default_templates.copy()
                    for key, template in self.user_templates.items():
                        if self._validate_template_structure(template):
                            self.templates[key] = template
        except Exception as backup_error:
            logging.exception(f"Could not restore backup: {backup_error}")

    @property
    def default_format_mapping(self) -> Dict[str, str]:
        """Get default format mapping."""
        return {
            'dpx': 'Scan/Dpx',
            'exr': 'Scan/Exr',
            'tif': 'Scan/Tiff',
            'tiff': 'Scan/Tiff',
            'mov': 'Scan/Mov',
            'avi': 'Scan/Avi',
            'png': 'Scan/Png',
            'jpg': 'Scan/Jpg',
            'jpeg': 'Scan/Jpg',
            'raw': 'Scan/Raw',
            'cine': 'Scan/Cine',
            'r3d': 'Scan/R3d'
        }

    @property
    def default_global_settings(self) -> Dict[str, Any]:
        """Get default global settings."""
        return {
            'restore_last_paths': True,
            'dry_run_enabled': False,
            'last_template_used': 'standard',
            'ui_scale_override': 0.0,
            'theme': 'dark',
            'window_geometry': '',
            'branding_logo_path': ''
        }

    @property
    def default_ingest_rules(self) -> Dict[str, Any]:
        """SECURE: Get default smart ingest rules."""
        return {
            "00_Documents": {
                "aliases": ["script", "doc", "pdf", "report", "budget", "schedule", "excel", "sheet"],
                "extensions": [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".md"],
                "priority": 5
            },
            "02_Audio": {
                "aliases": ["audio", "sound", "voice", "vo", "sfx", "music", "track"],
                "extensions": [".wav", ".mp3", ".aif", ".aiff", ".m4a", ".ogg"],
                "priority": 15
            },
            "03_3D": {
                "aliases": ["3d", "mesh", "model", "abc", "alembic", "obj", "fbx", "maya", "blender", "geo"],
                "extensions": [".fbx", ".obj", ".abc", ".ma", ".mb", ".blend", ".usd", ".usdc", ".usda"],
                "priority": 18
            },
            "02_Plate": {
                "aliases": ["plate", "plt", "bg", "background", "footage", "scans", "ingest"],
                "extensions": [".dpx", ".cin", ".ari", ".exr", ".png", ".jpeg", ".jpg"], 
                "priority": 10
            },
            "06_Ref": {
                "aliases": ["ref", "reference", "texture", "tex", "lookdev"],
                "extensions": [".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif"],
                "priority": 12
            },
            "05_Prep": {
                "aliases": ["prep", "paint", "cleanup", "clean", "remove", "patch"],
                "extensions": [".exr", ".dpx", ".nk", ".png", ".jpg"],
                "priority": 20
            },
            "06_Roto": {
                "aliases": ["roto", "matte", "wishp", "open poly", "silhouette", "mask", "alpha"],
                "extensions": [".sfx", ".nk", ".sfw", ".splines"],
                "priority": 25
            },
            "07_Comp": {
                "aliases": ["comp", "cmp", "composite", "final", "precomp"],
                "extensions": [".nk", ".nuke", ".mov", ".mp4"],
                "priority": 30
            },
            "08_Lighting": {
                "aliases": ["light", "lit", "render", "cg", "beauty", "pass"],
                "extensions": [".exr", ".hdr"],
                "priority": 40
            },
            "09_Render": {
                "aliases": ["daily", "dailies", "preview", "mov", "qt", "review"],
                "extensions": [".mov", ".mp4", ".mxf"],
                "priority": 45
            }
        }

    def load_settings(self) -> Dict[str, Any]:
        """SECURE: Load settings with security validation and merging with defaults."""
        try:
            settings = self.default_global_settings.copy()  # Start with defaults
            
            if self.settings_file.exists():
                # SECURITY: Validate settings file path
                file_valid, file_error = self.security_validator.validate_directory_path(
                    self.settings_file.parent, must_exist=True
                )
                if not file_valid:
                    logging.warning(f"Cannot access settings file: {file_error}")
                    return settings
                    
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    loaded_settings = json.load(f)

                # SECURITY: Validate loaded settings structure
                if not isinstance(loaded_settings, dict):
                    logging.warning("Settings file does not contain a dictionary. Using defaults.")
                    return settings

                # SECURITY: Sanitize loaded settings before merging
                sanitized_settings = self._sanitize_settings(loaded_settings)
                
                # Merge loaded settings with defaults
                self._secure_merge_dicts(settings, sanitized_settings)
                
                # CRITICAL FIX: Ensure 'ingest_rules' defaults exist if missing from file
                if 'ingest_rules' not in settings or not settings['ingest_rules']:
                     settings['ingest_rules'] = self.default_ingest_rules.copy()
                     logging.info("Restored missing ingest_rules from defaults.")

            return settings
            
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logging.warning(f"Could not load settings file {self.settings_file}: {e}. Using defaults.")
            return self.default_global_settings.copy()
        except Exception as e:
            logging.exception(f"Unexpected error loading settings: {e}. Using defaults.")
            return self.default_global_settings.copy()

    def _secure_merge_dicts(self, base_dict: Dict[str, Any], update_dict: Dict[str, Any]) -> None:
        """SECURE: Recursively merge dictionaries with type validation."""
        for key, value in update_dict.items():
            # SECURITY: Validate key
            if not isinstance(key, str):
                logging.warning(f"Skipping settings key with invalid type: {key}")
                continue
                
            if key in base_dict and isinstance(base_dict[key], dict) and isinstance(value, dict):
                self._secure_merge_dicts(base_dict[key], value)
            else:
                # SECURITY: Only update if types match or base doesn't have the key
                if key not in base_dict or type(base_dict[key]) == type(value):
                    base_dict[key] = value
                else:
                    logging.warning(f"Type mismatch for setting {key}: {type(base_dict[key])} vs {type(value)}")

    def _sanitize_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """SECURE: Recursively sanitize all string values in settings."""
        sanitized = {}
        
        for key, value in settings.items():
            # SECURITY: Validate key
            if not isinstance(key, str):
                logging.warning(f"Skipping settings key with invalid type: {key}")
                continue
                
            if isinstance(value, str):
                # SECURITY: Escape potentially dangerous characters and validate length
                if len(value) > 10000:  # Reasonable limit for settings
                    logging.warning(f"Settings value too long for key {key}, truncating")
                    value = value[:10000]
                sanitized[key] = html.escape(value)
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_settings(value)
            elif isinstance(value, list):
                sanitized[key] = [self._sanitize_settings(item) if isinstance(item, dict) 
                                else html.escape(item) if isinstance(item, str) 
                                else item for item in value]
            else:
                sanitized[key] = value
        
        return sanitized

    def save_settings(self, settings: Dict[str, Any]) -> bool:
        """SECURE: Save settings with security validation and backup."""
        try:
            # SECURITY: Validate settings structure
            if not isinstance(settings, dict):
                raise SecurityError("Settings data is not a dictionary")

            # SECURITY: Sanitize settings before saving
            sanitized_settings = self._sanitize_settings(settings)

            # Create backup of existing settings
            if self.settings_file.exists():
                # SECURITY: Validate backup path
                backup_path = self.settings_file.with_suffix('.bak')
                backup_valid, backup_error = self.security_validator.validate_directory_path(
                    backup_path.parent, must_exist=True
                )
                if backup_valid:
                    shutil.copy2(self.settings_file, backup_path)
                else:
                    logging.warning(f"Cannot create settings backup: {backup_error}")

            # SECURITY: Ensure directory exists with validation
            dir_valid, dir_error = self.security_validator.validate_directory_path(
                self.settings_file.parent, must_exist=False
            )
            if not dir_valid:
                raise SecurityError(f"Cannot access settings directory: {dir_error}")
                
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)

            # SECURITY: Save current settings
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(sanitized_settings, f, indent=4, ensure_ascii=False)

            logging.info(f"Settings saved successfully to {self.settings_file}")
            return True
            
        except SecurityError as e:
            logging.error(f"Security error saving settings: {e}")
            self._restore_settings_backup()
            return False
        except Exception as e:
            logging.exception(f"Could not save settings file {self.settings_file}: {e}")
            self._restore_settings_backup()
            return False

    def _restore_settings_backup(self) -> None:
        """SECURE: Attempt to restore settings from backup."""
        try:
            backup_path = self.settings_file.with_suffix('.bak')
            if backup_path.exists():
                # SECURITY: Validate backup before restoration
                backup_valid, backup_error = self.security_validator.validate_directory_path(
                    backup_path.parent, must_exist=True
                )
                if backup_valid:
                    shutil.copy2(backup_path, self.settings_file)
                    logging.info("Restored settings from backup")
        except Exception as backup_error:
            logging.exception(f"Could not restore settings backup: {backup_error}")

    def update_global_settings(self, new_settings: Dict[str, Any]) -> bool:
        """SECURE: Update global settings with validation."""
        try:
            # SECURITY: Validate new settings
            if not isinstance(new_settings, dict):
                raise SecurityError("New settings must be a dictionary")
                
            # SECURITY: Sanitize new settings
            sanitized_new_settings = self._sanitize_settings(new_settings)
            
            # Access the global_settings key within the main settings dict
            current_global_settings = self.settings.get('global_settings', self.default_global_settings.copy())
            
            # SECURITY: Merge with validation
            self._secure_merge_dicts(current_global_settings, sanitized_new_settings)
            self.settings['global_settings'] = current_global_settings
            
            return self.save_settings(self.settings)
            
        except SecurityError as e:
            logging.error(f"Security error updating global settings: {e}")
            return False
        except Exception as e:
            logging.exception(f"Error updating global settings: {e}")
            return False
