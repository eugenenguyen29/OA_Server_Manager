from __future__ import annotations

import logging
import re
from typing import Dict, Optional

from core.adapters.status_parser import StatusParser


PLAYER_SECTION_START = "---------players--------"
PLAYER_SECTION_END = "#end"

# Regex to match IP:port pattern (handles concatenated rate+address like "0127.0.0.1:1234")
IP_PORT_PATTERN = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)")


class AMPStatusParser(StatusParser):
    """
    Status parser for Dota 2 server status output via AMP.

    Example:
        ---------players--------
          id     time ping loss      state   rate adr name
          3    00:05   12    0   spawning  80000 127.190.6.117:52271 'quangminh2479'
          65535 [NoChan]    0    0   reserved      0127.190.6.117:49721 ''
        #end
    """

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)

    def is_status_header(self, line: str) -> bool:
        """Detect player section start marker."""
        return PLAYER_SECTION_START in line

    def is_section_end(self, line: str) -> bool:
        """Detect end of status output."""
        return line.strip() == PLAYER_SECTION_END

    def is_column_header(self, line: str) -> bool:
        """Detect the column header line (id, ping, adr, name)."""
        return "id" in line and "ping" in line and "adr" in line and "name" in line

    def is_separator(self, line: str) -> bool:
        """No separator line in this format."""
        return False

    def parse_client_line(self, line: str) -> Optional[Dict]:
        """
        Parse client line.

        Handles various formats:
        - Normal:      3    00:05   12    0   spawning  80000 127.190.6.117:52271 'name'
        - Concatenated: 65535 [NoChan] 0 0 reserved 0127.190.6.117:49721 ''
        - BOT:         1      BOT    0    0     active      0 'SourceTV'
        """
        try:
            # Skip BOT lines (no IP address)
            if " BOT " in line:
                return None

            # Find IP:port using regex (handles concatenated rate+address)
            ip_match = IP_PORT_PATTERN.search(line)
            if not ip_match:
                return None

            address = ip_match.group(1)
            ip = address.split(":")[0]

            if not self._is_valid_ip(ip):
                return None

            # Split line into before and after the IP:port
            ip_start = ip_match.start()
            ip_end = ip_match.end()

            before_ip = line[:ip_start].split()
            after_ip = line[ip_end:].strip()

            # Before IP should have: id, time, ping, loss, state, [partial_rate]
            if len(before_ip) < 5:
                return None

            client_id = int(before_ip[0])
            time_connected = before_ip[1]

            # Handle ping/loss - might not be pure integers in edge cases
            try:
                ping = int(before_ip[2])
            except ValueError:
                ping = 0

            try:
                loss = int(before_ip[3])
            except ValueError:
                loss = 0

            state = before_ip[4]

            # Rate might be in before_ip[5] or concatenated with IP
            rate = 0
            if len(before_ip) > 5:
                # Check if there's a partial rate before the IP
                rate_str = before_ip[5]
                # If rate is concatenated with IP (e.g., "0127.190..." -> rate=0)
                if rate_str.isdigit():
                    rate = int(rate_str)

            # Name is after the IP:port, strip quotes
            name = after_ip.strip("'\" ")

            return {
                "client_id": client_id,
                "time": time_connected,
                "ping": ping,
                "loss": loss,
                "state": state,
                "rate": rate,
                "ip": ip,
                "address": address,
                "name": name,
                "type": "HUMAN",
            }

        except Exception as e:
            self.logger.debug(f"Failed to parse status line '{line}': {e}")
            return None

    def _is_valid_ip(self, ip: str) -> bool:
        try:
            parts = ip.split(".")
            if len(parts) != 4:
                return False
            return all(0 <= int(p) <= 255 for p in parts)
        except Exception:
            return False
