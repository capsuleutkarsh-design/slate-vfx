"""
Enhanced Build Script for Slate
Uses PyInstaller to create standalone .exe with optimizations
"""

import PyInstaller.__main__
import argparse
import collections
import sys
import os
import shutil
import subprocess
import time
from pathlib import Path


def find_spec(name):
    """
    Locate a PyInstaller spec by filename.

    They live in deployment/ since the project root was tidied. PyInstaller
    runs a spec with the working directory set to the spec's own folder, so each
    spec steps back to the project root itself before its relative paths
    resolve - see the preamble at the top of any of them.
    """
    found = [os.path.join(folder, name)
             for folder in ("deployment", ".")
             if os.path.exists(os.path.join(folder, name))]

    if len(found) > 1:
        # Two specs with the same name, and only one of them is the one this
        # project maintains. PyInstaller writes a spec into the working
        # directory whenever it is pointed at a script instead of a spec, so a
        # single manual "pyinstaller slate_server/main.py" leaves a stripped
        # copy at the root that knows nothing about the settings the server
        # needs bundling. Building from it produces an executable that looks
        # right, is the right size, and has no database password in it.
        print("ERROR: %s exists in more than one place:" % name)
        for candidate in found:
            print("   " + os.path.abspath(candidate))
        print()
        print("       The one in deployment/ is this project's. The other is "
              "almost certainly left over from running PyInstaller against a")
        print("       script by hand - delete it, then build again.")
        sys.exit(1)

    return found[0] if found else None


def build_quick():
    """Quick debug build for testing (includes console for debugging)"""
    print("Building QUICK DEBUG version...")
    print("  - Console enabled (see output)")
    print("  - Debug symbols included")
    print("  - Faster compilation")
    print()
    
    args = [
        'slate/gatekeeper_main.py',
        '--name=Slate_Debug',
        '--onefile',
        '--console',  # Show console for debugging
        
        # Critical imports
        '--hidden-import=qasync',
        '--hidden-import=psycopg2',
        '--hidden-import=PySide6.QtCore',
        '--hidden-import=PySide6.QtWidgets',
        '--hidden-import=PySide6.QtGui',
        '--hidden-import=OpenImageIO',
        '--hidden-import=fileseq',
        '--hidden-import=PyOpenColorIO',
        '--hidden-import=opentimelineio',
        '--hidden-import=opentimelineio.adapters',
        '--hidden-import=opentimelineio.plugins',
        
        # Conflict Resolution
        '--exclude-module=PyQt5',
        '--exclude-module=PyQt5.QtCore',
        '--exclude-module=PyQt5.QtWidgets',
        '--exclude-module=PyQt5.QtGui',
        
        # Dynamic Plugins
        '--collect-submodules=slate.gui.plugins',
        '--collect-submodules=slate.plugins',
    ]
    
    if os.path.exists('client_config.json'):
        args.append('--add-data=client_config.json;.')
    args.extend([
        '--add-data=slate/data;slate/data',
        '--add-data=slate/core/help_content.json;slate/core',
        '--add-data=slate/assets;slate/assets',
        '--add-data=slate/default_config.json;slate',
    ])
    PyInstaller.__main__.run(args)

