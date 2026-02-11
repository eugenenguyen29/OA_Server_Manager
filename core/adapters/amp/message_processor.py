from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from core.adapters.base import BaseMessageProcessor, MessageType, ParsedMessage
from core.adapters.amp.status_parser import AMPStatusParser


class AMPMessageProcessor(BaseMessageProcessor):
    """
    Message processor for Dota 2 server via AMP.

    Handles line-by-line console entries with stateful parsing
    for multi-line status output.
    """

    def __init__(self, send_command_callback: Optional[Callable[[str], None]] = None):
        super().__init__(send_command_callback)
        self.logger = logging.getLogger(__name__)

        self._status_parser = AMPStatusParser()
        self._in_player_section = False
        self._collected_clients: List[Dict] = []

    def get_supported_message_types(self) -> List[MessageType]:
        return [
            MessageType.STATUS_UPDATE,
        ]

    def process_message(self, raw_message: str) -> ParsedMessage:
        """
        Process a single console entry (line-by-line from AMP).

        Uses stateful parsing to handle multi-line status output
        that arrives as separate console entries.
        """
        raw_message = raw_message.strip()

        if not raw_message:
            self.logger.debug("Empty message received, skipping")
            return ParsedMessage(MessageType.UNKNOWN, raw_message)

        # Check for player section start marker
        if self._status_parser.is_status_header(raw_message):
            self.logger.debug(f"Player section START: {raw_message}")
            self._in_player_section = True
            self._collected_clients = []
            return ParsedMessage(MessageType.STATUS_UPDATE, raw_message)

        # Check for section end marker (only if we're in player section)
        if self._in_player_section and self._status_parser.is_section_end(raw_message):
            self.logger.debug(
                f"Player section END: {len(self._collected_clients)} clients collected"
            )
            self._in_player_section = False
            clients = self._collected_clients.copy()
            self._collected_clients = []
            return ParsedMessage(
                MessageType.STATUS_UPDATE,
                raw_message,
                {"clients": clients, "status_complete": True},
            )

        # If we're in the player section, try to parse
        if self._in_player_section:
            # Skip column header line
            if self._status_parser.is_column_header(raw_message):
                self.logger.debug(f"Column header, skipping: {raw_message}")
                return ParsedMessage(MessageType.STATUS_UPDATE, raw_message)

            # Try to parse as client line
            client = self._status_parser.parse_client_line(raw_message)
            if client:
                self._collected_clients.append(client)
                self.logger.info(
                    f"[STATUS] Client {client['client_id']} "
                    f"{client['name']} ({client['ip']})"
                )
                return ParsedMessage(
                    MessageType.STATUS_UPDATE,
                    raw_message,
                    {"client_data": client},
                )
            else:
                self.logger.debug(
                    f"In player section but not a client line: {raw_message[:50]}"
                )
                return ParsedMessage(MessageType.STATUS_UPDATE, raw_message)

        # Not in player section, regular message
        self.logger.debug(f"Regular message: {raw_message[:50]}...")
        return ParsedMessage(MessageType.UNKNOWN, raw_message)
