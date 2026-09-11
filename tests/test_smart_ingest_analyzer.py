"""
Smart Ingest Analyzer Unit Tests.

This logic is the brain of the 'Available Mode'. It tests:
1. Alias Matching: Can we identify a 'Plate' or 'Roto' based on filename keywords?
2. Extension Handing: Do we correctly fallback to extensions if names are ambiguous?
3. Priority Resolution: If a file matches two rules, does the higher priority one win?
"""

import unittest
from pathlib import Path
import sys
import os

# Ensure the project root is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ut_vfx.core.domain.ingest.analyzer import SmartIngestAnalyzer

class TestSmartIngestAnalyzer(unittest.TestCase):
    def setUp(self):
        # Mock Rules
        self.rules = {
            "Plate": {
                "aliases": ["plate", "bg", "background"],
                "extensions": [".exr", ".mov", ".mp4"],
                "priority": 10
            },
            "Roto": {
                "aliases": ["roto", "mask", "alpha", "matte"],
                "extensions": [".exr", ".png"],
                "priority": 50
            },
            "Prep": {
                "aliases": ["prep", "clean", "paint", "remove"],
                "extensions": [".exr", ".nk"],
                "priority": 20
            }
        }
        self.analyzer = SmartIngestAnalyzer(self.rules)

    def test_exact_name_match(self):
        """Test strong match purely based on filename."""
        # 0.9 confidence expected for direct name match
        cat, score, reason = self.analyzer.analyze_item(Path("shot010_roto_v01.exr"))
        self.assertEqual(cat, "Roto", f"Expected Roto, got {cat}")
        self.assertGreaterEqual(score, 0.9)
        self.assertIn("Matched Folder/File Name Alias", reason)

    def test_parent_context_match(self):
        """Test match based on parent folder name."""
        # 0.8 confidence expected for parent match
        cat, score, reason = self.analyzer.analyze_item(Path("incoming/roto/shot010_v01.exr"))
        self.assertEqual(cat, "Roto", f"Expected Roto, got {cat}")
        self.assertTrue(0.8 <= score < 0.9, f"Score {score} should be around 0.8")
        self.assertIn("Matched Parent Folder Alias", reason)

    def test_priority_resolution(self):
        """Test that higher priority rules override lower priority ones in the same name."""
        # "clean_plate" contains "clean" (Prep/20) and "plate" (Plate/10).
        # Prep should win because 20 > 10.
        cat, score, _ = self.analyzer.analyze_item(Path("shot010_clean_plate_v01.exr"))
        self.assertEqual(cat, "Prep", f"Expected Prep (Prio 20) over Plate (Prio 10). Got {cat}")

    def test_extension_fallback(self):
        """Test match based only on extension when name doesn't match."""
        # weak match (0.5) on extension if name doesn't match
        # .mov is in Plate
        
        # We must use a mock because analyzer checks path.is_file() which requires existence
        from unittest.mock import MagicMock
        mock_path = MagicMock(spec=Path)
        mock_path.name = "random_shot_v01.mov"
        mock_path.parent.name = "random_folder"
        mock_path.suffix = ".mov"
        mock_path.is_file.return_value = True

        cat, score, reason = self.analyzer.analyze_item(mock_path)
        self.assertEqual(cat, "Plate")
        self.assertEqual(score, 0.5)
        self.assertIn("Matched Extension", reason)

    def test_no_match(self):
        """Test file that matches nothing."""
        cat, score, _ = self.analyzer.analyze_item(Path("unknown_file.txt"))
        self.assertIsNone(cat)
        self.assertEqual(score, 0.0)

    def test_ambiguous_name_priority(self):
        """Test ambiguous naming where substring matching matters."""
        # "precomp" contains "comp" -> potentially dangerous if "comp" is an alias.
        # But here we test our specific rules.
        # "background_plate" -> "background" (Plate) and "plate" (Plate). Same cat.
        cat, score, _ = self.analyzer.analyze_item(Path("bg_plate_v1.exr"))
        self.assertEqual(cat, "Plate")

if __name__ == '__main__':
    unittest.main()
