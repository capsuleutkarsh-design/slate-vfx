# --- run from the project root -------------------------------------------
# PyInstaller executes a spec with the working directory set to the spec's own
# folder. Every path below is written relative to the project root, which is
# where these specs used to live, so step back to it before anything resolves.
# Located by looking for the package rather than by counting "..", so moving
# this file again does not silently break the build.
import os as _os_root

_root = _os_root.path.dirname(_os_root.path.abspath(SPECPATH))
_here = _os_root.path.abspath(SPECPATH)
if _os_root.path.isdir(_os_root.path.join(_here, "slate")):
    _root = _here
elif not _os_root.path.isdir(_os_root.path.join(_root, "slate")):
    raise SystemExit(
        "Cannot find the slate package from %s - this spec does not know where "
        "the project root is." % _here)
_os_root.chdir(_root)
# -------------------------------------------------------------------------


def R(*parts):
    """A path under the project root, absolute, for PyInstaller to resolve."""
    return _os_root.path.join(_root, *parts)


# -*- mode: python ; coding: utf-8 -*-

import sys
import os

block_cipher = None

# --- PATH SETUP ---
work_dir = os.path.abspath(os.getcwd())

# Determine project root
if os.path.exists(os.path.join(work_dir, 'slate')):
    project_root = work_dir
    launcher_script = os.path.join(project_root, 'slate', 'launcher.py')
    icon_path = os.path.join(project_root, 'slate', 'icons', 'app_icon_128.ico')
elif os.path.exists(os.path.join(work_dir, 'core')):
    # We are inside slate
    project_root = os.path.dirname(work_dir)
    launcher_script = os.path.join(project_root, 'slate', 'launcher.py')
    icon_path = os.path.join(project_root, 'slate', 'icons', 'app_icon_128.ico')
else:
    # Fallback/Guess
    launcher_script = os.path.join(work_dir, 'launcher.py')
    icon_path = None

if not os.path.exists(launcher_script):
    raise FileNotFoundError(f"Launcher script not found at {launcher_script}")

a = Analysis(
    [launcher_script],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SlateLauncher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SlateLauncher',
)
