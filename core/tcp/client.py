import socket
import threading
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

    # protocol details
    hello_line: str = "HELLO from TCP_client"
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

    def send_line(self, text: str) -> None:
        data = (text + "\n").encode("utf-8")
        with self._lock:
            sock = self._sock
        if not sock:
            raise RuntimeError("Not connected")
        sock.sendall(data)

    # ---------- internals ----------
    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                want_on = self._want_on
                connected = self._sock is not None

            if not want_on:
                if connected:
                    self._disconnect()
                import time

                time.sleep(0.1)
                continue

            if not connected:
                err = self._connect()
                if err is not None:
                    if self.cfg.reconnect:
                        import time

                        time.sleep(self.cfg.reconnect_delay_sec)
                        continue
                    with self._lock:
                        self._want_on = False
                    continue

            # connected: pump incoming lines
            try:
                self._pump()
            except Exception as e:
                self._disconnect(e)
                if self.cfg.reconnect:
                    import time

                    time.sleep(self.cfg.reconnect_delay_sec)

        self._disconnect()

    def _connect(self) -> Optional[Exception]:
        try:
            sock = socket.create_connection(
                (self.cfg.host, self.cfg.port),
                timeout=self.cfg.connect_timeout_sec,
            )
            sock.settimeout(self.cfg.recv_timeout_sec)

            with self._lock:
                self._sock = sock
                self._rx_buf = b""

            if self._on_connected:
                self._on_connected()

            # send HELLO (server expects this first)
            if self.cfg.auto_hello:
                self.send_line(self.cfg.hello_line)

            return None
        except Exception as e:
            try:
                sock.close()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            if self._on_disconnected:
                self._on_disconnected(e)
            return e

    def _disconnect(self, reason: Optional[Exception] = None) -> None:
        with self._lock:
            sock = self._sock
            self._sock = None
            self._rx_buf = b""

        if sock:
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
