# -*- mode: python ; coding: utf-8 -*-

import sys
import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# --- COMPREHENSIVE PYQT5 EXCLUSION ---
# Collect all PyQt5 submodules to ensure complete exclusion
pyqt5_excludes = ['PyQt5', 'PyQt5.Qt']
try:
    pyqt5_excludes.extend(collect_submodules('PyQt5'))
except:
    pass  # PyQt5 may not be installed, that's fine

# --- PATH AUTO-DETECTION ---
work_dir = os.path.abspath(os.getcwd())

# Determine where the source code lives
if os.path.exists(os.path.join(work_dir, 'core')):
    # Case A: Running inside 'ut_vfx'
    project_root = work_dir
    package_base = os.path.dirname(work_dir)
elif os.path.exists(os.path.join(work_dir, 'ut_vfx', 'core')):
    # Case B: Running from Root (V0027)
    project_root = os.path.join(work_dir, 'ut_vfx')
    package_base = work_dir
else:
    raise FileNotFoundError(f"CRITICAL: Could not find 'core' folder in {work_dir}")

# --- CRITICAL FIX ---
# Point to gatekeeper_main.py inside the package folder
gatekeeper_script = os.path.join(project_root, 'gatekeeper_main.py')
icon_path = os.path.join(project_root, 'icons', 'app_icon_128.ico')

if not os.path.exists(gatekeeper_script):
    # Try looking in the root just in case
    gatekeeper_script = os.path.join(package_base, 'gatekeeper_main.py')
    
if not os.path.exists(gatekeeper_script):
    raise FileNotFoundError(f"CRITICAL: Could not find gatekeeper_main.py at {gatekeeper_script}")

datas_list = [
    (os.path.join(project_root, 'core'), 'ut_vfx/core'),
    (os.path.join(project_root, 'gui'), 'ut_vfx/gui'),
    (os.path.join(project_root, 'utils'), 'ut_vfx/utils'),
    (os.path.join(project_root, 'data'), 'ut_vfx/data'),
    (os.path.join(project_root, 'icons'), 'ut_vfx/icons'),
    (os.path.join(project_root, 'resources', 'styles.qss'), 'ut_vfx/resources'),
    # BUNDLED CONFIG (Zero-Config Deployment)
    (os.path.join(project_root, 'default_config.json'), '.'),
    # BUNDLED CAPSULE MESSENGER (Client & Server)
    (os.path.join(package_base, 'ut_messenger'), 'ut_messenger'),
    # BUNDLED OLIVE EDITOR (Portable Mode)
    (os.path.join(package_base, 'external', 'olive-editor'), 'olive-editor'),
]

# Optional: bundle ffmpeg/ffprobe when present in ut_vfx/bin
ffmpeg_bin_dir = os.path.join(project_root, 'bin')
if os.path.isdir(ffmpeg_bin_dir):
    datas_list.append((ffmpeg_bin_dir, 'ut_vfx/bin'))

