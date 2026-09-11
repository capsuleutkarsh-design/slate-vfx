import os

log_path = r"%LOCALAPPDATA%\UTVFX\logs\latest.log"

if os.path.exists(log_path):
    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
        print(f"Total lines: {len(lines)}")
        print("======== LAST 50 LINES ========")
        for line in lines[-50:]:
            print(line.strip())
else:
    print(f"File not found: {log_path}")
