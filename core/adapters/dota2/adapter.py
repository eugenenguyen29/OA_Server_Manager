"""Dota 2 subprocess game adapter."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import threading
import time
from subprocess import PIPE, STDOUT, Popen
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from core.adapters.base import (
    ConnectionType,
    GameAdapter,
    GameAdapterConfig,
    MessageType,
    ParsedMessage,
)
from core.adapters.dota2.message_processor import Dota2MessageProcessor
from core.game.game_manager import GameManager
from core.game.state_manager import GameStateManager
from core.network.network_manager import NetworkManager
from core.obs.connection_manager import OBSConnectionManager
from core.experiment.experiment_manager import ExperimentManager
from core.tcp.connection_manager import TCPConnectionManager
from core.server.shutdown_strategies import (
    MatchShutdownStrategy,
    WarmupShutdownStrategy,
)
import core.utils.settings as settings


class Dota2GameAdapter(GameAdapter):
    """Subprocess-based adapter for Dota 2 dedicated server.

    Combines OpenArena's subprocess lifecycle (Popen, stdin/stdout pipes) with
    AMP's status-handling dispatch (STATUS_UPDATE-only message table, periodic polling).
    """

    def __init__(self, config: GameAdapterConfig) -> None:
        self.config = config
        self._process: Optional[Popen[bytes]] = None
        self._shutdown_event = threading.Event()
        self.logger = logging.getLogger(__name__)

        # Output / async plumbing
        self._output_handler: Optional[Callable[[str], None]] = None
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None

        # State
        self.nplayers_threshold: int = settings.nplayers_threshold
        self._insufficient_humans: bool = False
        self._current_map: str = ""

        # Sub-managers (compositional ownership)
        self._message_processor = Dota2MessageProcessor(self.send_command_sync)
        self._network_manager = NetworkManager(
            interface=settings.interface,
            send_command_callback=self.send_command_sync,
        )
        self._game_manager = GameManager(send_command_callback=self.send_command_sync)
        self._game_state_manager = GameStateManager(self.send_command_sync)
        self._obs_connection_manager = OBSConnectionManager(
            obs_port=int(getattr(settings, "obs_port", 4455)),
            obs_password=getattr(settings, "obs_password", None),
            obs_timeout=int(getattr(settings, "obs_connection_timeout", 30)),
            send_command_callback=self.send_command_sync,
            kick_client_callback=self._kick_client_by_ip,
        )
        self._tcp_connection_manager = TCPConnectionManager(
            tcp_port=settings.tcp_port,
            send_command_callback=self.send_command_sync,
        )
        self._experiment_manager = ExperimentManager(self._tcp_connection_manager)

        # Shutdown strategies
        self._shutdown_strategies: Dict[str, Any] = {
            "match_end": MatchShutdownStrategy(),
            "warmup_end": WarmupShutdownStrategy(),
        }

        # Status polling control
        self._polling_started: bool = False

        # Message dispatch table (STATUS_UPDATE only)
        self.message_handlers: Dict[MessageType, Callable[[ParsedMessage], None]] = {
            MessageType.STATUS_UPDATE: self._on_status,
        }

    # ── Kick callback ────────────────────────────────────────────

    def _kick_client_by_ip(self, client_ip: str) -> None:
        """Kick a client by IP address (used as OBS failure callback)."""
        client_id = self._network_manager.get_client_id_by_ip(client_ip)
        if client_id is not None:
            self.logger.info(
                f"Kicking client {client_id} (IP: {client_ip}) due to OBS connection failure"
            )
            self.run_async(self.kick_client(client_id))
        else:
            self.logger.warning(
                f"Cannot kick client at {client_ip}: client_id not found"
            )

    # ── ABC property implementations ─────────────────────────────

    @property
    def connection_type(self) -> ConnectionType:
        return ConnectionType.SUBPROCESS

    @property
    def is_connected(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def network_manager(self) -> NetworkManager:
        return self._network_manager

    @property
    def game_state_manager(self) -> GameStateManager:
        return self._game_state_manager

    @property
    def game_manager(self) -> GameManager:
        return self._game_manager

    @property
    def obs_connection_manager(self) -> OBSConnectionManager:
        return self._obs_connection_manager

    @property
    def tcp_connection_manager(self) -> TCPConnectionManager:
        return self._tcp_connection_manager

    @property
    def experiment_manager(self) -> ExperimentManager:
        return self._experiment_manager

    @property
    def message_processor(self) -> Dota2MessageProcessor:
        return self._message_processor

    @property
    def insufficient_humans(self) -> bool:
        return self._insufficient_humans

    @property
    def clients(self) -> List[Dict[str, Any]]:
        """Return list of tracked clients from network_manager."""
        result: List[Dict[str, Any]] = []
        for cid, ctype in self._network_manager.client_type_map.items():
            result.append(
                {
                    "client_id": cid,
                    "name": self._network_manager.client_name_map.get(cid, ""),
                    "ip": self._network_manager.client_ip_map.get(cid, ""),
                    "type": ctype,
                }
            )
        return result

    @property
    def server_state(self) -> str:
        return self._game_state_manager.get_current_state().name

    # ── Lifecycle ────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Connect to the Dota 2 server (start subprocess)."""
        self.logger.info("Connecting to Dota 2 server")
        return self.start_server()

    async def disconnect(self) -> None:
        """Disconnect from the Dota 2 server (stop subprocess)."""
        self.logger.info("Disconnecting from Dota 2 server")
        self.stop_server()

    async def send_command(self, command: str) -> Optional[str]:
        """Send command to Dota 2 server via stdin."""
        if not self._process or not self._process.stdin:
            self.logger.warning("Cannot send command: process not running")
            return None
        try:
            self._process.stdin.write(f"{command}\n".encode())
            self._process.stdin.flush()
            self.logger.debug(f"Sent command: {command}")
            return None
        except (BrokenPipeError, OSError) as e:
            self.logger.error(f"Error sending command: {e}")
            return None

    def _read_line(self) -> str:
        """Read one line from stdout (blocking). stderr is merged via STDOUT."""
        if self._process and self._process.stdout:
            return (
                self._process.stdout.readline()
                .decode("utf-8", errors="replace")
                .rstrip()
            )
        return ""

    async def read_messages(self) -> AsyncIterator[ParsedMessage]:
        """Async message iterator. Offloads blocking readline to a thread."""
        while not self._shutdown_event.is_set() and self.is_connected:
            try:
                line = await asyncio.to_thread(self._read_line)
                if line:
                    yield self._message_processor.process_message(line)
            except (OSError, ValueError) as e:
                self.logger.debug(f"Read stream closed or invalid: {e}")
                break

    def read_message_sync(self) -> Optional[str]:
        """Read one line from stdout (blocking). stderr is merged via STDOUT."""
        if not self._process or not self._process.stdout:
            return None
        try:
            return (
                self._process.stdout.readline()
                .decode("utf-8", errors="replace")
                .rstrip()
            )
        except (OSError, ValueError) as e:
            self.logger.error(f"Error reading from stdout: {e}")
            return None

    def start_server(self) -> bool:
        """Launch Dota 2 dedicated server via user-provided bash script.

        The script receives Dota 2 settings as environment variables:
            DOTA2_RCON_HOST, DOTA2_RCON_PORT, DOTA2_RCON_PASSWORD,
            DOTA2_GAMEMODE, DOTA2_CHEATS, DOTA2_POLL_INTERVAL
        """
        script_path = self.config.binary_path
        if not script_path:
            self.logger.error("No DOTA2_SCRIPT_PATH configured")
            return False

        env = {
            **os.environ,
            "DOTA2_RCON_HOST": str(settings.dota2_rcon_host),
            "DOTA2_RCON_PORT": str(settings.dota2_rcon_port),
            "DOTA2_RCON_PASSWORD": str(settings.dota2_rcon_password),
            "DOTA2_GAMEMODE": str(settings.dota2_gamemode),
            "DOTA2_CHEATS": str(settings.dota2_cheats).lower(),
            "DOTA2_POLL_INTERVAL": str(settings.dota2_poll_interval),
        }

        try:
            self._process = Popen(
                ["bash", script_path],
                stdout=PIPE,
                stdin=PIPE,
                stderr=STDOUT,
                env=env,
                universal_newlines=False,
                preexec_fn=os.setsid,
            )
            self.logger.info(
                f"Dota 2 server started via script {script_path} "
                f"(PID {self._process.pid})"
            )
            return True
        except FileNotFoundError:
            self.logger.error(f"Script not found: {script_path}")
            return False
        except Exception as e:
            self.logger.error(f"Failed to start Dota 2 server: {e}")
            return False

    def stop_server(self) -> None:
        """Stop the Dota 2 server process and all children via process group."""
        self._shutdown_event.set()
        if self._process and self._process.poll() is None:
            pgid = os.getpgid(self._process.pid)
            self.logger.info(f"Terminating Dota 2 process group (PGID {pgid})")
            try:
                os.killpg(pgid, signal.SIGTERM)
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.logger.warning(
                    "Process group did not terminate gracefully, sending SIGKILL"
                )
                os.killpg(pgid, signal.SIGKILL)
                self._process.wait()
            except ProcessLookupError:
                self.logger.info("Process group already exited")
            self.logger.info("Dota 2 server stopped")

    async def kick_client(self, client_id: int) -> None:
        """Kick a client by ID using Dota 2 kickid command."""
        self.logger.info(f"Kicking client {client_id}")
        await self.send_command(f"kickid {client_id}")

    async def add_bot(self) -> None:
        self.logger.info("Adding bot via dota_bot_populate")
        await self.send_command("dota_bot_populate")

    async def remove_all_bots(self) -> None:
        self.logger.info("Removing all bots")
        await self.send_command("dota_bot_depopulate")

    def request_shutdown(self) -> None:
        """Request graceful shutdown."""
        self._experiment_manager.stop()
        self._shutdown_event.set()

    def is_shutdown_requested(self) -> bool:
        """Check if shutdown has been requested."""
        return self._shutdown_event.is_set()

    # ── Output / async plumbing ──────────────────────────────────

    def set_output_handler(self, handler: Callable[[str], None]) -> None:
        """Set callback for server output messages."""
        self._output_handler = handler

    def set_async_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set the async event loop and start status polling."""
        self._async_loop = loop
        self._start_status_polling()

    def run_async(self, coro: Any) -> None:
        """Schedule a coroutine on the async loop (thread-safe)."""
        if self._async_loop and not self._shutdown_event.is_set():
            asyncio.run_coroutine_threadsafe(coro, self._async_loop)

    # ── Server loop ──────────────────────────────────────────────

    def run_server_loop(self) -> None:
        """Blocking loop: read server output, forward, dispatch."""
        self.logger.info("Starting Dota 2 server message processing loop")
        try:
            while not self.is_shutdown_requested():
                message = self.read_message_sync()
                if message:
                    if self._output_handler:
                        self._output_handler(message)
                    else:
                        print(f"[DOTA2] {message}")
                    self.process_server_message(message)
                else:
                    time.sleep(0.01)
        except KeyboardInterrupt:
            self.logger.info("Server loop interrupted by user")
        except Exception as e:
            self.logger.error(f"Error in Dota 2 server loop: {e}", exc_info=True)
        finally:
            self.logger.info("Server loop ended")

    def process_server_message(self, raw_message: str) -> None:
        """Parse raw message and dispatch to handler."""
        try:
            parsed = self._message_processor.process_message(raw_message)
            handler = self.message_handlers.get(parsed.message_type)
            if handler:
                handler(parsed)
        except Exception as e:
            self.logger.error(f"Error processing message: {e}", exc_info=True)

    # ── Status polling ───────────────────────────────────────────

    def _start_status_polling(self) -> None:
        """Start periodic status command polling on the async loop."""
        if self._polling_started:
            return
        if not self._async_loop:
            self.logger.warning("Cannot start polling: async loop not set")
            return
        if not self._process or self._process.poll() is not None:
            self.logger.warning("Cannot start polling: process not running")
            return

        self._polling_started = True
        asyncio.run_coroutine_threadsafe(self._status_poll_loop(), self._async_loop)
        self.logger.info("Status polling started")

    async def _status_poll_loop(self) -> None:
        """Periodically send 'status' command to discover players."""
        poll_interval = self.config.poll_interval or getattr(
            settings, "dota2_poll_interval", 5.0
        )
        self.logger.info(f"Status poll loop started (interval: {poll_interval}s)")
        while not self._shutdown_event.is_set():
            try:
                if self._process and self._process.poll() is None:
                    await self.send_command("status")
                else:
                    self.logger.warning("Process died, stopping poll loop")
                    break
            except Exception as e:
                self.logger.error(f"Status poll error: {e}")
            await asyncio.sleep(poll_interval)
        self.logger.info("Status poll loop ended")

    # ── Event handlers ───────────────────────────────────────────

    def _on_status(self, msg: ParsedMessage) -> None:
        """Handle STATUS_UPDATE messages from status command output."""
        if msg.data.get("status_complete"):
            current_ids: set[int] = set()
            for client_data in msg.data.get("clients", []):
                current_ids.add(client_data["client_id"])
                self._process_discovered_client(client_data)
            self._reconcile_clients(current_ids)
        elif msg.data.get("client_data"):
            self._process_discovered_client(msg.data["client_data"])

    def _reconcile_clients(self, current_client_ids: set[int]) -> None:
        """Remove tracked clients that are no longer present on the server."""
        tracked_ids = set(self._network_manager.client_type_map.keys())
        stale_ids = tracked_ids - current_client_ids

        for client_id in stale_ids:
            client_ip = self._network_manager.get_client_ip(client_id)
            if client_ip and self._obs_connection_manager.is_client_connected(
                client_ip
            ):
                self.run_async(
                    self._obs_connection_manager.disconnect_client(client_ip)
                )
            if client_ip and self._tcp_connection_manager.is_client_connected(
                client_ip
            ):
                self._tcp_connection_manager.disconnect_client(client_ip)
            self._network_manager.remove_client(client_id)
            self.logger.info(
                f"[RECONCILE] Removed stale client {client_id}. "
                f"Current players: {self._network_manager.get_client_count()}"
            )

        if stale_ids:
            self._update_insufficient_humans()

    def _process_discovered_client(self, client_data: Dict[str, Any]) -> None:
        """Register newly discovered client in network manager and trigger OBS."""
        client_id = client_data["client_id"]
        client_name = client_data.get("name", "")
        client_ip = client_data.get("ip", "")

        if client_id in self._network_manager.client_type_map:
            # Already tracked — but TCP might have failed after exhausting
            # max_retries. Re-attempt if the client's TCP is not active.
            client_ip = self._network_manager.get_client_ip(client_id)
            if client_ip and not self._tcp_connection_manager.is_client_connected(
                client_ip
            ):
                self.logger.debug(
                    f"[TCP] Re-attempting TCP for tracked client {client_id} "
                    f"(IP={client_ip})"
                )
                self._tcp_connection_manager.connect_single_client(
                    client_ip, self._network_manager
                )
            return

        if client_ip and client_ip != "bot":
            latency = settings.latencies[
                len(self._network_manager.ip_latency_map) % len(settings.latencies)
            ]
            self._network_manager.add_client(
                client_id=client_id,
                ip=client_ip,
                latency=latency,
                name=client_name,
                is_bot=False,
            )
            self.logger.info(
                f"[CLIENT] New HUMAN client: ID={client_id}, Name={client_name}, "
                f"IP={client_ip}, Latency={latency}ms"
            )
            self.run_async(
                self._obs_connection_manager.connect_single_client_immediately(
                    client_ip, self._network_manager
                )
            )
            if not self._tcp_connection_manager.is_client_connected(client_ip):
                self._tcp_connection_manager.connect_single_client(
                    client_ip, self._network_manager
                )
        elif client_ip == "bot":
            self._network_manager.add_client(
                client_id=client_id,
                ip=None,
                latency=None,
                name=client_name,
                is_bot=True,
            )
            self.logger.info(f"[CLIENT] BOT client: ID={client_id}, Name={client_name}")

        self._update_insufficient_humans()

    def _update_insufficient_humans(self) -> None:
        """Update insufficient_humans flag based on current human count."""
        human_count = self._network_manager.get_human_count()
        self._insufficient_humans = human_count < self.nplayers_threshold

    # ── Compat: dispose / is_running (used by TUI) ───────────────

    def dispose(self) -> None:
        """Clean shutdown of adapter and all owned resources."""
        self.logger.info("Disposing Dota 2 adapter")
        self._game_manager.reset_bot_state()
        self.stop_server()

    def is_running(self) -> bool:
        """Check if server process is running."""
        return self._process is not None and self._process.poll() is None
