"""Tests for core.tcp.connection_manager — protocol commands + tracker lock."""

import threading
from unittest.mock import MagicMock

from core.adapters.base import ClientTracker
from core.tcp.connection_manager import TCPConnectionManager
from core.tcp.manager import TCPClientManager


class TestProtocolCommands:
    def _make_manager(self):
        mgr = TCPConnectionManager(tcp_port=9999)
        mgr.tcp_manager = MagicMock(spec=TCPClientManager)
        return mgr

    def test_set_latency_sends_correct_command(self):
        mgr = self._make_manager()
        mgr.tcp_manager.send_cmd.return_value = "OK"

        result = mgr.set_latency("10.0.0.1", 50)

        mgr.tcp_manager.send_cmd.assert_called_once_with("10.0.0.1", "SET_LATENCY 50")
        assert result == "OK"

    def test_ping_sends_correct_command(self):
        mgr = self._make_manager()
        mgr.tcp_manager.send_cmd.return_value = "PONG"

        result = mgr.ping("10.0.0.1")

        mgr.tcp_manager.send_cmd.assert_called_once_with("10.0.0.1", "PING")
        assert result == "PONG"

    def test_send_stop(self):
        mgr = self._make_manager()
        mgr.tcp_manager.send_cmd.return_value = "OK"

        mgr.send_stop("10.0.0.1")
        mgr.tcp_manager.send_cmd.assert_called_once_with("10.0.0.1", "STOP")

    def test_send_obs_on(self):
        mgr = self._make_manager()
        mgr.send_obs_on("10.0.0.1")
        mgr.tcp_manager.send_cmd.assert_called_once_with("10.0.0.1", "OBS_ON")

    def test_send_obs_off(self):
        mgr = self._make_manager()
        mgr.send_obs_off("10.0.0.1")
        mgr.tcp_manager.send_cmd.assert_called_once_with("10.0.0.1", "OBS_OFF")

    def test_send_input_on(self):
        mgr = self._make_manager()
        mgr.send_input_on("10.0.0.1")
        mgr.tcp_manager.send_cmd.assert_called_once_with("10.0.0.1", "INPUT_ON")

    def test_send_input_off(self):
        mgr = self._make_manager()
        mgr.send_input_off("10.0.0.1")
        mgr.tcp_manager.send_cmd.assert_called_once_with("10.0.0.1", "INPUT_OFF")

    def test_quit_client(self):
        mgr = self._make_manager()
        mgr.quit_client("10.0.0.1")
        mgr.tcp_manager.send_cmd.assert_called_once_with("10.0.0.1", "QUIT")

    def test_set_latency_all(self):
        mgr = self._make_manager()
        mgr.tcp_manager.send_cmd.return_value = "OK"

        latency_map = {"10.0.0.1": 50, "10.0.0.2": 100}
        results = mgr.set_latency_all(latency_map)

        assert mgr.tcp_manager.send_cmd.call_count == 2
        assert results == {"10.0.0.1": "OK", "10.0.0.2": "OK"}

    def test_broadcast_cmd(self):
        mgr = self._make_manager()
        mgr.tcp_manager.broadcast_cmd.return_value = {"10.0.0.1": "OK"}

        result = mgr.broadcast_cmd("PING")

        mgr.tcp_manager.broadcast_cmd.assert_called_once_with("PING")
        assert result == {"10.0.0.1": "OK"}


class TestTrackerMapThreadSafety:
    def test_tracker_map_thread_safe(self):
        """Concurrent connect_single_client + disconnect_client must not crash."""
        mgr = TCPConnectionManager(tcp_port=9999)
        mgr.tcp_manager = MagicMock(spec=TCPClientManager)

        errors = []

        def connect_worker(ip):
            try:
                tracker = MagicMock(spec=ClientTracker)
                mgr.connect_single_client(ip, tracker)
            except Exception as e:
                errors.append(e)

        def disconnect_worker(ip):
            try:
                mgr.disconnect_client(ip)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(20):
            ip = f"10.0.0.{i % 5}"  # Overlap IPs for contention
            if i % 2 == 0:
                t = threading.Thread(target=connect_worker, args=(ip,), daemon=True)
            else:
                t = threading.Thread(target=disconnect_worker, args=(ip,), daemon=True)
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(errors) == 0, f"Got errors: {errors}"
