"""Tests for core.tcp.manager — thread safety + CommandChannel integration."""

import threading
from unittest.mock import MagicMock, patch

from core.tcp.client import CommandChannel, TCPClient
from core.tcp.manager import TCPClientManager


class TestBug3_ThreadSafety:
    def test_concurrent_connect_disconnect_no_crash(self):
        """Concurrent connect/disconnect on different IPs must not crash."""
        mgr = TCPClientManager(tcp_port=9999, reconnect=False)

        errors = []

        def connect_worker(ip):
            try:
                mgr.connect_client(ip)
            except Exception as e:
                errors.append(e)

        def disconnect_worker(ip):
            try:
                mgr.disconnect_client(ip)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(10):
            ip = f"10.0.0.{i}"
            t1 = threading.Thread(target=connect_worker, args=(ip,), daemon=True)
            t2 = threading.Thread(target=disconnect_worker, args=(ip,), daemon=True)
            threads.extend([t1, t2])

        # Apply patch once at the test level — concurrent per-thread patching
        # is not thread-safe and can leave socket.create_connection permanently
        # replaced with a mock.
        with patch(
            "core.tcp.client.socket.create_connection",
            side_effect=OSError("refused"),
        ):
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

        mgr.disconnect_all()

        assert len(errors) == 0, f"Got errors: {errors}"


class TestBug5_HelloLine:
    def test_hello_line_includes_client_ip(self):
        """Manager must set hello_line to 'HELLO {client_ip}'."""
        mgr = TCPClientManager(tcp_port=9999, reconnect=False)

        configs_seen = []
        original_init = TCPClient.__init__

        def capture_init(self, config, **kwargs):
            configs_seen.append(config)
            original_init(self, config, **kwargs)

        with (
            patch.object(TCPClient, "__init__", capture_init),
            patch.object(TCPClient, "set_on"),
        ):
            mgr.connect_client("192.168.1.42")

        assert len(configs_seen) == 1
        assert configs_seen[0].hello_line == "HELLO 192.168.1.42"


class TestBroadcastCmd:
    def test_broadcast_cmd_collects_responses(self):
        """broadcast_cmd sends to all and returns responses."""
        mgr = TCPClientManager(tcp_port=9999, reconnect=False)

        # Create mock channels
        ch1 = MagicMock(spec=CommandChannel)
        ch1.send_cmd.return_value = "OK"
        ch2 = MagicMock(spec=CommandChannel)
        ch2.send_cmd.return_value = "OK"

        with mgr._lock:
            mgr._clients["10.0.0.1"] = MagicMock(spec=TCPClient)
            mgr._clients["10.0.0.2"] = MagicMock(spec=TCPClient)
            mgr._channels["10.0.0.1"] = ch1
            mgr._channels["10.0.0.2"] = ch2

        results = mgr.broadcast_cmd("PING")
        assert results == {"10.0.0.1": "OK", "10.0.0.2": "OK"}

    def test_broadcast_cmd_keeps_unresponsive_clients(self):
        """broadcast_cmd keeps clients that return None (no auto-removal)."""
        mgr = TCPClientManager(tcp_port=9999, reconnect=False)

        ch_alive = MagicMock(spec=CommandChannel)
        ch_alive.send_cmd.return_value = "OK"
        ch_dead = MagicMock(spec=CommandChannel)
        ch_dead.send_cmd.return_value = None

        mock_client_alive = MagicMock(spec=TCPClient)
        mock_client_dead = MagicMock(spec=TCPClient)

        with mgr._lock:
            mgr._clients["10.0.0.1"] = mock_client_alive
            mgr._clients["10.0.0.2"] = mock_client_dead
            mgr._channels["10.0.0.1"] = ch_alive
            mgr._channels["10.0.0.2"] = ch_dead

        results = mgr.broadcast_cmd("PING")

        assert results["10.0.0.1"] == "OK"
        assert results["10.0.0.2"] is None

        # Unresponsive client should still be in _clients (NOT removed)
        with mgr._lock:
            assert "10.0.0.2" in mgr._clients
            assert "10.0.0.1" in mgr._clients


class TestSendCmd:
    def test_send_cmd_returns_none_for_unknown_ip(self):
        mgr = TCPClientManager(tcp_port=9999, reconnect=False)
        assert mgr.send_cmd("1.2.3.4", "PING") is None
