
try:
    import pandas as pd
    print("Pandas imported successfully.")
except ImportError as e:
    print(f"FAILED to import pandas: {e}")
except Exception as e:
    print(f"Error: {e}")
