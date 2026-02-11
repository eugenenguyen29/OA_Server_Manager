from __future__ import annotations
import unittest

from core.adapters.amp.status_parser import AMPStatusParser


class TestAMPStatusParser(unittest.TestCase):
    def setUp(self):
        self.parser = AMPStatusParser()

    def test_detect_status_header(self):
        header = "---------players--------"
        self.assertTrue(self.parser.is_status_header(header))

    def test_parse_valid_client_line(self):
        line = (
            "3    00:05   12    0   spawning  80000 127.190.6.117:52271 'quangminh2479'"
        )

        client = self.parser.parse_client_line(line)

        self.assertIsNotNone(client)
        self.assertEqual(client["client_id"], 3)
        self.assertEqual(client["ping"], 12)
        self.assertEqual(client["loss"], 0)
        self.assertEqual(client["state"], "spawning")
        self.assertEqual(client["rate"], 80000)
        self.assertEqual(client["ip"], "127.190.6.117")
        self.assertEqual(client["name"], "quangminh2479")
        self.assertEqual(client["type"], "HUMAN")

    def test_invalid_ip(self):
        line = "3    00:05   12    0   spawning  80000 abc.def.1.2:12345 'badip'"

        client = self.parser.parse_client_line(line)
        self.assertIsNone(client)


if __name__ == "__main__":
    unittest.main()
