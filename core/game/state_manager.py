import logging
from enum import Enum
from typing import Callable

import core.utils.settings as settings


class GameState(Enum):
    WAITING = 1
    WARMUP = 2
    RUNNING = 3


class GameStateManager:
    """Reactive game state tracking and match progression."""

    def __init__(self, send_command_callback: Callable[[str], None]):
        self.current_state = GameState.WAITING
        self.round_count: int = 1  # Should start from round 1
        self.warmup_round_count: int = 0
        self.max_rounds: int = len(settings.latencies) * settings.repeats
        self.send_command = send_command_callback
        self.logger = logging.getLogger(__name__)

        self.logger.info(
            f"GameStateManager initialized: latencies={settings.latencies}, repeats={settings.repeats}, max_rounds={self.max_rounds}"
        )

    def handle_warmup_detected(self) -> dict:
        """React to server warmup message - purely reactive state tracking."""
        result = {
            "state_changed": False,
            "actions": [],
        }

        if self.current_state == GameState.WAITING:
            self.current_state = GameState.WARMUP
            result["state_changed"] = True
            self.logger.info("State tracked: WAITING -> WARMUP")
        elif self.current_state == GameState.WARMUP:
            self.logger.info("Warmup restarted")
        elif self.current_state == GameState.RUNNING:
            self.current_state = GameState.WARMUP
            result["state_changed"] = True
            self.logger.info(
                "State tracked: RUNNING -> WARMUP (match restarted with warmup)"
            )
        else:
            self.logger.warning(f"Unexpected warmup from state {self.current_state}")

        return result

    def handle_game_initialization_detected(self) -> dict:
        """React to game initialization - set state to RUNNING."""
        result = {
            "state_changed": False,
            "actions": [],
        }

        self.current_state = GameState.RUNNING

        result["state_changed"] = True

        return result

    def handle_match_start_detected(self) -> dict:
        """React to server match start - purely reactive state tracking."""
        result = {
            "state_changed": False,
            "actions": [],
        }

        if self.current_state == GameState.WARMUP:
            self.current_state = GameState.RUNNING
            result["state_changed"] = True
            result["actions"].extend(["start_match_recording", "apply_latency"])
            self.logger.info(
                f"State tracked: WARMUP -> RUNNING (starting round {self.round_count + 1})"
            )
        else:
            self.logger.warning(
                f"Unexpected match start from state {self.current_state}"
            )

        return result

    def handle_match_shutdown_detected(self) -> dict:
        """React to server shutdown the game match - acknowledge shutdown only."""
        result = {
            "round_completed": False,
            "experiment_finished": False,
            "actions": [],
        }

        if self.current_state == GameState.RUNNING:
            self.round_count += 1

            if self.round_count >= self.max_rounds:
                result["experiment_finished"] = True
                self.logger.info(
                    f"Experiment completed after {self.round_count} rounds (max: {self.max_rounds})"
                )
            else:
                result["round_completed"] = True
                result["actions"].extend(["rotate_latency"])
                self.logger.info(
                    f"Round {self.round_count} completed, preparing round {self.round_count + 1} (max: {self.max_rounds})"
                )

        return result

    def transition_to(self, new_state: GameState) -> None:
        """Transition to a new game state with logging.

        Args:
            new_state: The GameState to transition to.
        """
        old_state = self.current_state
        self.current_state = new_state
        self.logger.info(f"State transition: {old_state.name} -> {new_state.name}")

    def reset_to_waiting(self) -> None:
        """Reset game state to WAITING."""
        self.transition_to(GameState.WAITING)

    def get_current_state(self) -> GameState:
        """Get the current game state."""
        return self.current_state

    def get_round_info(self) -> dict:
        """Get current round information."""

        return {
            "current_round": self.round_count,
            "max_rounds": self.max_rounds,
            "warmup_rounds": self.warmup_round_count,
            "state": self.current_state.name,
        }

    def is_experiment_finished(self) -> bool:
        """Check if the experiment sequence is complete."""
        return (
            self.current_state == GameState.RUNNING
            and self.round_count >= self.max_rounds
        )

    def get_obs_status(self, obs_manager, client_manager) -> dict:
        """Get OBS connection status for all human clients."""
        human_ips = client_manager.get_human_clients()
        if not human_ips:
            return {"connected": 0, "total": 0, "all_connected": False}

        connected_count = sum(
            1 for ip in human_ips if obs_manager.is_client_connected(ip)
        )
        all_connected = connected_count == len(human_ips)

        self.logger.info(f"OBS status: {connected_count}/{len(human_ips)} connected")

        return {
            "connected": connected_count,
            "total": len(human_ips),
            "all_connected": all_connected,
        }
