import logging
# -*- coding: utf-8 -*-
"""
Attendance Data Cleanup Script
Removes duplicate entries from existing attendance data.
Run ONCE before deploying attendance fixes.
"""
import json
from pathlib import Path
import sys
import shutil
from datetime import datetime

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))
from core.infra.server_hub import ServerHub

def backup_attendance_data():
    """Create backup before modifying"""
    hub = ServerHub()
    att_dir = hub.server_root / "Attendance"
    backup_dir = hub.server_root / "Attendance_Backup" / datetime.now().strftime("%Y%m%d_%H%M%S")
    
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy all JSON files
    for json_file in att_dir.glob("*.json"):
        shutil.copy2(json_file, backup_dir / json_file.name)
    
    logging.info(f"[OK] Backup created: {backup_dir}")
    return backup_dir

def remove_duplicates(file_path):
    """Clean duplicates from a single attendance file"""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    fixed_count = 0
    
    for user_id, days in data.items():
        for day_key, entries in list(days.items()):
            # Check if multiple entries exist (shouldn't with dict structure, but checking)
            if isinstance(entries, list):
                # Multiple entries - keep only last one
                logging.warning(f"[WARN] Found list for {user_id} day {day_key}: {entries}")
                data[user_id][day_key] = entries[-1]
                fixed_count += 1
            elif isinstance(entries, dict):
                # Should be dict with "in", "out", "pc", etc.
                # No duplicates in dict structure, but validate
                pass
    
    # Save cleaned data
    if fixed_count > 0:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logging.info(f"[OK] Fixed {fixed_count} duplicates in {file_path.name}")
    
    return fixed_count

def main():
    logging.info("=" * 60)
    logging.info("ATTENDANCE DATA CLEANUP SCRIPT")
    logging.info("=" * 60)
    logging.info()
    
    # 1. Backup
    logging.info("Step 1: Creating backup...")
    backup_dir = backup_attendance_data()
    logging.info()
    
    # 2. Find attendance files
    logging.info("Step 2: Finding attendance files...")
    hub = ServerHub()
    att_dir = hub.server_root / "Attendance"
    
    if not att_dir.exists():
        logging.info(f"[ERR] Attendance directory not found: {att_dir}")
        return
    
    json_files = list(att_dir.glob("*.json"))
    logging.info(f"Found {len(json_files)} attendance files")
    logging.info()
    
    # 3. Clean each file
    logging.info("Step 3: Cleaning duplicates...")
    total_fixed = 0
    
    for json_file in json_files:
        fixed = remove_duplicates(json_file)
        total_fixed += fixed
    
    logging.info()
    logging.info("=" * 60)
    logging.info("[OK] CLEANUP COMPLETE!")
    logging.info(f"   Total duplicates fixed: {total_fixed}")
    logging.info(f"   Backup location: {backup_dir}")
    logging.info("=" * 60)
    
    if total_fixed == 0:
        logging.info()
        logging.info("[INFO] No duplicates found. This is normal if using dict structure.")
        logging.info("   The current data structure prevents list-based duplicates.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.exception(f"[ERR] ERROR: {e}")
        import traceback
        traceback.print_exc()
