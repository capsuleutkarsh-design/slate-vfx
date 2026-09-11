
import shutil
import os
from pathlib import Path

CORE = Path("ut_vfx/core")
INFRA = CORE / "infra"
DOMAIN = CORE / "domain"

INFRA.mkdir(exist_ok=True)
DOMAIN.mkdir(exist_ok=True)

# Define Moves
infra_files = [
    "database_manager.py", "config_manager.py", "global_config.py",
    "network_manager.py", "server_hub.py", "logging_config.py",
    "logging_utils.py", "central_logger.py", "audit_logger.py",
    "telemetry.py", "update_manager.py", "performance_monitor.py",
    "performance_config.py", "idle_monitor.py", "theme_manager.py",
    "file_operations.py"
]

domain_files = [
    "user_manager.py", "library_manager.py", "template_manager.py",
    "metadata_engine.py", "proxy_manager.py", "asset_ingestor.py",
    "central_attendance.py", "attendance_manager.py",
    "live_reporter.py"
]

domain_dirs = ["ingest", "workers"]

def move_file(filename, dest):
    src = CORE / filename
    dst = dest / filename
    if src.exists():
        print(f"Moving {src} -> {dst}")
        shutil.move(str(src), str(dst))
    else:
        print(f"Skipping {src} (Not found)")

def move_dir(dirname, dest):
    src = CORE / dirname
    dst = dest / dirname
    if src.exists():
        print(f"Moving Dir {src} -> {dst}")
        shutil.move(str(src), str(dst))
    else:
        print(f"Skipping Dir {src} (Not found)")

print("--- Starting Migration ---")
for f in infra_files: move_file(f, INFRA)
for f in domain_files: move_file(f, DOMAIN)
for d in domain_dirs: move_dir(d, DOMAIN)
print("--- Migration Complete ---")
