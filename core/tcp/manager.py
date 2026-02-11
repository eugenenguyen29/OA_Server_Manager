"""
TCP Client Manager - Manages per-client TCP connections.

Each game client (player) runs a TCP server. This manager maintains
a pool of TCPClient instances keyed by client IP, mirroring the
OBSManager pattern for multi-client connection pooling.
"""

import logging
from typing import Callable, Dict, List, Optional

from core.tcp.client import TCPClient, TCPClientConfig


class TCPClientManager:
    """
    Manages multiple TCP client connections, one per game client IP.

    Each game client runs its own TCP server. This manager creates,
    tracks, and tears down TCPClient instances keyed by IP address.
    """

    def __init__(
        self,
        tcp_port: int = 5000,
        connect_timeout: float = 5.0,
        reconnect: bool = True,
        on_client_connected: Optional[Callable[[str], None]] = None,
        on_client_disconnected: Optional[
            Callable[[str, Optional[Exception]], None]
        ] = None,
        on_client_line: Optional[Callable[[str, str], None]] = None,
    ):
        """
        Args:
            tcp_port: Default TCP port on each client's machine.
            connect_timeout: Connection timeout in seconds.
            reconnect: Whether to auto-reconnect on disconnect.
            on_client_connected: Callback(ip) when a client TCP connects.
            on_client_disconnected: Callback(ip, error) when a client TCP disconnects.
            on_client_line: Callback(ip, line) when a line is received from a client.
        """
        self.tcp_port = tcp_port
        self.connect_timeout = connect_timeout
        self.reconnect = reconnect
        self._clients: Dict[str, TCPClient] = {}
        self.logger = logging.getLogger(__name__)

        self._on_client_connected = on_client_connected
        self._on_client_disconnected = on_client_disconnected
        self._on_client_line = on_client_line

    def connect_client(self, client_ip: str, port: Optional[int] = None) -> None:
        """
        Connect to a game client's TCP server.

        If already connected, disconnects first.
        """
        if client_ip in self._clients:
            self.logger.info(f"Reconnecting TCP for {client_ip}")
            self.disconnect_client(client_ip)

        p = port or self.tcp_port

        cfg = TCPClientConfig(
            host=client_ip,
            port=p,
            connect_timeout_sec=self.connect_timeout,
            reconnect=self.reconnect,
        )

        def on_connected() -> None:
            self.logger.info(f"TCP connected to {client_ip}:{p}")
            if self._on_client_connected:
                self._on_client_connected(client_ip)

        def on_disconnected(err: Optional[Exception]) -> None:
            self.logger.info(f"TCP disconnected from {client_ip}: {err}")
            if self._on_client_disconnected:
                self._on_client_disconnected(client_ip, err)

        def on_line(line: str) -> None:
            if self._on_client_line:
                self._on_client_line(client_ip, line)

        client = TCPClient(
            cfg,
            on_connected=on_connected,
            on_disconnected=on_disconnected,
            on_line=on_line,
        )
        self._clients[client_ip] = client
        client.set_on(True)

    def disconnect_client(self, client_ip: str) -> None:
        """Disconnect and remove a single client."""
        client = self._clients.pop(client_ip, None)
        if client:
            client.close()
            self.logger.info(f"TCP client removed: {client_ip}")

    def disconnect_all(self) -> None:
        """Disconnect all TCP clients."""
        for ip in list(self._clients.keys()):
            self.disconnect_client(ip)
        self.logger.info("All TCP clients disconnected")

    def send_to_client(self, client_ip: str, text: str) -> None:
        """Send a line to a specific client's TCP server."""
        client = self._clients.get(client_ip)
        if client is None:
            raise KeyError(f"No TCP connection for {client_ip}")
        client.send_line(text)

    def send_to_all(self, text: str) -> Dict[str, bool]:
        """Send a line to all connected clients. Returns {ip: success}."""
        results: Dict[str, bool] = {}
        for ip, client in self._clients.items():
            try:
                client.send_line(text)
                results[ip] = True
            except Exception:
                results[ip] = False
        return results

    def is_client_connected(self, client_ip: str) -> bool:
        client = self._clients.get(client_ip)
        return client is not None and client.is_connected()

    def get_connected_clients(self) -> List[str]:
        return [ip for ip, c in self._clients.items() if c.is_connected()]

    def get_connection_count(self) -> int:
        return sum(1 for c in self._clients.values() if c.is_connected())