a = Analysis(
    [gatekeeper_script], 
    pathex=[package_base],
    binaries=[],
    datas=datas_list,
    hiddenimports=[
        'pandas', 'numpy', 'cv2', 'sqlite3', 'reportlab', 
        'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
        # UTVFX modules
        'ut_vfx.core.domain.user_manager',
        'ut_vfx.gui.admin_panel',
        'ut_vfx.gui.gatekeeper_window', 
        'ut_vfx.core.infra.central_logger',
        'ut_vfx.core.infra.server_hub',
        'ut_vfx.core.domain.library_manager',
        'ut_vfx.core.domain.central_attendance',
        'ut_vfx.core.infra.global_config',
        # UT Messenger modules (Client & Server)
        'ut_messenger.client',
        'ut_messenger.client.gui',
        'ut_messenger.client.models',
        'ut_messenger.client.logic',
        'ut_messenger.client.components',
        'ut_messenger.client.dialogs',
        'ut_messenger.client.workers',
        'ut_messenger.server',
        'ut_messenger.server.gui',
        # Other dependencies
        'bcrypt',
        '_cffi_backend',
        '_ctypes',  # Required by ctypes and matplotlib
        'qasync',
        'sentry_sdk',
        'tenacity',
        'keyring',
        'psycopg2.pool',
        'psycopg2.extras',
        'cryptography',
        'matplotlib',
        'seaborn',
        'websockets',  # Required by messenger
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=pyqt5_excludes + [
        'torch', 'torchvision', 'torchaudio', 
        'tensorflow', 'tensorboard', 'keras',
        'scipy', 'sklearn', 
        'tkinter', 'notebook', 'ipython',
        'PyQt5.sip',
    ],
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
    name='UTVFX',
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
    name='UTVFX'
)

# --- UPDATER BUILD CONFIG ---
updater_script = os.path.join(project_root, 'core', 'updater', 'updater_script.py')

a2 = Analysis(
    [updater_script],
    pathex=[package_base],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=pyqt5_excludes + ['torch', 'torchvision', 'torchaudio', 'tensorflow', 'tensorboard', 'keras', 'scipy', 'sklearn', 'tkinter', 'notebook', 'ipython'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz2 = PYZ(a2.pure, a2.zipped_data, cipher=block_cipher)

exe2 = EXE(
    pyz2,
    a2.scripts,
    [],
    exclude_binaries=True,
    name='UTVFXUpdater',
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

coll2 = COLLECT(
    exe2,
    a2.binaries,
    a2.zipfiles,
    a2.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='UTVFXUpdater'
)

# --- MESSENGER CLIENT BUILD CONFIG ---
# Point to gui.py which has all the application logic
messenger_client_script = os.path.join(package_base, 'ut_messenger', 'client', 'gui.py')
messenger_client_datas = [
    (os.path.join(package_base, 'ut_messenger', 'client'), 'ut_messenger/client'),
    (os.path.join(package_base, 'ut_messenger', 'server'), 'ut_messenger/server'),
    (os.path.join(package_base, 'ut_messenger', 'assets'), 'ut_messenger/assets'),
    # Include ut_vfx icons for messenger to use
    (os.path.join(project_root, 'icons'), 'ut_vfx/icons'),
]
a_msg = Analysis(
    [messenger_client_script],
    pathex=[package_base],
    binaries=[],
    datas=messenger_client_datas,
    hiddenimports=[
        'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 
        'websockets', 'requests',
        'ut_messenger',
        'ut_messenger.client',
        'ut_messenger.client.gui',
        'ut_messenger.client.models',
        'ut_messenger.client.logic',
        'ut_messenger.client.components',
        'ut_messenger.client.dialogs',
        'ut_messenger.client.workers',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=pyqt5_excludes + ['torch', 'torchvision', 'torchaudio', 'tensorflow', 'tensorboard', 'keras', 'scipy', 'sklearn', 'tkinter', 'notebook', 'ipython'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz_msg = PYZ(a_msg.pure, a_msg.zipped_data, cipher=block_cipher)

exe_msg = EXE(
    pyz_msg,
    a_msg.scripts,
    [],
    exclude_binaries=True,
    name='UTMessenger',
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

coll_msg = COLLECT(
    exe_msg,
    a_msg.binaries,
    a_msg.zipfiles,
    a_msg.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='UTMessenger'
)

# --- MESSENGER SERVER BUILD CONFIG ---
# Point to gui.py which has all the application logic
messenger_server_script = os.path.join(package_base, 'ut_messenger', 'server', 'gui.py')
messenger_server_datas = [
    (os.path.join(package_base, 'ut_messenger', 'client'), 'ut_messenger/client'),
    (os.path.join(package_base, 'ut_messenger', 'server'), 'ut_messenger/server'),
    (os.path.join(package_base, 'ut_messenger', 'assets'), 'ut_messenger/assets'),
    # Include ut_vfx icons for messenger server to use
    (os.path.join(project_root, 'icons'), 'ut_vfx/icons'),
]
a_srv = Analysis(
    [messenger_server_script],
    pathex=[package_base],
    binaries=[],
    datas=messenger_server_datas,
    hiddenimports=[
        'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 
        'uvicorn', 'fastapi', 'websockets', 'psycopg2',
        'ut_messenger',
        'ut_messenger.server',
        'ut_messenger.server.gui',
        'ut_messenger.server.endpoints',
        'ut_messenger.server.schemas',
        'ut_messenger.server.security',
        'ut_messenger.server.db_manager',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=pyqt5_excludes + ['torch', 'torchvision', 'torchaudio', 'tensorflow', 'tensorboard', 'keras', 'scipy', 'sklearn', 'tkinter', 'notebook', 'ipython'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz_srv = PYZ(a_srv.pure, a_srv.zipped_data, cipher=block_cipher)

exe_srv = EXE(
    pyz_srv,
    a_srv.scripts,
    [],
    exclude_binaries=True,
    name='UTMessengerServer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path
)

coll_srv = COLLECT(
    exe_srv,
    a_srv.binaries,
    a_srv.zipfiles,
    a_srv.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='UTMessengerServer'
)
