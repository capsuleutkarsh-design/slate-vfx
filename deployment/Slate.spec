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



import os as _os

def _optional_binaries():
    """Third-party binaries setup.bat downloads. Skipped when absent."""
    found = []
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        path = R("slate", "bin", name)
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
# core.infra as well: studio_policy is imported inside a function in
# app_context, and a module only reached that way is exactly the kind that
# builds fine and is missing at runtime.
hiddenimports += collect_submodules('slate.core.infra')
hiddenimports += collect_submodules('slate.core.infra.migrations')
hiddenimports += collect_submodules('slate.gui.tabs.vfx_dashboard_pro')

datas_qasync, binaries_qasync, hiddenimports_qasync = collect_all('qasync')
hiddenimports += hiddenimports_qasync

shared_datas = [
    (R('slate', 'data'), 'slate/data'),
    (R('slate', 'assets'), 'slate/assets'),
    (R('slate', 'default_config.json'), 'slate'),
    (R('slate', 'icons'), 'slate/icons'),
    (R('slate', 'resources'), 'slate/resources'),
    # FFmpeg is fetched by setup.bat rather than committed - it is a
    # 95MB third-party binary with its own licence. Include it only if
    # it is present, so a build on a machine that has run setup works
    # and a build on one that has not fails with a clear message
    # instead of a PyInstaller stack trace.

    (R('slate', 'core', 'help_content.json'), 'slate/core'),
    (R('slate', 'gui', 'tabs', 'vfx_dashboard_pro', 'config'), 'slate/gui/tabs/vfx_dashboard_pro/config'),
    (R('slate', 'gui', 'tabs', 'vfx_dashboard_pro', 'sample_project.xlsx'), 'slate/gui/tabs/vfx_dashboard_pro'),
] + datas_qasync

common_excludes = [
    'matplotlib', 'pytest', 'sphinx', 'IPython', 'notebook',
    'PyQt5', 'PyQt5.QtCore', 'PyQt5.QtWidgets', 'PyQt5.QtGui'
]

# 1. Slate Studio (Production Suite)
vfx_a = Analysis(
    [R('slate', 'vfx_studio_main.py')],
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
    icon=[R('slate', 'icons', 'app_icon.ico')],
)

# 2. Slate Operations (HRMS & IT Suite)
ops_a = Analysis(
    [R('slate', 'studio_ops_main.py')],
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
    name='Slate_Ops',
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
    icon=[R('slate', 'icons', 'app_icon.ico')],
)

# 3. The server is NOT built here.
#
# It ships as a one-file executable from Slate_Server.spec, which is what
# setup_slate_server.iss installs, and both client installers exclude
# Slate_Server.exe from this folder. A fourth full Analysis of the package for
# an executable nothing installed was a quarter of every build's time.

# 4. Legacy All-in-One Gatekeeper (Backwards Compatibility)
legacy_a = Analysis(
    [R('slate', 'gatekeeper_main.py')],
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
    # Windowed like its siblings. It was the only one of the four built with a
    # console, so the legacy launcher opened a black terminal behind the app.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[R('slate', 'icons', 'app_icon.ico')],
)

# 5. The sidecar updater.
#
# This has to be its own executable rather than part of the app, because its job
# is to overwrite the app: it runs after Slate has exited, replaces the files and
# starts the new build. A process cannot replace itself while it is running.
#
# sidecar_engine.py looks for it as SlateUpdater.exe next to the running
# executable, and logs "expected in dev but fatal in production" when it is
# missing - the update then stages and can never apply. So it is built here and
# collected into the same folder as the rest.
#
# updater_script.py is deliberately nothing but the standard library, so this
# Analysis stays small and does not drag Qt or the database drivers into a tool
# that only copies files around.
updater_a = Analysis(
    [R('slate', 'core', 'updater', 'updater_script.py')],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=common_excludes,
    noarchive=False,
    optimize=0,
)
updater_pyz = PYZ(updater_a.pure)
updater_exe = EXE(
    updater_pyz,
    updater_a.scripts,
    [],
    exclude_binaries=True,
    name='SlateUpdater',
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
    icon=[R('slate', 'icons', 'app_icon.ico')],
)

# Collective distribution folder containing all executables and shared dependencies
coll = COLLECT(
    vfx_exe,
    vfx_a.binaries,
    vfx_a.datas,
    ops_exe,
    ops_a.binaries,
    ops_a.datas,
    legacy_exe,
    legacy_a.binaries,
    legacy_a.datas,
    updater_exe,
    updater_a.binaries,
    updater_a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Slate',
)
