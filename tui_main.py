import asyncio
import logging
import signal
import sys
import threading
from logging.handlers import RotatingFileHandler
from typing import Optional

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


def _build_dota2_config() -> GameAdapterConfig:
    logger = logging.getLogger(__name__)
    logger.info(f"[Dota2] Script path: {settings.dota2_script_path}")
    return GameAdapterConfig(
        game_type="dota2",
        binary_path=settings.dota2_script_path,
        poll_interval=settings.dota2_poll_interval,
    )


def _build_amp_config() -> GameAdapterConfig:
    amp_user = getattr(settings, "amp_username", None)
    amp_pass = getattr(settings, "amp_password", None)
    password = f"{amp_user}:{amp_pass}" if amp_user and amp_pass else None
    config = GameAdapterConfig(
        game_type="amp",
        host=settings.amp_base_url,
        password=password,
        poll_interval=settings.amp_poll_interval,
    )
    if hasattr(settings, "amp_instance_id"):
        config.instance_id = settings.amp_instance_id  # type: ignore[attr-defined]
    return config


def _build_openarena_config() -> GameAdapterConfig:
    return GameAdapterConfig(
        game_type="openarena",
        host="localhost",
        port=settings.oa_port,
        binary_path=settings.oa_binary_path,
    )


_CONFIG_BUILDERS = {
    "dota2": _build_dota2_config,
    "amp": _build_amp_config,
    "openarena": _build_openarena_config,
}


