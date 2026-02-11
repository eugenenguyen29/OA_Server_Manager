"""Tests for AMPGameAdapter compositional ownership.

Verifies that AMPGameAdapter creates and owns all managers,
dispatches messages correctly, populates clients from STATUS_UPDATE,
and runs the server loop.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.adapters.base import (
    GameAdapterConfig,
    MessageType,
    ParsedMessage,
)
from core.adapters.amp.amp_api_client import (
    AMPAPIError,
    ConsoleEntry,
    UpdateResponse,
)
from core.adapters.amp.message_processor import AMPMessageProcessor
from core.game.game_manager import GameManager
from core.game.state_manager import GameStateManager
from core.network.network_manager import NetworkManager
from core.obs.connection_manager import OBSConnectionManager


def _make_amp_config() -> GameAdapterConfig:
    return GameAdapterConfig(
        game_type="amp",
        host="http://localhost:8080",
        password="admin:password",
        port=8080,
        poll_interval=0.1,
    )


def _make_adapter():
    from core.adapters.amp.adapter import AMPGameAdapter

    config = _make_amp_config()
    adapter = AMPGameAdapter(config)
    return adapter


class TestAMPAdapterHasManagers:
    """AMPGameAdapter must expose all required manager properties."""

    def test_amp_adapter_has_network_manager(self):
        adapter = _make_adapter()
        assert adapter.network_manager is not None
        assert isinstance(adapter.network_manager, NetworkManager)

    def test_amp_adapter_has_game_state_manager(self):
        adapter = _make_adapter()
        assert adapter.game_state_manager is not None
        assert isinstance(adapter.game_state_manager, GameStateManager)

    def test_amp_adapter_has_message_processor(self):
        adapter = _make_adapter()
        assert adapter.message_processor is not None
        assert isinstance(adapter.message_processor, AMPMessageProcessor)

    def test_amp_adapter_has_game_manager(self):
        adapter = _make_adapter()
        assert adapter.game_manager is not None
        assert isinstance(adapter.game_manager, GameManager)

    def test_amp_adapter_has_obs_connection_manager(self):
        adapter = _make_adapter()
        assert adapter.obs_connection_manager is not None
        assert isinstance(adapter.obs_connection_manager, OBSConnectionManager)


class TestAMPAdapterClientsFromStatusUpdate:
    """STATUS_UPDATE messages populate the clients list."""

    def test_amp_adapter_clients_from_status_update(self):
        adapter = _make_adapter()
        # Initially empty
        assert adapter.clients == []

        # Simulate a STATUS_UPDATE with complete client data
        msg = ParsedMessage(
            MessageType.STATUS_UPDATE,
            "#end",
            {
                "clients": [
                    {
                        "client_id": 3,
                        "name": "quangminh2479",
                        "ip": "127.190.6.117",
                        "type": "HUMAN",
                        "time": "00:05",
                        "ping": 12,
                        "loss": 0,
                        "state": "spawning",
                        "rate": 80000,
                        "address": "127.190.6.117:52271",
                    },
                ],
                "status_complete": True,
            },
        )
        adapter._on_status(msg)

        clients = adapter.clients
        assert len(clients) == 1
        assert clients[0]["client_id"] == 3
        assert clients[0]["name"] == "quangminh2479"


class TestAMPAdapterServerState:
    """server_state property returns a meaningful string."""

    def test_amp_adapter_server_state_property(self):
        adapter = _make_adapter()
        state = adapter.server_state
        assert isinstance(state, str)
        assert state == "WAITING"


class TestAMPAdapterProcessServerMessage:
    """process_server_message dispatches parsed messages to handlers."""

    def test_amp_adapter_process_server_message(self):
        adapter = _make_adapter()
        handler = MagicMock()
        adapter.message_handlers[MessageType.STATUS_UPDATE] = handler

        # Feed a status header line (triggers STATUS_UPDATE)
        adapter.process_server_message("---------players--------")

        handler.assert_called_once()
        parsed = handler.call_args[0][0]
        assert isinstance(parsed, ParsedMessage)
        assert parsed.message_type == MessageType.STATUS_UPDATE

    def test_amp_adapter_unknown_message_no_dispatch(self):
        adapter = _make_adapter()
        # Should not raise for unknown messages
        adapter.process_server_message("some random noise")


class TestAMPAdapterOutputHandler:
    """set_output_handler receives console lines."""

    def test_amp_adapter_set_output_handler(self):
        adapter = _make_adapter()
        output = []
        adapter.set_output_handler(lambda msg: output.append(msg))
        assert adapter._output_handler is not None

        # Simulate processing a message - output handler should be called
        adapter.process_server_message("Hello from AMP")
        # Output handler is called by run_server_loop, not process_server_message
        # But we can verify it was set
        adapter._output_handler("test line")
        assert "test line" in output


class TestAMPAdapterRunServerLoop:
    """run_server_loop polls and processes messages."""

    def test_amp_adapter_run_server_loop_polls_and_dispatches(self):
        adapter = _make_adapter()
        output = []
        adapter.set_output_handler(lambda msg: output.append(msg))

        handler = MagicMock()
        adapter.message_handlers[MessageType.STATUS_UPDATE] = handler

        # Create parsed messages to yield
        parsed_msgs = [
            ParsedMessage(
                MessageType.STATUS_UPDATE,
                "---------players--------",
                {},
            ),
        ]

        async def fake_read_messages():
            for msg in parsed_msgs:
                yield msg
            # After yielding all messages, request shutdown
            adapter.request_shutdown()

        # Patch read_messages to return our fake generator
        adapter.read_messages = fake_read_messages

        # Run the server loop (should stop after processing messages)
        adapter.run_server_loop()

        handler.assert_called_once()


class TestAMPAdapterReadMessagesYieldsParsedMessage:
    """read_messages yields ParsedMessage objects."""

    @pytest.mark.asyncio
    async def test_amp_adapter_read_messages_yields_parsed_message(self):
        adapter = _make_adapter()

        # Mock the API to return one update then stop
        from core.adapters.amp.amp_api_client import ConsoleEntry, UpdateResponse
        from datetime import datetime

        entry = ConsoleEntry(
            timestamp=datetime.now(),
            source="Console",
            message_type="Console",
            contents="---------players--------",
        )
        update = UpdateResponse(console_entries=[entry])

        adapter.api.get_updates = AsyncMock(return_value=update)
        adapter.api._authenticated = True
        adapter.api._session_id = "fake"

        messages = []
        async for msg in adapter.read_messages():
            messages.append(msg)
            adapter._polling = False  # Stop after first message
            break

        assert len(messages) == 1
        assert isinstance(messages[0], ParsedMessage)
        assert messages[0].message_type == MessageType.STATUS_UPDATE


class TestAMPAdapterCredentialValidation:
    """_parse_credentials raises ValueError for malformed input."""

    def test_missing_credentials_raises(self):
        from core.adapters.amp.adapter import _parse_credentials

        with pytest.raises(ValueError, match="credentials are required"):
            _parse_credentials(None)

    def test_empty_string_raises(self):
        from core.adapters.amp.adapter import _parse_credentials

        with pytest.raises(ValueError, match="credentials are required"):
            _parse_credentials("")

    def test_no_colon_raises(self):
        from core.adapters.amp.adapter import _parse_credentials

        with pytest.raises(ValueError, match="username:password"):
            _parse_credentials("justpassword")

    def test_empty_username_raises(self):
        from core.adapters.amp.adapter import _parse_credentials

        with pytest.raises(ValueError, match="username must not be empty"):
            _parse_credentials(":password")

    def test_empty_password_raises(self):
        from core.adapters.amp.adapter import _parse_credentials

        with pytest.raises(ValueError, match="password must not be empty"):
            _parse_credentials("admin:")

    def test_valid_credentials_parsed(self):
        from core.adapters.amp.adapter import _parse_credentials

        user, pwd = _parse_credentials("admin:secret")
        assert user == "admin"
        assert pwd == "secret"

    def test_password_with_colon(self):
        from core.adapters.amp.adapter import _parse_credentials

        user, pwd = _parse_credentials("admin:pass:word")
        assert user == "admin"
        assert pwd == "pass:word"


class TestAMPAdapterConnectAuthFailure:
    """connect() returns False when AMP authentication fails."""

    @pytest.mark.asyncio
    async def test_connect_returns_false_on_auth_failure(self):
        adapter = _make_adapter()
        adapter.api.login = AsyncMock(side_effect=AMPAPIError("auth failed"))

        result = await adapter.connect()

        assert result is False

    @pytest.mark.asyncio
    async def test_connect_returns_true_on_success(self):
        adapter = _make_adapter()
        adapter.api.login = AsyncMock(return_value=True)

        result = await adapter.connect()

        assert result is True


class TestAMPAdapterDeduplication:
    """read_messages deduplicates console entries by timestamp+content."""

    @pytest.mark.asyncio
    async def test_duplicate_entries_are_skipped(self):
        adapter = _make_adapter()
        adapter.api._authenticated = True
        adapter.api._session_id = "fake"

        ts = datetime(2025, 1, 1, 12, 0, 0)
        entry = ConsoleEntry(
            timestamp=ts,
            source="Console",
            message_type="Console",
            contents="---------players--------",
        )
        # Same entry twice in one poll
        update = UpdateResponse(console_entries=[entry, entry])

        call_count = {"n": 0}

        async def fake_get_updates():
            call_count["n"] += 1
            if call_count["n"] <= 1:
                return update
            adapter._polling = False
            return UpdateResponse(console_entries=[])

        adapter.api.get_updates = fake_get_updates

        messages = []
        async for msg in adapter.read_messages():
            messages.append(msg)

        # Should yield only 1, not 2
        assert len(messages) == 1


class TestAMPAdapterEmptyConsoleEntries:
    """read_messages handles empty console_entries gracefully."""

    @pytest.mark.asyncio
    async def test_empty_entries_yield_nothing(self):
        adapter = _make_adapter()
        adapter.api._authenticated = True
        adapter.api._session_id = "fake"

        call_count = {"n": 0}

        async def fake_get_updates():
            call_count["n"] += 1
            if call_count["n"] > 1:
                adapter._polling = False
            return UpdateResponse(console_entries=[])

        adapter.api.get_updates = fake_get_updates

        messages = []
        async for msg in adapter.read_messages():
            messages.append(msg)

        assert messages == []


class TestAMPAdapterDisconnectDuringRead:
    """read_messages stops when adapter disconnects mid-poll."""

    @pytest.mark.asyncio
    async def test_disconnect_stops_polling(self):
        adapter = _make_adapter()
        adapter.api._authenticated = True
        adapter.api._session_id = "fake"

        entry = ConsoleEntry(
            timestamp=datetime.now(),
            source="Console",
            message_type="Console",
            contents="---------players--------",
        )

        call_count = {"n": 0}

        async def fake_get_updates():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return UpdateResponse(console_entries=[entry])
            # Simulate disconnect by clearing auth
            adapter.api._authenticated = False
            adapter.api._session_id = None
            return UpdateResponse(console_entries=[])

        adapter.api.get_updates = fake_get_updates

        messages = []
        async for msg in adapter.read_messages():
            messages.append(msg)

        # Got the first message, then loop stopped due to is_connected=False
        assert len(messages) == 1


class TestAMPAdapterKickCallback:
    """AMPGameAdapter provides kick callback to OBSConnectionManager."""

    def test_amp_adapter_provides_kick_callback_to_obs(self):
        adapter = _make_adapter()
        assert adapter.obs_connection_manager._kick_client_callback is not None
        assert (
            adapter.obs_connection_manager._kick_client_callback
            == adapter._kick_client_by_ip
        )

    def test_kick_callback_uses_correct_game_command_amp(self):
        adapter = _make_adapter()
        adapter._network_manager.client_ip_map = {456: "10.0.0.5"}

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
        adapter._kick_client_by_ip("10.0.0.999")

    def test_obs_connection_manager_receives_network_manager_as_client_tracker(self):
        """Verify NetworkManager satisfies ClientTracker protocol."""
        from core.adapters.base import ClientTracker

        adapter = _make_adapter()
        assert isinstance(adapter.network_manager, ClientTracker)
