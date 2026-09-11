"""
Enhanced Build Script for UT_VFX
Uses PyInstaller to create standalone .exe with optimizations
"""

import PyInstaller.__main__
import argparse
import sys
import os
import re
import shutil
import subprocess
from pathlib import Path

def build_quick():
    """Quick debug build for testing (includes console for debugging)"""
    print("Building QUICK DEBUG version...")
    print("  - Console enabled (see output)")
    print("  - Debug symbols included")
    print("  - Faster compilation")
    print()
    
    args = [
        'ut_vfx/gatekeeper_main.py',
        '--name=UTVFX_Debug',
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
        '--collect-submodules=ut_vfx.gui.plugins',
        '--collect-submodules=ut_vfx.plugins',
    ]
    
    if os.path.exists('client_config.json'):
        args.append('--add-data=client_config.json;.')
    args.extend([
        '--add-data=ut_vfx/data;ut_vfx/data',
        '--add-data=ut_vfx/core/help_content.json;ut_vfx/core',
        '--add-data=ut_vfx/assets;ut_vfx/assets',
        '--add-data=ut_vfx/default_config.json;ut_vfx',
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
        'ut_vfx/gatekeeper_main.py',
        '--name=UTVFX',
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
        '--collect-submodules=ut_vfx.gui.plugins',
        '--collect-submodules=ut_vfx.plugins',
    ]
    
    # Add data files (use absolute paths or verify existence)
    if os.path.exists('client_config.json'):
        args.append('--add-data=client_config.json;.')
    
    if os.path.exists('ut_vfx/data'):
        args.append('--add-data=ut_vfx/data;ut_vfx/data')
    
    if os.path.exists('ut_vfx/assets'):
        args.append('--add-data=ut_vfx/assets;ut_vfx/assets')
        
    if os.path.exists('ut_vfx/default_config.json'):
        args.append('--add-data=ut_vfx/default_config.json;ut_vfx')
    
    if os.path.exists('ut_vfx/icons'):
        args.append('--add-data=ut_vfx/icons;ut_vfx/icons')

    if os.path.exists('ut_vfx/resources'):
        args.append('--add-data=ut_vfx/resources;ut_vfx/resources')
        
    if os.path.exists('ut_vfx/bin'):
        args.append('--add-data=ut_vfx/bin;ut_vfx/bin')
        
    if os.path.exists('external/olive-editor'):
        args.append('--add-data=external/olive-editor;external/olive-editor')
        
    # Help Content
    if os.path.exists('ut_vfx/core/help_content.json'):
         args.append('--add-data=ut_vfx/core/help_content.json;ut_vfx/core')
    
    # Add icon if it exists
    if os.path.exists('ut_vfx/icons/app_icon.ico'):
        args.append('--icon=ut_vfx/icons/app_icon.ico')
    
    # Database (In root)
    if os.path.exists('database'):
        args.append('--add-data=database;ut_vfx/database')
    
    PyInstaller.__main__.run(args)

def build_server_release():
    """Build UT Central Server"""
    print("Building SERVER version...")
    
    args = [
        'ut_server/main.py',
        '--name=UT_Server',
        '--onefile',
        '--windowed',
        
        # Critical imports
        '--hidden-import=PySide6.QtCore',
        '--hidden-import=PySide6.QtWidgets',
        '--hidden-import=PySide6.QtGui',
        '--hidden-import=psycopg2',
        
        # Add server data
        '--add-data=ut_server/bin;ut_server/bin',
    ]
    
    if os.path.exists('ut_vfx/icons/server_icon.ico'):
        args.append('--icon=ut_vfx/icons/server_icon.ico')
        
    PyInstaller.__main__.run(args)

def build_onedir():
    """Build as directory (faster startup, easier debugging)"""
    print("Building ONE-DIR version...")
    print("  - Creates folder instead of single .exe")
    print("  - Faster startup time")
    print("  - Easier to debug")
    print()
    
    # USE THE OFFICIAL SPEC FILE
    # This ensures consistent results with bundled config and dependencies
    spec_file = 'UTVFX.spec'
    
    if not os.path.exists(spec_file):
        print(f"ERROR: Spec file not found: {spec_file}")
        sys.exit(1)
        
    print(f"Building using spec file: {spec_file}")
    PyInstaller.__main__.run([spec_file, '--noconfirm'])
    
    print("\nCopying unmanaged data directories into dist/UTVFX...")
    dist_folder = os.path.join('dist', 'UTVFX')
    
    def copy_if_exists(src, dst):
        if os.path.exists(src):
            dst_path = os.path.join(dist_folder, dst)
            print(f'Copying {src} to {dst_path}')
            if os.path.exists(dst_path):
                shutil.rmtree(dst_path)
            shutil.copytree(src, dst_path)
            
    copy_if_exists('ut_vfx/bin', 'ut_vfx/bin')
    copy_if_exists('ut_server/bin', 'ut_server/bin')
    copy_if_exists('external/olive-editor', 'external/olive-editor')
    copy_if_exists('database', 'database')

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
        "vfx": (project_root / "deployment" / "setup_ut_vfx_client.iss").resolve(),
        "ops": (project_root / "deployment" / "setup_ut_studio_ops.iss").resolve(),
        "server": (project_root / "deployment" / "setup_ut_central_server.iss").resolve()
    }

    if target == "all":
        iss_scripts = list(all_scripts.values())
    elif target in all_scripts:
        iss_scripts = [all_scripts[target]]
    else:
        print(f"ERROR: Unknown target '{target}'. Choose from {list(all_scripts.keys())} or 'all'")
        sys.exit(1)

    dist_dir = (project_root / "dist" / "UTVFX").resolve()
    installer_dir = (project_root / "installers").resolve()
    icon_candidates_client = [
        (project_root / "ut_vfx" / "icons" / "app_icon.ico").resolve(),
        (project_root / "ut_vfx" / "icons" / "app_icon_128.ico").resolve(),
    ]
    icon_candidates_server = [
        (project_root / "ut_vfx" / "icons" / "server_icon.ico").resolve(),
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
        print("ERROR: dist/UTVFX not found!")
        print("Please run PyInstaller build first:")
        print("  python tools/build_pipeline.py --mode onedir")
        sys.exit(1)
    
    # Update version in .iss files
    print("Updating version in Inno Setup scripts...")
    for script in iss_scripts:
        with open(script, 'r', encoding='utf-8') as f:
            iss_content = f.read()
        
        updated_content = re.sub(
            r'#define MyAppVersion ".*?"',
            f'#define MyAppVersion "{version}"',
            iss_content
        )
        
        with open(script, 'w', encoding='utf-8') as f:
            f.write(updated_content)
        print(f"  [OK] Updated {script.name}")
    
    # Update version in Python __init__.py
    print("Updating version in Python package...")
    init_file = project_root / "ut_vfx" / "__init__.py"
    if init_file.exists():
        with open(init_file, 'r', encoding='utf-8') as f:
            init_content = f.read()
        
        # Replace __version__ line
        updated_init = re.sub(
            r'__version__\s*=\s*["\'].*?["\']',
            f'__version__ = "{version}"',
            init_content
        )
        
        with open(init_file, 'w', encoding='utf-8') as f:
            f.write(updated_init)
        print(f"  [OK] Updated {init_file}")
    else:
        print(f"  [!] Warning: {init_file} not found, skipping")
    
    print(f"  [OK] Updated __init__.py")
    
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
        iscc_args = [
            iscc_exe,
            f'/DMyAppVersion={version}',
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
    
    # Step 1: Build with PyInstaller (onedir mode required for Inno Setup)
    print("Step 1: Building with PyInstaller...")
    build_onedir()
    
    if target in ("all", "server"):
        print("\nStep 1b: Building standalone server executable...")
        build_server_release()
    
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
        description='Build UT_VFX executable and installer',
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
    if not os.path.exists('ut_vfx/gatekeeper_main.py'):
        # Check if we're in the tools directory
        if os.path.basename(os.getcwd()) == 'tools' and os.path.exists('../ut_vfx/gatekeeper_main.py'):
            # Change to parent directory (V0040)
            os.chdir('..')
            print(f"Auto-detected: Changed directory to {os.getcwd()}")
            print()
        else:
            print("ERROR: ut_vfx/gatekeeper_main.py not found!")
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
        print("  UT_VFX Build Script v2.0")
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
            exe_name = "UTVFX_Debug.exe"
        else:
            exe_name = "UTVFX.exe" if args.mode == 'release' else "UTVFX"
        
        output_path = Path("dist") / exe_name
        if output_path.exists():
            size_mb = output_path.stat().st_size / (1024 * 1024)
            print(f"\nOutput: {output_path}")
            print(f"Size: {size_mb:.1f} MB")
        
        print("\nNext steps:")
        print(f"  1. Test: {output_path}")
        print("  2. Run installer build: python tools/build_pipeline.py --mode installer")
        print("  3. Distribute to artists")
