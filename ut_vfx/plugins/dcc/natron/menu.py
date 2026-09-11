import os
import sqlite3
import glob
from pathlib import Path

# Only run if loaded in Natron GUI
try:
    import NatronEngine
    import NatronGui
except ImportError:
    pass

def get_shot_data():
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
    return folder_path.replace('\\', '/')

def load_plate(hotkey_type):
    try:
        app = natron.getGuiInstance(0)
    except NameError:
        return
        
    data = get_shot_data()
    if not data:
        app.message("UT VFX Error: Shot context not found in environment.")
        return
        
    scan_path = data.get("scan_path", "")
    if not scan_path:
        app.message("UT VFX Error: Scan path not defined in database.")
        return
        
    scan_path_obj = Path(scan_path)
    
    shot_root = None
    for parent in [scan_path_obj] + list(scan_path_obj.parents):
        if (parent / "01_Scan").exists():
            shot_root = parent
            break
            
    if not shot_root:
        app.message("UT VFX Error: Could not determine Shot Root directory.")
        return
        
    target_folder = ""
    node_name = ""
    
    if hotkey_type == "scan":
        target_folder = str(shot_root / "01_Scan")
        node_name = "Scan_Plate"
    elif hotkey_type == "prep":
        target_folder = str(shot_root / "05_Prep" / "Render")
        if not os.path.exists(target_folder):
            target_folder = str(shot_root / "05_Prep" / "Prep_out")
        node_name = "Prep_Plate"
    elif hotkey_type == "deage":
        target_folder = str(shot_root / "09_Deage" / "Output")
        node_name = "Deage_Plate"
    elif hotkey_type == "slapcomp":
        target_folder = str(shot_root / "08_Output" / "SLAPCOMP")
        node_name = "Slapcomp_Plate"
        
    seq_path = find_sequence(target_folder)
    
    if seq_path:
        read_node = app.createNode("fr.inria.built-in.Read")
        if read_node:
            read_node.getParam("filename").setValue(seq_path)
            read_node.setScriptName(node_name)
    else:
        app.message(f"UT VFX: No media found in {target_folder}")

def get_next_version(directory, base_name):
    version = 1
    if os.path.exists(directory):
        files = glob.glob(os.path.join(directory, f"{base_name}_v*.ntp"))
        for f in files:
            try:
                name = os.path.basename(f)
                v_str = name.split('_v')[-1].split('.ntp')[0]
                v_num = int(v_str)
                if v_num >= version:
                    version = v_num + 1
            except:
                pass
    return f"v{version:03d}"

def save_script_version(department):
    try:
        app = natron.getGuiInstance(0)
    except NameError:
        return
        
    data = get_shot_data()
    if not data:
        app.message("UT VFX Error: Shot context not found.")
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
        app.message("UT VFX Error: Could not determine Shot Root directory.")
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
    filename = f"{prefix}_{next_version}.ntp"
    full_path = str(script_dir / filename).replace('\\', '/')
    
    try:
        app.saveProjectAs(full_path)
        app.message(f"UT VFX: Successfully saved new version:\n{filename}")
    except Exception as e:
        app.message(f"UT VFX Error saving script: {e}")

def create_utvfx_menu():
    try:
        app = natron.getGuiInstance(0)
    except NameError:
        return
        
    menu = app.addMenu("UT VFX")
    
    scan_act = menu.addAction("Load Scan (Alt+1)")
    scan_act.triggered.connect(lambda: load_plate('scan'))
    
    prep_act = menu.addAction("Load Prep (Alt+2)")
    prep_act.triggered.connect(lambda: load_plate('prep'))
    
    deage_act = menu.addAction("Load Deage (Alt+3)")
    deage_act.triggered.connect(lambda: load_plate('deage'))
    
    slapcomp_act = menu.addAction("Load Slapcomp (Alt+4)")
    slapcomp_act.triggered.connect(lambda: load_plate('slapcomp'))
    
    menu.addSeparator()
    
    comp_save = menu.addAction("Save New Comp Version")
    comp_save.triggered.connect(lambda: save_script_version('Comp'))
    
    prep_save = menu.addAction("Save New Prep Version")
    prep_save.triggered.connect(lambda: save_script_version('Prep'))
    
    deage_save = menu.addAction("Save New Deage Version")
    deage_save.triggered.connect(lambda: save_script_version('Deage'))

# Call menu creation
if 'natron' in globals():
    create_utvfx_menu()
