"""Tests for Dota2GameAdapter compositional ownership.

Verifies that Dota2GameAdapter creates and owns all managers,
dispatches messages correctly, populates clients from STATUS_UPDATE,
and runs the server loop.
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

from core.adapters.base import (
    ConnectionType,
    GameAdapterConfig,
    MessageType,
    ParsedMessage,
)
from core.adapters.dota2.adapter import Dota2GameAdapter
from core.adapters.dota2.message_processor import Dota2MessageProcessor
from core.game.game_manager import GameManager
from core.game.state_manager import GameStateManager
from core.network.network_manager import NetworkManager
from core.obs.connection_manager import OBSConnectionManager


def _make_adapter() -> Dota2GameAdapter:
    config = GameAdapterConfig(game_type="dota2", binary_path="/fake/dota2")
    return Dota2GameAdapter(config)


class TestDota2AdapterCreatesManagers:
    """Dota2GameAdapter.__init__ must create all sub-managers."""

    def test_creates_message_processor(self):
        adapter = _make_adapter()
        assert isinstance(adapter.message_processor, Dota2MessageProcessor)

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
        assert MessageType.STATUS_UPDATE in adapter.message_handlers
        # Dota2 only handles STATUS_UPDATE (unlike OA which handles many types)
        assert len(adapter.message_handlers) == 1


class TestDota2AdapterProperties:
    """clients, server_state, connection_type, and related properties."""

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

    def test_connection_type_is_subprocess(self):
        adapter = _make_adapter()
        assert adapter.connection_type == ConnectionType.SUBPROCESS

    def test_is_connected_false_when_no_process(self):
        adapter = _make_adapter()
        assert adapter.is_connected is False

    def test_insufficient_humans_initially_true(self):
        adapter = _make_adapter()
        # nplayers_threshold defaults from settings (>= 1), 0 humans tracked
        adapter.nplayers_threshold = 1
        adapter._update_insufficient_humans()
        assert adapter.insufficient_humans is True


class TestDota2AdapterMessageDispatch:
    """process_server_message parses and dispatches correctly."""

    def test_dispatches_status_update(self):
        adapter = _make_adapter()
        handler = MagicMock()
        adapter.message_handlers[MessageType.STATUS_UPDATE] = handler

        # Feed a status header line (triggers STATUS_UPDATE via AMPMessageProcessor)
        adapter.process_server_message("---------players--------")

        handler.assert_called_once()
        parsed = handler.call_args[0][0]
        assert isinstance(parsed, ParsedMessage)
        assert parsed.message_type == MessageType.STATUS_UPDATE

    def test_unknown_message_no_dispatch(self):
        adapter = _make_adapter()
        # Should not raise for unknown messages
        adapter.process_server_message("some random noise")


class TestDota2AdapterStatusHandling:
    """_on_status updates network_manager with discovered clients."""

    def test_on_status_registers_human_client(self):
        adapter = _make_adapter()
        msg = ParsedMessage(
            MessageType.STATUS_UPDATE,
            "#end",
            {
                "clients": [
                    {
                        "client_id": 3,
                        "name": "TestPlayer",
                        "ip": "192.168.1.10",
                        "type": "HUMAN",
                    },
                ],
                "status_complete": True,
            },
        )

        adapter._on_status(msg)

        assert 3 in adapter.network_manager.client_type_map
        assert adapter.network_manager.client_name_map[3] == "TestPlayer"

    def test_on_status_registers_bot_client(self):
        adapter = _make_adapter()
        msg = ParsedMessage(
            MessageType.STATUS_UPDATE,
            "#end",
            {
                "clients": [
                    {
                        "client_id": 5,
                        "name": "BotSteve",
                        "ip": "bot",
                        "type": "BOT",
                    },
                ],
                "status_complete": True,
            },
        )

        adapter._on_status(msg)

        assert 5 in adapter.network_manager.client_type_map
        assert adapter.network_manager.client_name_map[5] == "BotSteve"

    def test_on_status_skips_already_tracked(self):
        adapter = _make_adapter()
        # Pre-register the client
        adapter.network_manager.add_client(
            client_id=3, ip="192.168.1.10", latency=20, name="TestPlayer", is_bot=False
        )

        msg = ParsedMessage(
            MessageType.STATUS_UPDATE,
            "#end",
            {
                "clients": [
                    {
                        "client_id": 3,
                        "name": "TestPlayer",
                        "ip": "192.168.1.10",
                        "type": "HUMAN",
                    },
                ],
                "status_complete": True,
            },
        )

        # Should not raise or duplicate
        adapter._on_status(msg)
        assert adapter.network_manager.client_type_map[3] is not None


class TestDota2AdapterServerLoop:
    """run_server_loop reads messages and processes them."""

    def test_run_server_loop_reads_and_processes(self):
        adapter = _make_adapter()
        messages = ["---------players--------", None]
        call_index = {"i": 0}

        def fake_read():
            idx = call_index["i"]
            call_index["i"] += 1
            if idx < len(messages):
                return messages[idx]
            adapter.request_shutdown()
            return None

        adapter.read_message_sync = fake_read
        handler = MagicMock()
        adapter.message_handlers[MessageType.STATUS_UPDATE] = handler

        adapter.run_server_loop()

        handler.assert_called_once()

    def test_output_handler_called(self):
        adapter = _make_adapter()
        output = []
        adapter.set_output_handler(lambda msg: output.append(msg))

        messages = ["Hello server", None]
        call_index = {"i": 0}

        def fake_read():
            idx = call_index["i"]
            call_index["i"] += 1
            if idx < len(messages):
                return messages[idx]
            adapter.request_shutdown()
            return None

        adapter.read_message_sync = fake_read

        adapter.run_server_loop()

        assert "Hello server" in output


class TestDota2AdapterKickCallback:
    """Dota2GameAdapter provides kick callback to OBSConnectionManager."""

    def test_provides_kick_callback_to_obs(self):
        adapter = _make_adapter()
        assert adapter.obs_connection_manager._kick_client_callback is not None
        assert (
            adapter.obs_connection_manager._kick_client_callback
            == adapter._kick_client_by_ip
        )

    def test_kick_callback_uses_correct_command(self):
        adapter = _make_adapter()
        adapter._network_manager.client_ip_map = {456: "10.0.0.5"}
        adapter._network_manager._ip_to_client_id = {"10.0.0.5": 456}

        with patch.object(adapter, "kick_client", new_callable=AsyncMock) as mock_kick:
            loop = asyncio.new_event_loop()
            adapter.set_async_loop(loop)

            t = threading.Thread(target=loop.run_forever, daemon=True)
            t.start()

            try:
                adapter._kick_client_by_ip("10.0.0.5")
                time.sleep(0.1)
                mock_kick.assert_called_once_with(456)
            finally:
                loop.call_soon_threadsafe(loop.stop)
                t.join(timeout=1)
                loop.close()

    def test_kick_callback_handles_missing_client_id(self):
        adapter = _make_adapter()
        # No clients registered - should not raise
        adapter._kick_client_by_ip("192.168.1.999")


class TestDota2AdapterLifecycle:
    """Lifecycle methods: start, stop, dispose, shutdown."""

    def test_start_server_no_binary_path_returns_false(self):
        config = GameAdapterConfig(game_type="dota2", binary_path=None)
        adapter = Dota2GameAdapter(config)
        assert adapter.start_server() is False

    def test_stop_server_sets_shutdown_event(self):
        adapter = _make_adapter()
        adapter.stop_server()
        assert adapter.is_shutdown_requested() is True

    def test_dispose_stops_server(self):
        adapter = _make_adapter()
        adapter.dispose()
        assert adapter.is_shutdown_requested() is True

    def test_request_shutdown_sets_event(self):
        adapter = _make_adapter()
        assert adapter.is_shutdown_requested() is False
        adapter.request_shutdown()
        assert adapter.is_shutdown_requested() is True

    def test_is_shutdown_requested(self):
        adapter = _make_adapter()
        assert adapter.is_shutdown_requested() is False
        adapter._shutdown_event.set()
        assert adapter.is_shutdown_requested() is True
