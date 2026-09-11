# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['qasync', 'psycopg2', 'PySide6.QtCore', 'PySide6.QtWidgets', 'PySide6.QtGui', 'OpenImageIO', 'fileseq', 'PyOpenColorIO', 'opentimelineio', 'opentimelineio.adapters', 'opentimelineio.plugins']
hiddenimports += collect_submodules('ut_vfx.gui.plugins')
hiddenimports += collect_submodules('ut_vfx.plugins')


a = Analysis(
    ['ut_vfx\\gatekeeper_main.py'],
    pathex=[],
    binaries=[],
    datas=[('ut_vfx/data', 'ut_vfx/data'), ('ut_vfx/core/help_content.json', 'ut_vfx/core'), ('ut_vfx/assets', 'ut_vfx/assets'), ('ut_vfx/default_config.json', 'ut_vfx')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt5.QtCore', 'PyQt5.QtWidgets', 'PyQt5.QtGui'],
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
    name='UTVFX_Debug',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
