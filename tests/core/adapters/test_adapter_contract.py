"""Contract tests for GameAdapter ABC.

Verifies that all concrete adapters expose the required properties
and methods defined by the compositional adapter interface.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from core.adapters.base import (
    GameAdapterConfig,
    MessageType,
)


def _make_oa_adapter():
    """Create an OAGameAdapter with mocked dependencies."""
    config = GameAdapterConfig(
        game_type="openarena",
        binary_path="/usr/bin/oa_ded",
        port=27960,
    )
    with patch.dict("os.environ", {}, clear=False):
        from core.adapters.openarena.adapter import OAGameAdapter

        adapter = OAGameAdapter(config)
    return adapter


def _make_amp_adapter():
    """Create an AMPGameAdapter with mocked dependencies."""
    config = GameAdapterConfig(
        game_type="amp",
        host="http://localhost:8080",
        password="admin:password",
        port=8080,
        poll_interval=0.1,
    )
    from core.adapters.amp.adapter import AMPGameAdapter

    return AMPGameAdapter(config)


@pytest.fixture(params=["oa", "amp"], ids=["OAGameAdapter", "AMPGameAdapter"])
def adapter(request):
    """Parametrized fixture yielding each concrete adapter."""
    if request.param == "oa":
        return _make_oa_adapter()
    return _make_amp_adapter()


class TestAdapterContractProperties:
    """Every GameAdapter must expose manager properties."""

    def test_adapter_has_network_manager(self, adapter):
        assert hasattr(adapter, "network_manager")
        assert adapter.network_manager is not None

    def test_adapter_has_game_state_manager(self, adapter):
        assert hasattr(adapter, "game_state_manager")
        assert adapter.game_state_manager is not None

    def test_adapter_has_game_manager(self, adapter):
        assert hasattr(adapter, "game_manager")
        assert adapter.game_manager is not None

    def test_adapter_has_obs_connection_manager(self, adapter):
        assert hasattr(adapter, "obs_connection_manager")
        assert adapter.obs_connection_manager is not None

    def test_adapter_has_message_processor(self, adapter):
        assert hasattr(adapter, "message_processor")
        assert adapter.message_processor is not None

    def test_adapter_has_clients_property(self, adapter):
        clients = adapter.clients
        assert isinstance(clients, list)

    def test_adapter_has_server_state_property(self, adapter):
        state = adapter.server_state
        assert isinstance(state, str)


class TestAdapterContractMethods:
    """Every GameAdapter must implement these methods."""

    def test_adapter_set_output_handler(self, adapter):
        handler = MagicMock()
        adapter.set_output_handler(handler)
        assert adapter._output_handler is handler

    def test_adapter_set_async_loop(self, adapter):
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        adapter.set_async_loop(loop)
        assert adapter._async_loop is loop

    def test_adapter_process_server_message(self, adapter):
        # Should not raise - dispatches parsed message to handler
        adapter.process_server_message("some unknown message")

    def test_adapter_has_message_handlers(self, adapter):
        assert hasattr(adapter, "message_handlers")
        assert isinstance(adapter.message_handlers, dict)
        assert MessageType.STATUS_UPDATE in adapter.message_handlers


class TestAdapterIndependence:
    """Adapters must not depend on the legacy Server class."""

    def test_adapter_does_not_depend_on_server_module(self, adapter):
        """Adapter module must not import from core.server.server."""
        import ast
        import inspect

        source_file = inspect.getfile(type(adapter))
        with open(source_file) as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module != "core.server.server", (
                    f"{type(adapter).__name__} imports from core.server.server"
                )
