
import os
import shutil
import glob
from pathlib import Path

def cleanup():
    root = Path.cwd()
    archive_dir = root / "debug_archive"
    archive_dir.mkdir(exist_ok=True)
    
    # 1. Move Debug Files
    patterns = [
        "debug_*.py",
        "verify_*.py",
        "add_methods.py",
        "temp_methods.py",
        "demo_pyblish.py",
        "test_progress.py",
        "test_telemetry.py",
        "debug_loader.py"
    ]
    
    mooved_count = 0
    for pat in patterns:
        for f in root.glob(pat):
            try:
                shutil.move(str(f), str(archive_dir / f.name))
                print(f"Moved: {f.name}")
                mooved_count += 1
            except Exception as e:
                print(f"Error moving {f.name}: {e}")
                
    # 2. Delete Temp Dirs
    temp_dirs = [
        "temp_debug_project",
        "temp_deep_debug",
        "temp_seq_test"
    ]
    
    deleted_count = 0
    for d in temp_dirs:
        d_path = root / d
        if d_path.exists() and d_path.is_dir():
            try:
                shutil.rmtree(d_path)
                print(f"Deleted: {d}")
                deleted_count += 1
            except Exception as e:
                print(f"Error deleting {d}: {e}")

    print(f"Cleanup Complete. Moved {mooved_count} files, Deleted {deleted_count} folders.")

if __name__ == "__main__":
    cleanup()
