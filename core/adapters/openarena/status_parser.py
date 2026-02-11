"""OpenArena-specific status parsing.

This module provides the OAStatusParser class that handles parsing
of OpenArena server status output, including client detection
(human vs bot differentiation) and IP address extraction.
"""

import logging
from typing import Dict, Optional

from core.adapters.status_parser import StatusParser


class OAStatusParser(StatusParser):
    """OpenArena-specific status parsing.

    Handles parsing of OpenArena/Quake III Arena server status output.
    The status format is:

        map: <mapname>
        num score ping name            lastmsg address               qport rate
        --- ----- ---- --------------- ------- --------------------- ----- -----
          0    5  100 Player1              0 192.168.1.100:27961   12345 25000
          1    3   50 BotPlayer            0 bot                       0 25000

    This parser extracts client information including:
    - Client ID, score, ping, name
    - IP address (for humans) or "bot" marker
    - Client type (HUMAN or BOT)
    """

    def __init__(self) -> None:
        """Initialize the OpenArena status parser."""
        super().__init__()
        self.logger = logging.getLogger(__name__)

    def is_status_header(self, line: str) -> bool:
        """Check if line is the status header.

        Args:
            line: The line to check.

        Returns:
            True if the line is the status header, False otherwise.
        """
        return "num score ping name" in line and "address" in line

    def is_separator(self, line: str) -> bool:
        """Check if line is a separator line.

        Args:
            line: The line to check.

        Returns:
            True if the line starts with dashes, False otherwise.
        """
        return line.startswith("---")

    def parse_client_line(self, line: str) -> Optional[Dict]:
        """Parse a client line from status output.

        Status line format:
        num score ping name lastmsg address qport rate
          0    5  100 Player1      0 192.168.1.100:27961   12345 25000

        Args:
            line: The status line to parse.

        Returns:
            Dictionary containing client data, or None if parsing fails.
        """
        try:
            parts = line.split()
            self.logger.debug(f"[STATUS] Line parts: {parts}")

            if len(parts) < 6:
                self.logger.debug(f"Line has insufficient parts: {len(parts)}")
                return None

            # Status line format:
            # num score ping name lastmsg address qport rate
            #  0    1     2    3      4       5     6    7
            try:
                client_id = int(parts[0])
                score = int(parts[1])
                ping = int(parts[2])
                name = parts[3]
                lastmsg = int(parts[4])
                address = parts[5]
                qport = int(parts[6]) if len(parts) > 6 and parts[6] != "0" else 0
                rate = int(parts[7]) if len(parts) > 7 else 0
            except (ValueError, IndexError) as e:
                self.logger.debug(f"Error parsing line parts: {e}")
                return None

            if address == "bot":
                client_type = "BOT"
                ip_address = "bot"
            else:
                client_type = "HUMAN"
                ip_address = address.split(":")[0] if ":" in address else address
                if not self._is_valid_ip(ip_address):
                    self.logger.warning(f"Invalid IP format: {ip_address}")
                    return None

            return {
                "client_id": client_id,
                "score": score,
                "ping": ping,
                "name": name,
                "lastmsg": lastmsg,
                "ip": ip_address,
                "qport": qport,
                "rate": rate,
                "type": client_type,
            }

        except Exception as e:
            self.logger.error(f"Error extracting client from line '{line}': {e}")
            return None

    def _is_valid_ip(self, ip: str) -> bool:
        """Validate IP address format.

        Args:
            ip: The IP address string to validate.

        Returns:
            True if the IP address is valid IPv4 format, False otherwise.
        """
        if not ip:
            return False

        try:
            parts = ip.split(".")
            if len(parts) != 4:
                return False
            for part in parts:
                num = int(part)
                if num < 0 or num > 255:
                    return False
            return True
        except (ValueError, AttributeError):
            return False
