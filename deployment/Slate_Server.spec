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
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = [
    'PySide6.QtCore', 'PySide6.QtWidgets', 'PySide6.QtGui', 'psycopg2',
    'psutil',
    # The web API runs inside the server process now (slate_server/core/
    # api_server.py) instead of being launched through a Python that an
    # installed machine does not have. uvicorn picks its event loop and
    # protocol classes by name at run time, so they have to be collected.
    'fastapi', 'uvicorn',
]
hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('slate.api')
hiddenimports += collect_submodules('slate.core.updater')

a = Analysis(
    [R('slate_server', 'main.py')],
    pathex=[],
    binaries=[],
    datas=[
        # Without this the server ships with no settings at all. It then has no
        # database password, cannot create the accounts, and hardens the cluster
        # regardless - which is how an install ends up with a database that
        # nothing, including the server itself, can ever log in to.
        (R('slate', 'default_config.json'), 'slate'),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'pytest', 'sphinx', 'IPython', 'notebook',
              'PyQt5', 'PyQt5.QtCore', 'PyQt5.QtWidgets', 'PyQt5.QtGui'],
    noarchive=False,
    optimize=0,
)

# The bundled PostgreSQL, minus what the server never runs. A one-file build
# unpacks everything it carries to %TEMP% on every start, and the folder held
# 190 MB of pgAdmin 4 and 20 MB of HTML documentation beside the 70 MB of
# binaries that matter. 'bin', 'lib' and 'share' are the working database.
a.datas += Tree(R('slate_server', 'bin'), prefix='slate_server/bin',
                excludes=['pgAdmin 4', 'doc', 'include', 'symbols',
                          'StackBuilder', '*.pdb'])

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
