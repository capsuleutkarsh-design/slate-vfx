"""
Template Manager Unit Tests.

This suite validates the Project Template system:
1. Creation: Verifies that 'create_template_from_current_project' generates valid JSON.
2. Search: Tests filter logic for finding templates by tag/name.
3. CRUD: Tests updating and deleting templates (mocked).
"""

import unittest
from unittest.mock import patch
from pathlib import Path
import sys
import os

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ut_vfx.core.domain.template_manager import TemplateManager

class TestTemplateManager(unittest.TestCase):

    def setUp(self):
        # Mock the GlobalConfig or initialized path to use a temp dir
        self.mock_dir = Path("mock_templates")
        self.manager = TemplateManager(templates_dir=self.mock_dir)
        
        # Clear cache for isolation
        self.manager.template_cache = {}

    @patch('ut_vfx.core.domain.template_manager.SafeJsonIO.save_json')
    def test_create_template_success(self, mock_save):
        """Test creating a valid custom template."""
        
        success, msg = self.manager.create_template_from_current_project(
            name="My Cinematic Template",
            description="A test template",
            base_folders=["01_Test"],
            production_subfolders=["01_Shots"],
            outsource_subfolders=[],
            shot_folders=["01_Plate"],
            author="Tester"
        )
        
        self.assertTrue(success, f"creation failed: {msg}")
        
        # Verify save called
        mock_save.assert_called_once()
        args = mock_save.call_args
        data = args[0][1] # The second arg is the data dict
        
        self.assertEqual(data['name'], "My Cinematic Template")
        self.assertEqual(data['template_type'], "custom")
        self.assertIn("custom", data['tags'])

    def test_search_logic(self):
        """Test search and filtering functionality."""
        
        # Inject Mock Data into Cache
        self.manager.template_cache = {
            "t1.json": {
                "name": "Film Noir",
                "description": "Dark style",
                "tags": ["film", "bw"],
                "template_type": "custom",
                "author": "Tester A"
            },
            "t2.json": {
                "name": "Cartoony",
                "description": "Bright style",
                "tags": ["animation", "2d"],
                "template_type": "custom",
                "author": "Tester B"
            }
        }
        
        # 1. Search by Name
        results = self.manager.search_templates(query="Noir")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], "Film Noir")
        
        # 2. Search by Tag
        results = self.manager.search_templates(query="", tags=["animation"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], "Cartoony")
        
        # 3. Search Fail
        results = self.manager.search_templates(query="Horror")
        self.assertEqual(len(results), 0)

    @patch('pathlib.Path.unlink') # Mock file deletion
    def test_delete_template(self, mock_unlink):
        """Test template deletion."""
        
        # Inject a template
        self.manager.template_cache = {
            "my_temp.json": {"name": "Delete Me", "template_type": "custom", "author": "Me"}
        }
        
        # Mock exists check
        with patch('pathlib.Path.exists', return_value=True):
            success, msg = self.manager.delete_template("Delete Me")
            
        self.assertTrue(success)
        self.assertNotIn("my_temp.json", self.manager.template_cache)
        mock_unlink.assert_called_once()

    def test_cannot_delete_default_template(self):
        """Ensure default templates are protected."""
        
        # "standard" is a default key
        success, msg = self.manager.delete_template("standard")
        
        self.assertFalse(success)
        self.assertIn("Cannot delete default", msg)

if __name__ == '__main__':
    unittest.main()
