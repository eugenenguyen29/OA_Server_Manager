from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.adapters.base import GameAdapter, ParsedMessage


class ShutdownStrategy:
    def handle(self, adapter: GameAdapter, msg: ParsedMessage) -> None:
        raise NotImplementedError


class MatchShutdownStrategy(ShutdownStrategy):
    def handle(self, adapter: GameAdapter, msg: ParsedMessage) -> None:
        adapter.logger.info("ShutdownGame: Match ended completely")

        adapter.run_async(
            adapter.obs_connection_manager.stop_match_recording(
                adapter.game_state_manager
            )
        )

        result = adapter.game_state_manager.handle_match_shutdown_detected()

        if result and "actions" in result:
            self._process_match_shutdown_actions(adapter, result["actions"])

        if result and result.get("experiment_finished"):
            adapter.send_command_sync("killserver")
            adapter.logger.info("Sent killserver command to stop the server")
        else:
            adapter.send_command_sync("say Match completed!")

        adapter.game_state_manager.reset_to_waiting()

    @staticmethod
    def _process_match_shutdown_actions(adapter: GameAdapter, actions: dict) -> None:
        if "rotate_latency" in actions:
            adapter.network_manager.rotate_latencies()


class WarmupShutdownStrategy(ShutdownStrategy):
    def handle(self, adapter: GameAdapter, msg: ParsedMessage) -> None:
        adapter.logger.info("ShutdownGame: Warmup ended, match starting")

        if adapter.insufficient_humans:
            adapter.game_manager.set_next_round_with_warmup_phase()
            return

        result = adapter.game_state_manager.handle_match_start_detected()
        if result and "actions" in result:
            actions = result["actions"]
            if "start_match_recording" in actions:
                adapter.run_async(
                    adapter.obs_connection_manager.start_match_recording(
                        adapter.game_state_manager
                    )
                )
            if "apply_latency" in actions:
                if adapter.network_manager.is_enabled():
                    adapter.network_manager.apply_latency_rules()

        adapter.game_state_manager.reset_to_waiting()
