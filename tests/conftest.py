"""Shared pytest fixtures for ASTRID Framework tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def mock_send_command():
    """Fixture that returns a no-op send_command callback."""

    def _send_command(cmd: str) -> None:
        pass

    return _send_command
