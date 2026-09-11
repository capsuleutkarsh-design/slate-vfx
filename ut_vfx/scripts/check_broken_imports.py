import logging

import os

def check_imports(root_dir):
    logging.info(f"Scanning {root_dir}...")
    
    target_string = "ut_vfx.core.utils"
    relative_target = ".core.utils" # might appear as relative import
    
    found = False
    
    for dirpath, dirnames, filenames in os.walk(root_dir):
        for filename in filenames:
            if filename.endswith(".py"):
                filepath = os.path.join(dirpath, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                        for i, line in enumerate(lines):
                            if target_string in line or relative_target in line:
                                logging.info(f"Found in {filepath}:{i+1}")
                                logging.info(f"  Line: {line.strip()}")
                                found = True
                except Exception as e:
                    logging.info(f"Could not read {filepath}: {e}")

    if not found:
        logging.info("Target string not found.")

if __name__ == "__main__":
    check_imports(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
