# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['..\\ut_vfx\\launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('client_config.json', '.'), ('ut_vfx/icons', 'ut_vfx/icons')],
    hiddenimports=['psycopg2', 'psycopg2.pool', 'psycopg2.extras', 'PySide6.QtCore', 'PySide6.QtWidgets', 'PySide6.QtGui', 'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'numpy', 'pandas', 'cv2', 'OpenImageIO', 'keyring', 'cryptography', 'openpyxl'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'pytest', 'sphinx', 'IPython', 'notebook'],
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
    name='UTVFX',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
