import sys
import json
import logging
from dataclasses import asdict
from pathlib import Path

# Ensure we can import from project root (works from any launch directory)
current_path = Path(__file__).resolve()
root_dir = next((p for p in current_path.parents if (p / "ut_vfx").exists()), current_path.parents[5])
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from ut_vfx.core.infra.database_manager import database_manager
from ut_vfx.gui.tabs.vfx_dashboard_pro.core.project_manager import ProjectManager
from ut_vfx.gui.tabs.vfx_dashboard_pro.core.excel_handler import ExcelHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Migration")

def migrate():
    logging.info("Starting Migration: Excel -> Database")
    
    pm = ProjectManager()
    projects = pm.get_all_projects()
    
    if not projects:
        logging.info("No projects found in projects.json")
        return

    success_count = 0
    fail_count = 0

    for proj in projects:
        logging.info(f"\nProcessing Project: {proj.name} ({proj.code})")
        
        # 1. Migrate Project Config
        try:
            # Serialize ProjectConfig
            # ProjectConfig is a dataclass, so asdict works
            config_dict = asdict(proj)
            config_json = json.dumps(config_dict)
            # save_tracking_project is fire-and-forget in current DB manager.
            database_manager.save_tracking_project(proj.code, proj.name, config_json)
            logging.info("  [OK] Saved Config")
                
        except Exception as e:
            logging.exception(f"  [ERROR] Config serialization failed: {e}")
            fail_count += 1
            continue

        # 2. Migrate Shots
        excel_path = pm.get_excel_path(proj.code)
        if not excel_path or not Path(excel_path).exists():
            logging.info(f"  [SKIP] No Excel file found at: {excel_path}")
            continue
            
        logging.info(f"  Reading Excel: {excel_path}")
        handler = ExcelHandler(excel_path, proj)
        shots = handler.read_shots()
        
        if not shots:
            logging.warning("  [WARN] No shots found (or file empty).")
            continue
            
        logging.info(f"  Found {len(shots)} shots. Saving to DB...")
        
        # Prepare batch data
        batch_data = []
        for shot in shots:
            try:
                full_dict = asdict(shot)
                # Cleanup internal fields if any
                if "_row_idx" in full_dict: del full_dict["_row_idx"]
                if "_modified" in full_dict: del full_dict["_modified"]
                
                json_str = json.dumps(full_dict)
                batch_data.append((shot.shot_name, shot.status, shot.priority, json_str))
            except Exception as e:
                logging.exception(f"  [ERROR] Output serialization failed for {shot.shot_name}: {e}")
        
        if database_manager.save_tracking_shots(proj.code, batch_data):
            logging.info(f"  [OK] Saved {len(batch_data)} shots to DB.")
            success_count += 1
        else:
            logging.error("  [FAIL] Database write failed for shots.")
            fail_count += 1

    logging.info("\n" + "="*30)
    logging.error(f"Migration Complete. Success: {success_count}, Failed: {fail_count}")

if __name__ == "__main__":
    migrate()
