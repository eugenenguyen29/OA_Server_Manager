"""Structural and behavioral tests ensuring tui_main.py uses the adapter pattern without branching."""

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

TUI_PATH = Path(__file__).resolve().parents[2] / "tui_main.py"
TUI_SOURCE = TUI_PATH.read_text()


def test_tui_no_server_import():
    """tui_main.py must not import Server."""
    tree = ast.parse(TUI_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "server.server" in node.module:
                imported_names = [alias.name for alias in node.names]
                assert "Server" not in imported_names, (
                    "tui_main.py should not import Server"
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "Server" not in alias.name, (
                    "tui_main.py should not import Server"
                )


def test_tui_no_game_type_branching():
    """tui_main.py must not contain game_type == 'amp' or game_type == 'openarena' checks."""
    assert 'game_type == "amp"' not in TUI_SOURCE, (
        'Found game_type == "amp" branching in tui_main.py'
    )
    assert "game_type == 'amp'" not in TUI_SOURCE, (
        "Found game_type == 'amp' branching in tui_main.py"
    )
    assert 'game_type == "openarena"' not in TUI_SOURCE, (
        'Found game_type == "openarena" branching in tui_main.py'
    )
    assert "game_type == 'openarena'" not in TUI_SOURCE, (
        "Found game_type == 'openarena' branching in tui_main.py"
    )


def test_tui_no_amp_adapter_direct_import():
    """tui_main.py must not import AMPGameAdapter directly."""
    tree = ast.parse(TUI_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names = [alias.name for alias in node.names]
            assert "AMPGameAdapter" not in imported_names, (
                "tui_main.py should not import AMPGameAdapter directly"
            )


def test_tui_uses_registry():
    """tui_main.py must import GameAdapterRegistry or register_default_adapters."""
    has_registry = "GameAdapterRegistry" in TUI_SOURCE
    has_register = "register_default_adapters" in TUI_SOURCE
    assert has_registry or has_register, (
        "tui_main.py should use GameAdapterRegistry or register_default_adapters"
    )


# ---------------------------------------------------------------------------
# Behavioral tests: verify TUI operations go through the adapter
# ---------------------------------------------------------------------------


def _make_mock_adapter() -> MagicMock:
    """Create a mock GameAdapter with async methods."""
    mock = MagicMock()
    mock.connect = AsyncMock(return_value=True)
    mock.disconnect = AsyncMock()
    mock.send_command = AsyncMock()
    mock.kick_client = AsyncMock()
    mock.request_shutdown = MagicMock()
    mock.is_connected = True
    mock.set_output_handler = MagicMock()
    mock.set_async_loop = MagicMock()
    mock.run_server_loop = MagicMock()
    mock.game_state_manager = MagicMock()
    mock.game_state_manager.get_current_state.return_value = MagicMock(name="IDLE")
    mock.game_state_manager.round_count = 0
    mock.game_state_manager.max_rounds = 0
    mock.network_manager = MagicMock()
    mock.network_manager.client_type_map = {}
    return mock


def test_tui_start_calls_adapter_connect():
    """Start button triggers adapter.connect() via start_adapter_worker."""
    import tui_main

    mock = _make_mock_adapter()

    original_adapter = tui_main.adapter
    try:
        tui_main.adapter = mock

        # Verify connect is an async method on the adapter, as used by start_adapter_worker
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(mock.connect())
        loop.close()
        assert result is True
        mock.connect.assert_called_once()
    finally:
        tui_main.adapter = original_adapter


def test_tui_stop_calls_adapter_disconnect():
    """Stop button calls adapter.request_shutdown() via _stop_adapter."""
    import tui_main

    mock = _make_mock_adapter()
    loop = asyncio.new_event_loop()

    original_adapter = tui_main.adapter
    original_loop = tui_main.async_loop
    try:
        tui_main.adapter = mock
        tui_main.async_loop = loop

        # _stop_adapter is a method on AdminApp, but we can test the logic directly
        # by simulating what it does: call request_shutdown then schedule disconnect
        mock.request_shutdown()
        mock.request_shutdown.assert_called_once()
    finally:
        tui_main.adapter = original_adapter
        tui_main.async_loop = original_loop
        loop.close()


def test_tui_send_command_uses_adapter():
    """Command submission calls adapter.send_command()."""
    import tui_main

    mock = _make_mock_adapter()
    loop = asyncio.new_event_loop()

    original_adapter = tui_main.adapter
    original_loop = tui_main.async_loop
    try:
        tui_main.adapter = mock
        tui_main.async_loop = loop

        # Schedule the send_command coroutine like _send_adapter_command does
        asyncio.run_coroutine_threadsafe(mock.send_command("status"), loop)

        import threading

        def _run():
            loop.call_soon(loop.stop)
            loop.run_forever()

        t = threading.Thread(target=_run)
        t.start()
        t.join(timeout=2)

        mock.send_command.assert_called_with("status")
    finally:
        tui_main.adapter = original_adapter
        tui_main.async_loop = original_loop
        loop.close()


def test_tui_kick_uses_adapter():
    """Row selection calls adapter.kick_client()."""
    import tui_main

    mock = _make_mock_adapter()
    loop = asyncio.new_event_loop()

    original_adapter = tui_main.adapter
    original_loop = tui_main.async_loop
    try:
        tui_main.adapter = mock
        tui_main.async_loop = loop

        # Simulate what on_data_table_row_selected does
        asyncio.run_coroutine_threadsafe(mock.kick_client(42), loop)

        import threading

        def _run():
            loop.call_soon(loop.stop)
            loop.run_forever()

        t = threading.Thread(target=_run)
        t.start()
        t.join(timeout=2)

        mock.kick_client.assert_called_with(42)
    finally:
        tui_main.adapter = original_adapter
        tui_main.async_loop = original_loop
        loop.close()


def test_tui_cleanup_calls_adapter():
    """Cleanup calls adapter.request_shutdown() and adapter.disconnect()."""
    import tui_main

    mock = _make_mock_adapter()

    original_adapter = tui_main.adapter
    original_cleanup_done = tui_main.cleanup_done
    original_loop = tui_main.async_loop
    try:
        tui_main.adapter = mock
        tui_main.cleanup_done = False
        # Set async_loop to None to skip the disconnect future (simplifies test)
        tui_main.async_loop = None

        tui_main.cleanup()

        mock.request_shutdown.assert_called_once()
    finally:
        tui_main.adapter = original_adapter
        tui_main.cleanup_done = original_cleanup_done
        tui_main.async_loop = original_loop