def build_release():
    """Full release build (optimized for size and performance)"""
    print("Building RELEASE version...")
    print("  - Windowed application (no console)")
    print("  - Optimized for size")
    print("  - All dependencies included")
    print()
    
    # Build arguments list
    args = [
        'slate/gatekeeper_main.py',
        '--name=Slate',
        '--onefile',
        '--windowed',  # No console window
        
        # Performance optimizations
        '--noupx',                    # Skip UPX compression (faster startup)
        '--strip',                    # Remove debug symbols (smaller size)
        
        # Critical imports - Core
        '--collect-all=qasync',
        '--hidden-import=psycopg2',
        '--hidden-import=psycopg2.pool',
        '--hidden-import=psycopg2.extras',
        
        # Critical imports - PySide6
        '--hidden-import=PySide6.QtCore',
        '--hidden-import=PySide6.QtWidgets',
        '--hidden-import=PySide6.QtGui',
        '--hidden-import=PySide6.QtMultimedia',
        '--hidden-import=PySide6.QtMultimediaWidgets',
        
        # Critical imports - Scientific
        '--hidden-import=numpy',
        '--hidden-import=pandas',
        '--hidden-import=cv2',
        '--hidden-import=OpenImageIO',
        
        # Critical imports - Utilities
        '--hidden-import=keyring',
        '--hidden-import=keyring.backends',
        '--hidden-import=win32ctypes',
        '--hidden-import=win32ctypes.core',
        '--hidden-import=cryptography',
        '--hidden-import=openpyxl',
        
        # Exclude unnecessary packages (reduce size)
        '--exclude-module=matplotlib',  # Only exclude if not used
        '--exclude-module=pytest',
        '--exclude-module=sphinx',
        '--exclude-module=IPython',
        '--exclude-module=notebook',
        '--exclude-module=IPython',
        '--exclude-module=notebook',
        
        # Conflict Resolution
        '--exclude-module=PyQt5',
        '--exclude-module=PyQt5.QtCore',
        '--exclude-module=PyQt5.QtWidgets',
        '--exclude-module=PyQt5.QtGui',
        
        # New dependencies
        '--hidden-import=fileseq',
        '--hidden-import=PyOpenColorIO',
        '--hidden-import=opentimelineio',
        '--hidden-import=opentimelineio.adapters',
        '--hidden-import=opentimelineio.plugins',
        # Dynamic plugins
        '--collect-submodules=slate.gui.plugins',
        '--collect-submodules=slate.plugins',
    ]
    
    # Add data files (use absolute paths or verify existence)
    if os.path.exists('client_config.json'):
        args.append('--add-data=client_config.json;.')
    
    if os.path.exists('slate/data'):
        args.append('--add-data=slate/data;slate/data')
    
    if os.path.exists('slate/assets'):
        args.append('--add-data=slate/assets;slate/assets')
        
    if os.path.exists('slate/default_config.json'):
        args.append('--add-data=slate/default_config.json;slate')
    
    if os.path.exists('slate/icons'):
        args.append('--add-data=slate/icons;slate/icons')

    if os.path.exists('slate/resources'):
        args.append('--add-data=slate/resources;slate/resources')
        
    if os.path.exists('slate/bin'):
        args.append('--add-data=slate/bin;slate/bin')
        
    if os.path.exists('external/olive-editor'):
        args.append('--add-data=external/olive-editor;external/olive-editor')
        
    # Help Content
    if os.path.exists('slate/core/help_content.json'):
         args.append('--add-data=slate/core/help_content.json;slate/core')
    
    # Add icon if it exists
    if os.path.exists('slate/icons/app_icon.ico'):
        args.append('--icon=slate/icons/app_icon.ico')
    
    # Database (In root)
    if os.path.exists('database'):
        args.append('--add-data=database;slate/database')
    
    PyInstaller.__main__.run(args)

# There is deliberately no build_server_release() any more. It rebuilt
# dist/Slate_Server.exe a second time from bare command-line flags - without
# the settings the spec bundles - straight after build_onedir had built and
# verified the real one, so the server that shipped was the settings-less one
# the check below exists to catch. PyInstaller also wrote its generated spec
# into the project root, which find_spec() then refused on every later build.
# The spec build inside build_onedir is the only server build.

