# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['qasync', 'psycopg2', 'PySide6.QtCore', 'PySide6.QtWidgets', 'PySide6.QtGui', 'OpenImageIO', 'fileseq', 'PyOpenColorIO', 'opentimelineio', 'opentimelineio.adapters', 'opentimelineio.plugins']
hiddenimports += collect_submodules('slate.gui.plugins')
hiddenimports += collect_submodules('slate.plugins')


a = Analysis(
    ['slate\\gatekeeper_main.py'],
    pathex=[],
    binaries=[],
    datas=[('slate/data', 'slate/data'), ('slate/core/help_content.json', 'slate/core'), ('slate/assets', 'slate/assets'), ('slate/default_config.json', 'slate')],
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
    name='Slate_Debug',
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
