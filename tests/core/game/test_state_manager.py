"""Tests for state management encapsulation.

Phase 5: Fix State Management Encapsulation
Tests for GameStateManager transition methods and encapsulation verification.
"""

import ast
import pytest
from pathlib import Path

from core.game.state_manager import GameStateManager, GameState


class TestGameStateManagerTransitions:
    """Test state transition methods."""

    def test_has_transition_method(self):
        """GameStateManager should have transition_to method."""
        manager = GameStateManager(send_command_callback=lambda x: None)
        assert hasattr(manager, "transition_to")
        assert callable(manager.transition_to)

    def test_has_reset_to_waiting_method(self):
        """GameStateManager should have reset_to_waiting method."""
        manager = GameStateManager(send_command_callback=lambda x: None)
        assert hasattr(manager, "reset_to_waiting")
        assert callable(manager.reset_to_waiting)

    def test_transition_to_changes_state(self):
        """transition_to should change current_state."""
        manager = GameStateManager(send_command_callback=lambda x: None)
        assert manager.current_state == GameState.WAITING

        manager.transition_to(GameState.WARMUP)
        assert manager.current_state == GameState.WARMUP

    def test_reset_to_waiting_sets_waiting(self):
        """reset_to_waiting should set state to WAITING."""
        manager = GameStateManager(send_command_callback=lambda x: None)

        # First transition to a different state
        manager.transition_to(GameState.RUNNING)
        assert manager.current_state == GameState.RUNNING

        # Now reset to waiting
        manager.reset_to_waiting()
        assert manager.current_state == GameState.WAITING

    def test_transition_from_any_state(self):
        """Should be able to transition from any state to any other state."""
        manager = GameStateManager(send_command_callback=lambda x: None)

        # Test all state transitions
        all_states = [GameState.WAITING, GameState.WARMUP, GameState.RUNNING]

        for from_state in all_states:
            for to_state in all_states:
                manager.transition_to(from_state)
                assert manager.current_state == from_state

                manager.transition_to(to_state)
                assert manager.current_state == to_state


class TestShutdownStrategiesUseTransitionMethod:
    """Test that shutdown strategies use proper encapsulation."""

    def test_no_direct_state_assignment(self):
        """Shutdown strategies should not directly assign to current_state."""
        shutdown_file = (
            Path(__file__).resolve().parents[3] / "core" / "server" / "shutdown_strategies.py"
        )
        if not shutdown_file.exists():
            pytest.skip("shutdown_strategies.py not found")

        source = shutdown_file.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        if target.attr == "current_state":
                            pytest.fail(
                                "Found direct assignment to current_state. "
                                "Use transition_to() or reset_to_waiting() instead."
                            )
