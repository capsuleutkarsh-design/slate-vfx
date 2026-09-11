import sys

# Add project root to path
START_DIR = r"c:/Users/capadmin/Documents/Studio_soft_2/V0040"
sys.path.append(START_DIR)

print(f"Checking imports from {START_DIR}...")

components = [
    ("ut_vfx.gui.tabs.stock_browser.ui.inspector", "StockInspectorPanel"),
    ("ut_vfx.gui.tabs.stock_browser.ui.sidebar", "StockSidebar"),
    ("ut_vfx.gui.tabs.stock_browser.ui.gallery", "StockGallery"),
    ("ut_vfx.gui.tabs.stock_browser.ingest_controller", "StockIngestController"),
    ("ut_vfx.gui.tabs.stock_browser_tab", "StockBrowserTab")
]

failed = False
for module, cls in components:
    try:
        mod = __import__(module, fromlist=[cls])
        obj = getattr(mod, cls)
        print(f"[OK] {cls} imported successfully.")
    except Exception as e:
        print(f"[FAIL] {cls} failed to import from {module}")
        print(f"Error: {e}")
        # traceback.print_exc()
        failed = True

if failed:
    sys.exit(1)
else:
    print("All components verified successfully.")
    sys.exit(0)
