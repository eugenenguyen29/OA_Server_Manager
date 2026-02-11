"""Tests for ClientTracker Protocol and OBSConnectionManager kick callback."""

import inspect
from typing import Any, List, Optional
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.adapters.base import ClientTracker
from core.network.network_manager import NetworkManager
from core.obs.connection_manager import OBSConnectionManager


def test_client_tracker_protocol_has_required_methods() -> None:
    """Verify ClientTracker Protocol defines all 5 required methods."""
    assert hasattr(ClientTracker, "set_obs_status")
    assert hasattr(ClientTracker, "get_client_id_by_ip")
    assert hasattr(ClientTracker, "get_client_info_table")
    assert hasattr(ClientTracker, "get_human_count")
    assert hasattr(ClientTracker, "get_bot_count")


def test_network_manager_satisfies_client_tracker_protocol() -> None:
    """NetworkManager should satisfy ClientTracker Protocol."""
    nm = NetworkManager()
    assert isinstance(nm, ClientTracker)


def test_obs_connection_manager_accepts_kick_callback() -> None:
    """OBSConnectionManager constructor should accept kick_client_callback parameter."""

    def my_kick(ip: str) -> None:
        pass

    mgr = OBSConnectionManager(kick_client_callback=my_kick)
    assert mgr._kick_client_callback is my_kick


def test_obs_connection_manager_kick_callback_optional() -> None:
    """kick_client_callback should default to None."""
    mgr = OBSConnectionManager()
    assert mgr._kick_client_callback is None


# --- Phase 2 Tests ---


@pytest.mark.asyncio
async def test_connection_failure_invokes_kick_callback() -> None:
    """When connection fails, kick_callback should be called with client_ip."""
    kick_callback = Mock()
    obs_mgr = OBSConnectionManager(kick_client_callback=kick_callback)
    client_tracker = Mock(spec=ClientTracker)

    result = await obs_mgr._handle_connection_failure("192.168.1.100", client_tracker)

    assert result is False
    kick_callback.assert_called_once_with("192.168.1.100")


@pytest.mark.asyncio
async def test_connection_failure_without_callback_returns_false() -> None:
    """When kick_callback is None, _handle_connection_failure returns False gracefully."""
    obs_mgr = OBSConnectionManager(kick_client_callback=None)
    client_tracker = Mock(spec=ClientTracker)

    result = await obs_mgr._handle_connection_failure("10.0.0.1", client_tracker)

    assert result is False


def test_no_hardcoded_game_commands_in_handle_failure() -> None:
    """No 'clientkick' or 'kickid' strings should appear in connection_manager.py source."""
    source = inspect.getsource(OBSConnectionManager._handle_connection_failure)
    assert "clientkick" not in source
    assert "kickid" not in source


@pytest.mark.asyncio
async def test_client_tracker_methods_called_correctly() -> None:
    """Mock ClientTracker verifies set_obs_status is called during connect flow."""
    client_tracker = Mock(spec=ClientTracker)
    obs_mgr = OBSConnectionManager()

    with patch.object(
        obs_mgr.obs_manager,
        "connect_client_obs",
        new_callable=AsyncMock,
        return_value=True,
    ):
        with patch.object(obs_mgr.display_utils, "display_client_table"):
            result = await obs_mgr.connect_single_client_immediately(
                "192.168.1.50", client_tracker
            )

    assert result is True
    client_tracker.set_obs_status.assert_called_once_with("192.168.1.50", True)
