import nuke
import os
import sys

# Slate Plugin Initialization
nuke.pluginAddPath('./')

# We inject PYTHONPATH in the dcc_launcher so that slate is importable here.
# This init runs when Nuke starts.
nuke.tprint("Slate Nuke Plugin Initialized.")