def build_onedir():
    """Build as directory (faster startup, easier debugging)"""
    print("Building ONE-DIR version...")
    print("  - Creates folder instead of single .exe")
    print("  - Faster startup time")
    print("  - Easier to debug")
    print()
    
    # USE THE OFFICIAL SPEC FILE
    # This ensures consistent results with bundled config and dependencies
    #
    # The spec files were moved into deployment/ when the project root was
    # tidied, and this was still looking for one beside itself. The old location
    # is tried too, so a checkout that has not been reorganised still builds.
    spec_file = find_spec('Slate.spec')

    if spec_file is None:
        print("ERROR: Spec file not found: Slate.spec")
        print("       Looked in deployment/ and the project root.")
        sys.exit(1)
        
    print(f"Building using spec file: {spec_file}")
    PyInstaller.__main__.run([spec_file, '--noconfirm'])

    # The server is installed on its own, into its own folder, so it is built as
    # a single self-contained executable rather than as part of the shared
    # folder above. setup_slate_server.iss takes dist\Slate_Server.exe, and
    # nothing here used to produce it - so the server installer silently picked
    # up whatever one-file build happened to be left in dist from an earlier
    # session. A studio then installed a server weeks older than the client it
    # was built alongside, and nothing said so.
    server_spec = find_spec('Slate_Server.spec')
    if server_spec is None:
        print("ERROR: Spec file not found: Slate_Server.spec")
        print("       setup_slate_server.iss needs dist/Slate_Server.exe, and "
              "without this spec it would install a stale one.")
        sys.exit(1)

    # Absolute, so nothing here depends on what the first build left the
    # working directory as.
    server_spec = os.path.abspath(server_spec)
    server_exe = os.path.abspath(os.path.join("dist", "Slate_Server.exe"))
    stamp_before = os.path.getmtime(server_exe) if os.path.exists(server_exe) else 0

    print(f"\nBuilding the standalone server using: {server_spec}")

    # In a separate process rather than a second PyInstaller.__main__.run().
    # PyInstaller keeps build state in module globals, and a spec changes the
    # working directory as it loads; two runs in one process share both.
    #
    # Streamed and kept. The first version of this reported only "did not
    # build" and threw away everything PyInstaller had said about why, which
    # is the same fault it exists to prevent - a build that fails without
    # saying what it could not do.
    tail = collections.deque(maxlen=40)
    try:
        server_build = subprocess.Popen(
            [sys.executable, "-m", "PyInstaller", server_spec, "--noconfirm"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1)
    except OSError as exc:
        # No way to re-enter this interpreter. The separate process is a
        # precaution, not a requirement, so build in-process rather than not
        # at all - and say that is what happened.
        print(f"Could not start a second PyInstaller ({exc}).")
        print("Building the server in this process instead.")
        PyInstaller.__main__.run([server_spec, "--noconfirm"])
    else:
        for line in server_build.stdout:
            line = line.rstrip()
            tail.append(line)
            print(line)
        server_build.wait()

        if server_build.returncode != 0:
            print()
            print("=" * 70)
            print("ERROR: The standalone server did not build "
                  f"(exit code {server_build.returncode}).")
            print("=" * 70)
            print("The last lines PyInstaller printed:")
            for line in tail:
                print("   " + line)
            print()
            print("The two usual reasons, in the order worth checking:")
            print("  1. dist/Slate_Server.exe is in use - close Slate Server, "
                  "and anything that opened it from dist")
            print("  2. antivirus is holding the newly written file; "
                  "exclude the dist and build folders")
            print()
            print("Nothing else is stale: setup_slate_server.iss installs "
                  "dist/Slate_Server.exe, so shipping the old one would put a "
                  "server older than its clients on a studio machine.")
            sys.exit(1)

    # A clean exit code is not the same as a new executable. Check the thing
    # the installer will actually pick up.
    if not os.path.exists(server_exe):
        print("ERROR: PyInstaller reported success but dist/Slate_Server.exe "
              "is not there.")
        sys.exit(1)
    if os.path.getmtime(server_exe) <= stamp_before:
        print("ERROR: dist/Slate_Server.exe was not rewritten, so it is the "
              "one from a previous build. Refusing to package a stale server.")
        sys.exit(1)

    size_mb = os.path.getsize(server_exe) / (1024 * 1024)
    print(f"\nStandalone server built: {server_exe} ({size_mb:.0f} MB)")

    _check_server_carries_its_settings(server_exe)
    _copy_unmanaged_data()


def _copy_unmanaged_data():
    """
    Put the folders PyInstaller does not manage next to the executables.

    This used to sit inside _check_server_carries_its_settings, after its early
    returns - so a bundle that could not be opened skipped the copy as well,
    and the build shipped without Olive or the database scripts and said
    nothing. It is its own step now, and it always runs.

    slate/bin is not copied: the spec already places ffmpeg and ffprobe in
    _internal/slate/bin, which is where ResourcePathManager looks first, so the
    copy was a second 190 MB of the same two files in every installer.
    slate_server/bin is not copied either: the server ships as a one-file
    executable built from Slate_Server.spec, and both client installers exclude
    the folder - it was 340 MB copied on every build and read by nothing.
    """
    print("\nCopying unmanaged data directories into dist/Slate...")
    dist_folder = os.path.join('dist', 'Slate')

    def force_rmtree(path):
        """
        Remove a tree that Windows is being difficult about.

        A plain rmtree over a large tree fails with "The directory is not
        empty" often enough to break a build that was otherwise finished. The
        directory is not really non-empty: a file was still being released as
        the walk passed it. So clear the read-only bit on whatever refused,
        and give the filesystem a moment before each retry.
        """
        def _clear_readonly(func, failed_path, _exc):
            try:
                os.chmod(failed_path, 0o700)
                func(failed_path)
            except OSError:
                pass

        for attempt in range(4):
            try:
                shutil.rmtree(path, onerror=_clear_readonly)
                if not os.path.exists(path):
                    return
            except OSError:
                pass
            time.sleep(0.5 * (attempt + 1))

        if os.path.exists(path):
            raise OSError(
                f"Could not clear {path}. Something is holding a file open in "
                f"it - a running Slate, an antivirus scan, or an open "
                f"Explorer window - close it and build again.")

    def copy_if_exists(src, dst):
        if os.path.exists(src):
            dst_path = os.path.join(dist_folder, dst)
            print(f'Copying {src} to {dst_path}')
            if os.path.exists(dst_path):
                force_rmtree(dst_path)
            shutil.copytree(src, dst_path)
        else:
            print(f'  ({src} not present - skipped)')

    copy_if_exists('external/olive-editor', 'external/olive-editor')
    copy_if_exists('database', 'database')


def _check_server_carries_its_settings(server_exe):
    """
    Open the finished executable and confirm the settings are inside it.

    Checking the spec is not enough, because the spec that gets used is not
    always the spec that is meant. A server built without its settings has no
    database password: it creates none of the accounts, reports that on its own
    dashboard, and every workstation is turned away at the login screen. The
    executable is the right size and starts perfectly, so nothing about it looks
    wrong until a studio tries to log in.
    """
    import json

    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as exc:
        print(f"   (could not verify the bundle: {exc})")
        return

    try:
        archive = CArchiveReader(server_exe)
        entries = list(archive.toc)
    except Exception as exc:
        print(f"   (could not read the bundle: {exc})")
        return

    settings = [name for name in entries if "default_config" in name]
    if not settings:
        print()
        print("=" * 70)
        print("ERROR: The server was built without its settings.")
        print("=" * 70)
        print("dist/Slate_Server.exe contains no default_config.json, so it "
              "will start with no database password.")
        print("It will then create none of the accounts, and every workstation "
              "will be turned away at the login screen.")
        print()
        print("The spec that built this is not the one in deployment/. Check "
              "for a stray Slate_Server.spec at the project root.")
        sys.exit(1)

    try:
        raw = archive.extract(settings[0])
        if isinstance(raw, tuple):
            raw = raw[1]
        config = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        print(f"   (settings are bundled but could not be read: {exc})")
        return

    if not config.get("db_password"):
        print("ERROR: The bundled settings carry no db_password, so the server "
              "cannot create its accounts.")
        sys.exit(1)

    print("   settings bundled: %s (database %s, account %s)"
          % (settings[0], config.get("db_name"), config.get("db_user")))


def build_installer(version=None, target="all"):
    """Build Windows installer using Inno Setup"""
    print("=" * 70)
    print(f"  Building Installer [{target.upper()}] with Inno Setup")
    print("=" * 70)
    print()
    
    # Prompt for version number if not provided
    if not version:
        print("Enter version number (e.g., BETA 1.3.0):")
        version = input("> ").strip()
        
        if not version:
            print("ERROR: Version number is required!")
            sys.exit(1)
        
        print(f"\nBuilding installer for version: {version}")
        print()
    
    project_root = Path.cwd().resolve()
    all_scripts = {
        "vfx": (project_root / "deployment" / "setup_slate_client.iss").resolve(),
        "ops": (project_root / "deployment" / "setup_slate_ops.iss").resolve(),
        # setup_slate_server.iss, not setup_slate_central_server.iss: the file
        # was renamed to match its two siblings, while this reference went
        # through a text rule that turned "ut_central" into "slate_central" and
        # grew a word the file never had.
        "server": (project_root / "deployment" / "setup_slate_server.iss").resolve()
    }

    if target == "all":
        iss_scripts = list(all_scripts.values())
    elif target in all_scripts:
        iss_scripts = [all_scripts[target]]
    else:
        print(f"ERROR: Unknown target '{target}'. Choose from {list(all_scripts.keys())} or 'all'")
        sys.exit(1)

    dist_dir = (project_root / "dist" / "Slate").resolve()
    installer_dir = (project_root / "installers").resolve()
    icon_candidates_client = [
        (project_root / "slate" / "icons" / "app_icon.ico").resolve(),
        (project_root / "slate" / "icons" / "app_icon_128.ico").resolve(),
    ]
    icon_candidates_server = [
        (project_root / "slate" / "icons" / "server_icon.ico").resolve(),
    ]
    setup_icon_client = next((p for p in icon_candidates_client if p.exists()), None)
    setup_icon_server = next((p for p in icon_candidates_server if p.exists()), None)

    # Check if Inno Setup is installed
    inno_paths = [
        r"C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
        r"C:\Program Files\Inno Setup 7\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
    ]
    
    iscc_exe = os.getenv("ISCC_EXE") or shutil.which("ISCC.exe") or shutil.which("ISCC")
    if iscc_exe and not os.path.exists(iscc_exe):
        print(f"WARNING: ISCC_EXE is set but not found: {iscc_exe}")
        iscc_exe = None

    for path in inno_paths:
        if not iscc_exe and os.path.exists(path):
            iscc_exe = path
            break
    
    if not iscc_exe:
        print("ERROR: Inno Setup not found!")
        print("Please install Inno Setup from: https://jrsoftware.org/isdl.php")
        print("Or set ISCC_EXE to your ISCC.exe path.")
        print("Expected locations:")
        for path in inno_paths:
            print(f"  - {path}")
        sys.exit(1)
    
    # Check if the .iss scripts exist
    for script in iss_scripts:
        if not script.exists():
            print(f"ERROR: Inno Setup script not found: {script}")
            sys.exit(1)
    
    # Check if dist folder exists with required files
    if not dist_dir.exists():
        print("ERROR: dist/Slate not found!")
        print("Please run PyInstaller build first:")
        print("  python tools/build_pipeline.py --mode onedir")
        sys.exit(1)
    
    # One writer for the version, over every file that carries it: the three
    # installer scripts, slate/__init__.py and pyproject.toml. This used to
    # rewrite the first two here and leave pyproject.toml behind, while
    # bump_version.py pointed at an installer script that no longer existed -
    # so the three copies of the version number drifted apart.
    print("Setting the version everywhere it is recorded...")
    sys.path.insert(0, str(project_root / "tools"))
    from bump_version import set_version
    for path in set_version(version, root=project_root):
        print(f"  [OK] {path.relative_to(project_root)}")

    print(f"Using Inno Setup: {iscc_exe}")
    print(f"DistDir: {dist_dir}")
    print(f"OutputDir: {installer_dir}")
    if setup_icon_client:
        print(f"SetupIcon (Client): {setup_icon_client}")
    if setup_icon_server:
        print(f"SetupIcon (Server): {setup_icon_server}")
    else:
        print("SetupIcon: default from .iss (no local override found)")
    print()
    print("Compiling installers...")
    
    # Run Inno Setup for both scripts
    installer_dir.mkdir(parents=True, exist_ok=True)
    
    for script in iss_scripts:
        print(f"\nBuilding: {script.name}")
        # The version is not passed as /DMyAppVersion: each script #defines it
        # unconditionally, so the define on the command line was overridden
        # anyway. set_version() above wrote it into the scripts.
        iscc_args = [
            iscc_exe,
            f'/DInstallerOutputDir={installer_dir}',
        ]
        if "server" in script.name.lower() and setup_icon_server:
            iscc_args.append(f"/DSetupIconPath={setup_icon_server}")
        elif setup_icon_client:
            iscc_args.append(f"/DSetupIconPath={setup_icon_client}")
        iscc_args.append(str(script))

        result = subprocess.run(iscc_args)
        if result.returncode != 0:
            print(f"ERROR: Failed to build {script.name}")
            sys.exit(1)
    
    if result.returncode == 0:
        print()
        print("=" * 70)
        print("  [SUCCESS] Installer built successfully!")
        print("=" * 70)
        
        # Find the output installer
        if installer_dir.exists():
            installers = list(installer_dir.glob("setup_*.exe"))
            if installers:
                latest = max(installers, key=lambda p: p.stat().st_mtime)
                size_mb = latest.stat().st_size / (1024 * 1024)
                print(f"\nInstaller: {latest}")
                print(f"Size: {size_mb:.1f} MB")
                print(f"Version: {version}")
                print(f"\nReady to distribute!")
    else:
        # Output is now streamed directly to console
        sys.exit(1)

def build_full(version=None, target="all"):
    """Build everything: PyInstaller + Inno Setup"""
    print("=" * 70)
    print(f"  FULL BUILD [{target.upper()}]: PyInstaller + Inno Setup Installer")
    print("=" * 70)
    print()
    
    # Prompt for version number if not provided
    if not version:
        print("Enter version number (e.g., BETA 1.3.0):")
        version = input("> ").strip()
    
    if not version:
        print("ERROR: Version number is required!")
        sys.exit(1)
    
    print(f"\nBuilding installer for version: {version}")
    print()
    
    # Step 1: Build with PyInstaller (onedir mode required for Inno Setup).
    # This also builds and verifies dist/Slate_Server.exe from its spec, for
    # every target, so there is no separate server step.
    print("Step 1: Building with PyInstaller...")
    build_onedir()

    print()
    print("=" * 70)
    print()
    
    # Step 2: Build installer
    print("Step 2: Creating installer...")
    build_installer(version, target=target)

# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Build Slate executable and installer',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Build Modes:
  quick      Quick debug build with console (fast, for testing)
  release    Full release build, optimized (default)
  onedir     Build as directory instead of single .exe (faster startup)
  installer  Build Windows installer using Inno Setup (requires onedir first)
  full       Complete build: PyInstaller + Inno Setup installer

Examples:
  python build_pipeline.py                    # Release build (.exe)
  python build_pipeline.py --mode quick       # Quick test build
  python build_pipeline.py --mode onedir      # Directory build
  python build_pipeline.py --mode installer   # Create installer (after onedir)
  python build_pipeline.py --mode full        # Build everything!
        """
    )
    parser.add_argument(
        '--mode', 
        choices=['quick', 'release', 'onedir', 'installer', 'full'], 
        default='release',
        help='Build mode (default: release)'
    )
    
    parser.add_argument(
        '--version',
        help='Version number for installer (e.g., "BETA 1.3.0"). If not provided, script may prompt.',
        default=None
    )

    parser.add_argument(
        '--target',
        choices=['all', 'vfx', 'ops', 'server'],
        default='all',
        help='Target suite component for installer/full build (all, vfx, ops, server)'
    )
    
    args = parser.parse_args()
    
    # Verify we're in the correct directory (or auto-fix if run from tools/)
    if not os.path.exists('slate/gatekeeper_main.py'):
        # Check if we're in the tools directory
        if os.path.basename(os.getcwd()) == 'tools' and os.path.exists('../slate/gatekeeper_main.py'):
            # Change to parent directory (V0040)
            os.chdir('..')
            print(f"Auto-detected: Changed directory to {os.getcwd()}")
            print()
        else:
            print("ERROR: slate/gatekeeper_main.py not found!")
            print("Make sure you're running this from the V0040 directory")
            print(f"Current directory: {os.getcwd()}")
            sys.exit(1)
    
    # Execute chosen build mode
    if args.mode == 'installer':
        # Only build installer
        build_installer(args.version, target=args.target)
    elif args.mode == 'full':
        # Build everything
        build_full(args.version, target=args.target)
    else:
        # Standard PyInstaller builds
        print("=" * 70)
        print("  Slate Build Script v2.0")
        print("=" * 70)
        print()
        
        if args.mode == 'quick':
            build_quick()
        elif args.mode == 'onedir':
            build_onedir()
        else:
            build_release()
        
        print()
        print("=" * 70)
        print("  Build complete!")
        print("=" * 70)
        
        # Show output location
        if args.mode == 'quick':
            exe_name = "Slate_Debug.exe"
        else:
            exe_name = "Slate.exe" if args.mode == 'release' else "Slate"
        
        output_path = Path("dist") / exe_name
        if output_path.exists():
            size_mb = output_path.stat().st_size / (1024 * 1024)
            print(f"\nOutput: {output_path}")
            print(f"Size: {size_mb:.1f} MB")
        
        print("\nNext steps:")
        print(f"  1. Test: {output_path}")
        print("  2. Run installer build: python tools/build_pipeline.py --mode installer")
        print("  3. Distribute to artists")
