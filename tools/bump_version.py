"""
AUTOMATIC VERSION BUMPER
========================
Updates the version number in all critical files:
1. ut_vfx/__init__.py (The Source of Truth)
2. setup_ut_vfx.iss (The Installer)
3. pyproject.toml (Packaging metadata)
"""

import re
from pathlib import Path

# Files to update
INIT_FILE = Path("ut_vfx/__init__.py") # Assumes CWD is Project Root
ISS_FILE = Path("deployment/setup_ut_vfx.iss")
PYPROJECT_FILE = Path("pyproject.toml")

def get_current_version():
    """Read version from __init__.py"""
    with open(INIT_FILE, 'r') as f:
        content = f.read()
        match = re.search(r'__version__\s*=\s*"([^"]+)"', content)
        if match:
            return match.group(1)
    return "0.0.0"

def update_file(path, pattern, replacement):
    """Regex replace content in a file."""
    if not path.exists():
        print(f"❌ File not found: {path}")
        return

    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    new_content = re.sub(pattern, replacement, content)
    
    if content != new_content:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"[UPDATED] {path}")
    else:
        print(f"[NO CHANGES] {path} (Pattern match failed?)")

def main():
    print("--- UT_VFX VERSION MANAGER ---")
    current_ver = get_current_version()
    print(f"Current Source Version: {current_ver}")
    
    # Check .iss version too
    iss_ver = "Unknown"
    if ISS_FILE.exists():
        with open(ISS_FILE, 'r') as f:
            match = re.search(r'#define MyAppVersion "([^"]+)"', f.read())
            if match: iss_ver = match.group(1)
    print(f"Current Installer Version: {iss_ver}")
    
    if current_ver != iss_ver:
        print("WARNING: Version mismatch detected!")
    
    import sys
    if len(sys.argv) > 1:
        new_ver = sys.argv[1]
    else:
        new_ver = input(f"Enter New Version (Current: {current_ver}): ").strip()
        
    if not new_ver:
        print("Cancelled.")
        return

    print(f"\nBumping version to {new_ver}...")

    # 1. Update Python Source (__init__.py)
    # Pattern: __version__ = "..."
    update_file(INIT_FILE, r'__version__\s*=\s*"[^"]+"', f'__version__ = "{new_ver}"')

    # 2. Update Installer Config (.iss)
    # Pattern: #define MyAppVersion "..."
    update_file(ISS_FILE, r'#define MyAppVersion "[^"]+"', f'#define MyAppVersion "{new_ver}"')

    # 3. Update PyProject (.toml)
    # Pattern: version = "..."
    if PYPROJECT_FILE.exists():
        update_file(PYPROJECT_FILE, r'version\s*=\s*"[^"]+"', f'version = "{new_ver}"')

    print("\n[DONE] Version bump complete!")
    print(f"Synced {INIT_FILE}, {ISS_FILE}, and pyproject.toml to v{new_ver}")

if __name__ == "__main__":
    main()
