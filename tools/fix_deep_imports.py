
import os
from pathlib import Path

ROOT = Path("ut_vfx/core")
DIRS = [ROOT / "domain", ROOT / "infra"]

def fix_file(filepath):
    try:
        content = filepath.read_text(encoding="utf-8")
        original_content = content
        
        # Replace 'from ..utils' with 'from ut_vfx.utils'
        # This handles the depth change caused by moving files deeper into core/X
        content = content.replace("from ..utils", "from ut_vfx.utils")
        
        # Replace 'from ..core' with 'from ut_vfx.core', just in case
        content = content.replace("from ..core", "from ut_vfx.core")
        
        if content != original_content:
            print(f"Patched deep imports in {filepath.name}")
            filepath.write_text(content, encoding="utf-8")
            
    except Exception as e:
        print(f"Error checking {filepath}: {e}")

print("--- Fixing Deep Imports ---")
for d in DIRS:
    if d.exists():
        for f in os.listdir(d):
            if f.endswith(".py"): fix_file(d / f)

print("--- Done ---")
