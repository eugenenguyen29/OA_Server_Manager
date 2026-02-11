"""TUI page for testing OBS WebSocket connections."""

import asyncio
import logging
import threading

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Label, Log

from core.obs.controller import OBSWebSocketClient

obs_client: OBSWebSocketClient | None = None
async_loop: asyncio.AbstractEventLoop | None = None


def _run_async_loop() -> None:
    """Run the shared async event loop in a background thread."""
    global async_loop
    async_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(async_loop)
    async_loop.run_forever()


class OBSTestApp(App):
    CSS = """
    #main-container {
        height: 100%;
        width: 100%;
    }

    #config-panel {
        height: 5;
        padding: 0 1;
        margin-bottom: 1;
    }

    #config-panel Input {
        width: 1fr;
        margin-right: 1;
    }

    #config-panel Button {
        width: auto;
    }

    #action-panel {
        height: 3;
        padding: 0 1;
        margin-bottom: 1;
    }

    #action-panel Button {
        margin-right: 1;
    }

    #scene-input {
        width: 1fr;
    }

    #content-panel {
        height: 1fr;
    }

    #left-panel {
        width: 40%;
        height: 100%;
    }

    #right-panel {
        width: 60%;
        height: 100%;
    }

    #scene-table {
        height: 1fr;
        border: solid $primary;
        border-title-color: $accent;
    }

    #status-table {
        height: auto;
        max-height: 10;
        border: solid $primary;
        border-title-color: $accent;
        margin-bottom: 1;
    }

    #obs-log {
        height: 1fr;
        border: solid $primary;
        border-title-color: $accent;
    }

    #conn-status {
        width: auto;
        margin-right: 1;
        background: $surface-lighten-1;
        border: solid $primary;
        padding: 0 2;
        content-align: center middle;
    }
    """

    def compose(self) -> ComposeResult:
        yield Vertical(
            Horizontal(
                Input(placeholder="Host (default: localhost)", id="host-input"),
                Input(placeholder="Port (default: 4455)", id="port-input"),
                Input(
                    placeholder="Password (optional)",
                    id="password-input",
                    password=True,
                ),
                Button("Connect", id="connect-btn", variant="success"),
                Button("Disconnect", id="disconnect-btn", variant="error"),
                Label("Disconnected", id="conn-status"),
                id="config-panel",
            ),
            Horizontal(
                Button("Start Record", id="start-rec-btn", variant="primary"),
                Button("Stop Record", id="stop-rec-btn", variant="warning"),
                Button("Get Status", id="status-btn", variant="default"),
                Button("List Scenes", id="scenes-btn", variant="default"),
                Input(placeholder="Scene name...", id="scene-input"),
                Button("Set Scene", id="set-scene-btn", variant="primary"),
                id="action-panel",
            ),
            Horizontal(
                Vertical(
                    DataTable(id="status-table"),
                    DataTable(id="scene-table"),
                    id="left-panel",
                ),
                Vertical(
                    Log(id="obs-log"),
                    id="right-panel",
                ),
                id="content-panel",
            ),
            id="main-container",
        )

    def on_mount(self) -> None:
        obs_log = self.query_one("#obs-log", Log)
        obs_log.border_title = "OBS Log"

        status_table = self.query_one("#status-table", DataTable)
        status_table.border_title = "Recording Status"
        status_table.add_columns("Property", "Value")

        scene_table = self.query_one("#scene-table", DataTable)
        scene_table.border_title = "Scenes"
        scene_table.add_columns("#", "Scene Name")

        self._set_actions_disabled(True)
        self._log("Ready. Enter OBS host/port and click Connect.")

        # Start persistent async loop in background thread
        t = threading.Thread(target=_run_async_loop, daemon=True)
        t.start()

    def _log(self, msg: str) -> None:
        try:
            self.query_one("#obs-log", Log).write_line(msg)
        except Exception:
            pass

    def _set_actions_disabled(self, disabled: bool) -> None:
        for btn_id in [
            "#start-rec-btn",
            "#stop-rec-btn",
            "#status-btn",
            "#scenes-btn",
            "#set-scene-btn",
        ]:
            try:
                self.query_one(btn_id, Button).disabled = disabled
            except Exception:
                pass

    def _update_conn_status(self, connected: bool) -> None:
        label = self.query_one("#conn-status", Label)
        label.update("Connected" if connected else "Disconnected")
        self.query_one("#connect-btn", Button).disabled = connected
        self.query_one("#disconnect-btn", Button).disabled = not connected
        self._set_actions_disabled(not connected)

    def _run_obs_coro(self, coro, timeout: float = 15.0):
        """Schedule a coroutine on the shared async loop and wait for the result."""
        if not async_loop or not async_loop.is_running():
            raise RuntimeError("Async loop not running")
        future = asyncio.run_coroutine_threadsafe(coro, async_loop)
        return future.result(timeout=timeout)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "connect-btn":
            self._do_connect()
        elif btn == "disconnect-btn":
            self._do_disconnect()
        elif btn == "start-rec-btn":
            self._do_start_record()
        elif btn == "stop-rec-btn":
            self._do_stop_record()
        elif btn == "status-btn":
            self._do_get_status()
        elif btn == "scenes-btn":
            self._do_list_scenes()
        elif btn == "set-scene-btn":
            self._do_set_scene()

    @work(thread=True)
    def _do_connect(self) -> None:
        global obs_client
        host = self.query_one("#host-input", Input).value.strip() or "localhost"
        port_str = self.query_one("#port-input", Input).value.strip() or "4455"
        password = self.query_one("#password-input", Input).value.strip() or None

        try:
            port = int(port_str)
        except ValueError:
            self.call_from_thread(self._log, f"Invalid port: {port_str}")
            return

        self.call_from_thread(self._log, f"Connecting to {host}:{port}...")
        obs_client = OBSWebSocketClient(host=host, port=port, password=password)

        try:
            success = self._run_obs_coro(obs_client.connect(), timeout=30.0)
            if success:
                self.call_from_thread(self._log, f"Connected to OBS at {host}:{port}")
                self.call_from_thread(self._update_conn_status, True)
            else:
                self.call_from_thread(self._log, "Connection failed")
                obs_client = None
        except Exception as e:
            self.call_from_thread(self._log, f"Connection error: {e}")
            obs_client = None

    @work(thread=True)
    def _do_disconnect(self) -> None:
        global obs_client
        if obs_client is None:
            return
        try:
            self._run_obs_coro(obs_client.disconnect())
            self.call_from_thread(self._log, "Disconnected")
            self.call_from_thread(self._update_conn_status, False)
        except Exception as e:
            self.call_from_thread(self._log, f"Disconnect error: {e}")
        finally:
            obs_client = None

    @work(thread=True)
    def _do_start_record(self) -> None:
        if obs_client is None:
            return
        try:
            success = self._run_obs_coro(obs_client.start_record())
            msg = "Recording started" if success else "Failed to start recording"
            self.call_from_thread(self._log, msg)
            if success:
                self._refresh_status()
        except Exception as e:
            self.call_from_thread(self._log, f"Start record error: {e}")

    @work(thread=True)
    def _do_stop_record(self) -> None:
        if obs_client is None:
            return
        try:
            success = self._run_obs_coro(obs_client.stop_record())
            msg = "Recording stopped" if success else "Failed to stop recording"
            self.call_from_thread(self._log, msg)
            if success:
                self._refresh_status()
        except Exception as e:
            self.call_from_thread(self._log, f"Stop record error: {e}")

    @work(thread=True)
    def _do_get_status(self) -> None:
        if obs_client is None:
            return
        try:
            self._refresh_status()
        except Exception as e:
            self.call_from_thread(self._log, f"Status error: {e}")

    def _refresh_status(self) -> None:
        status = self._run_obs_coro(obs_client.get_record_status())
        self.call_from_thread(self._update_status_table, status)
        self.call_from_thread(
            self._log,
            f"Status: active={status['active']}, paused={status['paused']}, "
            f"duration={status['duration']}ms, bytes={status['bytes']}",
        )

    def _update_status_table(self, status: dict) -> None:
        table = self.query_one("#status-table", DataTable)
        table.clear()
        table.add_row("Active", str(status["active"]))
        table.add_row("Paused", str(status["paused"]))
        table.add_row("Duration", f"{status['duration']}ms")
        table.add_row("Bytes", str(status["bytes"]))

    @work(thread=True)
    def _do_list_scenes(self) -> None:
        if obs_client is None:
            return
        try:
            scenes = self._run_obs_coro(obs_client.get_scene_list())
            self.call_from_thread(self._update_scene_table, scenes)
            self.call_from_thread(self._log, f"Found {len(scenes)} scene(s)")
        except Exception as e:
            self.call_from_thread(self._log, f"Scene list error: {e}")

    def _update_scene_table(self, scenes: list) -> None:
        table = self.query_one("#scene-table", DataTable)
        table.clear()
        for i, name in enumerate(scenes, 1):
            table.add_row(str(i), name)

    @work(thread=True)
    def _do_set_scene(self) -> None:
        if obs_client is None:
            return
        scene_name = self.query_one("#scene-input", Input).value.strip()
        if not scene_name:
            self.call_from_thread(self._log, "Enter a scene name first")
            return
        try:
            success = self._run_obs_coro(obs_client.set_current_scene(scene_name))
            msg = (
                f"Switched to scene: {scene_name}"
                if success
                else f"Failed to set scene: {scene_name}"
            )
            self.call_from_thread(self._log, msg)
        except Exception as e:
            self.call_from_thread(self._log, f"Set scene error: {e}")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    app = OBSTestApp()
    app.run()


if __name__ == "__main__":
    main()
