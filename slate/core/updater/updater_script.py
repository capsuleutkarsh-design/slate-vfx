import logging
import sys
import os
import time
import shutil
import tempfile
import zipfile
import traceback
import subprocess
import ctypes
from pathlib import Path

def log(msg):
    logging.info(f"[Updater] {msg}")
    # In a real scenario, write to a log file in Temp for debugging
    try:
        # C:\Temp was the fallback, and a default Windows install has no
        # such folder - so on a machine with no TEMP set the updater's log
        # went nowhere, which is the one moment a log is worth having.
        temp_dir = os.environ.get('TEMP') or tempfile.gettempdir()
        with open(Path(temp_dir) / "slate_update_log.txt", "a") as f:
            f.write(f"{msg}\n")
    except OSError as exc:
        logging.warning(f"[Updater] log write warning: {exc}")

def error_exit(msg):
    log(f"ERROR: {msg}")
    ctypes.windll.user32.MessageBoxW(0, f"Update Failed:\n{msg}", "Slate Updater", 0x10)
    sys.exit(1)

def warn_exit(msg):
    """Show warning and exit without treating it as a hard crash."""
    log(f"WARNING: {msg}")
    ctypes.windll.user32.MessageBoxW(0, msg, "Slate Updater", 0x30)
    sys.exit(0)

def _is_process_running(pid):
    """Return True when the target PID is still listed by tasklist."""
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "tasklist failed")

    output = (result.stdout or "").lower()
    return str(pid) in output and "no tasks are running" not in output

def kill_process(pid):
    """Wait for process to stop. Force-kill if needed. Returns True if stopped."""
    log(f"Waiting for PID {pid} to close...")
    try:
        for _ in range(30):  # Wait up to 30 seconds
            if not _is_process_running(pid):
                log("Process closed.")
                time.sleep(1)  # Extra safety buffer
                return True
            time.sleep(1)

        # Force kill if still running
        log("Process stuck. Forcing kill...")
        kill_res = subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        if kill_res.returncode not in (0, 128):
            log(f"Force-kill failed: {kill_res.stderr.strip() or kill_res.stdout.strip()}")
            return False

        time.sleep(1)
        if _is_process_running(pid):
            log(f"Process {pid} is still running after force-kill.")
            return False
        return True
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log(f"Process termination failed: {e}")
        return False


# What an update must not touch. This list does three jobs: these are skipped
# when the backup copy is made, they are left alone by a rollback, and - the
# dangerous half - anything whose top-level name is NOT here and is not in the
# new package gets deleted by the cleanup after extraction.
#
# So the names from before the product was renamed have to stay. A machine
# updating off an older build still has .capsule_vfx and ut_server_config.json
# sitting at the install root, under those names, because the previous update
# preserved them rather than renaming them. Drop them from this list and the
# next update deletes the file that says which database to use - and the
# server, finding no settings, treats it as a first run and builds an empty
# one. Keeping both spellings costs nothing; guessing wrong costs a studio
# its server settings.
PERSISTENT_ITEMS = [
    "LocalDatabase", "database", "logs", "payload.json", "Backups",
    # current names
    ".slate_vfx", "slate_server_config.json", "client_config.json",
    # names still on disk from earlier builds
    ".capsule_vfx", ".ut_vfx", "ut_server_config.json",
]

# Not backed up because they are regenerated, and for the same reason not
# something a rollback should delete.
PERSISTENT_PATTERNS = ["Cache", "*.log", "tmp"]


def is_persistent(name: str) -> bool:
    """Whether a top-level entry in the install folder belongs to the studio, not the build."""
    import fnmatch
    if name in PERSISTENT_ITEMS:
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in PERSISTENT_PATTERNS)


def restore_backup(backup_dir: Path, install_dir: Path) -> bool:
    """
    Put the previous build back, leaving the studio's own files where they are.

    The backup was made without the persistent items - the database, the logs,
    the settings files - so this must not clear the install folder and copy the
    backup over the gap: that deleted exactly the things the exclusion list
    existed to protect, and a failed update then cost a studio its server
    settings on the way back. Only the build's own files are replaced.
    """
    try:
        if not backup_dir.exists():
            log(f"Rollback skipped: backup not found at {backup_dir}")
            return False

        log("Restoring backup...")
        if install_dir.exists():
            for entry in list(install_dir.iterdir()):
                if is_persistent(entry.name):
                    continue
                try:
                    if entry.is_dir() and not entry.is_symlink():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink()
                except OSError as exc:
                    log(f"Could not remove {entry.name} before restoring: {exc}")
        shutil.copytree(backup_dir, install_dir, dirs_exist_ok=True)
        log("Rollback completed.")
        return True
    except (OSError, shutil.Error) as e:
        log(f"Rollback failed: {e}")
        return False

