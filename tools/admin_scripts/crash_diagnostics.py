#!/usr/bin/env python3
"""
Diagnostic script to identify EXR loading code paths
"""
import os
import sys
import re
from pathlib import Path

# Search for all iio.imread calls
patterns = {
    'iio.imread': r'iio\.imread\(',
    'cv2.imread': r'cv2\.imread\(',
    'OIIO': r'from\s+OpenImageIO|import\s+OpenImageIO|HAS_OIIO',
    'imageio import': r'import\s+imageio|from\s+imageio'
}

def scan_file(filepath):
    """Scan a Python file for dangerous patterns"""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except:
        return []
    
    results = []
    for i, pattern_name in enumerate(patterns.keys()):
        pattern = patterns[pattern_name]
        for line_no, line in enumerate(lines, 1):
            if re.search(pattern, line):
                # Check if it's in a protected context
                results.append({
                    'file': filepath,
                    'line': line_no,
                    'pattern': pattern_name,
                    'code': line.strip()[:80]
                })
    return results

def main():
    root = Path(__file__).parent
    
    # Focus on shot_review code
    shot_review_path = root / 'ut_vfx' / 'gui' / 'tabs' / 'shot_review'
    
    print("=" * 80)
    print("SCANNING SHOT REVIEW TAB FOR DANGEROUS IMAGE LOADING PATTERNS")
    print("=" * 80)
    
    all_issues = []
    for py_file in shot_review_path.glob('**/*.py'):
        issues = scan_file(py_file)
        if issues:
            all_issues.extend(issues)
            print(f"\n📄 {py_file.relative_to(root)}:")
            for issue in issues:
                print(f"  Line {issue['line']}: {issue['pattern']}")
                print(f"    → {issue['code']}")
    
    print("\n" + "=" * 80)
    print(f"TOTAL ISSUES FOUND: {len(all_issues)}")
    print("=" * 80)
    
    # Check for EXR_LOADING_ENABLED status
    print("\n✓ VERIFICATION: EXR_LOADING_ENABLED status")
    image_loader = root / 'ut_vfx' / 'utils' / 'image_loader.py'
    if image_loader.exists():
        with open(image_loader, 'r') as f:
            content = f.read()
            if 'EXR_LOADING_ENABLED = False' in content:
                print("  ✓ EXR_LOADING_ENABLED = False (SAFE)")
            else:
                print("  ✗ EXR_LOADING_ENABLED not properly set")
    
    # Check tech_check_dialog for EXR protection
    print("\n✓ VERIFICATION: EXR protection in tech_check_dialog")
    tech_check = root / 'ut_vfx' / 'gui' / 'tabs' / 'shot_review' / 'tech_check_dialog.py'
    if tech_check.exists():
        with open(tech_check, 'r') as f:
            content = f.read()
            if ".exr" in content.lower() and "first_img.suffix.lower() != '.exr'" in content:
                print("  ✓ EXR check present in cv2.imread fallback")
            else:
                print("  ⚠ Check if EXR protection is in place")
    
    # Check live_tech_check for EXR protection  
    print("\n✓ VERIFICATION: EXR protection in live_tech_check")
    live_check = root / 'ut_vfx' / 'gui' / 'tabs' / 'shot_review' / 'live_tech_check.py'
    if live_check.exists():
        with open(live_check, 'r') as f:
            content = f.read()
            if "if ext == '.exr':" in content:
                print("  ✓ EXR early-exit present")
            else:
                print("  ✗ EXR protection missing")

if __name__ == '__main__':
    main()
