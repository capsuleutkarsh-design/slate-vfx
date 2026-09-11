# Digital Content Creation (DCC) Integrations

UT_VFX seamlessly integrates with industry-standard DCC tools (Foundry Nuke, Blender) and provides an embedded timeline workflow using Olive Video Editor 0.2. This guide provides a deep dive into how these tools interface with the UT_VFX core database and environment.

> [!NOTE]
> All DCC integrations rely on environment variables injected by the `DCCLauncher` (`dcc_launcher.launch()`). Specifically, `UTVFX_SHOT_ID` and `UTVFX_DB_PATH` are passed to the child processes so they can directly query the SQLite database.

---

## 1. Olive Video Editor Integration (Embedded Timeline)

UT_VFX features an embedded portal for Olive 0.2, allowing production supervisors to generate cut lineups dynamically based on approved shots.

### Embedding Mechanism (Windows API)

Olive is not just launched; it is physically embedded into the PyQt interface:
1. **Launch**: `LineupEditorMode.launch_olive()` spawns `olive-editor.exe`.
2. **Window Capture**: The `find_olive_window()` function polls Windows using `user32.EnumWindows`, matching the spawned process PID to find Olive's HWND.
3. **Reparenting**: Using `user32.SetParent(hwnd, container_hwnd)`, the Olive window is moved inside the `olive_container` QFrame.
4. **Style Stripping**: The window borders and title bar are stripped using `user32.SetWindowLongW` (`~WS_CAPTION & ~WS_THICKFRAME | WS_CHILD`).
5. **DPI Scaling**: The embedded window automatically resizes based on the `devicePixelRatio()` of the PySide6 container using `user32.MoveWindow`.

### The `.ovexml` Generator (Olive Bridge)

Instead of relying on EDLs or XMLs that require parsing, UT_VFX generates raw Olive 0.2 `.ovexml` project files natively using `OliveBridge`. 

Olive 0.2 uses a node-based architecture internally. `OliveBridge` constructs the required node-graph structure:
- **Root `org.olivevideoeditor.Olive.folder`**: The main project bin.
- **Sequence `org.olivevideoeditor.Olive.sequence`**: The master timeline.
- **Track `org.olivevideoeditor.Olive.track`**: Tracks for "Scans" (V1) and "Renders" (V2).

For every approved shot, it creates a media chain and connects it to the Track:
1. `Footage` Node: Points to the physical file (`file_in`).
2. `Transform` Node: Connected to Footage.
3. `Clip` Node: Connected to Transform. Controls the `timeline_in`, `media_in`, and `length_in` using rational seconds (e.g., `1001/24000`).

> [!TIP]
> `OliveBridge` intelligently searches for lightweight MP4/MOV proxies in sibling folders (e.g., `/proxy/`) to ensure the generated timeline plays back smoothly without caching massive EXR sequences.

---

## 2. Foundry Nuke Integration

The Nuke integration runs on startup by mapping `UTVFX_NUKE_PATH` in the system environment to point to `ut_vfx/plugins/dcc/nuke/`.

### Initialization
When Nuke launches, `init.py` registers the plugin path (`nuke.pluginAddPath('./')`), and `menu.py` loads the custom `UT VFX` menu into the Nodes toolbar.

### Database Connection
Instead of requiring a REST server, Nuke's Python environment connects directly to the UT_VFX SQLite database via the `get_shot_data()` function using the injected `UTVFX_DB_PATH` and `UTVFX_SHOT_ID` environment variables.

### Key Tools & Hotkeys

- **Plate Ingestion (Alt+1 to Alt+4)**:
  `load_plate(hotkey_type)` automatically traverses the shot folder structure to find the highest-resolution image sequence (EXR, DPX, PNG) and creates a native Nuke `Read` node pre-populated with the sequence path.
  - **Alt+1**: Loads Scan Plates (`01_Scan`)
  - **Alt+2**: Loads Prep Plates (`05_Prep/Render`)
  - **Alt+3**: Loads Deage Plates (`09_Deage/Output`)
  - **Alt+4**: Loads Slapcomp Plates (`08_Output/SLAPCOMP`)

- **Strict Version Control Save**:
  `save_script_version(department)` forces scripts to be saved in a locked naming convention (`ShotName_dept_vXXX.nk`). It scans the destination directory (e.g., `07_Comp/Script`), calculates the highest `vXXX` string, increments it, and executes `nuke.scriptSaveAs()`.

---

## 3. Blender Integration

Similar to Nuke, the Blender integration maps the environment variable `UTVFX_BLENDER_PATH` and executes `ut_vfx_startup.py` when Blender launches.

### Key Tools

The integration registers custom UI panels in the `VIEW_3D` viewport under the **UT VFX** tab.

- **Load Scan Plate (`UTVFX_OT_LoadPlate`)**:
  Connects to the SQLite database, locates the `01_Scan` directory, and loads the first frame of the image sequence into Blender's `bpy.data.movieclips`. This allows artists to immediately use the scan as a background image in the camera for tracking or layout without manual folder traversal.

- **Save CG Version (`UTVFX_OT_SaveVersion`)**:
  Provides strict version control for `.blend` files. It automatically iterates the version number (e.g., `ShotName_cg_v001.blend`) and saves it to `06_CG/Script`.
  
  > [!IMPORTANT]
  > When saving a new version, this tool also automatically configures Blender's output node:
  > `bpy.context.scene.render.filepath = render_dir / f"{prefix}_{next_version}_"`
  > This guarantees that artists never accidentally overwrite previous render frames.
