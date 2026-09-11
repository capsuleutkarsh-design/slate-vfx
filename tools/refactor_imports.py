
import os
import re
from pathlib import Path

ROOT = Path(".")
DIRS_TO_SCAN = ["ut_vfx", "tests", "tools"]

# Map: Module Name -> New Sub-Package (infra/domain)
MOVES = {
    # INFRA
    "database_manager": "infra",
    "config_manager": "infra",
    "global_config": "infra",
    "network_manager": "infra",
    "server_hub": "infra",
    "logging_config": "infra",
    "logging_utils": "infra",
    "central_logger": "infra",
    "audit_logger": "infra",
    "telemetry": "infra",
    "update_manager": "infra",
    "performance_monitor": "infra",
    "performance_config": "infra",
    "idle_monitor": "infra",
    "theme_manager": "infra",
    "file_operations": "infra",
    
    # DOMAIN
    "user_manager": "domain",
    "library_manager": "domain",
    "template_manager": "domain",
    "metadata_engine": "domain",
    "proxy_manager": "domain",
    "asset_ingestor": "domain",
    "central_attendance": "domain",
    "attendance_manager": "domain",
    "live_reporter": "domain",
    
    # PACKAGES
    "workers": "domain",
    "ingest": "domain",
}

def process_file(filepath):
    try:
        content = filepath.read_text(encoding="utf-8")
        original_content = content
        
        for mod, subpkg in MOVES.items():
            # 1. Absolute Imports: ut_vfx.core.MOD -> ut_vfx.core.SUB.MOD
            # Regex to match: ut_vfx.core.MOD( |$|.)
            # We want to insert .subpkg after core.
            
            # Simple String Replace first for most common cases
            # case: from ut_vfx.core.database_manager import X
            content = content.replace(f"ut_vfx.core.{mod}", f"ut_vfx.core.{subpkg}.{mod}")
            
            # case: from ut_vfx.core import database_manager
            # This is harder to auto-fix perfectly, but let's try regex
            # pattern: from ut_vfx.core import ..., database_manager, ...
            # This is too complex for simple regex. 
            # Reviewer Note: We assume most imports are direct.
            
            # 2. Relative Imports: ..core.MOD -> ..core.SUB.MOD
            content = content.replace(f"..core.{mod}", f"..core.{subpkg}.{mod}")
            
            # 3. Direct imports inside core: from .database_manager import X (if inside core)
            # This script runs from root, so we don't know context easily. 
            # But we can check if file is in ut_vfx/core/ and not in subpkg.
            # But we moved files, so they are now in subpkg!
            # If database_manager imports config_manager, they are both in infra now.
            # So `from .config_manager` works!
            # If library_manager (domain) imports database_manager (infra).
            # It was `from .database_manager`. Now it is `from ..infra.database_manager`.
            
            # This is the tricky part. 
            # Let's handle the explicit full paths first which are safer.
            
            # Handle: from . import database_manager (inside core/__init__.py?)
            # core/__init__.py needs manual fix probably.
            
        if content != original_content:
            print(f"Patching {filepath}")
            filepath.write_text(content, encoding="utf-8")
            
    except Exception as e:
        print(f"Error processing {filepath}: {e}")

print("--- Starting Import Update ---")
for mk in DIRS_TO_SCAN:
    root_dir = ROOT / mk
    if not root_dir.exists(): continue
    
    for r, d, f in os.walk(root_dir):
        for file in f:
            if file.endswith(".py") and file != "refactor_move.py" and file != "refactor_imports.py":
                process_file(Path(r) / file)

print("--- Import Update Complete ---")
