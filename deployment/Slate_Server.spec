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


a = Analysis(
    [R('slate_server', 'main.py')],
    pathex=[],
    binaries=[],
    datas=[(R('slate_server', 'bin'), 'slate_server/bin')],
    hiddenimports=['PySide6.QtCore', 'PySide6.QtWidgets', 'PySide6.QtGui', 'psycopg2'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Slate_Server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[R('slate', 'icons', 'server_icon.ico')],
)
