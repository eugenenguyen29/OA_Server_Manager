"""OpenArena game adapter using subprocess/stdin communication."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
import time
from subprocess import PIPE, Popen
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from core.adapters.base import (
    ConnectionType,
    GameAdapter,
    GameAdapterConfig,
    MessageType,
    ParsedMessage,
)
from core.adapters.openarena.message_processor import OAMessageProcessor
from core.game.game_manager import GameManager
from core.game.state_manager import GameStateManager
from core.network.network_manager import NetworkManager
from core.obs.connection_manager import OBSConnectionManager
from core.server.shutdown_strategies import (
    MatchShutdownStrategy,
    WarmupShutdownStrategy,
)
import core.utils.settings as settings


class OAGameAdapter(GameAdapter):
    """OpenArena game adapter with full compositional ownership.

    This adapter owns the subprocess lifecycle AND all sub-managers
    (message processing, network tracking, game state, OBS integration).
    It is the single entry point for TUI and CLI consumers.
    """

    def __init__(self, config: GameAdapterConfig) -> None:
        self.config = config
        self._process: Optional[Popen] = None
        self._shutdown_event = threading.Event()
        self.logger = logging.getLogger(__name__)

        # Output / async plumbing
        self._output_handler: Optional[Callable[[str], None]] = None
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None

        # State
        self.nplayers_threshold: int = settings.nplayers_threshold
        self._insufficient_humans: bool = False
        self._current_map: str = ""

        # ── Sub-managers (compositional ownership) ───────────────
        self._message_processor = OAMessageProcessor(self.send_command_sync)
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

        # ── Shutdown strategies ──────────────────────────────────
        self._shutdown_strategies: Dict[str, Any] = {
            "match_end": MatchShutdownStrategy(),
            "warmup_end": WarmupShutdownStrategy(),
        }

        # ── Message dispatch table ───────────────────────────────
        self.message_handlers: Dict[MessageType, Callable[[ParsedMessage], None]] = {
            MessageType.CLIENT_CONNECT: self._on_client_connect,
            MessageType.CLIENT_DISCONNECT: self._on_client_disconnect,
            MessageType.GAME_INITIALIZATION: self._on_game_initialization,
            MessageType.GAME_END: self._on_match_end,
            MessageType.WARMUP_START: self._on_warmup,
            MessageType.SERVER_SHUTDOWN: self._on_shutdown,
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
    def message_processor(self) -> OAMessageProcessor:
        return self._message_processor

    @property
    def insufficient_humans(self) -> bool:
        """Whether the server has fewer humans than the required threshold."""
        return self._insufficient_humans

    @insufficient_humans.setter
    def insufficient_humans(self, value: bool) -> None:
        self._insufficient_humans = value

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
        """Return current game state as a string."""
        return self._game_state_manager.get_current_state().name

    # ── Lifecycle ────────────────────────────────────────────────

    async def connect(self) -> bool:
        return self.start_server()

    async def disconnect(self) -> None:
        self.stop_server()

    async def send_command(self, command: str) -> Optional[str]:
        if self._process and self._process.poll() is None:
            try:
                self.logger.debug(f"CMD_SEND: {command}")
                self._process.stdin.write(f"{command}\r\n".encode())
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                self.logger.error(f"Failed to send command: {e}")
        return None

    async def read_messages(self) -> AsyncIterator[ParsedMessage]:
        while not self._shutdown_event.is_set() and self.is_connected:
            try:
                line = (
                    self._process.stderr.readline()
                    .decode("utf-8", errors="replace")
                    .rstrip()
                )
                if line:
                    yield self._message_processor.process_message(line)
            except (OSError, ValueError):
                break
            await asyncio.sleep(0.01)

    def read_message_sync(self) -> str:
        """Synchronous message reading for the main loop."""
        try:
            return (
                self._process.stderr.readline()
                .decode("utf-8", errors="replace")
                .rstrip()
            )
        except (OSError, ValueError) as e:
            self.logger.error(f"Failed to read from server: {e}")
            return ""

    def start_server(self) -> bool:
        self.logger.info("Starting OpenArena server process")

        binary_path = self.config.binary_path or "oa_ded"
        port = self.config.port or 27960

        server_args = [
            binary_path,
            "+set",
            "dedicated",
            "1",
            "+set",
            "net_port",
            str(port),
            "+set",
            "com_legacyprotocol",
            "71",
            "+set",
            "com_protocol",
            "71",
            "+set",
            "sv_pure",
            "0",
            "+set",
            "sv_master1",
            "dpmaster.deathmask.net",
            "+set",
            "sv_maxclients",
            "4",
            "+set",
            "cl_motd",
            "Welcome To ASTRID lab",
        ]

        startup_config = {
            "timelimit": str(settings.timelimit),
            "capturelimit": str(settings.fraglimit),
            "g_doWarmup": "1" if settings.enable_warmup else "0",
            "g_warmup": str(settings.warmup_time),
        }
        for key, value in startup_config.items():
            server_args.extend(["+set", key, value])

        server_args.extend(["+exec", "t_server.cfg"])

        try:
            self._process = Popen(
                server_args,
                stdout=PIPE,
                stdin=PIPE,
                stderr=PIPE,
                universal_newlines=False,
            )
            self.logger.info(f"OpenArena server started with PID {self._process.pid}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to start OpenArena server: {e}")
            return False

    def stop_server(self) -> None:
        self._shutdown_event.set()
        if self._process and self._process.poll() is None:
            self.logger.info("Terminating OpenArena server process")
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self.logger.info("OpenArena server stopped")

    async def kick_client(self, client_id: int) -> None:
        self.logger.info(f"Kicking client {client_id}")
        await self.send_command(f"clientkick {client_id}")

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    def is_shutdown_requested(self) -> bool:
        return self._shutdown_event.is_set()

    # ── Output / async plumbing ──────────────────────────────────

    def set_output_handler(self, handler: Callable[[str], None]) -> None:
        self._output_handler = handler

    def set_async_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._async_loop = loop

    def run_async(self, coro: Any) -> None:
        """Schedule a coroutine on the async loop (thread-safe)."""
        if self._async_loop and not self._shutdown_event.is_set():
            asyncio.run_coroutine_threadsafe(coro, self._async_loop)

    # ── Server loop ──────────────────────────────────────────────

    def run_server_loop(self) -> None:
        """Main blocking loop: read, forward, dispatch."""
        self.logger.info("Starting server message processing loop")
        try:
            while not self.is_shutdown_requested():
                message = self.read_message_sync()
                if message:
                    if self._output_handler:
                        self._output_handler(message)
                    else:
                        print(f"[SERVER] {message}")
                    self.process_server_message(message)
                time.sleep(0.01)
        except KeyboardInterrupt:
            self.logger.info("Server loop interrupted by user")
        except Exception as e:
            self.logger.error(f"Error in server loop: {e}", exc_info=True)
        finally:
            self.logger.info("Server loop ended")

    def process_server_message(self, raw_message: str) -> None:
        """Parse raw message and dispatch to handler."""
        parsed = self._message_processor.process_message(raw_message)
        handler = self.message_handlers.get(parsed.message_type)
        if handler:
            handler(parsed)

    # ── Event handlers (absorbed from Server) ────────────────────

    def _on_client_connect(self, msg: ParsedMessage) -> None:
        client_id = msg.data["client_id"]
        self.logger.info(f"Processing client {client_id} connection")

    def _on_client_disconnect(self, msg: ParsedMessage) -> None:
        client_id = msg.data["client_id"]
        client_ip = self._network_manager.get_client_ip(client_id)

        if client_ip and self._obs_connection_manager.is_client_connected(client_ip):
            self.run_async(self._obs_connection_manager.disconnect_client(client_ip))

        self._network_manager.remove_client(client_id)
        self.logger.info(
            f"Client {client_id} disconnected. "
            f"Current players: {self._network_manager.get_client_count()}"
        )
        self._update_insufficient_humans()

    def _on_game_initialization(self, msg: ParsedMessage) -> None:
        self.logger.info("Game initialization detected")
        result = self._game_state_manager.handle_game_initialization_detected()
        if result.get("state_changed"):
            self.logger.info("Game state updated to RUNNING")

    def _on_match_end(self, msg: ParsedMessage) -> None:
        reason = msg.data.get("reason", "unknown")
        self.logger.info(f"Match ended - {reason} hit")
        self.send_command_sync(f"say Match ended! {reason} hit.")

    def _on_warmup(self, msg: ParsedMessage) -> None:
        warmup_info = msg.data.get("warmup_info", "")
        self.logger.info(f"Warmup phase started: {warmup_info}")
        self._game_state_manager.handle_warmup_detected()

        if (
            self._game_manager.should_add_bots()
            and not self._game_manager.are_bots_added()
            and not self._game_manager.is_bot_addition_in_progress()
        ):
            self.logger.info("Starting async bot addition")
            self.run_async(self._game_manager.add_bots_to_server_async())

    def _on_shutdown(self, msg: ParsedMessage) -> None:
        event_type = msg.data.get("event", "unknown")
        strategy = self._shutdown_strategies.get(event_type)
        if strategy:
            strategy.handle(self, msg)
        else:
            self.logger.warning(f"Unknown shutdown game event: {event_type}")

    def _on_status(self, msg: ParsedMessage) -> None:
        raw_message = msg.raw_message

        if raw_message.startswith("map:"):
            map_name = raw_message.split(":", 1)[1].strip()
            self._current_map = map_name
            self.logger.debug(f"Current map updated to: {map_name}")

        if msg.data.get("status_complete"):
            for client_data in msg.data.get("client_data", []):
                self._process_discovered_client(client_data)
        elif msg.data.get("client_data"):
            self._process_discovered_client(msg.data["client_data"])

    def _process_discovered_client(self, client_data: Dict[str, Any]) -> None:
        client_id = client_data["client_id"]
        client_name = client_data["name"]
        client_ip = client_data["ip"]

        if client_id in self._network_manager.client_type_map:
            self.logger.debug(f"[CLIENT] Client {client_id} already tracked")
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
        human_count = self._network_manager.get_human_count()
        self.insufficient_humans = human_count < self.nplayers_threshold

    # ── Compat: dispose (used by TUI) ────────────────────────────

    def dispose(self) -> None:
        """Clean shutdown of adapter and all owned resources."""
        self._shutdown_event.set()
        self._game_manager.reset_bot_state()
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None
