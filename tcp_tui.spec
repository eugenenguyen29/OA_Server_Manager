# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for TCP Testing TUI

block_cipher = None

a = Analysis(
    ['tcp_tui.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('tcp_tui.tcss', '.'),
    ],
    hiddenimports=[
        'core.tcp',
        'core.tcp.client',
        'core.tcp.manager',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='tcp-tui',
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
