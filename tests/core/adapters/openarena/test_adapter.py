"""Tests for OAGameAdapter compositional ownership.

Verifies that OAGameAdapter creates and owns all managers,
dispatches messages correctly, and runs the server loop.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.adapters.base import GameAdapterConfig, MessageType, ParsedMessage
from core.adapters.openarena.adapter import OAGameAdapter
from core.adapters.openarena.message_processor import OAMessageProcessor
from core.game.game_manager import GameManager
from core.game.state_manager import GameStateManager
from core.network.network_manager import NetworkManager
from core.obs.connection_manager import OBSConnectionManager


def _make_adapter() -> OAGameAdapter:
    config = GameAdapterConfig(
        game_type="openarena",
        binary_path="/usr/bin/oa_ded",
        port=27960,
    )
    return OAGameAdapter(config)


class TestOAAdapterCreatesManagers:
    """OAGameAdapter.__init__ must create all sub-managers."""

    def test_creates_message_processor(self):
        adapter = _make_adapter()
        assert isinstance(adapter.message_processor, OAMessageProcessor)

    def test_creates_network_manager(self):
        adapter = _make_adapter()
        assert isinstance(adapter.network_manager, NetworkManager)

    def test_creates_game_manager(self):
        adapter = _make_adapter()
        assert isinstance(adapter.game_manager, GameManager)

    def test_creates_game_state_manager(self):
        adapter = _make_adapter()
        assert isinstance(adapter.game_state_manager, GameStateManager)

    def test_creates_obs_connection_manager(self):
        adapter = _make_adapter()
        assert isinstance(adapter.obs_connection_manager, OBSConnectionManager)

    def test_creates_message_handlers_dict(self):
        adapter = _make_adapter()
        assert isinstance(adapter.message_handlers, dict)
        assert MessageType.CLIENT_CONNECT in adapter.message_handlers
        assert MessageType.CLIENT_DISCONNECT in adapter.message_handlers
        assert MessageType.STATUS_UPDATE in adapter.message_handlers
        assert MessageType.SERVER_SHUTDOWN in adapter.message_handlers


class TestOAAdapterMessageDispatch:
    """process_server_message parses and dispatches correctly."""

    def test_dispatches_client_disconnect(self):
        adapter = _make_adapter()
        handler = MagicMock()
        adapter.message_handlers[MessageType.CLIENT_DISCONNECT] = handler

        adapter.process_server_message("ClientDisconnect: 3")

        handler.assert_called_once()
        parsed = handler.call_args[0][0]
        assert parsed.message_type == MessageType.CLIENT_DISCONNECT
        assert parsed.data["client_id"] == 3

    def test_dispatches_warmup(self):
        adapter = _make_adapter()
        handler = MagicMock()
        adapter.message_handlers[MessageType.WARMUP_START] = handler

        adapter.process_server_message("Warmup: 15")

        handler.assert_called_once()

    def test_unknown_message_no_dispatch(self):
        adapter = _make_adapter()
        # Should not raise for unknown messages
        adapter.process_server_message("some random noise")


class TestOAAdapterStatusHandling:
    """_on_status updates network_manager with discovered clients."""

    def test_on_status_updates_network_manager(self):
        adapter = _make_adapter()
        client_data = {
            "client_id": 1,
            "name": "TestPlayer",
            "ip": "192.168.1.10",
            "type": "HUMAN",
        }
        msg = ParsedMessage(
            MessageType.STATUS_UPDATE,
            "STATUS_COMPLETE",
            {"client_data": [client_data], "status_complete": True},
        )

        adapter._on_status(msg)

        assert 1 in adapter.network_manager.client_type_map
        assert adapter.network_manager.client_name_map[1] == "TestPlayer"

    def test_on_client_disconnect_removes_client(self):
        adapter = _make_adapter()
        # Add a client first
        adapter.network_manager.add_client(
            client_id=2, ip="10.0.0.1", latency=50, name="Player2", is_bot=False
        )
        assert 2 in adapter.network_manager.client_type_map

        msg = ParsedMessage(
            MessageType.CLIENT_DISCONNECT,
            "ClientDisconnect: 2",
            {"client_id": 2},
        )
        adapter._on_client_disconnect(msg)

        assert 2 not in adapter.network_manager.client_type_map


class TestOAAdapterServerLoop:
    """run_server_loop reads messages and processes them."""

    def test_run_server_loop_reads_and_processes(self):
        adapter = _make_adapter()
        messages = ["ClientDisconnect: 1", "some noise", ""]
        call_index = {"i": 0}

        def fake_read():
            idx = call_index["i"]
            call_index["i"] += 1
            if idx < len(messages):
                return messages[idx]
            adapter.request_shutdown()
            return ""

        adapter.read_message_sync = fake_read
        handler = MagicMock()
        adapter.message_handlers[MessageType.CLIENT_DISCONNECT] = handler

        adapter.run_server_loop()

        handler.assert_called_once()

    def test_output_handler_called(self):
        adapter = _make_adapter()
        output = []
        adapter.set_output_handler(lambda msg: output.append(msg))

        messages = ["Hello server", ""]
        call_index = {"i": 0}

        def fake_read():
            idx = call_index["i"]
            call_index["i"] += 1
            if idx < len(messages):
                return messages[idx]
            adapter.request_shutdown()
            return ""

        adapter.read_message_sync = fake_read

        adapter.run_server_loop()

        assert "Hello server" in output


class TestOAAdapterProperties:
    """clients and server_state properties."""

    def test_clients_empty_initially(self):
        adapter = _make_adapter()
        assert adapter.clients == []

    def test_clients_returns_tracked_clients(self):
        adapter = _make_adapter()
        adapter.network_manager.add_client(
            client_id=0, ip="1.2.3.4", latency=20, name="Alice", is_bot=False
        )
        clients = adapter.clients
        assert len(clients) == 1
        assert clients[0]["name"] == "Alice"

    def test_server_state_returns_waiting(self):
        adapter = _make_adapter()
        assert adapter.server_state == "WAITING"


class TestOAAdapterKickCallback:
    """OAGameAdapter provides kick callback to OBSConnectionManager."""

    def test_oa_adapter_provides_kick_callback_to_obs(self):
        adapter = _make_adapter()
        assert adapter.obs_connection_manager._kick_client_callback is not None
        assert (
            adapter.obs_connection_manager._kick_client_callback
            == adapter._kick_client_by_ip
        )

    def test_kick_callback_uses_correct_game_command_oa(self):
        adapter = _make_adapter()
        adapter._network_manager.client_ip_map = {123: "192.168.1.100"}

        with patch.object(adapter, "kick_client", new_callable=AsyncMock) as mock_kick:
            # Set up a fake async loop for run_async
            loop = asyncio.new_event_loop()
            adapter.set_async_loop(loop)

            import threading

            def run_loop():
                loop.run_forever()

            t = threading.Thread(target=run_loop, daemon=True)
            t.start()

            try:
                adapter._kick_client_by_ip("192.168.1.100")
                import time

                time.sleep(0.1)
                mock_kick.assert_called_once_with(123)
            finally:
                loop.call_soon_threadsafe(loop.stop)
                t.join(timeout=1)
                loop.close()

    def test_kick_callback_handles_missing_client_id(self):
        adapter = _make_adapter()
        # No clients registered - should not raise
        adapter._kick_client_by_ip("192.168.1.999")
