"""
OBS Connection Manager - Handles all OBS WebSocket operations.

Extracted from server.py to separate concerns and reduce complexity.
"""

import asyncio
import logging
from typing import Callable, Dict, Optional

from core.adapters.base import ClientTracker
from core.obs.manager import OBSManager
from core.utils.display_utils import DisplayUtils


class OBSConnectionManager:
    """
    Manages OBS WebSocket connections and recording operations.

    Handles both immediate connections when clients join and batch
    connections during warmup phase.
    """

    def __init__(
        self,
        obs_port: int = 4455,
        obs_password: Optional[str] = None,
        obs_timeout: int = 30,
        send_command_callback: Optional[Callable[[str], None]] = None,
        kick_client_callback: Optional[Callable[[str], None]] = None,
    ):
        """
        Initialize OBS Connection Manager.

        Args:
            obs_port: OBS WebSocket port
            obs_password: OBS WebSocket password
            obs_timeout: Connection timeout in seconds
            send_command_callback: Function to send commands to game server
            kick_client_callback: Callback to kick a client by IP (game-specific)
        """
        self.obs_manager = OBSManager(
            obs_port=obs_port, obs_password=obs_password, connection_timeout=obs_timeout
        )
        self.display_utils = DisplayUtils()
        self.send_command = send_command_callback
        self._kick_client_callback = kick_client_callback
        self.logger = logging.getLogger(__name__)

        self._connection_tasks: Dict[str, asyncio.Task] = {}

    async def connect_single_client_immediately(
        self, client_ip: str, client_tracker: ClientTracker
    ) -> bool:
        """
        Connect to a single client's OBS instance immediately upon joining.

        Args:
            client_ip: Client IP address
            client_tracker: Client tracking interface (replaces NetworkManager)

        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.logger.info(f"Attempting immediate OBS connection for {client_ip}")

            if client_ip in self._connection_tasks:
                self._connection_tasks[client_ip].cancel()

            connected = await self.obs_manager.connect_client_obs(client_ip)

            client_tracker.set_obs_status(client_ip, connected)

            if connected:
                self.logger.info(f"✓ OBS connected for client {client_ip}")
                if self.send_command:
                    self.send_command(f"say OBS connected for {client_ip}")

                print(f"\n[OBS CONNECTION SUCCESS] {client_ip}")
                self.display_utils.display_client_table(
                    client_tracker, "UPDATED CLIENT STATUS"
                )
            else:
                self.logger.warning(f"✗ OBS connection failed for client {client_ip}")
                if self.send_command:
                    self.send_command(
                        f"say OBS connection failed for {client_ip} - will be kicked"
                    )

                print(f"\n[OBS CONNECTION FAILED] {client_ip}")
                self.display_utils.display_client_table(
                    client_tracker, "CLIENT STATUS - OBS CONNECTION FAILED"
                )

                return await self._handle_connection_failure(client_ip, client_tracker)

            return connected

        except Exception as e:
            self.logger.error(
                f"Error connecting to OBS for {client_ip}: {e}", exc_info=True
            )
            return False

    async def _handle_connection_failure(
        self, client_ip: str, client_tracker: ClientTracker
    ) -> bool:
        """Handle failed OBS connection by kicking the client.

        Args:
            client_ip: Client IP address
            client_tracker: Client tracking interface

        Returns:
            Always False (connection failed)
        """
        if self._kick_client_callback:
            self._kick_client_callback(client_ip)
            self.logger.info(f"Kick requested for {client_ip} - OBS connection failed")
        else:
            self.logger.warning(f"No kick callback configured for client {client_ip}")

        return False

    async def start_match_recording(self, game_state_manager) -> Dict[str, bool]:
        """Start recording for all connected OBS clients at match start."""
        try:
            if not self.obs_manager.get_connected_clients():
                self.logger.warning("No OBS clients connected for recording")
                return {}

            round_info = game_state_manager.get_round_info()
            self.display_utils.display_match_start(
                round_info["current_round"], round_info["max_rounds"]
            )

            self.logger.info(
                f"Starting recording for match {round_info['current_round']}"
            )
            recording_results = await self.obs_manager.start_all_recordings()

            for ip, success in recording_results.items():
                if success:
                    self.logger.info(f"Recording started for {ip}")
                else:
                    self.logger.warning(f"Failed to start recording for {ip}")

            return recording_results

        except Exception as e:
            self.logger.error(f"Error starting match recording: {e}", exc_info=True)
            return {}

    async def stop_match_recording(self, game_state_manager) -> Dict[str, bool]:
        """Stop recording for all connected OBS clients at match end."""
        try:
            if not self.obs_manager.get_connected_clients():
                return {}

            round_info = game_state_manager.get_round_info()
            self.display_utils.display_match_end(
                round_info["current_round"], round_info["max_rounds"]
            )

            await asyncio.sleep(2)

            self.logger.info(
                f"Stopping recording for match {round_info['current_round']}"
            )
            recording_results = await self.obs_manager.stop_all_recordings()

            for ip, success in recording_results.items():
                if success:
                    self.logger.info(f"Recording stopped for {ip}")
                else:
                    self.logger.warning(f"Failed to stop recording for {ip}")

            return recording_results

        except Exception as e:
            self.logger.error(f"Error stopping match recording: {e}", exc_info=True)
            return {}

    async def disconnect_client(self, client_ip: str):
        """Disconnect a single client's OBS connection."""
        try:
            if client_ip in self._connection_tasks:
                self._connection_tasks[client_ip].cancel()
                del self._connection_tasks[client_ip]

            await self.obs_manager.disconnect_client(client_ip)
            self.logger.info(
                f"OBS connection closed for disconnected client {client_ip}"
            )
        except Exception as e:
            self.logger.error(f"Error disconnecting OBS for {client_ip}: {e}")

    async def cleanup_all(self):
        """Clean up all OBS connections and tasks."""
        try:
            for task in self._connection_tasks.values():
                task.cancel()
            self._connection_tasks.clear()

            await self.obs_manager.disconnect_all()
            self.logger.info("All OBS connections cleaned up")
        except Exception as e:
            self.logger.error(f"Error cleaning up OBS connections: {e}")

    def is_client_connected(self, client_ip: str) -> bool:
        """Check if a client is connected."""
        return self.obs_manager.is_client_connected(client_ip)
