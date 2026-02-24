import logging
import sys
import threading

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Log

from core.tcp.manager import TCPClientManager


class TUILogHandler(logging.Handler):
    def __init__(self, log_widget):
        super().__init__()
        self.log_widget = log_widget

    def emit(self, record):
        try:
            msg = self.format(record)
            self.log_widget.write_line(msg)
        except Exception as e:
            print(f"TUI log error: {e}", file=sys.stderr)


class QuitConfirmScreen(ModalScreen[bool]):
    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Disconnect all clients and exit?"),
            Horizontal(
                Button("Yes", variant="error", id="yes"),
                Button("Cancel", variant="default", id="cancel"),
            ),
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class TCPTestApp(App):
    CSS_PATH = "tcp_tui.tcss"
    BINDINGS = [Binding("q", "quit", "Quit")]

    def __init__(self):
        super().__init__()
        self._manager = TCPClientManager(
            reconnect=False,
            on_client_connected=self._on_client_connected,
            on_client_disconnected=self._on_client_disconnected,
            on_client_line=self._on_client_line,
        )
        self._lock = threading.Lock()
        # Track all known client keys (ip:port) for table display
        self._client_keys: list[str] = []

    def compose(self) -> ComposeResult:
        yield Vertical(
            Horizontal(
                Input(value="127.0.0.1", id="host-input"),
                Input(value="5000", id="port-input"),
                Button("Connect", id="connect-btn", variant="success"),
                Button("Disconnect All", id="disconnect-all-btn", variant="error"),
                Label("Clients: 0", id="connection-count"),
                id="top-panel",
            ),
            Horizontal(
                Vertical(
                    DataTable(id="client-table"),
                    id="left-panel",
                ),
                Vertical(
                    Log(id="received-log", max_lines=2000),
                    Vertical(
                        Input(
                            placeholder="Message to send (Enter = selected, Ctrl+B = broadcast)",
                            id="send-input",
                        ),
                        Log(id="sent-log", max_lines=1000),
                        id="send-panel",
                    ),
                    id="right-panel",
                ),
                id="content-panel",
            ),
            id="main-container",
        )

    def on_mount(self) -> None:
        host_input = self.query_one("#host-input", Input)
        host_input.border_title = "Host"

        port_input = self.query_one("#port-input", Input)
        port_input.border_title = "Port"

        send_input = self.query_one("#send-input", Input)
        send_input.border_title = "Send Message"

        # Cache widget references to avoid repeated query_one calls
        self._client_table = self.query_one("#client-table", DataTable)
        self._client_table.border_title = "Connected Clients"
        self._client_table.cursor_type = "row"
        self._client_table.add_columns("IP", "Port", "Status")

        self._received_log = self.query_one("#received-log", Log)
        self._received_log.border_title = "Received Lines"

        self._sent_log = self.query_one("#sent-log", Log)
        self._sent_log.border_title = "Sent Messages"

        self._connection_count_label = self.query_one("#connection-count", Label)

        handler = TUILogHandler(self._received_log)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
        logging.getLogger("core.tcp").addHandler(handler)
        logging.getLogger("core.tcp").setLevel(logging.INFO)

        self.set_interval(1.0, self._refresh_table)

    def _thread_safe_log(self, text: str) -> None:
        """Log to received-log, safe to call from any thread."""
        try:
            self.call_from_thread(self._log_received, text)
        except RuntimeError:
            self._log_received(text)

    def _on_client_connected(self, ip: str) -> None:
        self._thread_safe_log(f"[{ip}] Connected")

    def _on_client_disconnected(self, ip: str, err: Exception | None) -> None:
        reason = str(err) if err else "clean"
        self._thread_safe_log(f"[{ip}] Disconnected ({reason})")

    def _on_client_line(self, ip: str, line: str) -> None:
        self._thread_safe_log(f"[{ip}] {line}")

    def _log_received(self, text: str) -> None:
        try:
            self._received_log.write_line(text)
        except Exception:
            pass

    def _log_sent(self, text: str) -> None:
        try:
            self._sent_log.write_line(text)
        except Exception:
            pass

    def _refresh_table(self) -> None:
        try:
            self._client_table.clear()

            with self._lock:
                keys = list(self._client_keys)

            for key in keys:
                ip, port_str = key.rsplit(":", 1)
                connected = self._manager.is_client_connected(ip)
                status = "Connected" if connected else "Disconnected"
                self._client_table.add_row(ip, port_str, status, key=key)

            count = self._manager.get_connection_count()
            self._connection_count_label.update(f"Clients: {count}")
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "connect-btn":
            self._do_connect()
        elif event.button.id == "disconnect-all-btn":
            self._do_disconnect_all()

    def _do_connect(self) -> None:
        host = self.query_one("#host-input", Input).value.strip()
        port_str = self.query_one("#port-input", Input).value.strip()

        if not host or not port_str:
            self._log_received("Error: Host and port are required")
            return

        try:
            port = int(port_str)
        except ValueError:
            self._log_received(f"Error: Invalid port '{port_str}'")
            return

        key = f"{host}:{port}"
        with self._lock:
            if key not in self._client_keys:
                self._client_keys.append(key)

        self._manager.connect_client(host, port)
        self._log_received(f"Connecting to {host}:{port}...")

    @work(thread=True)
    def _do_disconnect_all(self) -> None:
        self._manager.disconnect_all()
        with self._lock:
            self._client_keys.clear()
        self.call_from_thread(self._log_received, "All clients disconnected")

    def _get_selected_key(self) -> str | None:
        try:
            if self._client_table.cursor_row is not None and self._client_table.row_count > 0:
                cursor_key = list(self._client_table.rows.keys())[self._client_table.cursor_row]
                return str(cursor_key.value)
        except Exception:
            pass
        return None

    def on_input_submitted(self, message: Input.Submitted) -> None:
        if message.input.id == "send-input":
            text = message.value.strip()
            if not text:
                return

            selected = self._get_selected_key()
            if selected is None:
                self._log_received("Error: No client selected in table")
                return

            ip = selected.rsplit(":", 1)[0]
            try:
                self._manager.send_to_client(ip, text)
                self._log_sent(f"[{selected}] {text}")
            except Exception as e:
                self._log_received(f"Send error: {e}")

            message.input.value = ""

        elif message.input.id == "host-input" or message.input.id == "port-input":
            self._do_connect()

    def on_key(self, event) -> None:
        if event.key == "ctrl+b":
            send_input = self.query_one("#send-input", Input)
            text = send_input.value.strip()
            if not text:
                return

            results = self._manager.send_to_all(text)
            for ip, ok in results.items():
                status = "OK" if ok else "FAILED"
                self._log_sent(f"[broadcast → {ip}] ({status}) {text}")

            if not results:
                self._log_received("Broadcast: no clients connected")

            send_input.value = ""

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "client-table":
            key = str(event.row_key.value)
            ip = key.rsplit(":", 1)[0]
            connected = self._manager.is_client_connected(ip)
            self._disconnect_row(key, ip, connected)

    @work(thread=True)
    def _disconnect_row(self, key: str, ip: str, was_connected: bool) -> None:
        if was_connected:
            self._manager.disconnect_client(ip)
        with self._lock:
            if key in self._client_keys:
                self._client_keys.remove(key)
        msg = f"Disconnected {key}" if was_connected else f"Removed stale entry {key}"
        self.call_from_thread(self._log_received, msg)

    def action_quit(self) -> None:
        def check_quit(confirmed: bool | None) -> None:
            if confirmed:
                self._do_quit()

        self.push_screen(QuitConfirmScreen(), check_quit)

    @work(thread=True)
    def _do_quit(self) -> None:
        self._manager.disconnect_all()
        with self._lock:
            self._client_keys.clear()
        self.call_from_thread(self.exit)


def main():
    app = TCPTestApp()
    app.run()


if __name__ == "__main__":
    main()
