# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for ASTRID TUI

import os

block_cipher = None

a = Analysis(
    ['tui_main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('tui_main.tcss', '.'),
        ('.env', '.'),
    ],
    hiddenimports=[
        'core.adapters',
        'core.adapters.base',
        'core.adapters.registry',
        'core.adapters.openarena',
        'core.adapters.openarena.adapter',
        'core.adapters.openarena.message_processor',
        'core.adapters.openarena.status_parser',
        'core.adapters.openarena.game_manager',
        'core.adapters.amp',
        'core.adapters.amp.adapter',
        'core.adapters.amp.amp_api_client',
        'core.adapters.amp.message_processor',
        'core.adapters.amp.status_parser',
        'core.adapters.dota2',
        'core.adapters.dota2.adapter',
        'core.adapters.dota2.message_processor',
        'core.adapters.dota2.status_parser',
        'core.adapters.status_parser',
        'core.network.network_manager',
        'core.network.network_utils',
        'core.game.game_manager',
        'core.game.state_manager',
        'core.obs.controller',
        'core.obs.manager',
        'core.obs.connection_manager',
        'core.utils.settings',
        'core.utils.display_utils',
        'core.server.server',
        'core.server.shutdown_strategies',
        'aiohttp',
        'websockets',
        'tabulate',
        'dotenv',
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
    name='astrid-tui',
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
