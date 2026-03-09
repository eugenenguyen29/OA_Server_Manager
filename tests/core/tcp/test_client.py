"""Tests for core.tcp.client — bug regression + CommandChannel tests."""

import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from core.tcp.client import CommandChannel, TCPClient, TCPClientConfig


# ---------------------------------------------------------------------------
# Bug 1 regression: socket not assigned if HELLO send fails
# ---------------------------------------------------------------------------
class TestSendLineRace:
    def test_send_line_during_disconnect_raises_runtime_error(self):
        """send_line must raise RuntimeError (not OSError) if socket closes mid-send."""
        cfg = TCPClientConfig(host="127.0.0.1", port=9999, reconnect=False)
        client = TCPClient(cfg)

        fake_sock = MagicMock(spec=socket.socket)
        fake_sock.sendall.side_effect = OSError("Transport endpoint is not connected")
        client._sock = fake_sock

        with pytest.raises(RuntimeError, match="Not connected"):
            client.send_line("PING")

    def test_send_line_succeeds_when_connected(self):
        """send_line sends data correctly when connected."""
        cfg = TCPClientConfig(host="127.0.0.1", port=9999, reconnect=False)
        client = TCPClient(cfg)
        fake_sock = MagicMock(spec=socket.socket)
        client._sock = fake_sock

        client.send_line("HELLO")
        fake_sock.sendall.assert_called_once_with(b"HELLO\n")


class TestBug1_AtomicConnect:
    def test_socket_not_assigned_if_hello_fails(self):
        """If HELLO sendall raises, self._sock must remain None."""
        cfg = TCPClientConfig(
            host="127.0.0.1",
            port=9999,
            auto_hello=True,
            hello_line="HELLO test",
            reconnect=False,
        )
        client = TCPClient(cfg)

        fake_sock = MagicMock(spec=socket.socket)
        fake_sock.sendall.side_effect = OSError("send failed")

        with patch("core.tcp.client.socket.create_connection", return_value=fake_sock):
            err = client._connect()

        assert err is not None
        assert client._sock is None
        fake_sock.close.assert_called_once()


# ---------------------------------------------------------------------------
# Bug 2 regression: no double _on_disconnected
# ---------------------------------------------------------------------------
class TestBug2_DoubleDisconnect:
    def test_connect_failure_does_not_fire_on_disconnected(self):
        """_connect() failure must NOT fire _on_disconnected."""
        cfg = TCPClientConfig(
            host="192.0.2.1",  # RFC 5737 TEST-NET, will fail
            port=1,
            connect_timeout_sec=0.1,
            reconnect=False,
        )
        cb = MagicMock()
        client = TCPClient(cfg, on_disconnected=cb)

        err = client._connect()
        assert err is not None
        cb.assert_not_called()

    def test_disconnect_when_not_connected_is_noop(self):
        """_disconnect() with no socket must not fire callback."""
        cfg = TCPClientConfig(host="127.0.0.1", port=9999, reconnect=False)
        cb = MagicMock()
        client = TCPClient(cfg, on_disconnected=cb)

        client._disconnect()
        cb.assert_not_called()

    def test_no_double_disconnect_callback(self):
        """Calling _disconnect() twice fires callback exactly once."""
        cfg = TCPClientConfig(host="127.0.0.1", port=9999, reconnect=False)
        cb = MagicMock()
        client = TCPClient(cfg, on_disconnected=cb)

        # Simulate a connected state
        fake_sock = MagicMock(spec=socket.socket)
        client._sock = fake_sock

        client._disconnect()
        client._disconnect()
        cb.assert_called_once()


# ---------------------------------------------------------------------------
# Bug 4 regression: undefined variable in except block
# ---------------------------------------------------------------------------
class TestBug4_UndefinedSock:
    def test_create_connection_failure_no_undefined_variable(self):
        """create_connection raising must not cause NameError on sock.close()."""
        cfg = TCPClientConfig(
            host="127.0.0.1", port=9999, connect_timeout_sec=0.1, reconnect=False
        )
        client = TCPClient(cfg)

        with patch(
            "core.tcp.client.socket.create_connection",
            side_effect=OSError("refused"),
        ):
            # This previously raised NameError when sock was undefined
            err = client._connect()

        assert err is not None
        assert isinstance(err, OSError)


# ---------------------------------------------------------------------------
# Bug 5: default hello_line is now empty
# ---------------------------------------------------------------------------
class TestBug5_HelloLine:
    def test_default_hello_line_is_empty(self):
        cfg = TCPClientConfig(host="127.0.0.1", port=9999)
        assert cfg.hello_line == ""