def _create_adapter() -> GameAdapter:
    """Create the adapter from settings using the registry."""
    game_type = settings.game_type
    builder = _CONFIG_BUILDERS.get(game_type, _build_openarena_config)
    config = builder()
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
    def __init__(self, log_widget, app):
        super().__init__()
        self.log_widget = log_widget
        self.app = app

    def emit(self, record):
        try:
            msg = self.format(record)
            self.app.call_from_thread(self.log_widget.write_line, msg)
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
                    Horizontal(
                        Button("Start Rec", id="obs-rec-btn", variant="warning"),
                        Button("Start TCP", id="tcp-btn", variant="warning"),
                        id="module-control-buttons",
                    ),
                    Horizontal(
                        Button("Start Exp 1", id="exp-1-btn", variant="warning"),
                        Button("Start Exp 2", id="exp-2-btn", variant="warning"),
                        Button("Start Exp 3", id="exp-3-btn", variant="warning"),
                        id="experiment-control-buttons",
                    ),
                    Label("", id="exp-status-label"),
                    DataTable(id="user-table"),
                    id="left-panel",
                ),
                Vertical(
                    Log(id="app-log", max_lines=2000),
                    Log(id="server-log", max_lines=2000),
                    id="right-panel",
                ),
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
            for line in message.split("\n"):
                self._server_log.write_line(line)
        except Exception as e:
            logging.debug(f"Failed to update server log: {e}")

    def on_mount(self) -> None:
        global async_thread, adapter

        # Cache widget references to avoid repeated query_one calls
        self._app_log = self.query_one("#app-log", Log)
        self._server_log = self.query_one("#server-log", Log)
        self._user_table = self.query_one("#user-table", DataTable)
        self._state_label = self.query_one("#status-state", Label)
        self._round_label = self.query_one("#status-round", Label)
        self._start_btn = self.query_one("#start-server-btn", Button)
        self._obs_rec_btn = self.query_one("#obs-rec-btn", Button)
        self._tcp_btn = self.query_one("#tcp-btn", Button)
        self._exp_1_btn = self.query_one("#exp-1-btn", Button)
        self._exp_2_btn = self.query_one("#exp-2-btn", Button)
        self._exp_3_btn = self.query_one("#exp-3-btn", Button)
        self._exp_status_label = self.query_one("#exp-status-label", Label)
        self._obs_recording = False
        self._tcp_active = False
        self._tcp_busy = False
        self._exp_active: Optional[str] = None
        self._prev_table_data = []

        self._app_log.border_title = "App Logs"
        self._server_log.border_title = "Server Output"

        input_widget = self.query_one("#input", Input)
        input_widget.border_title = "Command Input"

        self._user_table.border_title = "Connected Users"
        self._user_table.cursor_type = "row"
        self._user_table.add_columns("ID", "Name", "IP", "OBS", "TCP", "Action")

        handler = TUILogHandler(self._app_log, self)
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
            current_state = "Idle"
            current_round = 0
            max_rounds = 0

            if adapter is not None:
                current_state = adapter.game_state_manager.get_current_state().name
                current_round = adapter.game_state_manager.round_count
                max_rounds = adapter.game_state_manager.max_rounds

            self._state_label.update(f"State: {current_state}")
            self._round_label.update(f"Round: {current_round}/{max_rounds}")
        except Exception as e:
            logging.error(f"Error updating status display: {e}")

    def update_user_table(self):
        try:
            if adapter is None:
                if self._prev_table_data:
                    self._prev_table_data = []
                    self._user_table.clear()
                return

            network_mgr = adapter.network_manager
            new_data = []

            for client_id, client_type in network_mgr.client_type_map.items():
                name = network_mgr.client_name_map.get(client_id, f"Client_{client_id}")
                client_ip = network_mgr.client_ip_map.get(client_id, "N/A")

                if client_type == "BOT":
                    obs_status = "N/A"
                    tcp_status = "N/A"
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
                    tcp_status = (
                        "+"
                        if (
                            client_ip
                            and adapter.tcp_connection_manager.is_client_connected(
                                client_ip
                            )
                        )
                        else "-"
                    )

                new_data.append(
                    (str(client_id), name, client_ip, obs_status, tcp_status, "Kick")
                )

            if new_data != self._prev_table_data:
                self._prev_table_data = new_data
                self._user_table.clear()
                for row in new_data:
                    self._user_table.add_row(*row)
        except Exception as e:
            logging.error(f"Error updating user table: {e}")

    def update_start_button(self):
        try:
            self._start_btn.disabled = adapter is not None and adapter.is_connected
        except Exception as e:
            logging.error(f"Error updating start button: {e}")

    def setup_periodic_updates(self):
        self.set_interval(2.0, self._periodic_update)
        self.set_interval(1.0, self._update_experiment_status)

    def _periodic_update(self):
        if not cleanup_done:
            self.update_status_display()
            self.update_user_table()
            self.update_start_button()

    def _update_experiment_status(self):
        if adapter is None or not adapter.is_connected:
            self._exp_status_label.update("")
            return
        try:
            exp_mgr = adapter.experiment_manager
            if not exp_mgr.is_active:
                self._exp_status_label.update("")
                return

            exp_num = exp_mgr.active_experiment
            step = exp_mgr.current_latency_step
            remaining = exp_mgr.seconds_until_next_step

            if step is not None and remaining is not None:
                idx, lat_ms = step
                total = exp_mgr.latency_step_count
                mins = int(remaining) // 60
                secs = int(remaining) % 60
                self._exp_status_label.update(
                    f"Exp {exp_num} | Step {idx + 1}/{total}: {lat_ms}ms "
                    f"| Next in {mins}:{secs:02d}"
                )
            else:
                self._exp_status_label.update(
                    f"Exp {exp_num} | Baseline (no latency schedule)"
                )
        except NotImplementedError:
            self._exp_status_label.update("")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-bot-btn":
            self._run_adapter_coro(adapter.add_bot())
            logging.info("Bot addition requested")

        elif event.button.id == "remove-bot-btn":
            self._run_adapter_coro(adapter.remove_all_bots())
            logging.info("All bots removal requested")

        elif event.button.id == "start-server-btn":
            self.start_adapter_worker()

        elif event.button.id == "kill-server-btn":
            self._stop_adapter()

        elif event.button.id == "obs-rec-btn":
            if not self._obs_recording:
                self._start_obs_recording()
            else:
                self._stop_obs_recording()

        elif event.button.id == "tcp-btn":
            if self._tcp_busy:
                return
            self._tcp_busy = True
            self._tcp_btn.disabled = True
            if not self._tcp_active:
                self._start_tcp()
            else:
                self._stop_tcp()

        elif event.button.id in ("exp-1-btn", "exp-2-btn", "exp-3-btn"):
            exp_num = event.button.id.split("-")[1]  # "1", "2", "3"
            if self._exp_active is None:
                self._start_experiment(int(exp_num))
            elif self._exp_active == exp_num:
                self._stop_experiment()

    # ── OBS Recording ─────────────────────────────────────────

    def _start_obs_recording(self):
        if adapter is None or not adapter.is_connected:
            logging.warning("Adapter not connected - cannot start recording")
            return

        async def do_start():
            try:
                obs_mgr = adapter.obs_connection_manager.obs_manager
                results = await obs_mgr.start_all_recordings()
                started = sum(1 for s in results.values() if s)
                logging.info(
                    f"OBS recording started for {started}/{len(results)} clients"
                )
                if any(results.values()):
                    self.call_from_thread(self._set_obs_recording_state, True)
            except Exception as e:
                logging.error(f"Failed to start OBS recording: {e}")

        if async_loop and async_loop.is_running():
            asyncio.run_coroutine_threadsafe(do_start(), async_loop)

    def _stop_obs_recording(self):
        if adapter is None or not adapter.is_connected:
            logging.warning("Adapter not connected - cannot stop recording")
            return

        async def do_stop():
            try:
                obs_mgr = adapter.obs_connection_manager.obs_manager
                results = await obs_mgr.stop_all_recordings()
                stopped = sum(1 for s in results.values() if s)
                logging.info(
                    f"OBS recording stopped for {stopped}/{len(results)} clients"
                )
                self.call_from_thread(self._set_obs_recording_state, False)
            except Exception as e:
                logging.error(f"Failed to stop OBS recording: {e}")

        if async_loop and async_loop.is_running():
            asyncio.run_coroutine_threadsafe(do_stop(), async_loop)

    def _set_obs_recording_state(self, recording: bool):
        self._obs_recording = recording
        if recording:
            self._obs_rec_btn.label = "Stop Rec"
            self._obs_rec_btn.variant = "error"
        else:
            self._obs_rec_btn.label = "Start Rec"
            self._obs_rec_btn.variant = "warning"

    # ── TCP Module ───────────────────────────────────────────

    def _set_tcp_busy(self, busy: bool):
        self._tcp_busy = busy
        self._tcp_btn.disabled = busy

    @work(thread=True)
    def _start_tcp(self):
        if adapter is None or not adapter.is_connected:
            logging.warning("Adapter not connected - cannot start TCP")
            self.call_from_thread(self._set_tcp_busy, False)
            return

        try:
            tcp_mgr = adapter.tcp_connection_manager
            network_mgr = adapter.network_manager
            count = 0
            # Snapshot to avoid RuntimeError from dict mutation during iteration
            client_items = list(network_mgr.client_type_map.items())
            for client_id, client_type in client_items:
                if client_type == "HUMAN":
                    client_ip = network_mgr.client_ip_map.get(client_id)
                    if client_ip and not tcp_mgr.is_client_connected(client_ip):
                        tcp_mgr.connect_single_client(client_ip, network_mgr)
                        count += 1
            logging.info(f"TCP connections started for {count} human clients")
            results = adapter.apply_tcp_latencies()
            applied = sum(1 for r in results.values() if r is not None)
            logging.info(f"TCP latencies applied to {applied}/{len(results)} clients")
            self.call_from_thread(self._set_tcp_state, True)
        except Exception as e:
            logging.error(f"Failed to start TCP connections: {e}")
            try:
                adapter.tcp_connection_manager.disconnect_all()
            except Exception:
                pass
        finally:
            self.call_from_thread(self._set_tcp_busy, False)

    @work(thread=True)
    def _stop_tcp(self):
        if adapter is None:
            self.call_from_thread(self._set_tcp_busy, False)
            return

        try:
            adapter.tcp_connection_manager.disconnect_all()
            logging.info("All TCP connections stopped")
        except Exception as e:
            logging.error(f"Failed to stop TCP connections: {e}")
        finally:
            self.call_from_thread(self._set_tcp_state, False)
            self.call_from_thread(self._set_tcp_busy, False)

    def _set_tcp_state(self, active: bool):
        self._tcp_active = active
        if active:
            self._tcp_btn.label = "Stop TCP"
            self._tcp_btn.variant = "error"
        else:
            self._tcp_btn.label = "Start TCP"
            self._tcp_btn.variant = "warning"

    # ── Experiment Controls ──────────────────────────────────

    @work(thread=True)
    def _start_experiment(self, exp_num: int):
        if adapter is None or not adapter.is_connected:
            logging.warning("Adapter not connected - cannot start experiment")
            return

        if not self._tcp_active:
            logging.warning("TCP not active - start TCP connections first")
            return

        try:
            if adapter.experiment_manager.start(exp_num):
                self.call_from_thread(self._set_exp_state, str(exp_num))
            else:
                logging.warning(f"Experiment {exp_num} could not start")
        except Exception as e:
            logging.error(f"Failed to start experiment {exp_num}: {e}")

    @work(thread=True)
    def _stop_experiment(self):
        if adapter is None:
            return

        try:
            adapter.experiment_manager.stop()
        except Exception as e:
            logging.error(f"Failed to stop experiment: {e}")
        # Always reset TUI state to prevent stuck buttons
        self.call_from_thread(self._set_exp_state, None)

    def _set_exp_state(self, exp_num: Optional[str]):
        """Update experiment button states. Called from main thread."""
        self._exp_active = exp_num
        btn_map = {
            "1": self._exp_1_btn,
            "2": self._exp_2_btn,
            "3": self._exp_3_btn,
        }
        for num, btn in btn_map.items():
            if exp_num is None:
                # No experiment active — all buttons reset
                btn.label = f"Start Exp {num}"
                btn.variant = "warning"
                btn.disabled = False
            elif num == exp_num:
                # This experiment is active — show stop
                btn.label = f"Stop Exp {num}"
                btn.variant = "error"
                btn.disabled = False
            else:
                # Another experiment is active — disable
                btn.disabled = True

    def _run_adapter_coro(self, coro) -> None:
        """Schedule an adapter coroutine on the async loop."""
        if adapter is None or not adapter.is_connected:
            logging.warning("Adapter not connected")
            return
        if async_loop and async_loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, async_loop)

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
                row_data = self._user_table.get_row(event.row_key)
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
