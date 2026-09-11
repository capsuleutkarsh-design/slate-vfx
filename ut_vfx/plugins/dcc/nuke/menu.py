import nuke
import os
import sqlite3
from pathlib import Path
import glob

def get_shot_data():
    """Reads the shot data directly from the SQLite database."""
    shot_id = os.environ.get("UTVFX_SHOT_ID")
    db_path = os.environ.get("UTVFX_DB_PATH")
    
    if not shot_id or not db_path or not os.path.exists(db_path):
        return None
        
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # We need the shot details and also the project code to resolve full paths
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
        nuke.tprint(f"UT VFX DB Error: {e}")
        
    return None

def find_sequence(folder_path):
    """Finds the first valid image sequence in the folder."""
    if not os.path.exists(folder_path):
        return None
        
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            if file.lower().endswith(('.exr', '.png', '.mov', '.dpx', '.jpg')):
                # Nuke can read the directory or specific files.
                # Returning the folder is often enough, but let's try to return the sequence path.
                return root.replace('\\', '/')
    return folder_path.replace('\\', '/')

def load_plate(hotkey_type):
    """
    Creates a Read node pointing to the requested plate type.
    """
    data = get_shot_data()
    if not data:
        nuke.message("UT VFX Error: Shot context not found in environment.")
        return
        
    # The scan_path in DB is usually something like "01_Scan/EXR" or the absolute path.
    # We will derive the base shot folder from the scan_path.
    scan_path = data.get("scan_path", "")
    if not scan_path:
        nuke.message("UT VFX Error: Scan path not defined in database.")
        return
        
    # Assume scan_path points to the folder containing the plates.
    # The shot root is typically the parent of "01_Scan".
    scan_path_obj = Path(scan_path)
    
    # Try to find the shot root by walking up until we find a parent that has 01_Scan
    shot_root = None
    for parent in [scan_path_obj] + list(scan_path_obj.parents):
        if (parent / "01_Scan").exists():
            shot_root = parent
            break
            
    if not shot_root:
        nuke.message("UT VFX Error: Could not determine Shot Root directory.")
        return
        
    target_folder = ""
    node_name = ""
    
    if hotkey_type == "scan":
        # Alt+1
        target_folder = str(shot_root / "01_Scan")
        node_name = "Scan_Plate"
    elif hotkey_type == "prep":
        # Alt+2
        target_folder = str(shot_root / "05_Prep" / "Render")
        if not os.path.exists(target_folder):
            target_folder = str(shot_root / "05_Prep" / "Prep_out")
        node_name = "Prep_Plate"
    elif hotkey_type == "deage":
        # Alt+3
        target_folder = str(shot_root / "09_Deage" / "Output")
        node_name = "Deage_Plate"
    elif hotkey_type == "slapcomp":
        # Alt+4
        target_folder = str(shot_root / "08_Output" / "SLAPCOMP")
        node_name = "Slapcomp_Plate"
        
    seq_path = find_sequence(target_folder)
    
    if seq_path:
        read_node = nuke.createNode("Read")
        read_node["file"].fromUserText(seq_path)
        read_node["name"].setValue(node_name)
    else:
        nuke.message(f"UT VFX: No media found in {target_folder}")

def get_next_version(directory, base_name):
    """Finds the highest version in the directory and returns the next version string."""
    version = 1
    if os.path.exists(directory):
        files = glob.glob(os.path.join(directory, f"{base_name}_v*.nk"))
        for f in files:
            try:
                # Extract the vXXX part
                name = os.path.basename(f)
                v_str = name.split('_v')[-1].split('.nk')[0]
                v_num = int(v_str)
                if v_num >= version:
                    version = v_num + 1
            except:
                pass
    return f"v{version:03d}"

def save_script_version(department):
    """
    Saves the current Nuke script with strict version control.
    department can be 'Comp', 'Prep', or 'Deage'
    """
    data = get_shot_data()
    if not data:
        nuke.message("UT VFX Error: Shot context not found.")
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
        nuke.message("UT VFX Error: Could not determine Shot Root directory.")
        return
        
    if department == "Comp":
        script_dir = shot_root / "07_Comp" / "Script"
        prefix = f"{shot_name}_comp"
    elif department == "Prep":
        script_dir = shot_root / "05_Prep" / "Script"
        prefix = f"{shot_name}_prep"
    elif department == "Deage":
        script_dir = shot_root / "09_Deage" / "Script"
        prefix = f"{shot_name}_deage"
    else:
        return
        
    script_dir.mkdir(parents=True, exist_ok=True)
    
    next_version = get_next_version(str(script_dir), prefix)
    filename = f"{prefix}_{next_version}.nk"
    full_path = str(script_dir / filename).replace('\\', '/')
    
    try:
        nuke.scriptSaveAs(full_path)
        nuke.message(f"UT VFX: Successfully saved new version:\n{filename}")
    except Exception as e:
        nuke.message(f"UT VFX Error saving script: {e}")

# --- Setup Menu and Hotkeys ---

toolbar = nuke.menu("Nodes")
utvfx_menu = toolbar.addMenu("UT VFX", icon="CustomIcon.png") # Uses default icon if missing

utvfx_menu.addCommand("Load Scan (Alt+1)", "load_plate('scan')", "Alt+1")
utvfx_menu.addCommand("Load Prep (Alt+2)", "load_plate('prep')", "Alt+2")
utvfx_menu.addCommand("Load Deage (Alt+3)", "load_plate('deage')", "Alt+3")
utvfx_menu.addCommand("Load Slapcomp (Alt+4)", "load_plate('slapcomp')", "Alt+4")

utvfx_menu.addSeparator()

utvfx_menu.addCommand("Save New Comp Version", "save_script_version('Comp')")
utvfx_menu.addCommand("Save New Prep Version", "save_script_version('Prep')")
utvfx_menu.addCommand("Save New Deage Version", "save_script_version('Deage')")