def main():
    if len(sys.argv) < 5:
        log("Usage: updater.exe <PID> <ZIP_PATH> <INSTALL_DIR> <EXE_NAME>")
        sys.exit(1)

    try:
        pid = int(sys.argv[1])
    except ValueError:
        error_exit(f"Invalid PID: {sys.argv[1]!r}")
    zip_path = Path(sys.argv[2])
    install_dir = Path(sys.argv[3])
    exe_name = sys.argv[4]

    log(f"Starting Update:\nPID={pid}\nPackage={zip_path}\nTarget={install_dir}")

    # 1. Wait for Main App to Close
    if pid != 0:
        if not kill_process(pid):
            error_exit(f"Failed to close application process {pid}. Update aborted.")
    else:
        log("PID=0 provided, skipping process kill (Emergency Update Mode).")

    if not zip_path.exists():
        error_exit(f"Update package not found: {zip_path}")
    if not install_dir.exists():
        error_exit(f"Install directory not found: {install_dir}")

    # 2. Backup Current Version
    backup_dir = install_dir.parent / "Backups" / f"Backup_{int(time.time())}"
    log(f" Creating Backup at {backup_dir}...")

    try:
        backup_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            install_dir,
            backup_dir,
            ignore=shutil.ignore_patterns(*PERSISTENT_ITEMS, *PERSISTENT_PATTERNS)
        )
    except (OSError, shutil.Error) as e:
        error_exit(f"Backup failed, aborting update: {e}")

    # 3. Apply Update
    log("Extracting files...")
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_namelist = zip_ref.namelist()
            
            # Scan current install_dir BEFORE extracting for smart cleanup
            old_files = []
            for root, dirs, files in os.walk(install_dir):
                for file in files:
                    rel_path = os.path.relpath(os.path.join(root, file), install_dir)
                    old_files.append(rel_path.replace("\\", "/"))

            # We enforce overwrite
            zip_ref.extractall(install_dir)
            log("Extraction complete. Performing smart cleanup...")
            
            for old_file in old_files:
                top_level = old_file.split("/")[0]
                if not is_persistent(top_level) and old_file not in zip_namelist:
                    file_to_remove = install_dir / old_file
                    if file_to_remove.exists():
                        try:
                            os.remove(file_to_remove)
                        except OSError as e:
                            log(f"Could not remove {old_file}: {e}")
            log("Smart cleanup complete.")
    except (zipfile.BadZipFile, OSError, RuntimeError) as e:
        rollback_ok = restore_backup(backup_dir, install_dir)
        if rollback_ok:
            error_exit(f"Failed to extract update: {e}\nRollback completed.")
        error_exit(f"Failed to extract update: {e}\nRollback failed; manual restore required.")

    # 4. Cleanup
    try:
        os.remove(zip_path)
    except OSError as e:
        log(f"Cleanup warning: could not remove update package {zip_path}: {e}")

    # 5. Relaunch
    exe_path = install_dir / exe_name
    log(f"Relaunching {exe_path}...")
    launch_ok = False
    if exe_path.exists():
        try:
            subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent))
            launch_ok = True
        except OSError as exc:
            log(f"Relaunch failed: {exc}")

    if not launch_ok:
        log("Relaunch failed. Attempting rollback...")
        rollback_ok = restore_backup(backup_dir, install_dir)
        if rollback_ok:
            rollback_exe = install_dir / exe_name
            if rollback_exe.exists():
                try:
                    subprocess.Popen([str(rollback_exe)], cwd=str(rollback_exe.parent))
                    warn_exit(
                        "Update could not launch the new build.\n"
                        "Previous version was restored and restarted."
                    )
                except OSError as exc:
                    error_exit(
                        "Update relaunch failed. Rollback completed, but previous build "
                        f"could not be started ({exc})."
                    )
            error_exit("Update relaunch failed. Rollback completed, but executable was not found.")

        error_exit("Update relaunch failed and rollback failed; manual restore is required.")

    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"Critical Crash: {traceback.format_exc()}")
        ctypes.windll.user32.MessageBoxW(0, f"Updater Crashed:\n{e}", "Slate Updater", 0x10)
        sys.exit(1)
