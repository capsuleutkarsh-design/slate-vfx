import bpy
import os
import sqlite3
import glob
from pathlib import Path

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

class UTVFX_OT_LoadPlate(bpy.types.Operator):
    """Load the Scan Plate as a Background Image in the Camera"""
    bl_idname = "utvfx.load_plate"
    bl_label = "Load Scan Plate"

    def execute(self, context):
        data = get_shot_data()
        if not data:
            self.report({'ERROR'}, "UT VFX Error: Shot context not found.")
            return {'CANCELLED'}

        scan_path = data.get("scan_path", "")
        if not scan_path:
            self.report({'ERROR'}, "UT VFX Error: Scan path not defined.")
            return {'CANCELLED'}

        scan_path_obj = Path(scan_path)
        shot_root = None
        for parent in [scan_path_obj] + list(scan_path_obj.parents):
            if (parent / "01_Scan").exists():
                shot_root = parent
                break

        if not shot_root:
            self.report({'ERROR'}, "Could not determine Shot Root directory.")
            return {'CANCELLED'}

        target_folder = str(shot_root / "01_Scan")
        
        # Load image sequence (first found)
        # Blender's API for loading a sequence as a background image can be complex.
        # We will load the first frame of the sequence into a movie clip for the compositor/camera.
        try:
            for root, dirs, files in os.walk(target_folder):
                images = [f for f in files if f.lower().endswith(('.exr', '.png', '.jpg', '.mov'))]
                if images:
                    images.sort()
                    first_image_path = os.path.join(root, images[0])
                    
                    clip = bpy.data.movieclips.load(first_image_path)
                    self.report({'INFO'}, f"Loaded Plate: {images[0]}")
                    return {'FINISHED'}
                    
            self.report({'WARNING'}, f"No media found in {target_folder}")
        except Exception as e:
            self.report({'ERROR'}, f"Load Error: {str(e)}")
            
        return {'CANCELLED'}

class UTVFX_OT_SaveVersion(bpy.types.Operator):
    """Save strict version controlled script"""
    bl_idname = "utvfx.save_version"
    bl_label = "Save CG Version"

    def execute(self, context):
        data = get_shot_data()
        if not data:
            self.report({'ERROR'}, "UT VFX Error: Shot context not found.")
            return {'CANCELLED'}

        scan_path = data.get("scan_path", "")
        shot_name = data.get("shot_name", "Unknown_Shot")

        scan_path_obj = Path(scan_path)
        shot_root = None
        for parent in [scan_path_obj] + list(scan_path_obj.parents):
            if (parent / "01_Scan").exists():
                shot_root = parent
                break

        if not shot_root:
            self.report({'ERROR'}, "Could not determine Shot Root.")
            return {'CANCELLED'}

        script_dir = shot_root / "06_CG" / "Script"
        prefix = f"{shot_name}_cg"
        script_dir.mkdir(parents=True, exist_ok=True)

        # Get next version
        version = 1
        if os.path.exists(script_dir):
            files = glob.glob(os.path.join(script_dir, f"{prefix}_v*.blend"))
            for f in files:
                try:
                    name = os.path.basename(f)
                    v_str = name.split('_v')[-1].split('.blend')[0]
                    v_num = int(v_str)
                    if v_num >= version:
                        version = v_num + 1
                except:
                    pass
                    
        next_version = f"v{version:03d}"
        filename = f"{prefix}_{next_version}.blend"
        full_path = str(script_dir / filename).replace('\\', '/')

        try:
            bpy.ops.wm.save_as_mainfile(filepath=full_path)
            
            # Also auto-configure the render output path!
            render_dir = shot_root / "06_CG" / "Render" / next_version
            render_dir.mkdir(parents=True, exist_ok=True)
            bpy.context.scene.render.filepath = str(render_dir / f"{prefix}_{next_version}_")
            
            self.report({'INFO'}, f"Saved version: {filename}")
        except Exception as e:
            self.report({'ERROR'}, f"Save Error: {str(e)}")

        return {'FINISHED'}

class UTVFX_PT_Panel(bpy.types.Panel):
    """UT VFX integration panel"""
    bl_label = "UT VFX"
    bl_idname = "UTVFX_PT_Panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'UT VFX'

    def draw(self, context):
        layout = self.layout
        layout.operator("utvfx.load_plate")
        layout.operator("utvfx.save_version")

classes = (
    UTVFX_OT_LoadPlate,
    UTVFX_OT_SaveVersion,
    UTVFX_PT_Panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
    print("UT VFX Blender Plugin Initialized.")
