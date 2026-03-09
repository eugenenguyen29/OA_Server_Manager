"""Tests for Dota2GameAdapter TCP retry-after-exhaustion logic.

When a client is already tracked in network_manager but its TCP connection
has dropped (e.g. max_retries exhausted), _process_discovered_client()
should re-attempt TCP.  If TCP is still active, it should skip.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from core.adapters.base import GameAdapterConfig
from core.adapters.dota2.adapter import Dota2GameAdapter


def _make_adapter() -> Dota2GameAdapter:
    config = GameAdapterConfig(game_type="dota2", binary_path="/fake/dota2")
    return Dota2GameAdapter(config)


class TestTCPRetryForTrackedClient:
    def test_tcp_retry_for_tracked_but_disconnected_client(self):
        """Re-attempt TCP when client is tracked but TCP is not connected."""
        adapter = _make_adapter()

        # Pre-register the client (simulating prior discovery)
        adapter._network_manager.add_client(
            client_id=1, ip="1.2.3.4", latency=20, name="test", is_bot=False
        )

        # Mock TCP connection manager
        adapter._tcp_connection_manager.is_client_connected = MagicMock(
            return_value=False
        )
        adapter._tcp_connection_manager.connect_single_client = MagicMock()

        client_data = {"client_id": 1, "name": "test", "ip": "1.2.3.4"}
        adapter._process_discovered_client(client_data)

        adapter._tcp_connection_manager.connect_single_client.assert_called_once_with(
            "1.2.3.4", adapter._network_manager
        )

    def test_no_tcp_retry_when_already_connected(self):
        """Skip TCP re-attempt when client is tracked and TCP is active."""
        adapter = _make_adapter()

        adapter._network_manager.add_client(
            client_id=1, ip="1.2.3.4", latency=20, name="test", is_bot=False
        )

        adapter._tcp_connection_manager.is_client_connected = MagicMock(
            return_value=True
        )
        adapter._tcp_connection_manager.connect_single_client = MagicMock()

        client_data = {"client_id": 1, "name": "test", "ip": "1.2.3.4"}
        adapter._process_discovered_client(client_data)

        adapter._tcp_connection_manager.connect_single_client.assert_not_called()

    def test_no_tcp_retry_when_client_ip_is_none(self):
        """Skip TCP re-attempt when tracked client has no IP (bot)."""
        adapter = _make_adapter()

        # Register a bot (no IP)
        adapter._network_manager.add_client(
            client_id=5, ip=None, latency=None, name="BotSteve", is_bot=True
        )

        adapter._tcp_connection_manager.is_client_connected = MagicMock()
        adapter._tcp_connection_manager.connect_single_client = MagicMock()

        client_data = {"client_id": 5, "name": "BotSteve", "ip": "bot"}
        adapter._process_discovered_client(client_data)

        adapter._tcp_connection_manager.connect_single_client.assert_not_called()
