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

# The bundled PostgreSQL, minus what the server never runs. 'bin', 'lib' and
# 'share' are the working database; pgAdmin 4 and the HTML documentation were
# 210 MB the server never opened.
a.datas += Tree(R('slate_server', 'bin'), prefix='slate_server/bin',
                excludes=['pgAdmin 4', 'doc', 'include', 'symbols',
                          'StackBuilder', '*.pdb'])

pyz = PYZ(a.pure)

# A folder, like the clients - not a single file.
#
# The one-file build unpacked itself into %TEMP%\_MEIxxxx on every start and
# ran from there: PostgreSQL, PgBouncer, Qt, everything. Studios clean %TEMP%
# as routine housekeeping, and doing so while the server was running pulled
# the database binaries out from under it - the server "failed" for no reason
# anyone could see. Installed as a folder, nothing the server needs lives in a
# place that gets cleaned, and it no longer spends its start-up unpacking
# 300 MB.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Slate_Server',
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
    icon=[R('slate', 'icons', 'server_icon.ico')],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Slate_Server',
)
