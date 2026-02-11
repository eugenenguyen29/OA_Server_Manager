import asyncio
import logging
import random
import signal
import sys
import threading
from logging.handlers import RotatingFileHandler

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Log

import core.utils.settings as settings
from core.adapters import register_default_adapters
from core.adapters.base import GameAdapter, GameAdapterConfig
from core.adapters.registry import GameAdapterRegistry
from core.network.network_utils import NetworkUtils

# Register available game adapters
register_default_adapters()

# Single adapter instance -- resolved at startup via registry
adapter: GameAdapter | None = None

async_loop = None
async_thread = None
cleanup_done = False


def _create_adapter() -> GameAdapter:
    """Create the adapter from settings using the registry."""
    # Build password field: AMP expects "username:password", OA ignores it
    amp_user = getattr(settings, "amp_username", None)
    amp_pass = getattr(settings, "amp_password", None)
    password = f"{amp_user}:{amp_pass}" if amp_user and amp_pass else None

    config = GameAdapterConfig(
        game_type=settings.game_type,
        host=getattr(settings, "amp_base_url", "localhost"),
        password=password,
        poll_interval=getattr(settings, "amp_poll_interval", 2.0),
        binary_path=getattr(settings, "oa_binary_path", None),
        port=getattr(settings, "oa_port", 27960),
    )
    if hasattr(settings, "amp_instance_id"):
        config.instance_id = settings.amp_instance_id  # type: ignore[attr-defined]
    return GameAdapterRegistry.create(config)


def cleanup():
    global cleanup_done
    if cleanup_done:
        return
    cleanup_done = True

    logging.info("Starting cleanup...")

    if adapter is not None:
        try:
            adapter.request_shutdown()
            if async_loop and async_loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    adapter.disconnect(), async_loop
                )
                future.result(timeout=2)
        except Exception as e:
            logging.warning(f"Adapter disconnect error: {e}")

    if getattr(settings, "enable_latency_control", False):
        try:
            NetworkUtils.dispose(settings.interface)
        except Exception as e:
            logging.warning(f"Network cleanup skipped: {e}")

    if async_loop and async_loop.is_running():
        async_loop.call_soon_threadsafe(async_loop.stop)

    logging.info("Cleanup completed")


