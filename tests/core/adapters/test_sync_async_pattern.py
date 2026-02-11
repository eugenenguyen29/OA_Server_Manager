"""Tests for unified sync/async command pattern.

This module tests the consolidation of send_command_sync() into the base
GameAdapter class, ensuring all adapters inherit the same implementation
rather than duplicating code.

TDD Phase: RED - These tests define the expected behavior.
"""

from unittest.mock import AsyncMock, MagicMock, patch


class TestBaseAdapterSyncCommand:
    """Test base adapter send_command_sync implementation."""

    def test_base_adapter_has_send_command_sync(self):
        """Base GameAdapter should have send_command_sync method."""
        from core.adapters.base import GameAdapter

        assert hasattr(GameAdapter, "send_command_sync")

    def test_send_command_sync_is_concrete_not_abstract(self):
        """send_command_sync should be a concrete implementation, not abstract."""
        from core.adapters.base import GameAdapter

        # Check that it's not an abstract method
        method = getattr(GameAdapter, "send_command_sync", None)
        assert method is not None
        assert not getattr(method, "__isabstractmethod__", False)

    def test_send_command_sync_signature(self):
        """send_command_sync should accept command string and return None."""
        from core.adapters.base import GameAdapter
        import inspect

        sig = inspect.signature(GameAdapter.send_command_sync)
        params = list(sig.parameters.keys())

        # Should have self and command parameters
        assert "self" in params
        assert "command" in params


class TestSendCommandSyncEventLoopHandling:
    """Test event loop handling in send_command_sync."""

    def test_sync_falls_back_to_asyncio_run_when_no_loop(self):
        """send_command_sync should use asyncio.run() when no loop running."""
        from core.adapters.openarena.adapter import OAGameAdapter
        from core.adapters.base import GameAdapterConfig

        config = GameAdapterConfig(
            game_type="openarena",
            binary_path="/fake/path",
        )
        adapter = OAGameAdapter(config)
        adapter.send_command = AsyncMock()

        # get_running_loop raises RuntimeError when no loop is running
        with patch("asyncio.get_running_loop", side_effect=RuntimeError("no running loop")):
            with patch("asyncio.run") as mock_run:
                adapter.send_command_sync("test command")
                mock_run.assert_called_once()

    def test_sync_uses_run_coroutine_threadsafe_when_loop_running(self):
        """send_command_sync should use run_coroutine_threadsafe when loop running."""
        from core.adapters.amp.adapter import AMPGameAdapter
        from core.adapters.base import GameAdapterConfig

        config = GameAdapterConfig(
            game_type="amp",
            host="http://localhost:8080",
            password="admin:password",
        )
        adapter = AMPGameAdapter(config)
        adapter.send_command = AsyncMock()

        mock_loop = MagicMock()
        with patch("asyncio.get_running_loop", return_value=mock_loop):
            with patch("asyncio.run_coroutine_threadsafe") as mock_rcts:
                adapter.send_command_sync("status")
                mock_rcts.assert_called_once()

    def test_sync_handles_runtime_error(self):
        """send_command_sync should handle RuntimeError (no event loop)."""
        from core.adapters.amp.adapter import AMPGameAdapter
        from core.adapters.base import GameAdapterConfig

        config = GameAdapterConfig(
            game_type="amp",
            host="http://localhost:8080",
            password="admin:password",
        )
        adapter = AMPGameAdapter(config)
        adapter.send_command = AsyncMock()

        with patch("asyncio.get_running_loop", side_effect=RuntimeError("no running loop")):
            with patch("asyncio.run") as mock_run:
                adapter.send_command_sync("test")
                mock_run.assert_called_once()


class TestAdaptersDontDuplicateSyncCommand:
    """Test that adapters don't override send_command_sync unnecessarily."""

    def test_oa_adapter_uses_base_implementation(self):
        """OAGameAdapter should inherit send_command_sync from base."""
        from core.adapters.openarena.adapter import OAGameAdapter
        from core.adapters.base import GameAdapter

        # The method should be inherited, not overridden
        assert OAGameAdapter.send_command_sync is GameAdapter.send_command_sync

    def test_amp_adapter_uses_base_implementation(self):
        """AMPGameAdapter should inherit send_command_sync from base."""
        from core.adapters.amp.adapter import AMPGameAdapter
        from core.adapters.base import GameAdapter

        assert AMPGameAdapter.send_command_sync is GameAdapter.send_command_sync


class TestSendCommandSyncIntegration:
    """Integration tests for send_command_sync behavior."""

    def test_oa_adapter_sync_command_delegates_to_async(self):
        """OAGameAdapter sync command should delegate to async send_command."""
        from core.adapters.openarena.adapter import OAGameAdapter
        from core.adapters.base import GameAdapterConfig

        config = GameAdapterConfig(
            game_type="openarena",
            binary_path="/fake/path",
        )
        adapter = OAGameAdapter(config)

        # Create a mock for the async method
        send_command_mock = AsyncMock(return_value=None)
        adapter.send_command = send_command_mock

        # No running loop -> falls back to asyncio.run which runs the coroutine
        with patch("asyncio.get_running_loop", side_effect=RuntimeError("no running loop")):
            adapter.send_command_sync("test command")

            # Verify send_command was called with the right argument
            send_command_mock.assert_awaited_once_with("test command")

    def test_all_adapters_have_consistent_sync_behavior(self):
        """All adapters should have the same send_command_sync behavior."""
        from core.adapters.openarena.adapter import OAGameAdapter
        from core.adapters.amp.adapter import AMPGameAdapter
        from core.adapters.base import GameAdapterConfig

        # Create all adapter types
        oa_config = GameAdapterConfig(game_type="openarena", binary_path="/fake")
        amp_config = GameAdapterConfig(
            game_type="amp", host="http://localhost:8080", password="admin:pass"
        )

        oa_adapter = OAGameAdapter(oa_config)
        amp_adapter = AMPGameAdapter(amp_config)

        # All should use the same method implementation
        assert (
            type(oa_adapter).send_command_sync is type(amp_adapter).send_command_sync
        )


class TestSendCommandSyncReturnType:
    """Test that send_command_sync returns None."""

    def test_sync_command_returns_none(self):
        """send_command_sync should return None (fire-and-forget)."""
        from core.adapters.openarena.adapter import OAGameAdapter
        from core.adapters.base import GameAdapterConfig

        config = GameAdapterConfig(
            game_type="openarena",
            binary_path="/fake/path",
        )
        adapter = OAGameAdapter(config)
        adapter.send_command = AsyncMock(return_value="response")

        # No running loop -> uses asyncio.run
        with patch("asyncio.get_running_loop", side_effect=RuntimeError("no running loop")):
            result = adapter.send_command_sync("test")

            # send_command_sync should return None even if async returns value
            assert result is None
