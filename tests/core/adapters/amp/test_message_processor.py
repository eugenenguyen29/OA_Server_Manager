from __future__ import annotations
import unittest
from core.adapters.amp.message_processor import AMPMessageProcessor
from core.adapters.base import MessageType


class TestAMPMessageProcessor(unittest.TestCase):
    def setUp(self):
        self.processor = AMPMessageProcessor()

    def test_full_status_parsing_flow(self):
        messages = [
            "---------players--------",
            "id     time ping loss      state   rate adr name",
            "3    00:05   12    0   spawning  80000 127.190.6.117:52271 'quangminh2479'",
            "#end",
        ]

        parsed_messages = []
        for msg in messages:
            parsed = self.processor.process_message(msg)
            parsed_messages.append(parsed)
        # Find STATUS_COMPLETE
        status_complete = [
            p
            for p in parsed_messages
            if p.message_type == MessageType.STATUS_UPDATE
            and p.data
            and p.data.get("status_complete")
        ]

        self.assertEqual(len(status_complete), 1)

        clients = status_complete[0].data["clients"]
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0]["name"], "quangminh2479")
        self.assertEqual(clients[0]["ip"], "127.190.6.117")
