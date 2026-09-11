# -*- mode: python ; coding: utf-8 -*-

import sys
import os

block_cipher = None

# --- PATH SETUP ---
work_dir = os.path.abspath(os.getcwd())

# Determine project root
if os.path.exists(os.path.join(work_dir, 'ut_vfx')):
    project_root = work_dir
    launcher_script = os.path.join(project_root, 'ut_vfx', 'launcher.py')
    icon_path = os.path.join(project_root, 'ut_vfx', 'icons', 'app_icon_128.ico')
elif os.path.exists(os.path.join(work_dir, 'core')):
    # We are inside ut_vfx
    project_root = os.path.dirname(work_dir)
    launcher_script = os.path.join(project_root, 'ut_vfx', 'launcher.py')
    icon_path = os.path.join(project_root, 'ut_vfx', 'icons', 'app_icon_128.ico')
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
    name='CapsuleLauncher',
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
    name='CapsuleLauncher',
)
