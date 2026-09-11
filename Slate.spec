
import os as _os

def _optional_binaries():
    """Third-party binaries setup.bat downloads. Skipped when absent."""
    found = []
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        path = _os.path.join("slate", "bin", name)
        if _os.path.exists(path):
            found.append((path, "slate/bin"))
        else:
            print("  [spec] %s not present - run setup.bat before building "
                  "if the build needs it" % path)
    return found

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
    'slate.data',
]
hiddenimports += collect_submodules('slate.gui.plugins')
hiddenimports += collect_submodules('slate.plugins')

# Much of the app imports these lazily, inside functions, so that opening a tab
# does not drag in the whole dashboard. PyInstaller usually finds those, but
# collecting them explicitly removes a class of "works in dev, missing in the
# build" failure.
hiddenimports += collect_submodules('slate.core.domain')
hiddenimports += collect_submodules('slate.core.infra.migrations')
hiddenimports += collect_submodules('slate.gui.tabs.vfx_dashboard_pro')

datas_qasync, binaries_qasync, hiddenimports_qasync = collect_all('qasync')
hiddenimports += hiddenimports_qasync

shared_datas = [
    ('slate/data', 'slate/data'),
    ('slate/assets', 'slate/assets'),
    ('slate/default_config.json', 'slate'),
    ('slate/icons', 'slate/icons'),
    ('slate/resources', 'slate/resources'),
    # FFmpeg is fetched by setup.bat rather than committed - it is a
    # 95MB third-party binary with its own licence. Include it only if
    # it is present, so a build on a machine that has run setup works
    # and a build on one that has not fails with a clear message
    # instead of a PyInstaller stack trace.

    ('slate/core/help_content.json', 'slate/core'),
    ('slate/gui/tabs/vfx_dashboard_pro/config', 'slate/gui/tabs/vfx_dashboard_pro/config'),
    ('slate/gui/tabs/vfx_dashboard_pro/sample_project.xlsx', 'slate/gui/tabs/vfx_dashboard_pro'),
] + datas_qasync

common_excludes = [
    'matplotlib', 'pytest', 'sphinx', 'IPython', 'notebook',
    'PyQt5', 'PyQt5.QtCore', 'PyQt5.QtWidgets', 'PyQt5.QtGui'
]

# 1. Slate Studio (Production Suite)
vfx_a = Analysis(
    ['slate\\vfx_studio_main.py'],
    pathex=[],
    binaries=_optional_binaries() + binaries_qasync,
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
    name='Slate_Studio',
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
    icon=['slate\\icons\\app_icon.ico'],
)

# 2. Slate Operations (HRMS & IT Suite)
ops_a = Analysis(
    ['slate\\studio_ops_main.py'],
    pathex=[],
    binaries=_optional_binaries() + binaries_qasync,
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
    icon=['slate\\icons\\app_icon.ico'],
)

# 3. Slate Central Server
server_a = Analysis(
    ['slate_server\\main.py'],
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
    icon=['slate\\icons\\server_icon.ico'],
)

# 4. Legacy All-in-One Gatekeeper (Backwards Compatibility)
legacy_a = Analysis(
    ['slate\\gatekeeper_main.py'],
    pathex=[],
    binaries=_optional_binaries() + binaries_qasync,
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
    name='Slate',
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
    icon=['slate\\icons\\app_icon.ico'],
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
    name='Slate',
)
