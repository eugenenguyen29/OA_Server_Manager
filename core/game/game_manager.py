import asyncio
import logging
from typing import Callable, Dict

import core.utils.settings as settings


class GameManager:
    """Manages game configuration and bot operations."""

    def __init__(self, send_command_callback: Callable[[str], None]):
        self.send_command = send_command_callback
        self.logger = logging.getLogger(__name__)

        self._current_config = {}
        self._bots_added = False
        self._bot_addition_in_progress = False

    def should_add_bots(self) -> bool:
        return settings.bot_enable and settings.bot_count > 0

    def are_bots_added(self) -> bool:
        return self._bots_added

    def is_bot_addition_in_progress(self) -> bool:
        return self._bot_addition_in_progress

    async def add_bots_to_server_async(self) -> bool:
        """Add bots to the server asynchronously."""
        if self._bots_added:
            self.logger.debug("Bots already added, skipping")
            return True

        if not self.should_add_bots():
            self.logger.info("Bot addition disabled or count is 0")
            return False

        if self._bot_addition_in_progress:
            self.logger.info("Bot addition already in progress")
            return False

        self.logger.info("Starting asynchronous bot addition")
        self._bot_addition_in_progress = True
        try:
            for i in range(settings.bot_count):
                if i < len(settings.bot_names) and settings.bot_names[i]:
                    bot_name = settings.bot_names[i]
                else:
                    bot_names = [
                        "Angelyss",
                        "Arachna",
                        "Major",
                        "Sarge",
                        "Skelebot",
                        "Merman",
                        "Beret",
                        "Kyonshi",
                    ]
                    bot_name = bot_names[i % len(bot_names)]

                self.send_command(f"addbot {bot_name} {settings.bot_difficulty}")
                self.logger.info(
                    f"Added bot {bot_name} with difficulty {settings.bot_difficulty}"
                )
                await asyncio.sleep(0.1)

            self._bots_added = True
            self.send_command(f"say Added {settings.bot_count} bots to the server")
            self.logger.info(f"Successfully added {settings.bot_count} bots")
            return True

        except Exception as e:
            self.logger.error(f"Error adding bots: {e}")
            return False
        finally:
            self._bot_addition_in_progress = False

    def initialize_bot_settings(self, nplayers_threshold: int) -> bool:
        """Initialize bot-related settings."""
        try:
            if self.should_add_bots():
                self.send_command("set bot_minplayers 0")
                self.logger.info(
                    f"Bot settings initialized for {settings.bot_count} bots"
                )
                return True
            return False
        except Exception as e:
            self.logger.error(f"Error initializing bot settings: {e}")
            return False

    def reset_bot_state(self) -> None:
        """Reset bot-related state."""
        self._bots_added = False
        self._bot_addition_in_progress = False
        self.logger.info("Bot state reset")

    def apply_startup_config(self) -> Dict[str, str]:
        """Get startup configuration for server."""
        return {
            "timelimit": str(settings.timelimit),
            "capturelimit": str(settings.fraglimit),
            "g_doWarmup": "1" if settings.enable_warmup else "0",
            "g_warmup": str(settings.warmup_time),
        }

    def apply_default_config(self) -> bool:
        """Apply default game configuration."""
        try:
            self.send_command(f"set timelimit {settings.timelimit}")
            self.send_command(f"set fraglimit {settings.fraglimit}")

            if settings.enable_warmup:
                self.send_command("set g_doWarmup 1")
                self.send_command(f"set g_warmup {settings.warmup_time}")

            self.logger.info("Default game configuration applied")
            return True

        except Exception as e:
            self.logger.error(f"Error applying default config: {e}")
            return False

    def set_flaglimit(self, limit: int) -> bool:
        """Set the capturelimit for matches."""
        try:
            self.send_command(f"say flaglimit set to {limit}")
            self.logger.info(f"Flaglimit set to {limit}")
            return True
        except Exception as e:
            self.logger.error(f"Error setting flaglimit: {e}")
            return False

    def disable_next_round_warmup(self) -> bool:
        """Disable Warm up for next round and send player directly to the match"""
        try:
            self.send_command("set g_doWarmup 0")
            self.logger.info("Warmup disable")
            return True
        except Exception as e:
            self.logger.error(f"Error restarting warmup: {e}")
            return False

    def set_next_round_with_warmup_phase(self) -> bool:
        """Set the next round with a warmup phase."""
        try:
            self.send_command("set g_doWarmup 1")
            self.send_command(f"set g_warmup {settings.warmup_time}")
            self.logger.info("Warmup phase started")
            return True
        except Exception as e:
            self.logger.error(f"Error starting warmup phase: {e}")
            return False

    def restart_map(self) -> bool:
        """Restart the current map."""
        try:
            self.send_command("map_restart")
            self.logger.info("Map restarted")
            return True
        except Exception as e:
            self.logger.error(f"Error restarting map: {e}")
            return False
