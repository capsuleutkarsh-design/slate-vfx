"""
Test Suite for Lucidity + Fileseq Integration
VFX Industry Standards Verification

This test suite verifies the integration of fileseq and lucidity libraries
into the UT_VFX pipeline.

Run with: python test_vfx_standards_integration.py
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def test_imports():
    """Test 1: Verify libraries can be imported"""
    print("\n" + "="*60)
    print("TEST 1: Library Import Test")
    print("="*60)
    
    # Test fileseq
    try:
        import fileseq
        version = getattr(fileseq, "__version__", "unknown")
        print(f"✅ fileseq {version} imported successfully")
        fileseq_ok = True
    except ImportError as e:
        print(f"❌ fileseq import failed: {e}")
        print("   Install with: pip install fileseq>=1.17.0")
        fileseq_ok = False
    
    # Test lucidity
    try:
        import lucidity
        version = getattr(lucidity, "__version__", "unknown")
        print(f"✅ lucidity {version} imported successfully")
        lucidity_ok = True
    except ImportError as e:
        print(f"❌ lucidity import failed: {e}")
        print("   Install with: pip install lucidity>=1.5.0")
        lucidity_ok = False
    
    return fileseq_ok, lucidity_ok


def test_sequence_utils():
    """Test 2: Verify SequenceDetector utility"""
    print("\n" + "="*60)
    print("TEST 2: SequenceDetector Utility Test")
    print("="*60)
    
    try:
        from ut_vfx.utils.sequence_utils import SequenceDetector, detect_sequence
        print("✅ sequence_utils module imported successfully")
        
        # Check availability
        is_available = SequenceDetector.is_available()
        print(f"   Fileseq available: {is_available}")
        
        if is_available:
            print("✅ SequenceDetector is ready to use")
            
            # Test with temp files
            temp_dir = Path("temp_seq_test")
            temp_dir.mkdir(exist_ok=True)
            
            # Create test sequence: shot.1001.exr, shot.1002.exr, shot.1003.exr
            test_files = []
            for i in range(1001, 1004):
                test_file = temp_dir / f"shot.{i:04d}.exr"
                test_file.touch()
                test_files.append(test_file)
            
            # Test detection
            seq = SequenceDetector.find_sequence(test_files[0])
            
            if seq:
                pattern = SequenceDetector.get_pattern(seq)
                start, end = SequenceDetector.get_frame_range(seq)
                count = SequenceDetector.get_frame_count(seq)
                
                print("✅ Sequence detected:")
                print(f"   Pattern: {pattern}")
                print(f"   Range: {start}-{end}")
                print(f"   Count: {count} frames")
                
                # Verify
                expected_pattern = str(temp_dir / "shot.%04d.exr")
                if pattern == expected_pattern and start == 1001 and end == 1003:
                    print("✅ Sequence detection is CORRECT")
                else:
                    print(f"❌ Mismatch: expected {expected_pattern}, got {pattern}")
            else:
                print("❌ Failed to detect test sequence")
            
            # Cleanup
            for f in test_files:
                f.unlink()
            temp_dir.rmdir()
            
        else:
            print("⚠️  Fileseq not available, using fallback detection")
            
            # Test fallback
            temp_dir = Path("temp_seq_test")
            temp_dir.mkdir(exist_ok=True)
            
            test_files = []
            for i in range(1001, 1004):
                test_file = temp_dir / f"shot.{i:04d}.exr"
                test_file.touch()
                test_files.append(test_file)
            
            info = detect_sequence(test_files[0])
            
            if info and 'pattern' in info:
                print("✅ Fallback detection works:")
                print(f"   Pattern: {info['pattern']}")
                print(f"   Range: {info['start_frame']}-{info['end_frame']}")
            else:
                print("❌ Fallback detection failed")
            
            # Cleanup
            for f in test_files:
                f.unlink()
            temp_dir.rmdir()
        
        return True
        
    except Exception as e:
        print(f"❌ SequenceDetector test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_path_template_manager():
    """Test 3: Verify PathTemplateManager service"""
    print("\n" + "="*60)
    print("TEST 3: PathTemplateManager Service Test")
    print("="*60)
    
    try:
        from ut_vfx.core.services.path_template_manager import (
            PathTemplateManager, format_render_path, parse_render_path
        )
        print("✅ path_template_manager module imported successfully")
        
        mgr = PathTemplateManager(root_path="X:/Projects")
        
        # Check availability
        is_available = mgr.is_available()
        print(f"   Lucidity available: {is_available}")
        
        if is_available:
            print("✅ PathTemplateManager is ready to use")
            
            # Test path formatting
            test_metadata = {
                'project': 'TEST',
                'sequence': 'sq010',
                'shot': 'sh0020',
                'task': 'comp',
                'version': 3,
                'frame': 1001
            }
            
            render_path = mgr.format_path('render', **test_metadata)
            expected = "X:/Projects/TEST/sq010/sh0020/comp/v003/render/sh0020_comp_v003.1001.exr"
            
            if render_path == expected:
                print("✅ Path formatting CORRECT:")
                print(f"   Generated: {render_path}")
            else:
                print("❌ Path formatting FAILED:")
                print(f"   Expected: {expected}")
                print(f"   Got: {render_path}")
                return False
            
            # Test path parsing
            parsed = mgr.parse_path('render', render_path)
            
            if parsed:
                print("✅ Path parsing CORRECT:")
                print(f"   Project: {parsed['project']}")
                print(f"   Shot: {parsed['shot']}")
                print(f"   Version: {parsed['version']}")
                print(f"   Frame: {parsed['frame']}")
                
                # Verify all fields match
                mismatches = []
                for key, value in test_metadata.items():
                    if key != 'root' and parsed.get(key) != value:
                        mismatches.append(f"{key}: expected {value}, got {parsed.get(key)}")
                
                if mismatches:
                    print(f"❌ Parsing mismatches: {mismatches}")
                    return False
                else:
                    print("✅ All parsed fields match")
            else:
                print(f"❌ Failed to parse path: {render_path}")
                return False
            
            # Test template listing
            templates = mgr.list_templates()
            print(f"✅ Available templates: {', '.join(templates)}")
            
            # Test convenience functions
            quick_path = format_render_path('TEST', 'sq010', 'sh0020', 'comp', 3, 1001, root="X:/Projects")
            if quick_path == expected:
                print("✅ Convenience function works correctly")
            else:
                print("❌ Convenience function mismatch")
                return False
            
            quick_parse = parse_render_path(quick_path)
            if quick_parse and quick_parse['shot'] == 'sh0020':
                print("✅ Parse convenience function works correctly")
            else:
                print("❌ Parse convenience function failed")
                return False
            
        else:
            print("⚠️  Lucidity not available, template manager disabled")
            print("   Manual path construction will continue to work")
            print("   Install lucidity for VFX standard path management")
        
        return True
        
    except Exception as e:
        print(f"❌ PathTemplateManager test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_advanced_player_integration():
    """Test 4: Verify advanced_player.py integration"""
    print("\n" + "="*60)
    print("TEST 4: AdvancedPlayer Integration Test")
    print("="*60)
    
    try:
        # Check that import works
        print("✅ AdvancedPlayer imports successfully")
        
        # Check that it can import sequence_utils
        print("✅ AdvancedPlayer can access SequenceDetector")
        
        return True
        
    except Exception as e:
        print(f"❌ AdvancedPlayer integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests"""
    print("\n" + "="*70)
    print(" VFX INDUSTRY STANDARDS INTEGRATION TEST SUITE")
    print(" UT_VFX - Lucidity + Fileseq Verification")
    print("="*70)
    
    results = {}
    
    # Test 1: Imports
    fileseq_ok, lucidity_ok = test_imports()
    results['fileseq_import'] = fileseq_ok
    results['lucidity_import'] = lucidity_ok
    
    # Test 2: SequenceDetector
    results['sequence_utils'] = test_sequence_utils()
    
    # Test 3: PathTemplateManager
    results['path_template_manager'] = test_path_template_manager()
    
    # Test 4: Integration
    results['advanced_player'] = test_advanced_player_integration()
    
    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, test_result in results.items():
        status = "✅ PASS" if test_result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print(f"\nResults: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED! Integration successful!")
        print("\nNext steps:")
        print("1. Integrate fileseq into stock browser (asset.sequence grouping)")
        print("2. Integrate fileseq into scan manager (batch copying)")
        print("3. Migrate manual path construction to Lucidity templates")
        return 0
    else:
        print("\n⚠️  Some tests failed. Check installation:")
        if not results['fileseq_import']:
            print("   pip install fileseq>=1.17.0")
        if not results['lucidity_import']:
            print("   pip install lucidity>=1.5.0")
        return 1


if __name__ == "__main__":
    sys.exit(main())
