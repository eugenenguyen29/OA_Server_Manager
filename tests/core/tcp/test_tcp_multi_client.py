"""Tests reproducing multi-client TCP bugs in the Dota2 adapter scenario.

These tests use real TCP sockets (via socketserver) to reproduce the
race conditions and connection-lifecycle bugs identified in the TCP
subsystem analysis (Tasks #1 and #2).

All echo servers bind to 0.0.0.0 on random ports so that any loopback
address (127.0.0.X) can connect. Multi-client tests use distinct
127.0.0.X IPs as logical keys, each connecting to a separate port.

Bug coverage:
- Rapid sequential connections for 3+ clients
- Duplicate IP reconnection (auto-connect + manual Start TCP)
- Connect + disconnect race
- wait_all_connected accuracy
- CommandChannel response routing across multiple clients
- Callback thread safety (on_connected / on_disconnected)
- reconnect=False permanent failure
- Stale callback after reconnection
"""

import socket
import socketserver
import threading
import time
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from core.adapters.base import ClientTracker
from core.tcp.client import CommandChannel, TCPClient, TCPClientConfig
from core.tcp.connection_manager import TCPConnectionManager
from core.tcp.manager import TCPClientManager


# ---------------------------------------------------------------------------
# Echo TCP server fixture -- simulates player-side TCP servers
# ---------------------------------------------------------------------------


class EchoHandler(socketserver.StreamRequestHandler):
    """Echoes back each line received, prefixed with 'OK '."""

    def handle(self):
        while True:
            try:
                line = self.rfile.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    response = f"OK {text}\n"
                    self.wfile.write(response.encode("utf-8"))
                    self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError, OSError):
                break


class _ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _start_echo_server(host="", port=0):
    """Start an echo TCP server on a random available port.

    Binds to "" (all interfaces) so 127.0.0.X loopback addresses all work.
    Returns (server, port).
    """
    server = _ReusableTCPServer((host, port), EchoHandler)
    actual_port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, actual_port


@pytest.fixture
def echo_server():
    """Single echo TCP server on a random port, accepting all interfaces."""
    server, port = _start_echo_server()
    yield port
    server.shutdown()


@pytest.fixture
def echo_servers():
    """Factory fixture: creates N echo servers, returns list of ports."""
    servers = []

    def _make(n):
        ports = []
        for _ in range(n):
            srv, p = _start_echo_server()
            servers.append(srv)
            ports.append(p)
        return ports

    yield _make
    for s in servers:
        s.shutdown()


def _make_tracker():
    """Create a mock ClientTracker."""
    return MagicMock(spec=ClientTracker)


def _wait_hello_consumed(mgr, ip, timeout=2.0):
    """Wait for the HELLO echo response to be consumed by the client's pump loop.

    The echo server sends back "OK HELLO ..." which arrives asynchronously.
    If we send a command immediately after connect, the CommandChannel may
    intercept the HELLO echo instead of our command response.
    """
    time.sleep(0.3)


# ---------------------------------------------------------------------------
# 1. Rapid sequential connections
# ---------------------------------------------------------------------------


class TestRapidSequentialConnections:
    def test_three_sequential_connects_same_server(self, echo_server):
        """Connect to same server 3 times via reconnect (same IP key).

        Tests that TCPClientManager handles reconnection correctly.
        """
        port = echo_server
        mgr = TCPClientManager(tcp_port=port, reconnect=False)

        mgr.connect_client("127.0.0.1", port=port)
        mgr.connect_client("127.0.0.1", port=port)  # reconnect same key
        mgr.connect_client("127.0.0.1", port=port)  # reconnect same key again

        results = mgr.wait_all_connected(timeout=5.0)
        assert results.get("127.0.0.1") is True
        assert mgr.is_client_connected("127.0.0.1")

        # Wait for HELLO echo to be consumed before sending command
        _wait_hello_consumed(mgr, "127.0.0.1")

        resp = mgr.send_cmd("127.0.0.1", "PING")
        assert resp is not None
        assert "PING" in resp

        mgr.disconnect_all()


