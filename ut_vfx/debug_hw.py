import logging

import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from ut_vfx.core.system.hardware_info import HardwareInfo

logging.info("--- DEBUGGING HARDWARE INFO ---")
try:
    logging.info("Calling get_static_specs()...")
    static = HardwareInfo.get_static_specs()
    logging.info("STATIC SPECS RESULT:", static)
except Exception as e:
    logging.exception("STATIC SPECS ERROR:", e)
    import traceback
    traceback.print_exc()

logging.info("\nCalling get_dynamic_specs()...")
try:
    dynamic = HardwareInfo.get_dynamic_specs()
    logging.info("DYNAMIC SPECS RESULT:", dynamic)
except Exception as e:
    logging.exception("DYNAMIC SPECS ERROR:", e)
    import traceback
    traceback.print_exc()

logging.info("--- END DEBUG ---")
