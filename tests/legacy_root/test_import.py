import traceback

with open("crash_log.txt", "w") as f:
    try:
        f.write("Import successful!\n")
    except Exception:
        f.write(traceback.format_exc())
