import json
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import logging
from datetime import datetime
import re
from enum import Enum
from ..infra.global_config import GlobalConfig
from ut_vfx.utils.safe_json import SafeJsonIO

class TemplateType(Enum):
    STANDARD = "standard"
    CUSTOM = "custom"

class TemplateValidator:
    """Validates template structure and integrity."""
    
    @staticmethod
    def validate_template_structure(data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validates if the template dictionary matches required schema.
        Returns (True, "") if valid, or (False, ErrorMessage).
        """
        required_fields = ["name", "description", "version", "template_type", "structure"]
        for field in required_fields:
            if field not in data:
                return False, f"Missing required field: {field}"

        structure = data.get("structure", {})
        if not isinstance(structure, dict):
            return False, "'structure' must be a dictionary"

        required_folders = ["base_folders", "production_subfolders", "outsource_subfolders", "shot_folders"]
        for folder_key in required_folders:
            if folder_key not in structure:
                return False, f"Missing folder definition: {folder_key}"
            if not isinstance(structure[folder_key], list):
                return False, f"'{folder_key}' must be a list of strings"

        return True, "Valid"

class TemplateManager:
    """Advanced template management system."""
    
    def __init__(self, templates_dir: Optional[Path] = None):
        # 1. Use GlobalConfig to find the shared network path (X:/Extra/UT_Central/Templates)
        # If network path is invalid, it falls back to local AppData
        if templates_dir:
            self.templates_dir = templates_dir
        else:
            self.templates_dir = GlobalConfig.server_root() / "Templates"

        self.templates_dir.mkdir(parents=True, exist_ok=True)
        
        # Default templates
        self.default_templates = self._load_default_templates()
        
        # Initialize template cache
        self.template_cache = {}
        self._cache_templates()
        
        logging.info(f"Template manager initialized with directory: {self.templates_dir}")
    
    def _get_default_templates_directory(self) -> Path:
        """DEPRECATED: Now handled by GlobalConfig logic in __init__."""
        return GlobalConfig.server_root() / "Templates"
    
    def _load_default_templates(self) -> Dict[str, Any]:
        """Load default template definitions."""
        return {
            "standard": {
                "name": "Standard VFX Pipeline",
                "description": "Standard VFX pipeline structure for feature film production",
                "author": "UT_VFX Team",
                "version": "1.0.0",
                "created_date": "2025-01-01T00:00:00Z",
                "last_modified": "2025-01-01T00:00:00Z",
                "template_type": "standard",
                "compatibility": ["2.0", "2.1"],
                "tags": ["vfx", "film", "standard", "production"],
                "structure": {
                    "base_folders": [
                        "01_From_Client",
                        "02_Edit",
                        "03_References", 
                        "04_Production",
                        "05_Reels",
                        "06_Feedback",
                        "07_Archive",
                        "08_To_Client"
                    ],
                    "production_subfolders": [
                        "01_ARTIST_SOW_SHEET",
                        "02_Contact_sheet"
                    ],
                    "outsource_subfolders": [
                        "01_To_Outsource"
                    ],
                    "shot_folders": [
                        "00_Annotation",
                        "01_Scan/Dpx",
                        "01_Scan/Exr", 
                        "01_Scan/Tiff",
                        "01_Scan/Mov",
                        "01_Scan/Jpg",
                        "01_Scan/Raw",
                        "02_Dmp",
                        "03_Cg",
                        "04_Roto",
                        "05_Prep", 
                        "06_Cmm",
                        "07_Comp",
                        "08_Output/EXR",
                        "08_Output/MOV",
                        "08_Output/SLAPCOMP"
                    ]
                }
            },
            "tv_series": {
                "name": "TV Series Pipeline",
                "description": "Optimized folder structure for TV series production",
                "author": "UT_VFX Team", 
                "version": "1.0.0",
                "created_date": "2025-01-01T00:00:00Z",
                "last_modified": "2025-01-01T00:00:00Z",
                "template_type": "standard",
                "compatibility": ["2.0", "2.1"],
                "tags": ["tv", "series", "episodic", "production"],
                "structure": {
                    "base_folders": [
                        "01_From_Network",
                        "02_Previs",
                        "03_Edit",
                        "04_References",
                        "05_Production",
                        "06_Episodes",
                        "07_Feedback", 
                        "08_Archive",
                        "09_To_Network"
                    ],
                    "production_subfolders": [
                        "01_Sequences",
                        "02_Assets",
                        "03_Shots"
                    ],
                    "outsource_subfolders": [
                        "01_To_Vendors"
                    ],
                    "shot_folders": [
                        "01_Animatic",
                        "02_Scan",
                        "03_Track",
                        "04_Roto",
                        "05_Animation",
                        "06_Lighting",
                        "07_Compositing",
                        "08_Render",
                        "09_Output"
                    ]
                }
            }
        }
    
    def create_template_from_current_project(self, name: str, description: str, 
                                           base_folders: List[str],
                                           production_subfolders: List[str],
                                           outsource_subfolders: List[str], 
                                           shot_folders: List[str],
                                           author: str = "User") -> Tuple[bool, str]:
        """Create a template from current project settings."""
        try:
            # Validate inputs
            if not name or not name.strip():
                return False, "Template name is required"
            
            # Create template structure
            template_data = {
                "name": name.strip(),
                "description": description.strip(),
                "author": author.strip(),
                "version": "1.0.0",  # Start with version 1.0.0 for user-created templates
                "created_date": datetime.now().isoformat() + "Z",
                "last_modified": datetime.now().isoformat() + "Z",
                "template_type": "custom",
                "compatibility": ["2.1"],  # Current version
                "tags": ["custom", "user-generated"],
                "structure": {
                    "base_folders": base_folders,
                    "production_subfolders": production_subfolders,
                    "outsource_subfolders": outsource_subfolders,
                    "shot_folders": shot_folders
                }
            }
            
            # Validate template
            is_valid, validation_msg = TemplateValidator.validate_template_structure(template_data)
            if not is_valid:
                return False, f"Template validation failed: {validation_msg}"
            
            # Generate unique filename
            safe_name = re.sub(r'[^\w\s-]', '_', name.strip()).replace(' ', '_')
            template_filename = f"{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            template_path = self.templates_dir / template_filename
            
            # Write template to file
            SafeJsonIO.save_json(template_path, template_data, indent=2)
            
            # Update cache
            self.template_cache[template_filename] = template_data
            
            logging.info(f"Created template: {template_filename}")
            return True, f"Template '{name}' created successfully at {template_path}"
            
        except Exception as e:
            error_msg = f"Error creating template: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False, error_msg
    
    def import_template(self, template_path: Path) -> Tuple[bool, str]:
        """Import a template from a file."""
        try:
            if not template_path.exists():
                return False, f"Template file does not exist: {template_path}"
            
            # Read template file
            template_data = SafeJsonIO.load_json(template_path)
            
            # Validate template
            is_valid, validation_msg = TemplateValidator.validate_template_structure(template_data)
            if not is_valid:
                return False, f"Template validation failed: {validation_msg}"
            
            # Generate safe filename for import
            original_name = template_data.get('name', 'unnamed_template')
            safe_name = re.sub(r'[^\w\s-]', '_', original_name).replace(' ', '_')
            import_filename = f"{safe_name}_imported_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            import_path = self.templates_dir / import_filename
            
            # Copy to local templates directory
            import shutil
            shutil.copy2(template_path, import_path)
            
            # Update cache
            self.template_cache[import_filename] = template_data
            
            logging.info(f"Imported template: {import_filename}")
            return True, f"Template imported successfully as {import_filename}"
            
        except json.JSONDecodeError:
            return False, "Invalid JSON format in template file"
        except Exception as e:
            error_msg = f"Error importing template: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False, error_msg
    
    def export_template(self, template_name: str, export_path: Path) -> Tuple[bool, str]:
        """Export a template to a file."""
        try:
            # Find template
            template_data = self.get_template_by_name(template_name)
            if not template_data:
                return False, f"Template not found: {template_name}"
            
            # Ensure export directory exists
            export_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write template to file
            SafeJsonIO.save_json(export_path, template_data, indent=2)
            
            logging.info(f"Exported template: {template_name} to {export_path}")
            return True, f"Template exported successfully to {export_path}"
            
        except Exception as e:
            error_msg = f"Error exporting template: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False, error_msg
    
    def get_available_templates(self) -> List[Dict[str, Any]]:
        """Get list of all available templates."""
        templates = []
        
        # Add default templates
        for key, template in self.default_templates.items():
            templates.append({
                'key': key,
                'name': template['name'],
                'description': template['description'],
                'author': template['author'],
                'type': template['template_type'],
                'location': 'default'
            })
        
        # Add user templates
        for filename, template_data in self.template_cache.items():
            templates.append({
                'key': filename,
                'name': template_data['name'],
                'description': template_data['description'],
                'author': template_data['author'],
                'type': template_data['template_type'],
                'location': 'user'
            })
        
        return templates
    
    def get_template_by_name(self, template_name: str) -> Optional[Dict[str, Any]]:
        """Get template by name."""
        # Check default templates first
        if template_name in self.default_templates:
            return self.default_templates[template_name]
        
        # Check cached user templates
        for filename, template_data in self.template_cache.items():
            if template_data.get('name') == template_name:
                return template_data
        
        # Check if template_name is a filename
        template_path = self.templates_dir / template_name
        if template_path.exists():
            try:
                template_data = SafeJsonIO.load_json(template_path)
                return template_data
            except Exception as exc:
                logging.debug("Template file parse failed at %s: %s", template_path, exc)
        
        return None
    
    def get_template_structure(self, template_name: str) -> Optional[Dict[str, List[str]]]:
        """Get the folder structure for a template."""
        template = self.get_template_by_name(template_name)
        if template:
            return template.get('structure', {})
        return None
    
    def update_template(self, template_name: str, new_data: Dict[str, Any]) -> Tuple[bool, str]:
        """Update an existing template."""
        try:
            # Find the template file
            template_file = None
            template_data = None
            
            # Check if it's a default template
            if template_name in self.default_templates:
                return False, "Cannot modify default templates"
            
            # Look for user template
            for filename, cached_data in self.template_cache.items():
                if cached_data.get('name') == template_name:
                    template_file = self.templates_dir / filename
                    template_data = cached_data
                    break
            
            # If not found in cache, try to find by filename
            if not template_data:
                template_path = self.templates_dir / template_name
                if template_path.exists():
                    template_file = template_path
                    template_data = SafeJsonIO.load_json(template_path)
            
            if not template_data:
                return False, f"Template not found: {template_name}"
            
            # Update template with new data (preserving metadata)
            updated_template = template_data.copy()
            updated_template.update(new_data)
            updated_template['last_modified'] = datetime.now().isoformat() + "Z"
            
            # Validate updated template
            is_valid, validation_msg = TemplateValidator.validate_template_structure(updated_template)
            if not is_valid:
                return False, f"Updated template validation failed: {validation_msg}"
            
            # Write updated template
            SafeJsonIO.save_json(template_file, updated_template, indent=2)
            
            # Update cache
            self.template_cache[template_file.name] = updated_template
            
            logging.info(f"Updated template: {template_name}")
            return True, f"Template '{template_name}' updated successfully"
            
        except Exception as e:
            error_msg = f"Error updating template: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False, error_msg
    
    def delete_template(self, template_name: str) -> Tuple[bool, str]:
        """Delete a user template."""
        try:
            # Cannot delete default templates
            if template_name in self.default_templates:
                return False, "Cannot delete default templates"
            
            # Find template file
            template_file = None
            for filename, cached_data in self.template_cache.items():
                if cached_data.get('name') == template_name:
                    template_file = self.templates_dir / filename
                    break
            
            # If not found in cache, try filename
            if not template_file:
                template_path = self.templates_dir / template_name
                if template_path.exists():
                    template_file = template_path
            
            if not template_file or not template_file.exists():
                return False, f"Template not found: {template_name}"
            
            # Delete the file
            template_file.unlink()
            
            # Remove from cache
            if template_file.name in self.template_cache:
                del self.template_cache[template_file.name]
            
            logging.info(f"Deleted template: {template_name}")
            return True, f"Template '{template_name}' deleted successfully"
            
        except Exception as e:
            error_msg = f"Error deleting template: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False, error_msg
    
    def _cache_templates(self):
        """Cache all user templates for faster access."""
        try:
            template_files = list(self.templates_dir.glob("*.json"))
            
            for template_file in template_files:
                try:
                    template_data = SafeJsonIO.load_json(template_file)
                    
                    # Validate template before caching
                    is_valid, _ = TemplateValidator.validate_template_structure(template_data)
                    if is_valid:
                        self.template_cache[template_file.name] = template_data
                    else:
                        logging.warning(f"Invalid template file skipped: {template_file}")
                        
                except Exception as e:
                    logging.exception(f"Could not load template {template_file}: {e}")
            
            logging.info(f"Cached {len(self.template_cache)} user templates")
        except Exception as e:
            logging.exception(f"Error caching templates: {e}")
    
    def search_templates(self, query: str, tags: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Search templates by name, description, or tags."""
        results = []
        
        # Search default templates
        for key, template in self.default_templates.items():
            if self._matches_search_criteria(template, query, tags):
                results.append({
                    'key': key,
                    'name': template['name'],
                    'description': template['description'],
                    'author': template['author'],
                    'type': template['template_type'],
                    'location': 'default'
                })
        
        # Search user templates
        for filename, template_data in self.template_cache.items():
            if self._matches_search_criteria(template_data, query, tags):
                results.append({
                    'key': filename,
                    'name': template_data['name'],
                    'description': template_data['description'],
                    'author': template_data['author'],
                    'type': template_data['template_type'],
                    'location': 'user'
                })
        
        return results
    
    def _matches_search_criteria(self, template: Dict[str, Any], query: str, tags: Optional[List[str]]) -> bool:
        """Check if template matches search criteria."""
        query_lower = query.lower() if query else ""
        
        # Check name and description
        name_match = query_lower in template.get('name', '').lower()
        desc_match = query_lower in template.get('description', '').lower()
        
        # Check tags if provided
        tag_match = True
        if tags:
            template_tags = [tag.lower() for tag in template.get('tags', [])]
            tag_match = any(tag.lower() in template_tags for tag in tags)
        
        return (name_match or desc_match) and tag_match
    
    def get_template_statistics(self) -> Dict[str, Any]:
        """Get statistics about available templates."""
        default_count = len(self.default_templates)
        user_count = len(self.template_cache)
        total_count = default_count + user_count
        
        # Get template types breakdown
        type_counts = {}
        for template in self.default_templates.values():
            template_type = template.get('template_type', 'unknown')
            type_counts[template_type] = type_counts.get(template_type, 0) + 1
        
        for template_data in self.template_cache.values():
            template_type = template_data.get('template_type', 'unknown')
            type_counts[template_type] = type_counts.get(template_type, 0) + 1
        
        return {
            'total_templates': total_count,
            'default_templates': default_count,
            'user_templates': user_count,
            'template_types': type_counts,
            'last_updated': datetime.now().isoformat()
        }


# Global template manager instance
template_manager = TemplateManager()
