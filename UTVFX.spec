# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, collect_all

hiddenimports = [
    'qasync', 'psycopg2', 'psycopg2.pool', 'psycopg2.extras', 
    'PySide6.QtCore', 'PySide6.QtWidgets', 'PySide6.QtGui', 
    'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 
    'numpy', 'pandas', 'cv2', 'OpenImageIO', 
    'keyring', 'keyring.backends', 'win32ctypes', 'win32ctypes.core', 
    'cryptography', 'openpyxl', 'fileseq', 'PyOpenColorIO', 
    'opentimelineio', 'opentimelineio.adapters', 'opentimelineio.plugins',
    # importlib.resources reads departments.json / access.json / templates.json
    # out of this package, so it must survive the freeze.
    'ut_vfx.data',
]
hiddenimports += collect_submodules('ut_vfx.gui.plugins')
hiddenimports += collect_submodules('ut_vfx.plugins')

# Much of the app imports these lazily, inside functions, so that opening a tab
# does not drag in the whole dashboard. PyInstaller usually finds those, but
# collecting them explicitly removes a class of "works in dev, missing in the
# build" failure.
hiddenimports += collect_submodules('ut_vfx.core.domain')
hiddenimports += collect_submodules('ut_vfx.core.infra.migrations')
hiddenimports += collect_submodules('ut_vfx.gui.tabs.vfx_dashboard_pro')

datas_qasync, binaries_qasync, hiddenimports_qasync = collect_all('qasync')
hiddenimports += hiddenimports_qasync

shared_datas = [
    ('ut_vfx/data', 'ut_vfx/data'),
    ('ut_vfx/assets', 'ut_vfx/assets'),
    ('ut_vfx/default_config.json', 'ut_vfx'),
    ('ut_vfx/icons', 'ut_vfx/icons'),
    ('ut_vfx/resources', 'ut_vfx/resources'),
    ('ut_vfx/bin/ffmpeg.exe', 'ut_vfx/bin'),
    ('ut_vfx/bin/ffprobe.exe', 'ut_vfx/bin'),
    ('ut_vfx/core/help_content.json', 'ut_vfx/core'),
    ('ut_vfx/gui/tabs/vfx_dashboard_pro/config', 'ut_vfx/gui/tabs/vfx_dashboard_pro/config'),
    ('ut_vfx/gui/tabs/vfx_dashboard_pro/sample_project.xlsx', 'ut_vfx/gui/tabs/vfx_dashboard_pro'),
] + datas_qasync

common_excludes = [
    'matplotlib', 'pytest', 'sphinx', 'IPython', 'notebook',
    'PyQt5', 'PyQt5.QtCore', 'PyQt5.QtWidgets', 'PyQt5.QtGui'
]

# 1. UT VFX Studio (Production Suite)
vfx_a = Analysis(
    ['ut_vfx\\vfx_studio_main.py'],
    pathex=[],
    binaries=[] + binaries_qasync,
    datas=shared_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=common_excludes,
    noarchive=False,
    optimize=0,
)
vfx_pyz = PYZ(vfx_a.pure)
vfx_exe = EXE(
    vfx_pyz,
    vfx_a.scripts,
    [],
    exclude_binaries=True,
    name='UT_VFX_Studio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['ut_vfx\\icons\\app_icon.ico'],
)

# 2. UT Studio Operations (HRMS & IT Suite)
ops_a = Analysis(
    ['ut_vfx\\studio_ops_main.py'],
    pathex=[],
    binaries=[] + binaries_qasync,
    datas=shared_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=common_excludes,
    noarchive=False,
    optimize=0,
)
ops_pyz = PYZ(ops_a.pure)
ops_exe = EXE(
    ops_pyz,
    ops_a.scripts,
    [],
    exclude_binaries=True,
    name='UT_Studio_Ops',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['ut_vfx\\icons\\app_icon.ico'],
)

# 3. UT Central Server
server_a = Analysis(
    ['ut_server\\main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=common_excludes,
    noarchive=False,
    optimize=0,
)
server_pyz = PYZ(server_a.pure)
server_exe = EXE(
    server_pyz,
    server_a.scripts,
    [],
    exclude_binaries=True,
    name='UT_Server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['ut_vfx\\icons\\server_icon.ico'],
)

# 4. Legacy All-in-One Gatekeeper (Backwards Compatibility)
legacy_a = Analysis(
    ['ut_vfx\\gatekeeper_main.py'],
    pathex=[],
    binaries=[] + binaries_qasync,
    datas=shared_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=common_excludes,
    noarchive=False,
    optimize=0,
)
legacy_pyz = PYZ(legacy_a.pure)
legacy_exe = EXE(
    legacy_pyz,
    legacy_a.scripts,
    [],
    exclude_binaries=True,
    name='UTVFX',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['ut_vfx\\icons\\app_icon.ico'],
)

# Collective distribution folder containing all executables and shared dependencies
coll = COLLECT(
    vfx_exe,
    vfx_a.binaries,
    vfx_a.datas,
    ops_exe,
    ops_a.binaries,
    ops_a.datas,
    server_exe,
    server_a.binaries,
    server_a.datas,
    legacy_exe,
    legacy_a.binaries,
    legacy_a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='UTVFX',
)