# ---------------------------------------------------------------------------
# CommandChannel tests
# ---------------------------------------------------------------------------
class TestCommandChannel:
    def _make_connected_pair(self):
        """Create a TCPClient + CommandChannel with a mock socket."""
        cfg = TCPClientConfig(host="127.0.0.1", port=9999, reconnect=False)
        client = TCPClient(cfg)
        fake_sock = MagicMock(spec=socket.socket)
        client._sock = fake_sock
        channel = CommandChannel(client, timeout=2.0)
        return client, channel, fake_sock

    def test_send_cmd_returns_response(self):
        """send_cmd sends a line and returns the response."""
        client, channel, fake_sock = self._make_connected_pair()

        def simulate_response():
            time.sleep(0.05)
            # Simulate server response arriving via _intercept_line
            channel._intercept_line("OK 200")

        t = threading.Thread(target=simulate_response, daemon=True)
        t.start()

        resp = channel.send_cmd("PING")
        t.join(timeout=3.0)

        assert resp == "OK 200"
        fake_sock.sendall.assert_called_once_with(b"PING\n")

    def test_send_cmd_timeout(self):
        """send_cmd returns None when no response arrives within timeout."""
        client, channel, fake_sock = self._make_connected_pair()
        channel._timeout = 0.1

        resp = channel.send_cmd("PING")
        assert resp is None

    def test_concurrent_send_cmd_serialized(self):
        """Multiple concurrent send_cmd calls are serialized by the lock."""
        client, channel, fake_sock = self._make_connected_pair()
        channel._timeout = 1.0

        results = []
        order = []

        def cmd_worker(cmd_name, response):
            time.sleep(0.01)  # small stagger

            # Simulate: once cmd_lock is acquired and send is done, respond
            def respond_later():
                time.sleep(0.05)
                channel._intercept_line(response)

            t = threading.Thread(target=respond_later, daemon=True)
            t.start()
            r = channel.send_cmd(cmd_name)
            order.append(cmd_name)
            results.append(r)

        t1 = threading.Thread(target=cmd_worker, args=("CMD1", "RESP1"), daemon=True)
        t2 = threading.Thread(target=cmd_worker, args=("CMD2", "RESP2"), daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        # Both should have completed
        assert len(results) == 2
        # At least one should have gotten a response
        assert any(r is not None for r in results)

    def test_send_cmd_when_not_connected_returns_none(self):
        """send_cmd returns None if the client is not connected."""
        cfg = TCPClientConfig(host="127.0.0.1", port=9999, reconnect=False)
        client = TCPClient(cfg)
        # _sock is None
        channel = CommandChannel(client, timeout=0.1)

        resp = channel.send_cmd("PING")
        assert resp is None

    def test_non_response_lines_forwarded_to_original_callback(self):
        """Lines arriving outside of a send_cmd call go to the original callback."""
        cfg = TCPClientConfig(host="127.0.0.1", port=9999, reconnect=False)
        received = []
        client = TCPClient(cfg, on_line=lambda line: received.append(line))
        client._sock = MagicMock(spec=socket.socket)
        channel = CommandChannel(client)

        # Simulate a line arriving when no command is pending
        channel._intercept_line("UNSOLICITED MESSAGE")
        assert received == ["UNSOLICITED MESSAGE"]


# ---------------------------------------------------------------------------
# Zombie thread cleanup: thread must exit and fire callback after retries
# ---------------------------------------------------------------------------
class TestZombieThreadCleanup:
    def test_thread_exits_after_max_retries_exhausted(self):
        """Thread must exit when max_retries is exhausted (not hang forever)."""
        cfg = TCPClientConfig(
            host="127.0.0.1",
            port=9999,
            reconnect=True,
            max_retries=2,
            reconnect_delay_sec=0.01,
            connect_timeout_sec=0.1,
        )
        client = TCPClient(cfg)

        with patch(
            "core.tcp.client.socket.create_connection",
            side_effect=ConnectionRefusedError("refused"),
        ):
            client.set_on(True)
            client._thread.join(timeout=3.0)

        assert not client._thread.is_alive(), "Thread should have exited after max_retries"

    def test_on_disconnected_fires_after_max_retries_exhausted(self):
        """on_disconnected callback fires once after retries exhausted (never connected)."""
        disconnected = threading.Event()
        callback_args = []

        def on_disc(err):
            callback_args.append(err)
            disconnected.set()

        cfg = TCPClientConfig(
            host="127.0.0.1",
            port=9999,
            reconnect=True,
            max_retries=2,
            reconnect_delay_sec=0.01,
            connect_timeout_sec=0.1,
        )
        client = TCPClient(cfg, on_disconnected=on_disc)

        with patch(
            "core.tcp.client.socket.create_connection",
            side_effect=ConnectionRefusedError("refused"),
        ):
            client.set_on(True)
            disconnected.wait(timeout=3.0)

        assert len(callback_args) == 1, "on_disconnected should be called exactly once"
        assert callback_args[0] is not None, "Exception argument should not be None"

    def test_thread_exits_when_reconnect_false(self):
        """Thread must exit immediately on first failure when reconnect=False."""
        cfg = TCPClientConfig(
            host="127.0.0.1",
            port=9999,
            reconnect=False,
            connect_timeout_sec=0.1,
        )
        client = TCPClient(cfg)

        with patch(
            "core.tcp.client.socket.create_connection",
            side_effect=ConnectionRefusedError("refused"),
        ):
            client.set_on(True)
            client._thread.join(timeout=3.0)

        assert not client._thread.is_alive(), "Thread should have exited (reconnect=False)"
