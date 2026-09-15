"""
Slate emergency updater.

Forces an update from the studio's shared folder without starting the
application - for the morning the application will not start at all.

    runtime\\python\\python.exe tools\\emergency_updater.py
    runtime\\python\\python.exe tools\\emergency_updater.py --target server --install-dir "C:\\...\\Slate Server"

It reads the same settings the application reads, looks for the same manifest
the application looks for (SERVER_ROOT/Updates/releases/manifest_<target>.json),
verifies the package the same way, and hands it to the same sidecar updater.

The previous version of this tool could not have worked on any machine: it
read a settings file under ~/.slate_vfx that nothing has ever written, looked
for an updater_script.exe that no build produces, and told the sidecar to
relaunch slate.exe, which is not what the product is called.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# What the sidecar relaunches, per target, in the order to look for it.
EXE_NAMES = {
    "client": ("Slate_Studio.exe", "Slate_Ops.exe", "Slate.exe"),
    "server": ("Slate_Server.exe",),
}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def studio_root() -> Path:
    """
    Where the studio's shared folder is, resolved the way the application does.

    GlobalConfig reads only the standard library at import time, so it is safe
    to use here even when the rest of the application cannot start.
    """
    try:
        from slate.core.infra.global_config import GlobalConfig
        root = Path(str(GlobalConfig.get("SERVER_ROOT") or "").strip())
        if str(root) and root.exists():
            return root
    except Exception as exc:
        print(f"  (could not read the application's settings: {exc})")

    # The same files, read by hand, in case the application's own reader is
    # what is broken this morning.
    candidates = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Slate" / "config.json")
    candidates.append(Path.home() / "RuntimeData" / "Slate" / "config.json")
    candidates.append(ROOT / "slate" / "config.json")
    candidates.append(ROOT / "client_config.json")
    for candidate in candidates:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            value = str(data.get("SERVER_ROOT") or "").strip()
            if value and Path(value).exists():
                return Path(value)
        except (OSError, ValueError):
            continue
    raise SystemExit("Cannot find the studio's shared folder (SERVER_ROOT) in any "
                     "settings file. Set it in Slate's Settings, or pass --server-root.")


def find_install_dir(target: str, given: str | None) -> Path:
    if given:
        return Path(given)
    local = os.environ.get("LOCALAPPDATA", "")
    names = {"client": ("Slate Studio", "Slate Operations", "Slate"),
             "server": ("Slate Server",)}[target]
    for name in names:
        for base in (Path(local) / "Programs", Path(local)):
            folder = base / name
            if any((folder / exe).exists() for exe in EXE_NAMES[target]):
                return folder
    raise SystemExit("Cannot find an installed %s. Pass --install-dir." % target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--target", choices=("client", "server"),
                        help="which half of the product to update (asked if omitted)")
    parser.add_argument("--install-dir", help="the folder the software is installed in")
    parser.add_argument("--server-root", help="the studio's shared folder, if settings are unreadable")
    args = parser.parse_args()

    print("=" * 50)
    print("   Slate Emergency Recovery Updater")
    print("=" * 50)
    print("Forces an update from the studio's shared folder without starting "
          "the application.\n")

    target = args.target or input("Update the 'client' or the 'server'? [client/server]: ").strip().lower()
    if target not in EXE_NAMES:
        print("Invalid target. Must be 'client' or 'server'.")
        return 1

    root = Path(args.server_root) if args.server_root else studio_root()
    print(f"Studio folder: {root}")

    from slate.core.updater.manifest import manifest_name, problems, releases_dir
    releases = releases_dir(root / "Updates")
    manifest_path = releases / manifest_name(target)
    if not manifest_path.exists():
        print(f"ERROR: No {target} update has been published: {manifest_path} is not there.")
        return 1

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: The manifest could not be read: {exc}")
        return 1

    faults = problems(manifest)
    if faults:
        print("ERROR: This update cannot be installed: " + "; ".join(faults))
        return 1

    package = releases / manifest["package_name"]
    if not package.exists():
        print(f"ERROR: The manifest names {manifest['package_name']}, which is not in {releases}.")
        return 1
    print(f"Found update: v{manifest['version']} ({package.name})")

    install_dir = find_install_dir(target, args.install_dir)
    exe_name = next((n for n in EXE_NAMES[target] if (install_dir / n).exists()),
                    EXE_NAMES[target][0])
    print(f"Install folder: {install_dir}")
    print(f"Will relaunch:  {exe_name}")

    staging = Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "SlateUpdate"
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / package.name
    print("Copying the package...")
    shutil.copy2(package, staged)

    print("Verifying the package...")
    actual = sha256_of(staged)
    if actual != manifest["hash_sha256"]:
        staged.unlink(missing_ok=True)
        print("ERROR: The package does not match its manifest. It was not installed.")
        return 1
    print("Package verified.")

    # The same sidecar the application uses, from the same places it looks.
    updater_exe = next((p for p in (install_dir / "SlateUpdater.exe",
                                    ROOT / "dist" / "Slate" / "SlateUpdater.exe")
                        if p.exists()), None)
    updater_py = ROOT / "slate" / "core" / "updater" / "updater_script.py"
    if updater_exe is not None:
        cmd = [str(updater_exe)]
    elif updater_py.exists():
        cmd = [sys.executable, str(updater_py)]
    else:
        print("ERROR: No SlateUpdater.exe beside the installed software and no "
              "updater_script.py in this checkout.")
        return 1

    # PID 0: nothing to wait for, the application is not running.
    cmd += ["0", str(staged), str(install_dir), exe_name]
    print("\nStarting the updater...")
    try:
        subprocess.Popen(cmd, cwd=str(install_dir))
    except OSError as exc:
        print(f"ERROR: The updater would not start: {exc}")
        return 1
    print("The updater has taken over. This window can be closed.")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if exc.code and not isinstance(exc.code, int):
            print(exc.code)
    except Exception:
        print("Critical error:\n" + traceback.format_exc())
        code = 1
    input("Press Enter to exit...")
    sys.exit(code)
