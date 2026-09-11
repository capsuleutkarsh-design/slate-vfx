
import os
import re
from pathlib import Path

ROOT = Path("ut_vfx/core")
DOMAIN_DIR = ROOT / "domain"
INFRA_DIR = ROOT / "infra"

def fix_file(filepath):
    try:
        content = filepath.read_text(encoding="utf-8")
        original_content = content
        
        lines = content.splitlines()
        new_lines = []
        
        for line in lines:
            # Regex: matches 'from .utils', 'from ..utils', 'from ...utils' etc.
            # Convert to 'from ut_vfx.utils'
            line = re.sub(r'from \.+utils', 'from ut_vfx.utils', line)
            
            # Regex: matches 'from .core' etc (if any exist)
            line = re.sub(r'from \.+core', 'from ut_vfx.core', line)
            
            new_lines.append(line)
        
        new_content = "\n".join(new_lines)
        if new_content != original_content:
            print(f"Patched deep imports in {filepath.name}")
            filepath.write_text(new_content, encoding="utf-8")
            
    except Exception as e:
        print(f"Error checking {filepath}: {e}")

print("--- Fixing Recursive Deep Imports V2 ---")

for root, dirs, files in os.walk(DOMAIN_DIR):
    for f in files:
        if f.endswith(".py"): fix_file(Path(root) / f)

for root, dirs, files in os.walk(INFRA_DIR):
    for f in files:
        if f.endswith(".py"): fix_file(Path(root) / f)

print("--- Done ---")
