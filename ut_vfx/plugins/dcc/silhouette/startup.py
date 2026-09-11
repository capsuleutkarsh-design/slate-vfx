import os
import sys
import sqlite3
import glob
from pathlib import Path

try:
    import fx
except ImportError:
    # Not running inside Silhouette
    pass

def get_shot_data():
    """Reads the shot data directly from the SQLite database."""
    shot_id = os.environ.get("UTVFX_SHOT_ID")
    db_path = os.environ.get("UTVFX_DB_PATH")
    
    if not shot_id or not db_path or not os.path.exists(db_path):
        return None
        
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT shot_name, scan_path, render_path, project_code FROM shots WHERE id = ?", (shot_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "shot_name": row[0],
                "scan_path": row[1],
                "render_path": row[2],
                "project_code": row[3]
            }
    except Exception as e:
        print(f"UT VFX DB Error: {e}")
        
    return None

def find_sequence(folder_path):
    if not os.path.exists(folder_path):
        return None
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            if file.lower().endswith(('.exr', '.png', '.mov', '.dpx', '.jpg')):
                return root.replace('\\', '/')
    return None

class UTVFXLoadPlateAction(fx.Action):
    def __init__(self):
        super().__init__("UT VFX|Load Scan Plate")

    def execute(self):
        data = get_shot_data()
        if not data:
            fx.displayError("UT VFX Error", "Shot context not found in environment.")
            return

        scan_path = data.get("scan_path", "")
        if not scan_path:
            fx.displayError("UT VFX Error", "Scan path not defined in database.")
            return

        scan_path_obj = Path(scan_path)
        shot_root = None
        for parent in [scan_path_obj] + list(scan_path_obj.parents):
            if (parent / "01_Scan").exists():
                shot_root = parent
                break

        if not shot_root:
            fx.displayError("UT VFX Error", "Could not determine Shot Root directory.")
            return

        target_folder = str(shot_root / "01_Scan")
        seq_path = find_sequence(target_folder)

        if seq_path:
            try:
                # Create a new session and load the source
                session = fx.app.session()
                if not session:
                    session = fx.Session()
                    fx.app.setSession(session)
                
                source = fx.app.importSource(seq_path)
                if source:
                    fx.displayMessage("UT VFX", f"Loaded plate from: {seq_path}")
            except Exception as e:
                fx.displayError("UT VFX Load Error", str(e))
        else:
            fx.displayError("UT VFX", f"No media found in {target_folder}")

class UTVFXSaveVersionAction(fx.Action):
    def __init__(self):
        super().__init__("UT VFX|Save Roto Version")

    def execute(self):
        data = get_shot_data()
        if not data:
            fx.displayError("UT VFX Error", "Shot context not found.")
            return

        scan_path = data.get("scan_path", "")
        shot_name = data.get("shot_name", "Unknown_Shot")

        scan_path_obj = Path(scan_path)
        shot_root = None
        for parent in [scan_path_obj] + list(scan_path_obj.parents):
            if (parent / "01_Scan").exists():
                shot_root = parent
                break

        if not shot_root:
            fx.displayError("UT VFX Error", "Could not determine Shot Root directory.")
            return

        script_dir = shot_root / "04_Roto" / "Script"
        prefix = f"{shot_name}_roto"
        script_dir.mkdir(parents=True, exist_ok=True)

        # Get next version
        version = 1
        if os.path.exists(script_dir):
            files = glob.glob(os.path.join(script_dir, f"{prefix}_v*.sfx"))
            for f in files:
                try:
                    name = os.path.basename(f)
                    v_str = name.split('_v')[-1].split('.sfx')[0]
                    v_num = int(v_str)
                    if v_num >= version:
                        version = v_num + 1
                except:
                    pass
                    
        next_version = f"v{version:03d}"
        filename = f"{prefix}_{next_version}.sfx"
        full_path = str(script_dir / filename).replace('\\', '/')

        try:
            # Save the Silhouette session
            if fx.app.session():
                fx.app.saveSessionAs(full_path)
                fx.displayMessage("UT VFX", f"Successfully saved new version:\n{filename}")
            else:
                fx.displayError("UT VFX", "No active session to save.")
        except Exception as e:
            fx.displayError("UT VFX Save Error", str(e))

# Register actions when Silhouette starts
try:
    fx.addAction(UTVFXLoadPlateAction())
    fx.addAction(UTVFXSaveVersionAction())
    print("UT VFX Silhouette Plugin Initialized.")
except NameError:
    pass
