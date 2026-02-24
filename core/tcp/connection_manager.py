"""TCP Connection Manager - Layer 3 game integration for per-player TCP connections.

Mirrors the OBS 3-layer pattern:
- Layer 1: TCPClient (client.py) — raw socket, single connection
- Layer 2: TCPClientManager (manager.py) — multi-client pool
- Layer 3: TCPConnectionManager (this file) — game integration via ClientTracker

Key differences from OBSConnectionManager:
- Sync, not async — TCPClientManager uses threads, not asyncio
- No kick on failure — just log and update status
"""

import logging
import threading
from typing import Callable, Dict, Optional

from core.adapters.base import ClientTracker
from core.tcp.manager import TCPClientManager


class TCPConnectionManager:
    """Manages per-player TCP connections with game integration.

    Connects to each player's TCP server when they join the game.
    Updates ClientTracker with connection status via callbacks.
    Does NOT kick players on connection failure (unlike OBS).
    """

    def __init__(
        self,
        tcp_port: int = 5000,
        send_command_callback: Optional[Callable[[str], None]] = None,
    ):
        self.tcp_manager = TCPClientManager(
            tcp_port=tcp_port,
            reconnect=False,
            on_client_connected=self._on_connected,
            on_client_disconnected=self._on_disconnected,
        )
        self.send_command = send_command_callback
        self._tracker_lock = threading.Lock()
        self._client_tracker_map: Dict[str, ClientTracker] = {}
        self.logger = logging.getLogger(__name__)

    def connect_single_client(
        self, client_ip: str, client_tracker: ClientTracker
    ) -> None:
        """Connect to a client's TCP server."""
        with self._tracker_lock:
            self._client_tracker_map[client_ip] = client_tracker
        self.tcp_manager.connect_client(client_ip)
        self.logger.info(f"TCP connection initiated for {client_ip}")

    def _on_connected(self, client_ip: str) -> None:
        """Callback when TCP connection succeeds."""
        with self._tracker_lock:
            tracker = self._client_tracker_map.get(client_ip)
        if tracker:
            tracker.set_tcp_status(client_ip, True)
        self.logger.info(f"TCP connected for client {client_ip}")
        if self.send_command:
            self.send_command(f"say TCP connected for {client_ip}")

    def _on_disconnected(self, client_ip: str, error: Optional[Exception]) -> None:
        """Callback when TCP connection drops. No kick — just log and update status."""
        with self._tracker_lock:
            tracker = self._client_tracker_map.get(client_ip)
        if tracker:
            tracker.set_tcp_status(client_ip, False)
        self.logger.info(f"TCP disconnected for {client_ip}: {error}")

    def disconnect_client(self, client_ip: str) -> None:
        """Disconnect a single client's TCP connection."""
        self.tcp_manager.disconnect_client(client_ip)
        with self._tracker_lock:
            self._client_tracker_map.pop(client_ip, None)

    def is_client_connected(self, client_ip: str) -> bool:
        """Check if a client's TCP connection is active."""
        return self.tcp_manager.is_client_connected(client_ip)

    def disconnect_all(self) -> None:
        """Disconnect all TCP client connections."""
        self.tcp_manager.disconnect_all()
        with self._tracker_lock:
            self._client_tracker_map.clear()

    # ---------- protocol command helpers ----------

    def send_cmd(self, client_ip: str, cmd: str) -> Optional[str]:
        """Send a command to a single client and wait for response."""
        return self.tcp_manager.send_cmd(client_ip, cmd)

    def broadcast_cmd(self, cmd: str) -> Dict[str, Optional[str]]:
        """Send a command to all connected clients and collect responses."""
        return self.tcp_manager.broadcast_cmd(cmd)

    def set_latency(self, client_ip: str, ms: int) -> Optional[str]:
        """Send SET_LATENCY command to a single client."""
        return self.tcp_manager.send_cmd(client_ip, f"SET_LATENCY {ms}")

    def set_latency_all(self, latency_map: Dict[str, int]) -> Dict[str, Optional[str]]:
        """Send SET_LATENCY to each client with per-client values."""
        results: Dict[str, Optional[str]] = {}
        for ip, ms in latency_map.items():
            results[ip] = self.tcp_manager.send_cmd(ip, f"SET_LATENCY {ms}")
        return results

    def send_stop(self, client_ip: str) -> Optional[str]:
        """Send STOP command to a client."""
        return self.tcp_manager.send_cmd(client_ip, "STOP")

    def send_obs_on(self, client_ip: str) -> Optional[str]:
        """Send OBS_ON command to a client."""
        return self.tcp_manager.send_cmd(client_ip, "OBS_ON")

    def send_obs_off(self, client_ip: str) -> Optional[str]:
        """Send OBS_OFF command to a client."""
        return self.tcp_manager.send_cmd(client_ip, "OBS_OFF")

    def send_input_on(self, client_ip: str) -> Optional[str]:
        """Send INPUT_ON command to a client."""
        return self.tcp_manager.send_cmd(client_ip, "INPUT_ON")

    def send_input_off(self, client_ip: str) -> Optional[str]:
        """Send INPUT_OFF command to a client."""
        return self.tcp_manager.send_cmd(client_ip, "INPUT_OFF")

    def ping(self, client_ip: str) -> Optional[str]:
        """Send PING command to a client."""
        return self.tcp_manager.send_cmd(client_ip, "PING")

    def quit_client(self, client_ip: str) -> Optional[str]:
        """Send QUIT command to a client."""
        return self.tcp_manager.send_cmd(client_ip, "QUIT")
