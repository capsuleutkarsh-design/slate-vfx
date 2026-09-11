"""
UT VFX Studio - Standalone Entry Point.
Launches the specialized VFX Production suite (Home, Folder Creator, Scan Ingest,
CAP Rename, Stock Viewer, Shot Review, VFX Dashboard, Scheduling, Bidding).
"""

import sys
from pathlib import Path

current_file = Path(__file__).resolve()
package_dir = current_file.parent
root_dir = package_dir.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from ut_vfx.gatekeeper_main import ApplicationEntry
from ut_vfx.utils.single_instance import SingleInstance

if __name__ == "__main__":
    if not SingleInstance("UTVFX_Process_vfx").check():
        sys.exit(0)

    entry = ApplicationEntry(app_mode="vfx")
    entry.run()
