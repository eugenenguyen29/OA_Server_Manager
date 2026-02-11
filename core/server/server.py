import asyncio
import logging
import subprocess
import threading
import time
import warnings
from subprocess import PIPE, Popen
from typing import Optional

import core.utils.settings as settings
from core.adapters.base import MessageType
from core.adapters.openarena.message_processor import OAMessageProcessor
from core.game.game_manager import GameManager
from core.game.state_manager import GameStateManager
from core.network.network_manager import NetworkManager
from core.obs.connection_manager import OBSConnectionManager
from core.server.shutdown_strategies import (
    MatchShutdownStrategy,
    WarmupShutdownStrategy,
)
from core.utils.display_utils import DisplayUtils


class Server:
    """
    Refactored OpenArena server management with separated concerns.

    This class now focuses solely on:
    - Server process management
    - Message processing coordination
    - Component integration
    """

    def __init__(self):
        """Initialize server with all specialized managers.

        .. deprecated::
            Use ``OAGameAdapter`` or ``AMPGameAdapter`` via the adapter
            registry instead.  The Server class is retained only for the
            legacy ``main.py`` CLI entry-point.
        """
        warnings.warn(
            "Server class is deprecated. Use GameAdapter implementations "
            "(OAGameAdapter / AMPGameAdapter) via the adapter registry instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.logger = logging.getLogger(__name__)
        self.nplayers_threshold = settings.nplayers_threshold
        self._output_handler = None

        self._process: Optional[Popen] = None
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown_event = threading.Event()
        self.insufficient_humans = False
        self._current_map = ""

        self.network_manager = NetworkManager(
            interface=settings.interface, send_command_callback=self.send_command
        )
        self.game_manager = GameManager(send_command_callback=self.send_command)
        self.game_state_manager = GameStateManager(self.send_command)
        self.message_processor = OAMessageProcessor(self.send_command)
        self.display_utils = DisplayUtils()

        self.obs_connection_manager = OBSConnectionManager(
            obs_port=int(getattr(settings, "obs_port", 4455)),
            obs_password=getattr(settings, "obs_password", None),
            obs_timeout=int(getattr(settings, "obs_connection_timeout", 30)),
            send_command_callback=self.send_command,
        )

        self._shutdown_strategies = {
            "match_end": MatchShutdownStrategy(),
            "warmup_end": WarmupShutdownStrategy(),
        }

        self.message_handlers = {
            MessageType.CLIENT_CONNECT: self._on_client_connect,
            MessageType.CLIENT_DISCONNECT: self._on_client_disconnect,
            MessageType.GAME_INITIALIZATION: self._on_game_initialization,
            MessageType.GAME_END: self._on_match_end,
            MessageType.WARMUP_START: self._on_warmup,
            MessageType.SERVER_SHUTDOWN: self._on_shutdown,
            MessageType.STATUS_UPDATE: self._on_status,
        }

    def start_server(self):
        """Start the OpenArena dedicated server process."""
        self.logger.info("Starting OpenArena server process")

        startup_config = self.game_manager.apply_startup_config()

        server_args = [
            "oa_ded",
            "+set",
            "dedicated",
            "1",
            "+set",
            "net_port",
            "27960",
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

        for key, value in startup_config.items():
            server_args.extend(["+set", key, value])

        server_args.extend(["+exec", "t_server.cfg"])

        self._process = Popen(
            server_args,
            stdout=PIPE,
            stdin=PIPE,
            stderr=PIPE,
            universal_newlines=False,
        )

        self._initialize_server()

    def _initialize_server(self):
        """Initialize server with bot and game settings."""
        self.game_manager.initialize_bot_settings(self.nplayers_threshold)
        self.game_manager.apply_default_config()

    def send_command(self, command: str):
        """Send a command to the server's stdin."""
        if self._process and self._process.poll() is None:
            try:
                self.logger.debug(f"CMD_SEND: {command}")
                self._process.stdin.write(f"{command}\r\n".encode())
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                self.logger.error(f"Failed to send command: {e}")

    def kick_client(self, client_id: int):
        """Kick a client by their slot number (client_id)."""
        if client_id in self.network_manager.client_type_map:
            client_name = self.network_manager.client_name_map.get(client_id, "Unknown")
            client_type = self.network_manager.client_type_map.get(client_id, "Unknown")
            self.send_command(f"clientkick {client_id}")
            self.logger.info(f"Kicked {client_type} client {client_id} ({client_name})")

            time.sleep(0.5)
            self.send_command("status")
        else:
            self.logger.warning(f"Cannot kick client {client_id}: client not found")

    def read_server(self) -> str:
        """Read a message from the server's stderr."""
        try:
            return (
                self._process.stderr.readline()
                .decode("utf-8", errors="replace")
                .rstrip()
            )
        except (OSError, ValueError) as e:
            self.logger.error(f"Failed to read from server: {e}")
            return ""

    def dispose(self):
        self._shutdown_event.set()
        self.game_manager.reset_bot_state()

        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()

    def set_async_loop(self, loop: asyncio.AbstractEventLoop):
        self._async_loop = loop

    def set_output_handler(self, handler):
        self._output_handler = handler

    async def cleanup_obs_async(self):
        await self.obs_connection_manager.cleanup_all()

    def run_async(self, coro):
        if self._async_loop and not self._shutdown_event.is_set():
            asyncio.run_coroutine_threadsafe(coro, self._async_loop)

    def is_shutdown_requested(self):
        """Check if shutdown has been requested."""
        return self._shutdown_event.is_set()

    def is_running(self):
        """Check if server process is running (thread-safe)."""
        return self._process is not None and self._process.poll() is None

    def process_server_message(self, raw_message: str):
        """Process server message using dispatch dictionary."""
        parsed = self.message_processor.process_message(raw_message)

        handler = self.message_handlers.get(parsed.message_type)
        if handler:
            handler(parsed)

    def _update_player_status(self):
        """Common player status update logic."""
        current_players = self.network_manager.get_client_count()
        human_count = self.network_manager.get_human_count()
        current_state = self.game_state_manager.get_current_state().name

        if current_state == "WAITING":
            self.send_command(
                f"say WAITING ROOM: {human_count}/{self.nplayers_threshold} players connected"
            )

        if current_players > 0:
            self.display_utils.display_client_table(
                self.network_manager, "CLIENT STATUS UPDATE"
            )

    def _on_client_connect(self, msg):
        """Handle client connection event."""
        client_id = msg.data["client_id"]
        self.logger.info(f"Processing client {client_id} connection")

    def _on_game_initialization(self, msg):
        self.logger.info("Game initialization detected")

        result = self.game_state_manager.handle_game_initialization_detected()
        if result.get("state_changed"):
            self.logger.info("Game state updated to RUNNING")

    def _on_match_end(self, msg):
        reason = msg.data.get("reason", "unknown")
        self.logger.info(f"Match ended - {reason} hit")

        self.send_command(f"say Match ended! {reason} hit.")

    def _on_warmup(self, msg):
        """Handle warmup state transition."""
        warmup_info = msg.data.get("warmup_info", "")
        self.logger.info(f"Warmup phase started: {warmup_info}")

        self.game_state_manager.handle_warmup_detected()

        if (
            self.game_manager.should_add_bots()
            and not self.game_manager.are_bots_added()
            and not self.game_manager.is_bot_addition_in_progress()
        ):
            self.logger.info("Starting async bot addition")
            self.run_async(self.game_manager.add_bots_to_server_async())

    def _on_shutdown(self, msg):
        event_type = msg.data.get("event", "unknown")
        strategy = self._shutdown_strategies.get(event_type)

        if strategy:
            strategy.handle(self, msg)
        else:
            self.logger.warning(f"Unknown shutdown game event: {event_type}")

    def _on_status(self, msg):
        """Handle server status output."""
        raw_message = msg.raw_message

        if raw_message.startswith("map:"):
            map_name = raw_message.split(":", 1)[1].strip()
            self._current_map = map_name
            self.logger.debug(f"Current map updated to: {map_name}")

        if msg.data.get("status_complete"):
            client_data_list = msg.data.get("client_data", [])
            for client_data in client_data_list:
                self._process_discovered_client(client_data)
        elif msg.data.get("client_data"):
            client_data = msg.data["client_data"]
            self._process_discovered_client(client_data)

    def _process_discovered_client(self, client_data):
        """Process individual discovered client from status output."""
        client_id = client_data["client_id"]
        client_name = client_data["name"]
        client_ip = client_data["ip"]

        if client_id in self.network_manager.client_type_map:
            self.logger.debug(f"[CLIENT] Client {client_id} already tracked")
            return

        if client_ip and client_ip != "bot":
            latency = settings.latencies[
                len(self.network_manager.ip_latency_map) % len(settings.latencies)
            ]

            self.network_manager.add_client(
                client_id=client_id,
                ip=client_ip,
                latency=latency,
                name=client_name,
                is_bot=False,
            )
            self.logger.info(
                f"[CLIENT] New HUMAN client: ID={client_id}, Name={client_name}, IP={client_ip}, Latency={latency}ms"
            )
            self.run_async(
                self.obs_connection_manager.connect_single_client_immediately(
                    client_ip, self.network_manager
                )
            )

        elif client_ip == "bot":
            self.network_manager.add_client(
                client_id=client_id,
                ip=None,
                latency=None,
                name=client_name,
                is_bot=True,
            )
            self.logger.info(f"[CLIENT] BOT client: ID={client_id}, Name={client_name}")

        self._update_player_status()
        self._update_insufficient_humans()

    def _on_client_disconnect(self, msg):
        """Handle client disconnection event."""
        client_id = msg.data["client_id"]
        client_ip = self.network_manager.get_client_ip(client_id)

        if client_ip and self.obs_connection_manager.is_client_connected(client_ip):
            self.run_async(self.obs_connection_manager.disconnect_client(client_ip))

        self.network_manager.remove_client(client_id)
        self.logger.info(
            f"Client {client_id} disconnected. Current players: {self.network_manager.get_client_count()}"
        )
        self._update_player_status()
        self._update_insufficient_humans()

    def _update_insufficient_humans(self):
        human_count = self.network_manager.get_human_count()
        self.insufficient_humans = human_count < self.nplayers_threshold

    def run_server_loop(self):
        """Main server message processing loop."""
        self.logger.info("Starting server message processing loop")

        try:
            while not self.is_shutdown_requested():
                message = self.read_server()
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
