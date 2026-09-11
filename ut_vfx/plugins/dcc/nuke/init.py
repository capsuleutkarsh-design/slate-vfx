import nuke
import os
import sys

# UTVFX Plugin Initialization
nuke.pluginAddPath('./')

# We inject PYTHONPATH in the dcc_launcher so that ut_vfx is importable here.
# This init runs when Nuke starts.
nuke.tprint("UT VFX Nuke Plugin Initialized.")
