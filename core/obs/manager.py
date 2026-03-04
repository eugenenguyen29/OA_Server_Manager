import asyncio
import logging
from typing import Dict, List, Optional

from core.obs.controller import OBSWebSocketClient


class OBSManager:
    """
    Manages multiple OBS WebSocket connections asynchronously.

    Handles connection establishment, recording control, and status monitoring
    for multiple OBS instances corresponding to different game clients.
    """

    def __init__(
        self,
        obs_port: int = 4455,
        obs_password: Optional[str] = None,
        connection_timeout: int = 30,
        max_retries: int = 3,
        reconnect_delay: float = 2.0,
    ):
        """
        Initialize OBS Manager.

        Args:
            obs_port: Default OBS WebSocket port
            obs_password: OBS WebSocket password (if configured)
            connection_timeout: Timeout in seconds for connection attempts
            max_retries: Max reconnection attempts on unexpected disconnect (0=disabled)
            reconnect_delay: Seconds between reconnection attempts
        """
        self.obs_port = obs_port
        self.obs_password = obs_password
        self.connection_timeout = connection_timeout
        self.max_retries = max_retries
        self.reconnect_delay = reconnect_delay
        self.obs_clients: Dict[str, OBSWebSocketClient] = {}
        self._reconnect_tasks: Dict[str, asyncio.Task] = {}
        self._shutting_down = False
        self.logger = logging.getLogger(__name__)

    async def connect_client_obs(
        self,
        client_ip: str,
        obs_port: Optional[int] = None,
        password: Optional[str] = None,
    ) -> bool:
        """
        Connect to a single client's OBS instance.

        Args:
            client_ip: IP address of the client
            obs_port: OBS WebSocket port (uses default if None)
            password: OBS password (uses default if None)

        Returns:
            True if connection successful, False otherwise
        """
        try:
            port = obs_port or self.obs_port
            pwd = password or self.obs_password

            self.logger.info(f"Attempting OBS connection to {client_ip}:{port}")

            # Create OBS client instance
            def _on_client_disconnected():
                if client_ip in self.obs_clients:
                    del self.obs_clients[client_ip]
                    self.logger.warning(f"OBS client {client_ip} disconnected (reader exited)")
                    # Schedule reconnection if not shutting down
                    if (
                        not self._shutting_down
                        and self.max_retries > 0
                        and client_ip not in self._reconnect_tasks
                    ):
                        try:
                            loop = asyncio.get_event_loop()
                            task = loop.create_task(
                                self._reconnect_client(client_ip, port, pwd)
                            )
                            self._reconnect_tasks[client_ip] = task
                        except RuntimeError:
                            pass  # No event loop running

            obs_client = OBSWebSocketClient(
                host=client_ip, port=port, password=pwd,
                on_disconnected=_on_client_disconnected,
            )

            # Attempt connection with timeout
            connected = await asyncio.wait_for(
                obs_client.connect(), timeout=self.connection_timeout
            )

            if connected:
                self.obs_clients[client_ip] = obs_client
                self.logger.info(f"Successfully connected to OBS at {client_ip}")
                return True
            else:
                self.logger.warning(f"Failed to connect to OBS at {client_ip}")
                return False

        except asyncio.TimeoutError:
            self.logger.warning(
                f"OBS connection timeout for {client_ip} ({self.connection_timeout}s)"
            )
            self.logger.warning(
                f"Check if OBS is running at {client_ip}:{port} with WebSocket enabled"
            )
            return False
        except ConnectionRefusedError:
            self.logger.warning(
                f"Connection refused by {client_ip}:{port} - OBS WebSocket not enabled or not running"
            )
            return False
        except Exception as e:
            self.logger.error(
                f"OBS connection error for {client_ip}: {e}", exc_info=True
            )
            return False

    async def _reconnect_client(
        self, client_ip: str, port: int, password: Optional[str]
    ) -> None:
        """Attempt to reconnect a dropped OBS client up to max_retries times."""
        for attempt in range(1, self.max_retries + 1):
            if self._shutting_down:
                break
            self.logger.info(
                f"OBS reconnecting to {client_ip} "
                f"(attempt {attempt}/{self.max_retries})"
            )
            await asyncio.sleep(self.reconnect_delay)
            if self._shutting_down:
                break
            connected = await self.connect_client_obs(client_ip, port, password)
            if connected:
                self.logger.info(f"OBS reconnected to {client_ip}")
                self._reconnect_tasks.pop(client_ip, None)
                return
        self.logger.warning(
            f"OBS reconnection failed for {client_ip} "
            f"after {self.max_retries} attempts"
        )
        self._reconnect_tasks.pop(client_ip, None)

    async def connect_all_clients(
        self, client_ips: List[str], timeout: Optional[int] = None
    ) -> Dict[str, bool]:
        """
        Connect to multiple OBS instances in parallel.

        Args:
            client_ips: List of client IP addresses
            timeout: Override default timeout if specified

        Returns:
            Dictionary mapping IP to connection success status
        """
        if timeout:
            self.connection_timeout = timeout

        self.logger.info(f"Connecting to {len(client_ips)} OBS instances...")

        # Create connection tasks for all clients
        tasks = [self.connect_client_obs(ip) for ip in client_ips]

        # Execute all connections in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Map results to IPs, treating exceptions as False
        connection_results = {}
        for ip, result in zip(client_ips, results):
            if isinstance(result, Exception):
                self.logger.error(f"OBS connection error for {ip}: {result}")
                connection_results[ip] = False
            else:
                connection_results[ip] = result

        # Log summary
        connected_count = sum(1 for success in connection_results.values() if success)
        self.logger.info(
            f"OBS connections: {connected_count}/{len(client_ips)} successful"
        )

        return connection_results

    async def start_recording(self, client_ip: str) -> bool:
        """
        Start recording for a specific client.

        Args:
            client_ip: IP address of the client

        Returns:
            True if recording started successfully
        """
        if client_ip not in self.obs_clients:
            self.logger.warning(f"No OBS connection for {client_ip}")
            return False

        try:
            success = await self.obs_clients[client_ip].start_record()
            if success:
                self.logger.info(f"Recording started for {client_ip}")
            else:
                self.logger.warning(f"Failed to start recording for {client_ip}")
            return success
        except Exception as e:
            self.logger.error(f"Error starting recording for {client_ip}: {e}")
            return False

    async def stop_recording(self, client_ip: str) -> bool:
        """
        Stop recording for a specific client.

        Args:
            client_ip: IP address of the client

        Returns:
            True if recording stopped successfully
        """
        if client_ip not in self.obs_clients:
            self.logger.warning(f"No OBS connection for {client_ip}")
            return False

        try:
            success = await self.obs_clients[client_ip].stop_record()
            if success:
                self.logger.info(f"Recording stopped for {client_ip}")
            else:
                self.logger.warning(f"Failed to stop recording for {client_ip}")
            return success
        except Exception as e:
            self.logger.error(f"Error stopping recording for {client_ip}: {e}")
            return False

    async def start_all_recordings(self) -> Dict[str, bool]:
        """
        Start recording for all connected OBS instances.

        Returns:
            Dictionary mapping IP to recording start success status
        """
        self.logger.info(f"Starting recording for {len(self.obs_clients)} clients")

        tasks = [self.start_recording(ip) for ip in self.obs_clients.keys()]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        recording_results = {}
        for ip, result in zip(list(self.obs_clients.keys()), results):
            if isinstance(result, Exception):
                self.logger.error(f"OBS recording start error for {ip}: {result}")
                recording_results[ip] = False
            else:
                recording_results[ip] = result

        # Log summary
        success_count = sum(1 for success in recording_results.values() if success)
        self.logger.info(
            f"Recording started: {success_count}/{len(self.obs_clients)} successful"
        )

        return recording_results

    async def stop_all_recordings(self) -> Dict[str, bool]:
        """
        Stop recording for all connected OBS instances.

        Returns:
            Dictionary mapping IP to recording stop success status
        """
        self.logger.info(f"Stopping recording for {len(self.obs_clients)} clients")

        tasks = [self.stop_recording(ip) for ip in self.obs_clients.keys()]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        recording_results = {}
        for ip, result in zip(list(self.obs_clients.keys()), results):
            if isinstance(result, Exception):
                self.logger.error(f"OBS recording stop error for {ip}: {result}")
                recording_results[ip] = False
            else:
                recording_results[ip] = result

        # Log summary
        success_count = sum(1 for success in recording_results.values() if success)
        self.logger.info(
            f"Recording stopped: {success_count}/{len(self.obs_clients)} successful"
        )

        return recording_results

    async def get_recording_status(self, client_ip: str) -> Dict:
        """
        Get recording status for a specific client.

        Args:
            client_ip: IP address of the client

        Returns:
            Recording status dictionary or empty dict if not connected
        """
        if client_ip not in self.obs_clients:
            return {"connected": False}

        try:
            status = await self.obs_clients[client_ip].get_record_status()
            status["connected"] = True
            return status
        except Exception as e:
            self.logger.error(f"Error getting status for {client_ip}: {e}")
            return {"connected": False, "error": str(e)}

    async def get_all_recording_status(self) -> Dict[str, Dict]:
        """
        Get recording status for all connected OBS instances.

        Returns:
            Dictionary mapping IP to recording status
        """
        tasks = [self.get_recording_status(ip) for ip in self.obs_clients.keys()]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        status_results = {}
        for ip, result in zip(list(self.obs_clients.keys()), results):
            if isinstance(result, Exception):
                self.logger.error(f"OBS status check error for {ip}: {result}")
                status_results[ip] = {"connected": False, "error": str(result)}
            else:
                status_results[ip] = result

        return status_results

    async def disconnect_client(self, client_ip: str) -> None:
        """
        Disconnect a specific OBS client.

        Args:
            client_ip: IP address of the client to disconnect
        """
        # Cancel any pending reconnection
        task = self._reconnect_tasks.pop(client_ip, None)
        if task:
            task.cancel()
        # Pop before disconnect so on_disconnected callback won't trigger reconnection
        client = self.obs_clients.pop(client_ip, None)
        if client:
            try:
                await client.disconnect()
                self.logger.info(f"Disconnected OBS client: {client_ip}")
            except Exception as e:
                self.logger.error(f"Error disconnecting {client_ip}: {e}")

    async def disconnect_all(self) -> None:
        """Disconnect all OBS clients."""
        self._shutting_down = True
        self.logger.info(f"Disconnecting {len(self.obs_clients)} OBS clients")

        # Cancel all pending reconnection tasks
        for task in self._reconnect_tasks.values():
            task.cancel()
        if self._reconnect_tasks:
            await asyncio.gather(
                *self._reconnect_tasks.values(), return_exceptions=True
            )
        self._reconnect_tasks.clear()

        tasks = [self.disconnect_client(ip) for ip in list(self.obs_clients.keys())]

        await asyncio.gather(*tasks, return_exceptions=True)
        self.obs_clients.clear()
        self.logger.info("All OBS clients disconnected")

    def get_connected_clients(self) -> List[str]:
        """
        Get list of currently connected client IPs.

        Returns:
            List of connected client IP addresses
        """
        return list(self.obs_clients.keys())

    def is_client_connected(self, client_ip: str) -> bool:
        """
        Check if a specific client is connected.

        Args:
            client_ip: IP address to check

        Returns:
            True if client is connected
        """
        return client_ip in self.obs_clients

    def get_connection_count(self) -> int:
        """
        Get number of connected OBS clients.

        Returns:
            Number of connected clients
        """
        return len(self.obs_clients)