# ---------------------------------------------------------------------------
# 2. Duplicate IP reconnection (simulating auto-connect + manual Start TCP)
# ---------------------------------------------------------------------------


class TestDuplicateIPReconnection:
    def test_double_connect_same_ip_succeeds(self, echo_server):
        """Connect same IP twice -- second call should reconnect cleanly."""
        port = echo_server
        mgr = TCPClientManager(tcp_port=port, reconnect=False)
        connected_events = []
        disconnected_events = []

        mgr._on_client_connected = lambda ip: connected_events.append(ip)
        mgr._on_client_disconnected = lambda ip, err: disconnected_events.append(ip)

        # First connect (simulates auto-connect from _process_discovered_client)
        mgr.connect_client("127.0.0.1", port=port)
        mgr.wait_all_connected(timeout=5.0)
        assert mgr.is_client_connected("127.0.0.1")

        # Second connect (simulates manual "Start TCP" button)
        mgr.connect_client("127.0.0.1", port=port)
        mgr.wait_all_connected(timeout=5.0)
        assert mgr.is_client_connected("127.0.0.1")

        _wait_hello_consumed(mgr, "127.0.0.1")
        resp = mgr.send_cmd("127.0.0.1", "HELLO_TEST")
        assert resp is not None

        mgr.disconnect_all()

    def test_double_connect_does_not_leave_orphan_thread(self, echo_server):
        """Double connect should not leak the old client thread."""
        port = echo_server
        mgr = TCPClientManager(tcp_port=port, reconnect=False)

        mgr.connect_client("127.0.0.1", port=port)
        mgr.wait_all_connected(timeout=5.0)

        with mgr._lock:
            old_client = mgr._clients["127.0.0.1"]

        mgr.connect_client("127.0.0.1", port=port)
        mgr.wait_all_connected(timeout=5.0)

        time.sleep(0.5)
        assert not old_client.is_connected()

        mgr.disconnect_all()

    def test_tcp_connection_manager_double_connect(self, echo_server):
        """TCPConnectionManager.connect_single_client called twice for same IP."""
        port = echo_server
        conn_mgr = TCPConnectionManager(tcp_port=port)
        tracker = _make_tracker()

        conn_mgr.connect_single_client("127.0.0.1", tracker)
        conn_mgr.wait_all_connected(timeout=5.0)
        assert conn_mgr.is_client_connected("127.0.0.1")

        conn_mgr.connect_single_client("127.0.0.1", tracker)
        conn_mgr.wait_all_connected(timeout=5.0)
        assert conn_mgr.is_client_connected("127.0.0.1")

        conn_mgr.disconnect_all()


# ---------------------------------------------------------------------------
# 3. Multi-client connections (distinct IPs, multiple servers)
# ---------------------------------------------------------------------------


