import sys

# Set up path to find ut_vfx
project_root = r"c:/Users/capadmin/Documents/Studio_soft_2/V0040"
sys.path.append(project_root)

try:
    print("Attempting to import StockBrowserTab...")
    print("SUCCESS: StockBrowserTab imported successfully.")
except Exception as e:
    print(f"FAILURE: Import failed: {e}")
    import traceback
    traceback.print_exc()