def run_async_loop(adapter_ref: GameAdapter | None = None):
    global async_loop
    async_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(async_loop)
    if adapter_ref is not None:
        adapter_ref.set_async_loop(async_loop)
    async_loop.run_forever()


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
            Label("Shutdown server and exit?"),
            Horizontal(
                Button("Yes", variant="error", id="yes"),
                Button("Cancel", variant="default", id="cancel"),
            ),
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class AdminApp(App):
    CSS_PATH = "tui_main.tcss"
    BINDINGS = [Binding("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        game_label = f"Game: {settings.game_type.upper()}"

        yield Vertical(
            Horizontal(
                Label(game_label, id="game-type-label"),
                Input(placeholder="Enter command...", id="input"),
                Label("State: Idle", id="status-state"),
                Label("Round: 0/0", id="status-round"),
                Button("Add Bot", id="add-bot-btn", variant="primary"),
                Button("Remove All Bot", id="remove-bot-btn", variant="default"),
                id="top-panel",
            ),
            Horizontal(
                Vertical(
                    Horizontal(
                        Button(
                            "Start Server", id="start-server-btn", variant="success"
                        ),
                        Button("Stop Server", id="kill-server-btn", variant="error"),
                        id="server-control-buttons",
                    ),
                    DataTable(id="user-table"),
                    id="left-panel",
                ),
                Vertical(Log(id="app-log"), Log(id="server-log"), id="right-panel"),
                id="content-panel",
            ),
            id="main-container",
        )

    @work(thread=True)
    def start_adapter_worker(self):
        """Start adapter: connect then run server loop in background thread."""
        global adapter
        if adapter is None:
            adapter = _create_adapter()
            if async_loop:
                adapter.set_async_loop(async_loop)

        adapter.set_output_handler(
            lambda msg: self.call_from_thread(self._update_server_log, msg)
        )

        logging.info("Adapter worker starting...")

        # Connect (async)
        if async_loop and async_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(adapter.connect(), async_loop)
            try:
                success = future.result(timeout=35)
                if not success:
                    logging.error("Adapter connection failed")
                    self.call_from_thread(self._update_server_log, "Connection failed")
                    return
                logging.info("Adapter connected successfully")
                self.call_from_thread(self._update_server_log, "Connected!")
            except Exception as e:
                logging.error(f"Adapter connect error: {e}")
                self.call_from_thread(self._update_server_log, f"Connection error: {e}")
                return

        # Run the blocking server loop (reads messages, dispatches events)
        adapter.run_server_loop()

    def _update_server_log(self, message: str):
        """Update server log from worker thread."""
        try:
            server_log = self.query_one("#server-log", Log)
            for line in message.split("\n"):
                server_log.write_line(line)
        except Exception as e:
            logging.debug(f"Failed to update server log: {e}")

    def on_mount(self) -> None:
        global async_thread, adapter

        app_log = self.query_one("#app-log", Log)
        server_log = self.query_one("#server-log", Log)

        app_log.border_title = "App Logs"
        server_log.border_title = "Server Output"

        input_widget = self.query_one("#input", Input)
        input_widget.border_title = "Command Input"

        user_table = self.query_one("#user-table", DataTable)
        user_table.border_title = "Connected Users"
        user_table.cursor_type = "row"
        user_table.add_columns("ID", "Name", "IP", "OBS", "Action")

        handler = TUILogHandler(app_log)
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger("core.adapters.amp").setLevel(logging.INFO)

        # Create adapter eagerly so it's available before the async thread starts
        adapter = _create_adapter()

        async_thread = threading.Thread(
            target=run_async_loop, args=(adapter,), daemon=True
        )
        async_thread.start()

        self.update_status_display()
        self.setup_periodic_updates()

    def on_input_submitted(self, message: Input.Submitted) -> None:
        input_id = message.input.id
        value = message.value.strip()

        if not value:
            return

        if input_id == "input":
            if value.lower() in ["quit", "exit"]:
                self.action_quit()
            else:
                self._send_adapter_command(value)
                message.input.value = ""

    def _send_adapter_command(self, command: str):
        """Send command to game server via adapter."""
        if adapter is None or not adapter.is_connected:
            logging.warning("Adapter not connected - cannot send command")
            return

        if not async_loop or not async_loop.is_running():
            logging.warning("Async loop not running - cannot send command")
            return

        self._update_server_log(f"> {command}")

        async def do_command():
            try:
                await adapter.send_command(command)
            except Exception as e:
                logging.error(f"Command error: {e}")
                self.call_from_thread(self._update_server_log, f"Command error: {e}")

        asyncio.run_coroutine_threadsafe(do_command(), async_loop)

    def action_quit(self) -> None:
        def check_quit(confirmed: bool | None) -> None:
            if confirmed:
                self._do_quit()

        self.push_screen(QuitConfirmScreen(), check_quit)

    @work(thread=True)
    def _do_quit(self):
        """Run cleanup in background thread so TUI doesn't freeze."""
        cleanup()
        self.call_from_thread(self.exit)

    def update_status_display(self):
        try:
            state_label = self.query_one("#status-state", Label)
            round_label = self.query_one("#status-round", Label)

            current_state = "Idle"
            current_round = 0
            max_rounds = 0

            if adapter is not None:
                current_state = adapter.game_state_manager.get_current_state().name
                current_round = adapter.game_state_manager.round_count
                max_rounds = adapter.game_state_manager.max_rounds

            state_label.update(f"State: {current_state}")
            round_label.update(f"Round: {current_round}/{max_rounds}")
        except Exception as e:
            logging.error(f"Error updating status display: {e}")

    def update_user_table(self):
        try:
            user_table = self.query_one("#user-table", DataTable)
            user_table.clear()

            if adapter is None:
                return

            network_mgr = adapter.network_manager

            for client_id, client_type in network_mgr.client_type_map.items():
                name = network_mgr.client_name_map.get(client_id, f"Client_{client_id}")
                client_ip = network_mgr.client_ip_map.get(client_id, "N/A")

                if client_type == "BOT":
                    obs_status = "N/A"
                else:
                    obs_status = (
                        "+"
                        if (
                            client_ip
                            and adapter.obs_connection_manager.is_client_connected(
                                client_ip
                            )
                        )
                        else "-"
                    )

                user_table.add_row(str(client_id), name, client_ip, obs_status, "Kick")
        except Exception as e:
            logging.error(f"Error updating user table: {e}")

    def update_start_button(self):
        try:
            start_btn = self.query_one("#start-server-btn", Button)
            start_btn.disabled = adapter is not None and adapter.is_connected
        except Exception as e:
            logging.error(f"Error updating start button: {e}")

    def setup_periodic_updates(self):
        def periodic_update():
            if not cleanup_done:
                self.update_status_display()
                self.update_user_table()
                self.update_start_button()
                timer = threading.Timer(2.0, periodic_update)
                timer.daemon = True
                timer.start()

        timer = threading.Timer(2.0, periodic_update)
        timer.daemon = True
        timer.start()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-bot-btn":
            bot_names = [
                "Angelyss",
                "Arachna",
                "Major",
                "Sarge",
                "Skelebot",
                "Merman",
                "Beret",
                "Kyonshi",
            ]
            bot_name = random.choice(bot_names)
            difficulty = settings.bot_difficulty
            self._send_adapter_command(f"addbot {bot_name} {difficulty}")
            logging.info(
                f"Bot addition requested: {bot_name} (difficulty {difficulty})"
            )

        elif event.button.id == "remove-bot-btn":
            self._send_adapter_command("kick allbots")
            logging.info("All bots removal requested")

        elif event.button.id == "start-server-btn":
            self.start_adapter_worker()

        elif event.button.id == "kill-server-btn":
            self._stop_adapter()

    def _stop_adapter(self):
        """Stop/disconnect the adapter."""
        if adapter is None:
            return

        adapter.request_shutdown()

        async def do_disconnect():
            try:
                await adapter.disconnect()
                logging.info("Adapter disconnected")
            except Exception as e:
                logging.error(f"Adapter disconnect error: {e}")

        if async_loop and async_loop.is_running():
            asyncio.run_coroutine_threadsafe(do_disconnect(), async_loop)

        self.update_user_table()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "user-table":
            try:
                user_table = self.query_one("#user-table", DataTable)
                row_data = user_table.get_row(event.row_key)
                client_id = int(row_data[0])
                user_name = row_data[1]

                logging.info(f"Kicking user: {user_name} (ID: {client_id})")

                if adapter is not None and async_loop and async_loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        adapter.kick_client(client_id), async_loop
                    )
            except Exception as e:
                logging.error(f"Error kicking user: {e}")


def signal_handler(sig, frame):
    cleanup()
    sys.exit(0)


def main():
    file_handler = RotatingFileHandler(
        "tui_app.log", maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logging.getLogger().addHandler(file_handler)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    app = AdminApp()
    app.run()


if __name__ == "__main__":
    main()