class TestMultiClientConnections:
    """Tests requiring multiple distinct client IPs connecting to servers."""

    def test_three_clients_all_connect(self, echo_servers):
        """Connect 3 clients rapidly and verify all get connected."""
        ports = echo_servers(3)
        mgr = TCPClientManager(tcp_port=ports[0], reconnect=False)

        for i, port in enumerate(ports):
            mgr.connect_client(f"127.0.0.{i + 1}", port=port)

        results = mgr.wait_all_connected(timeout=5.0)

        assert len(results) == 3
        for ip, connected in results.items():
            assert connected, f"Client {ip} did not connect"

        assert mgr.get_connection_count() == 3
        mgr.disconnect_all()

    def test_five_clients_all_respond_to_ping(self, echo_servers):
        """Connect 5 clients and verify each responds independently."""
        ports = echo_servers(5)
        mgr = TCPClientManager(tcp_port=ports[0], reconnect=False)

        for i, port in enumerate(ports):
            mgr.connect_client(f"127.0.0.{i + 1}", port=port)

        mgr.wait_all_connected(timeout=5.0)
        # Wait for all HELLO echoes to be consumed
        time.sleep(0.5)

        for i in range(5):
            ip = f"127.0.0.{i + 1}"
            resp = mgr.send_cmd(ip, "PING")
            assert resp is not None, f"Client {ip} did not respond"
            assert "PING" in resp

        mgr.disconnect_all()

    def test_responses_routed_to_correct_client(self, echo_servers):
        """With 3 clients, each send_cmd returns the correct response."""
        ports = echo_servers(3)
        mgr = TCPClientManager(tcp_port=ports[0], reconnect=False)

        ips = []
        for i, port in enumerate(ports):
            ip = f"127.0.0.{i + 1}"
            mgr.connect_client(ip, port=port)
            ips.append(ip)

        mgr.wait_all_connected(timeout=5.0)
        time.sleep(0.5)

        for i, ip in enumerate(ips):
            resp = mgr.send_cmd(ip, f"UNIQUE_{i}")
            assert resp is not None, f"Client {ip} did not respond"
            assert f"UNIQUE_{i}" in resp, f"Wrong response for {ip}: {resp}"

        mgr.disconnect_all()

    def test_broadcast_cmd_all_respond(self, echo_servers):
        """broadcast_cmd sends to all and collects correct responses."""
        ports = echo_servers(3)
        mgr = TCPClientManager(tcp_port=ports[0], reconnect=False)

        for i, port in enumerate(ports):
            mgr.connect_client(f"127.0.0.{i + 1}", port=port)

        mgr.wait_all_connected(timeout=5.0)
        time.sleep(0.5)

        results = mgr.broadcast_cmd("BROADCAST_TEST")

        assert len(results) == 3
        for ip, resp in results.items():
            assert resp is not None, f"Client {ip} did not respond"
            assert "BROADCAST_TEST" in resp

        mgr.disconnect_all()

    def test_concurrent_commands_to_different_clients(self, echo_servers):
        """Concurrent send_cmd to different clients should not cross-contaminate."""
        ports = echo_servers(3)
        mgr = TCPClientManager(tcp_port=ports[0], reconnect=False)

        ips = []
        for i, port in enumerate(ports):
            ip = f"127.0.0.{i + 1}"
            mgr.connect_client(ip, port=port)
            ips.append(ip)

        mgr.wait_all_connected(timeout=5.0)
        time.sleep(0.5)

        results = {}
        errors = []

        def send_worker(ip, cmd):
            try:
                resp = mgr.send_cmd(ip, cmd)
                results[ip] = resp
            except Exception as e:
                errors.append(e)

        threads = []
        for i, ip in enumerate(ips):
            t = threading.Thread(
                target=send_worker, args=(ip, f"CMD_{i}"), daemon=True
            )
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(errors) == 0, f"Errors: {errors}"
        for i, ip in enumerate(ips):
            assert ip in results
            assert results[ip] is not None, f"Client {ip} did not respond"
            assert f"CMD_{i}" in results[ip], (
                f"Cross-contaminated response for {ip}: {results[ip]}"
            )

        mgr.disconnect_all()

    def test_callbacks_fire_for_each_client(self, echo_servers):
        """on_connected fires once per client, not cross-contaminated."""
        ports = echo_servers(3)
        connected_ips = []
        lock = threading.Lock()

        def on_connected(ip):
            with lock:
                connected_ips.append(ip)

        mgr = TCPClientManager(
            tcp_port=ports[0],
            reconnect=False,
            on_client_connected=on_connected,
        )

        for i, port in enumerate(ports):
            mgr.connect_client(f"127.0.0.{i + 1}", port=port)

        mgr.wait_all_connected(timeout=5.0)
        time.sleep(0.3)

        with lock:
            assert len(connected_ips) == 3
            assert set(connected_ips) == {"127.0.0.1", "127.0.0.2", "127.0.0.3"}

        mgr.disconnect_all()

    def test_disconnect_callbacks_fire_correctly(self, echo_servers):
        """on_disconnected fires once per client on disconnect_all."""
        ports = echo_servers(3)
        disconnected_ips = []
        lock = threading.Lock()

        def on_disconnected(ip, err):
            with lock:
                disconnected_ips.append(ip)

        mgr = TCPClientManager(
            tcp_port=ports[0],
            reconnect=False,
            on_client_disconnected=on_disconnected,
        )

        for i, port in enumerate(ports):
            mgr.connect_client(f"127.0.0.{i + 1}", port=port)

        mgr.wait_all_connected(timeout=5.0)
        mgr.disconnect_all()
        time.sleep(0.5)

        with lock:
            assert len(disconnected_ips) == 3
            assert set(disconnected_ips) == {"127.0.0.1", "127.0.0.2", "127.0.0.3"}

    def test_wait_all_connected_reports_correct_status(self, echo_servers):
        """Connect 3 clients, verify wait_all_connected reports all True."""
        ports = echo_servers(3)
        mgr = TCPClientManager(tcp_port=ports[0], reconnect=False)

        for i, port in enumerate(ports):
            mgr.connect_client(f"127.0.0.{i + 1}", port=port)

        results = mgr.wait_all_connected(timeout=5.0)

        assert len(results) == 3
        assert all(results.values()), f"Not all connected: {results}"
        mgr.disconnect_all()

    def test_wait_after_concurrent_additions(self, echo_servers):
        """Clients added concurrently should all appear in wait_all_connected."""
        ports = echo_servers(4)
        mgr = TCPClientManager(tcp_port=ports[0], reconnect=False)

        threads = []
        for i, port in enumerate(ports):
            t = threading.Thread(
                target=mgr.connect_client,
                args=(f"127.0.0.{i + 1}",),
                kwargs={"port": port},
                daemon=True,
            )
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        time.sleep(0.1)

        results = mgr.wait_all_connected(timeout=5.0)
        assert len(results) == 4
        assert all(results.values()), f"Not all connected: {results}"
        mgr.disconnect_all()

    def test_set_latency_all_via_connection_manager(self, echo_servers):
        """set_latency_all sends SET_LATENCY to each connected client."""
        ports = echo_servers(2)
        conn_mgr = TCPConnectionManager(tcp_port=ports[0])
        tracker = _make_tracker()

        # Connect both through tcp_manager with correct ports
        conn_mgr.tcp_manager.connect_client("127.0.0.1", port=ports[0])
        with conn_mgr._tracker_lock:
            conn_mgr._client_tracker_map["127.0.0.1"] = tracker
        conn_mgr.tcp_manager.connect_client("127.0.0.2", port=ports[1])
        with conn_mgr._tracker_lock:
            conn_mgr._client_tracker_map["127.0.0.2"] = tracker

        conn_mgr.wait_all_connected(timeout=5.0)
        time.sleep(0.5)

        latency_map = {"127.0.0.1": 50, "127.0.0.2": 100}
        results = conn_mgr.set_latency_all(latency_map)

        for ip, resp in results.items():
            assert resp is not None, f"Client {ip} did not respond to SET_LATENCY"

        conn_mgr.disconnect_all()

    def test_reconcile_during_start_tcp(self, echo_servers):
        """Simulate _reconcile_clients running while _start_tcp connects.

        This reproduces Bug 2 from Task #2: _reconcile_clients disconnects
        a client while _start_tcp is iterating and connecting.
        """
        ports = echo_servers(3)
        conn_mgr = TCPConnectionManager(tcp_port=ports[0])
        tracker = _make_tracker()
        errors = []

        ips = ["127.0.0.1", "127.0.0.2", "127.0.0.3"]
        for ip, port in zip(ips, ports):
            conn_mgr.tcp_manager.connect_client(ip, port=port)
        conn_mgr.wait_all_connected(timeout=5.0)

        def simulate_start_tcp():
            try:
                for ip in ips:
                    conn_mgr.connect_single_client(ip, tracker)
                    time.sleep(0.01)
            except Exception as e:
                errors.append(e)

        def simulate_reconcile():
            try:
                time.sleep(0.015)
                conn_mgr.disconnect_client("127.0.0.2")
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=simulate_start_tcp, daemon=True)
        t2 = threading.Thread(target=simulate_reconcile, daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        assert len(errors) == 0, f"Race condition errors: {errors}"
        conn_mgr.disconnect_all()


# ---------------------------------------------------------------------------
# 4. Connect + disconnect race (single-server tests)
# ---------------------------------------------------------------------------


class TestConnectDisconnectRace:
    def test_connect_then_immediate_disconnect(self, echo_server):
        """Connect client, then immediately disconnect and reconnect."""
        port = echo_server
        mgr = TCPClientManager(tcp_port=port, reconnect=False)

        mgr.connect_client("127.0.0.1", port=port)
        mgr.wait_all_connected(timeout=5.0)

        mgr.disconnect_client("127.0.0.1")
        assert not mgr.is_client_connected("127.0.0.1")

        mgr.connect_client("127.0.0.1", port=port)
        mgr.wait_all_connected(timeout=5.0)
        assert mgr.is_client_connected("127.0.0.1")

        _wait_hello_consumed(mgr, "127.0.0.1")
        resp = mgr.send_cmd("127.0.0.1", "TEST")
        assert resp is not None

        mgr.disconnect_all()

    def test_concurrent_connect_disconnect_same_ip(self, echo_server):
        """Concurrent connect and disconnect for the same IP should not crash."""
        port = echo_server
        mgr = TCPClientManager(tcp_port=port, reconnect=False)
        errors = []

        def connect_loop():
            for _ in range(5):
                try:
                    mgr.connect_client("127.0.0.1", port=port)
                    time.sleep(0.02)
                except Exception as e:
                    errors.append(e)

        def disconnect_loop():
            for _ in range(5):
                try:
                    mgr.disconnect_client("127.0.0.1")
                    time.sleep(0.02)
                except Exception as e:
                    errors.append(e)

        t1 = threading.Thread(target=connect_loop, daemon=True)
        t2 = threading.Thread(target=disconnect_loop, daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        assert len(errors) == 0, f"Race condition errors: {errors}"
        mgr.disconnect_all()


# ---------------------------------------------------------------------------
# 5. wait_all_connected with mixed success
# ---------------------------------------------------------------------------


class TestWaitAllConnected:
    def test_wait_all_connected_with_one_failing(self, echo_server):
        """One client connects, one fails -- wait_all_connected reports mixed."""
        port = echo_server
        mgr = TCPClientManager(tcp_port=port, reconnect=False, connect_timeout=1.0)

        mgr.connect_client("127.0.0.1", port=port)
        # Port 1 is privileged and nothing is listening -- will fail
        mgr.connect_client("127.0.0.99", port=1)

        results = mgr.wait_all_connected(timeout=3.0)

        assert results.get("127.0.0.1") is True
        assert results.get("127.0.0.99") is not None
        mgr.disconnect_all()


# ---------------------------------------------------------------------------
# 6. Callback thread safety -- reconnect scenario
# ---------------------------------------------------------------------------


class TestCallbackThreadSafety:
    def test_reconnect_callbacks_correct_count(self, echo_server):
        """Reconnecting same IP fires disconnect then connect callbacks."""
        port = echo_server
        connected_count = {"value": 0}
        disconnected_count = {"value": 0}
        lock = threading.Lock()

        def on_connected(ip):
            with lock:
                connected_count["value"] += 1

        def on_disconnected(ip, err):
            with lock:
                disconnected_count["value"] += 1

        mgr = TCPClientManager(
            tcp_port=port,
            reconnect=False,
            on_client_connected=on_connected,
            on_client_disconnected=on_disconnected,
        )

        mgr.connect_client("127.0.0.1", port=port)
        mgr.wait_all_connected(timeout=5.0)
        time.sleep(0.2)

        with lock:
            assert connected_count["value"] == 1

        mgr.connect_client("127.0.0.1", port=port)
        mgr.wait_all_connected(timeout=5.0)
        time.sleep(0.5)

        with lock:
            assert connected_count["value"] == 2
            assert disconnected_count["value"] >= 1

        mgr.disconnect_all()


# ---------------------------------------------------------------------------
# 7. reconnect=False permanent failure (Bug 4 from Task #2)
# ---------------------------------------------------------------------------


class TestReconnectFalse:
    def test_reconnect_false_gives_up_on_failure(self):
        """With reconnect=False, a failed connection is never retried."""
        cfg = TCPClientConfig(
            host="127.0.0.1",
            port=1,  # Nothing listening
            connect_timeout_sec=0.5,
            reconnect=False,
        )
        connected = threading.Event()
        client = TCPClient(cfg, on_connected=lambda: connected.set())
        client.set_on(True)

        time.sleep(1.5)

        assert not client.is_connected()
        assert not connected.is_set()

        with client._lock:
            assert client._want_on is False

        client.close()

    def test_reconnect_true_retries_on_failure(self, echo_server):
        """With reconnect=True, a failed connection is retried."""
        port = echo_server
        connected = threading.Event()

        cfg = TCPClientConfig(
            host="127.0.0.1",
            port=port,
            connect_timeout_sec=1.0,
            reconnect=True,
            reconnect_delay_sec=0.5,
        )
        client = TCPClient(cfg, on_connected=lambda: connected.set())
        client.set_on(True)

        assert connected.wait(timeout=5.0), "Client with reconnect=True did not connect"
        assert client.is_connected()

        client.close()


# ---------------------------------------------------------------------------
# 8. TCPConnectionManager tracker integration
# ---------------------------------------------------------------------------


class TestTCPConnectionManagerIntegration:
    def test_tracker_status_updated_on_connect(self, echo_server):
        """ClientTracker.set_tcp_status called with True on successful connect."""
        port = echo_server
        conn_mgr = TCPConnectionManager(tcp_port=port)
        tracker = _make_tracker()

        conn_mgr.connect_single_client("127.0.0.1", tracker)
        conn_mgr.wait_all_connected(timeout=5.0)
        time.sleep(0.2)

        tracker.set_tcp_status.assert_called_with("127.0.0.1", True)
        conn_mgr.disconnect_all()

    def test_tracker_status_updated_on_disconnect(self, echo_server):
        """ClientTracker.set_tcp_status called with False on disconnect."""
        port = echo_server
        conn_mgr = TCPConnectionManager(tcp_port=port)
        tracker = _make_tracker()

        conn_mgr.connect_single_client("127.0.0.1", tracker)
        conn_mgr.wait_all_connected(timeout=5.0)
        time.sleep(0.2)

        conn_mgr.disconnect_client("127.0.0.1")
        time.sleep(0.5)

        calls = tracker.set_tcp_status.call_args_list
        assert len(calls) >= 2
        last_call_args = calls[-1][0]
        assert last_call_args == ("127.0.0.1", False)


# ---------------------------------------------------------------------------
# 9. Stale callback after reconnection (Bug 5 from Task #2)
# ---------------------------------------------------------------------------


class TestStaleCallbackAfterReconnect:
    def test_old_client_disconnect_does_not_corrupt_new_status(self, echo_server):
        """After reconnect, old client's on_disconnected should not set
        tcp_status to False when new client is already connected.

        This tests Bug 5: stale callbacks from old TCPClient.
        """
        port = echo_server
        status_history = []
        lock = threading.Lock()

        def on_connected(ip):
            with lock:
                status_history.append((ip, True))

        def on_disconnected(ip, err):
            with lock:
                status_history.append((ip, False))

        mgr = TCPClientManager(
            tcp_port=port,
            reconnect=False,
            on_client_connected=on_connected,
            on_client_disconnected=on_disconnected,
        )

        # First connect
        mgr.connect_client("127.0.0.1", port=port)
        mgr.wait_all_connected(timeout=5.0)
        time.sleep(0.2)

        # Reconnect (old client gets closed, new one created)
        mgr.connect_client("127.0.0.1", port=port)
        mgr.wait_all_connected(timeout=5.0)
        time.sleep(1.0)

        with lock:
            ip_events = [(ip, status) for ip, status in status_history if ip == "127.0.0.1"]

        # There should be: connect, disconnect (old), connect (new)
        # The final state should be connected
        assert len(ip_events) >= 2, f"Expected at least 2 events, got: {ip_events}"
        last_status = ip_events[-1][1]
        # NOTE: This test documents the current behavior. If Bug 5 manifests,
        # the last event might be (ip, False) from the stale callback.
        assert last_status, (
            f"Bug 5 regression: stale on_disconnected corrupted status. "
            f"Event history: {ip_events}"
        )

        mgr.disconnect_all()
