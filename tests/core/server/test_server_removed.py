"""Tests verifying Server class is no longer required by active code paths.

Phase 4: Server Deprecation and Cleanup.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class TestNoServerImports:
    """Verify that active code paths do not depend on Server class."""

    def test_no_server_import_in_tui(self) -> None:
        """tui_main.py must not reference core.server.server or Server."""
        source = (PROJECT_ROOT / "tui_main.py").read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "core.server.server" not in module, (
                    f"tui_main.py imports from core.server.server: {ast.dump(node)}"
                )
                for alias in node.names:
                    assert alias.name != "Server", (
                        f"tui_main.py imports Server: {ast.dump(node)}"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert "core.server.server" not in alias.name, (
                        f"tui_main.py imports core.server.server: {ast.dump(node)}"
                    )

    def test_no_server_import_in_main(self) -> None:
        """main.py still uses Server (legacy, out of scope for TUI migration).

        This test documents the current state. main.py is NOT part of the
        TUI code path and is expected to still reference Server until it is
        separately migrated.
        """
        source = (PROJECT_ROOT / "main.py").read_text()
        # Confirm main.py still has Server import (expected legacy state)
        assert "from core.server.server import Server" in source, (
            "main.py no longer imports Server -- update this test if main.py was migrated"
        )

    def test_oa_adapter_standalone_no_server_import(self) -> None:
        """OAGameAdapter module must not import from core.server.server."""
        source = (
            PROJECT_ROOT / "core" / "adapters" / "openarena" / "adapter.py"
        ).read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module != "core.server.server", (
                    f"OA adapter imports from core.server.server: {ast.dump(node)}"
                )
                for alias in node.names:
                    assert alias.name != "Server", (
                        f"OA adapter imports Server class: {ast.dump(node)}"
                    )

    def test_amp_adapter_standalone_no_server_import(self) -> None:
        """AMPGameAdapter module must not import from core.server.server."""
        source = (PROJECT_ROOT / "core" / "adapters" / "amp" / "adapter.py").read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module != "core.server.server", (
                    f"AMP adapter imports from core.server.server: {ast.dump(node)}"
                )

    def test_adapters_instantiate_without_server(self) -> None:
        """Both adapters can be instantiated without importing Server."""
        from core.adapters.base import GameAdapterConfig
        from core.adapters.openarena.adapter import OAGameAdapter
        from core.adapters.amp.adapter import AMPGameAdapter

        oa_config = GameAdapterConfig(
            game_type="openarena",
            binary_path="/usr/bin/oa_ded",
            port=27960,
        )
        oa = OAGameAdapter(oa_config)
        assert oa.network_manager is not None
        assert oa.game_state_manager is not None

        amp_config = GameAdapterConfig(
            game_type="amp",
            host="http://localhost:8080",
            password="admin:password",
            port=8080,
            poll_interval=0.1,
        )
        amp = AMPGameAdapter(amp_config)
        assert amp.network_manager is not None
        assert amp.game_state_manager is not None

    def test_shutdown_strategies_accept_adapter_not_server(self) -> None:
        """Shutdown strategies use GameAdapter type hint, not Server."""
        source = (
            PROJECT_ROOT / "core" / "server" / "shutdown_strategies.py"
        ).read_text()
        assert "GameAdapter" in source, (
            "Shutdown strategies should reference GameAdapter"
        )
        # Should NOT have a runtime import of Server
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module != "core.server.server", (
                    f"shutdown_strategies imports from core.server.server: {ast.dump(node)}"
                )


class TestServerDeprecation:
    """Verify the Server class has a deprecation warning."""

    def test_server_class_has_deprecation_warning(self) -> None:
        """Server.__init__ should emit a DeprecationWarning."""
        from core.server.server import Server

        with pytest.warns(DeprecationWarning, match="Server class is deprecated"):
            Server()
