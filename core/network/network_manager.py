import logging
from typing import Any, Dict, List, Optional, Callable

import core.utils.settings as settings
from core.network.network_utils import NetworkUtils


class NetworkManager:
    """Manages client connections and network latency simulation."""

    BOT_NAMES = [
        "Angelyss",
        "Arachna",
        "Major",
        "Sarge",
        "Skelebot",
        "Merman",
        "Beret",
        "Kyonshi",
    ]

    def __init__(
        self,
        interface: str = "enp1s0",
        send_command_callback: Optional[Callable[[str], None]] = None,
    ):
        self.interface = interface
        self.send_command = send_command_callback
        self.logger = logging.getLogger(__name__)

        self.ip_latency_map: Dict[str, int] = {}
        self.client_ip_map: Dict[int, str] = {}
        self.client_type_map: Dict[int, str] = {}
        self.client_name_map: Dict[int, str] = {}
        self.obs_status_map: Dict[str, bool] = {}
        self.player_count: int = 0
        self.human_count: int = 0
        self.bot_count: int = 0

        self._current_latencies = list(settings.latencies)
        self._round_count = 0
        self._enabled = settings.enable_latency_control

    def add_client(
        self,
        client_id: int,
        ip: Optional[str] = None,
        latency: Optional[int] = None,
        name: Optional[str] = None,
        is_bot: bool = False,
    ) -> None:
        """Add a new client with IP address and optional latency assignment."""
        if name and name in self.BOT_NAMES:
            is_bot = True

        self.client_type_map[client_id] = "BOT" if is_bot else "HUMAN"

        if name:
            self.client_name_map[client_id] = name

        if not is_bot and ip:
            if ip not in self.ip_latency_map:
                self.client_ip_map[client_id] = ip
                self.ip_latency_map[ip] = latency if latency is not None else 0
                self.obs_status_map[ip] = False
                self.logger.info(
                    f"Added HUMAN client {client_id} with IP {ip}, latency {latency}ms"
                )
            else:
                self.logger.debug(
                    f"Client IP {ip} already exists, updating client_id mapping"
                )
                self.client_ip_map[client_id] = ip
            self.human_count = len(
                [cid for cid, ctype in self.client_type_map.items() if ctype == "HUMAN"]
            )
        elif is_bot:
            self.logger.info(f"Added BOT client {client_id} with name {name}")
            self.bot_count = len(
                [cid for cid, ctype in self.client_type_map.items() if ctype == "BOT"]
            )

        self.player_count = self.human_count + self.bot_count

    def remove_client(self, client_id: int) -> None:
        """Remove client and clean up mappings."""
        client_type = self.client_type_map.get(client_id, "UNKNOWN")

        if client_id in self.client_ip_map:
            ip = self.client_ip_map[client_id]
            del self.client_ip_map[client_id]

            if ip not in self.client_ip_map.values():
                if ip in self.ip_latency_map:
                    del self.ip_latency_map[ip]
                if ip in self.obs_status_map:
                    del self.obs_status_map[ip]
                self.logger.info(
                    f"Removed {client_type} client {client_id} with IP {ip}"
                )
            else:
                self.logger.debug(
                    f"Client {client_id} removed but IP {ip} still in use"
                )

        if client_id in self.client_type_map:
            del self.client_type_map[client_id]
        if client_id in self.client_name_map:
            del self.client_name_map[client_id]

        self.human_count = len(
            [cid for cid, ctype in self.client_type_map.items() if ctype == "HUMAN"]
        )
        self.bot_count = len(
            [cid for cid, ctype in self.client_type_map.items() if ctype == "BOT"]
        )
        self.player_count = self.human_count + self.bot_count

        if client_type == "UNKNOWN" and client_id not in self.client_type_map:
            self.logger.warning(f"Attempted to remove unknown client {client_id}")

    def get_client_count(self) -> int:
        return self.player_count

    def get_human_count(self) -> int:
        return self.human_count

    def get_bot_count(self) -> int:
        return self.bot_count

    def get_client_ip(self, client_id: int) -> Optional[str]:
        return self.client_ip_map.get(client_id)

    def get_client_id_by_ip(self, ip: str) -> Optional[int]:
        """Return the client ID associated with the given IP address."""
        for client_id, client_ip in self.client_ip_map.items():
            if client_ip == ip:
                return client_id
        return None

    def get_human_clients(self) -> List[str]:
        human_ips = []
        for client_id, client_type in self.client_type_map.items():
            if client_type == "HUMAN" and client_id in self.client_ip_map:
                human_ips.append(self.client_ip_map[client_id])
        return human_ips

    def set_obs_status(self, ip: str, connected: bool) -> None:
        if ip in self.ip_latency_map:
            self.obs_status_map[ip] = connected
            self.logger.debug(f"Set OBS status for {ip} to {connected}")
        else:
            self.logger.warning(f"Attempted to set OBS status for unknown IP {ip}")

    def get_obs_status(self, ip: str) -> Optional[bool]:
        return self.obs_status_map.get(ip)

    def get_client_info_table(self) -> List[List[Any]]:
        table_data = []
        for client_id, client_type in self.client_type_map.items():
            row = [client_id]

            if client_type == "HUMAN":
                ip = self.client_ip_map.get(client_id, "N/A")
                row.append(ip)
                row.append("HUMAN")
                row.append(
                    f"{self.ip_latency_map.get(ip, 0)}ms" if ip != "N/A" else "N/A"
                )
                obs_status = self.obs_status_map.get(ip, False)
                row.append("Connected" if obs_status else "Not Connected")
            else:
                row.append("N/A")
                row.append("BOT")
                row.append("N/A")
                row.append("N/A")

            name = self.client_name_map.get(client_id, "Unknown")
            row.append(name)
            table_data.append(row)

        return table_data

    def assign_latencies(self, latencies: List[int]) -> None:
        """Distribute latencies across connected clients using round-robin."""
        if not self.ip_latency_map or not latencies:
            return

        ips = list(self.ip_latency_map.keys())
        for i, ip in enumerate(ips):
            self.ip_latency_map[ip] = latencies[i % len(latencies)]

        self.logger.info(
            f"Assigned latencies to {len(ips)} clients: {dict(zip(ips, [latencies[i % len(latencies)] for i in range(len(ips))]))}"
        )

    def get_latency_map(self) -> Dict[str, int]:
        return self.ip_latency_map.copy()

    def apply_latency_rules(self) -> bool:
        """Apply current latency rules to all connected clients."""
        if not self._enabled:
            self.logger.info(
                "Latency control is disabled, skipping latency application"
            )
            if self.send_command:
                self.send_command("say Latency control disabled")
            return True

        try:
            if not self.ip_latency_map:
                self.logger.warning("No clients available for latency application")
                return False

            NetworkUtils.apply_latency_rules(self.ip_latency_map, self.interface)

            self.logger.info(
                f"Applied latency rules to {len(self.ip_latency_map)} clients on interface {self.interface}"
            )

            if self.send_command:
                self.send_command(
                    f"say Latency rules applied to {len(self.ip_latency_map)} clients"
                )

            return True

        except Exception as e:
            self.logger.error(f"Error applying latency rules: {e}", exc_info=True)
            return False

    def rotate_latencies(self) -> bool:
        """Rotate latency assignments for the next round."""
        if not self._enabled:
            self.logger.info("Latency control is disabled, skipping latency rotation")
            return True

        try:
            self._current_latencies = (
                self._current_latencies[1:] + self._current_latencies[:1]
            )
            self._round_count += 1

            self.assign_latencies(self._current_latencies)

            self.logger.info(
                f"Round {self._round_count}: Rotated latencies to {self._current_latencies}"
            )

            if self.send_command:
                latency_str = ", ".join(f"{lat}ms" for lat in self._current_latencies)
                self.send_command(
                    f"say Round {self._round_count}: New latencies - {latency_str}"
                )

            return True

        except Exception as e:
            self.logger.error(f"Error rotating latencies: {e}", exc_info=True)
            return False

    def clear_latency_rules(self) -> bool:
        """Clear all latency rules from the network interface."""
        try:
            NetworkUtils.dispose(self.interface)
            self.logger.info("Cleared all latency rules from network interface")

            if self.send_command:
                self.send_command("say Network latency rules cleared")

            return True

        except Exception as e:
            self.logger.error(f"Error clearing latency rules: {e}", exc_info=True)
            return False

    def is_enabled(self) -> bool:
        return self._enabled
