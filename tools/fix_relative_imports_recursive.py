
import os
import re
from pathlib import Path

ROOT = Path("ut_vfx/core")
DOMAIN_DIR = ROOT / "domain"
INFRA_DIR = ROOT / "infra"

# Map: Module -> Package
MODULE_LOCATIONS = {}
if INFRA_DIR.exists():
    for f in os.listdir(INFRA_DIR):
        if f.endswith(".py"): MODULE_LOCATIONS[f[:-3]] = "infra"
if DOMAIN_DIR.exists():
    for f in os.listdir(DOMAIN_DIR):
        if f.endswith(".py"): MODULE_LOCATIONS[f[:-3]] = "domain"

def fix_file(filepath, current_top_pkg):
    try:
        content = filepath.read_text(encoding="utf-8")
        original_content = content
        
        lines = content.splitlines()
        new_lines = []
        
        for line in lines:
            # Handle: from ..performance_monitor import ...
            # Regex for 'from ..MODULE import' or 'from .MODULE import'
            match = re.search(r'^from (\.+)(\w+) import (.*)', line)
            if match:
                dots = match.group(1)
                mod_name = match.group(2)
                imports = match.group(3)
                
                target_pkg = MODULE_LOCATIONS.get(mod_name)
                
                if target_pkg and target_pkg != current_top_pkg:
                    print(f"Fixing import in {filepath.name}: {mod_name} is in {target_pkg} (was {dots}{mod_name})")
                    # Use absolute import for safety in deep structure
                    new_line = f"from ut_vfx.core.{target_pkg}.{mod_name} import {imports}"
                    new_lines.append(new_line)
                    continue
            
            new_lines.append(line)
        
        new_content = "\n".join(new_lines)
        if new_content != original_content:
            filepath.write_text(new_content, encoding="utf-8")
            
    except Exception as e:
        print(f"Error checking {filepath}: {e}")

print("--- Fixing Recursive Imports ---")

for root, dirs, files in os.walk(DOMAIN_DIR):
    for f in files:
        if f.endswith(".py"):
            fix_file(Path(root) / f, "domain")

for root, dirs, files in os.walk(INFRA_DIR):
    for f in files:
        if f.endswith(".py"):
            fix_file(Path(root) / f, "infra")

print("--- Done ---")
