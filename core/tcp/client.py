import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class TCPClientConfig:
    host: str
    port: int
    connect_timeout_sec: float = 5.0
    recv_timeout_sec: float = 0.5  # small so loop can notice "off"
    reconnect: bool = True
    reconnect_delay_sec: float = 1.5
    max_retries: int = 0  # 0 = unlimited when reconnect=True

    # protocol details
    hello_line: str = ""  # manager sets per-client value (Bug 5 fix)
    auto_hello: bool = True


class TCPClient:
    def __init__(
        self,
        config: TCPClientConfig,
        on_connected: Optional[Callable[[], None]] = None,
        on_disconnected: Optional[Callable[[Optional[Exception]], None]] = None,
        on_line: Optional[Callable[[str], None]] = None,
    ):
        self.cfg = config
        self._lock = threading.RLock()

        self._sock: Optional[socket.socket] = None
        self._rx_buf = b""

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._connected_event = threading.Event()
        self._want_on = False

        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._on_line = on_line

    # ---------- public API ----------
    def set_on(self, on: bool) -> None:
        with self._lock:
            self._want_on = on

        if on:
            self._ensure_thread()
        else:
            self._disconnect()

    def is_connected(self) -> bool:
        with self._lock:
            return self._sock is not None

    def close(self) -> None:
        with self._lock:
            self._want_on = False
        self._stop_event.set()
        self._disconnect()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def wait_until_connected(self, timeout: float = 10.0) -> bool:
        """Block until the socket is connected or timeout expires."""
        return self._connected_event.wait(timeout=timeout)

    def send_line(self, text: str) -> None:
        data = (text + "\n").encode("utf-8")
        with self._lock:
            sock = self._sock
        if not sock:
            raise RuntimeError("Not connected")
        try:
            sock.sendall(data)
        except OSError as e:
            raise RuntimeError("Not connected") from e

    # ---------- internals ----------
    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

    def _run_loop(self) -> None:
        retries = 0
        last_err: Optional[Exception] = None
        while not self._stop_event.is_set():
            with self._lock:
                want_on = self._want_on
                connected = self._sock is not None

            if not want_on:
                if connected:
                    self._disconnect()
                time.sleep(0.1)
                continue

            if not connected:
                err = self._connect()
                if err is not None:
                    if self.cfg.reconnect:
                        retries += 1
                        if self.cfg.max_retries > 0 and retries >= self.cfg.max_retries:
                            with self._lock:
                                self._want_on = False
                            self._stop_event.set()
                            last_err = err
                            continue
                        time.sleep(self.cfg.reconnect_delay_sec)
                        continue
                    # reconnect=False: give up immediately
                    with self._lock:
                        self._want_on = False
                    self._stop_event.set()
                    last_err = err
                    continue
                retries = 0  # reset on successful connect

            # connected: pump incoming lines
            try:
                self._pump()
            except Exception as e:
                self._disconnect(e)
                if self.cfg.reconnect:
                    retries += 1
                    if self.cfg.max_retries > 0 and retries >= self.cfg.max_retries:
                        with self._lock:
                            self._want_on = False
                        self._stop_event.set()
                        continue
                    time.sleep(self.cfg.reconnect_delay_sec)

        self._disconnect()

        # If retries were exhausted before ever connecting, _disconnect()
        # won't fire the callback (sock is None).  Notify the manager so
        # it can clean up the zombie entry.
        if last_err is not None and self._on_disconnected:
            with self._lock:
                still_none = self._sock is None
            if still_none:
                self._on_disconnected(last_err)

    def _connect(self) -> Optional[Exception]:
        """Atomic connect + handshake. Only assigns self._sock on full success."""
        sock: Optional[socket.socket] = None
        try:
            sock = socket.create_connection(
                (self.cfg.host, self.cfg.port),
                timeout=self.cfg.connect_timeout_sec,
            )
            sock.settimeout(self.cfg.recv_timeout_sec)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            # Send HELLO BEFORE assigning to self._sock (Bug 1 fix)
            if self.cfg.auto_hello and self.cfg.hello_line:
                sock.sendall((self.cfg.hello_line + "\n").encode("utf-8"))

            # Only assign after full success
            with self._lock:
                self._sock = sock
                self._rx_buf = b""

            self._connected_event.set()

            if self._on_connected:
                self._on_connected()

            return None
        except Exception as e:
            # Bug 4 fix: guard against sock being None
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            # Bug 2 fix: do NOT fire _on_disconnected here — we were never
            # in the "connected" state so there's nothing to disconnect from
            return e

    def _disconnect(self, reason: Optional[Exception] = None) -> None:
        """Disconnect. Only fires callback when actually transitioning from connected."""
        self._connected_event.clear()
        with self._lock:
            sock = self._sock
            self._sock = None
            self._rx_buf = b""

        # Bug 2 fix: only fire callback if we actually had a socket
        if sock is None:
            return

        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass

        if self._on_disconnected:
            self._on_disconnected(reason)

    def _pump(self) -> None:
        """Read bytes, split into \\n lines, callback each line."""
        with self._lock:
            sock = self._sock
        if not sock:
            return

        try:
            chunk = sock.recv(4096)
            if chunk == b"":
                raise ConnectionResetError("Server closed connection")

            self._rx_buf += chunk

            while b"\n" in self._rx_buf:
                line, self._rx_buf = self._rx_buf.split(b"\n", 1)
                text = line.decode("utf-8", errors="replace").strip()
                if text and self._on_line:
                    self._on_line(text)

        except socket.timeout:
            # normal, no data yet
            return


class CommandChannel:
    """Thread-safe request/response over an existing TCPClient.

    Intercepts the on_line callback to capture responses.
    Serializes concurrent send_cmd() calls via a lock.
    """

    def __init__(self, client: TCPClient, timeout: float = 5.0):
        self._client = client
        self._timeout = timeout
        self._cmd_lock = threading.Lock()
        self._response_event = threading.Event()
        self._response_line: Optional[str] = None
        self._awaiting_response = False

        # Chain onto the client's existing on_line callback
        self._original_on_line = client._on_line
        client._on_line = self._intercept_line

    def _intercept_line(self, line: str) -> None:
        """Capture first line as response if a command is pending."""
        if self._awaiting_response:
            self._response_line = line
            self._response_event.set()
        elif self._original_on_line:
            self._original_on_line(line)

    def send_cmd(self, cmd: str) -> Optional[str]:
        """Send a command and block until a response line is received.

        Returns the response line, or None on timeout.
        """
        with self._cmd_lock:
            self._response_event.clear()
            self._response_line = None
            self._awaiting_response = True
            try:
                self._client.send_line(cmd)
            except Exception:
                self._awaiting_response = False
                return None
            got = self._response_event.wait(timeout=self._timeout)
            self._awaiting_response = False
            return self._response_line if got else None
