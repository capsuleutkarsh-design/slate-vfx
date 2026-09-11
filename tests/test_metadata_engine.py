import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys
import os
import json

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ut_vfx.core.domain.metadata_engine import SmartMetadataManager

class TestMetadataEngine(unittest.TestCase):
    
    # --- 1. CATEGORY CLASSIFICATION TESTS ---
    def test_classify_category_regex(self):
        """Verify regex rules for file categorization."""
        
        test_cases = [
            # EXR files are aggressively caught as HDRI in current logic if they have 'exr' in name/ext?
            # Code: if re.search(r'(hdri|...|exr|hdr)', full_str) and suffix in ['.exr', '.hdr']
            # So ANY .exr file is HDRI? Yes.
            ("shot010_plate_v01.exr", "HDRI"),  
            
            # 1. Textures
            ("wood_diffuse.jpg", "Textures"),
            
            # 3. Stock
            ("campfire_flame.mp4", "Fire"),
            
            # 5. References (Must not be .exr to pass HDRI check)
            ("scan_ref_01.dpx", "References"),
            
            # 8. Fallback
            ("unknown_file.dat", "Unknown") 
        ]
        
        for fname, expected in test_cases:
            # Use 'Unknown' parent to avoid parent-name fallback triggering unintentionally
            path = Path(f"Unknown/{fname}") 
            result = SmartMetadataManager.classify_category(path)
            self.assertEqual(result, expected, f"Failed on {fname}: Got {result}")

    # --- 2. TECHNICAL METADATA (FFPROBE MOCK) ---
    @patch('ut_vfx.core.domain.metadata_engine.os.path.exists')
    @patch('ut_vfx.core.domain.metadata_engine.proxy_manager_meta')
    @patch('subprocess.run')
    def test_extract_tech_metadata_valid_json(self, mock_run, mock_meta, mock_exists):
        """Test metadata extraction when ffprobe returns valid JSON."""
        # Ensure regex checks pass
        mock_exists.return_value = True
        mock_meta.ffprobe_path = "ffprobe.exe"
        mock_meta.ffmpeg_path = "ffmpeg.exe"
        
        mock_output = {
            "format": {"duration": "120.5"},
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080, "r_frame_rate": "24/1"}]
        }
        
        mock_result = MagicMock()
        mock_result.stdout = json.dumps(mock_output)
        mock_run.return_value = mock_result
        
        meta = SmartMetadataManager.extract_tech_metadata(Path("test.mov"))
        
        self.assertEqual(meta['width'], 1920)
        self.assertEqual(meta['duration_sec'], 120.5)



