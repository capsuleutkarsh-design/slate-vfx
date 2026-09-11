import os

log_path = r"%LOCALAPPDATA%\UTVFX\logs\latest.log"
out_path = r".\log_dump.txt"

if os.path.exists(log_path):
    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
        with open(out_path, 'w', encoding='utf-8') as out:
            out.write(f"Total lines: {len(lines)}\n")
            out.write("======== LAST 500 LINES ========\n")
            out.writelines(lines[-500:])
else:
    with open(out_path, 'w', encoding='utf-8') as out:
        out.write(f"File not found: {log_path}")
