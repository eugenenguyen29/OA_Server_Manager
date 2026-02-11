"""AMP (CubeCoders) game adapter using HTTP API."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections import OrderedDict
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from core.adapters.base import (
    ConnectionType,
    GameAdapter,
    GameAdapterConfig,
    MessageType,
    ParsedMessage,
)
from core.adapters.amp.amp_api_client import AMPAPIClient, AMPAPIError
from core.adapters.amp.message_processor import AMPMessageProcessor
from core.game.game_manager import GameManager
from core.game.state_manager import GameStateManager
from core.network.network_manager import NetworkManager
from core.obs.connection_manager import OBSConnectionManager
import core.utils.settings as settings


def _parse_credentials(password_field: str | None) -> tuple[str, str]:
    """Parse 'username:password' format from config password field.

    Args:
        password_field: Credential string in 'username:password' format.

    Returns:
        Tuple of (username, password).

    Raises:
        ValueError: If the credential string is missing, empty, or
            does not contain both a non-empty username and password
            separated by ':'.
    """
    if not password_field:
        raise ValueError(
            "AMP credentials are required. "
            "Provide password as 'username:password' in config."
        )
    if ":" not in password_field:
        raise ValueError(
            "AMP credentials must be in 'username:password' format. "
            f"Got: {password_field!r} (missing ':')"
        )
    username, password = password_field.split(":", 1)
    if not username.strip():
        raise ValueError("AMP username must not be empty.")
    if not password.strip():
        raise ValueError("AMP password must not be empty.")
    return username.strip(), password.strip()


def _run_async_safe(
    coro_fn: Callable[[], Any], loop: asyncio.AbstractEventLoop | None = None
) -> Any:
    """Run an async coroutine from sync context in a thread-safe manner.

    If an explicit *loop* is provided and running, uses
    ``run_coroutine_threadsafe`` (safe from any thread).  Otherwise
    falls back to ``asyncio.run`` which creates a fresh event loop.

    Args:
        coro_fn: Zero-argument callable returning a coroutine.
        loop: Optional event loop to schedule on.

    Returns:
        The coroutine's result, or ``None`` when fire-and-forget via
        ``run_coroutine_threadsafe``.
    """
    if loop is not None and loop.is_running():
        asyncio.run_coroutine_threadsafe(coro_fn(), loop)
        return None
    # No running loop available -- create a temporary one.
    return asyncio.run(coro_fn())


class AMPGameAdapter(GameAdapter):
    """
    Game adapter for servers managed by CubeCoders AMP.

    Uses the AMP HTTP API to:
    - Stream console messages via Core.GetUpdates polling
    - Send commands via Core.SendConsoleMessage
    - Control server lifecycle (start/stop/restart)

    Owns all sub-managers (compositional ownership pattern):
    - MessageProcessor, NetworkManager, GameStateManager, GameManager, OBS
    """

    def __init__(self, config: GameAdapterConfig) -> None:
        self.config = config
        username, password = _parse_credentials(config.password)
        self.api = AMPAPIClient(
            base_url=config.host,
            username=username,
            password=password,
            instance_id=getattr(config, "instance_id", None),
            timeout=30.0,
        )
        self._polling = False
        self._poll_interval = config.poll_interval or 2.0
        self._shutdown_requested = False
        self._server_loop_future: Optional[asyncio.futures.Future[None]] = None
        self._seen_messages: OrderedDict[str, None] = OrderedDict()
        self._max_seen_cache = 1000
        self.logger = logging.getLogger(__name__)

        # Output / async plumbing
        self._output_handler: Optional[Callable[[str], None]] = None
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None

        # ── Sub-managers (compositional ownership) ───────────────
        self._message_processor = AMPMessageProcessor(
            send_command_callback=self.send_command_sync
        )
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

        # ── Message dispatch table ───────────────────────────────
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
        return ConnectionType.WEBSOCKET  # Closest match - HTTP polling

    @property
    def is_connected(self) -> bool:
        return self.api.is_authenticated

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
    def message_processor(self) -> AMPMessageProcessor:
        return self._message_processor

    @property
    def insufficient_humans(self) -> bool:
        """Whether the server has fewer humans than the required threshold."""
        human_count = self._network_manager.get_human_count()
        return human_count < settings.nplayers_threshold

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
        """Connect and authenticate with AMP API."""
        try:
            self.logger.info(f"Connecting to AMP at {self.config.host}")
            self._shutdown_requested = False
            self._polling = False
            await self.api.login()
            self.logger.info("Successfully authenticated with AMP")
            return True
        except AMPAPIError as e:
            self.logger.error(f"Failed to connect to AMP: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from AMP API. Safe to call multiple times."""
        self._polling = False
        self._shutdown_requested = True
        # Cancel the server loop if running
        if self._server_loop_future and not self._server_loop_future.done():
            self._server_loop_future.cancel()
        if not self.is_connected:
            return
        try:
            await self.api.close()
        except Exception:
            pass
        self._seen_messages.clear()
        self.logger.info("Disconnected from AMP")

    async def send_command(self, command: str) -> Optional[str]:
        """Send command to game server via AMP console."""
        try:
            await self.api.send_console_message(command)
            self.logger.debug(f"Sent command: {command}")
            return None
        except AMPAPIError as e:
            self.logger.error(f"Failed to send command: {e}")
            return None

    async def read_messages(self) -> AsyncIterator[ParsedMessage]:
        """
        Read console messages from AMP via polling.

        Yields ParsedMessage objects as they appear.
        """
        self._polling = True
        self._seen_messages.clear()
        while self._polling and self.is_connected and not self._shutdown_requested:
            try:
                updates = await self.api.get_updates()

                self.logger.debug(
                    f"GetUpdates returned {len(updates.console_entries)} console entries"
                )

                for entry in updates.console_entries:
                    self.logger.debug(
                        f"Console entry: [{entry.source}] {entry.contents[:100]!r}"
                    )
                    msg_key = f"{entry.timestamp.isoformat()}:{entry.contents}"

                    if msg_key not in self._seen_messages:
                        self._seen_messages[msg_key] = None

                        while len(self._seen_messages) > self._max_seen_cache:
                            self._seen_messages.popitem(last=False)

                        lines = entry.contents.split("\n")
                        for line in lines:
                            if line.strip():
                                parsed = self._message_processor.process_message(line)
                                self.logger.debug(
                                    f"Parsed message type: {parsed.message_type}"
                                )
                                yield parsed

            except AMPAPIError as e:
                self.logger.error(f"GetUpdates failed: {e}")
                try:
                    await self.api.login()
                    self.logger.info("Reconnected to AMP")
                except AMPAPIError:
                    self.logger.error("Reconnection failed, stopping polling")
                    break

            await asyncio.sleep(self._poll_interval)

    def start_server(self) -> bool:
        """Start game server via AMP."""
        self.logger.info("Requesting server start via AMP")
        try:
            _run_async_safe(self._start_server_async, self._async_loop)
            return True
        except Exception as e:
            self.logger.error(f"Failed to start server: {e}")
            return False

    async def _start_server_async(self) -> None:
        """Async helper for starting server."""
        try:
            await self.api.start_instance()
            self.logger.info("Server start command sent")
        except AMPAPIError as e:
            self.logger.error(f"Failed to start server: {e}")

    def stop_server(self) -> None:
        """Stop game server via AMP."""
        self._shutdown_requested = True
        self._polling = False
        _run_async_safe(self._stop_server_async, self._async_loop)

    async def _stop_server_async(self) -> None:
        """Async helper for stopping server."""
        try:
            await self.api.stop_instance()
            self.logger.info("Server stop command sent")
        except AMPAPIError as e:
            self.logger.debug(f"Error stopping server: {e}")

    def request_shutdown(self) -> None:
        """Request graceful shutdown."""
        self._shutdown_requested = True
        self._polling = False

    def is_shutdown_requested(self) -> bool:
        """Check if shutdown has been requested."""
        return self._shutdown_requested

    async def get_server_status(self) -> Optional[dict]:
        """Get detailed server status from AMP."""
        try:
            return await self.api.get_status()
        except AMPAPIError as e:
            self.logger.error(f"Failed to get status: {e}")
            return None

    async def kick_client(self, client_id: int) -> None:
        """Kick a client from the server by client ID."""
        self.logger.info(f"Kicking client {client_id}")
        await self.send_command(f"kickid {client_id}")

    # ── Output / async plumbing ──────────────────────────────────

    def set_output_handler(self, handler: Callable[[str], None]) -> None:
        """Set a callback that receives every raw server message."""
        self._output_handler = handler

    def set_async_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Provide an async event loop for scheduling coroutines."""
        self._async_loop = loop

    def run_async(self, coro: Any) -> None:
        """Schedule a coroutine on the async loop (thread-safe)."""
        if self._async_loop and not self._shutdown_requested:
            asyncio.run_coroutine_threadsafe(coro, self._async_loop)

    # ── Message processing ───────────────────────────────────────

    def process_server_message(self, raw_message: str) -> None:
        """Parse raw message and dispatch to handler."""
        parsed = self._message_processor.process_message(raw_message)
        handler = self.message_handlers.get(parsed.message_type)
        if handler:
            handler(parsed)

    # ── Server loop ──────────────────────────────────────────────

    def run_server_loop(self) -> None:
        """Blocking loop: poll messages via read_messages, forward and dispatch."""
        self.logger.info("Starting AMP server message processing loop")
        try:
            if self._async_loop and self._async_loop.is_running():
                # Schedule on existing event loop (required for aiohttp timeout)
                self._server_loop_future = asyncio.run_coroutine_threadsafe(
                    self._run_server_loop_async(), self._async_loop
                )
                self._server_loop_future.result()  # Block until done
            else:
                # Fallback: create a new loop and run as a task
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(
                        asyncio.ensure_future(self._run_server_loop_async())
                    )
                finally:
                    loop.close()
        except (
            KeyboardInterrupt,
            asyncio.CancelledError,
            concurrent.futures.CancelledError,
        ):
            self.logger.info("Server loop interrupted")
        except Exception as e:
            self.logger.error(f"Error in server loop: {e}", exc_info=True)
        finally:
            self._server_loop_future = None
            self.logger.info("Server loop ended")

    async def _run_server_loop_async(self) -> None:
        """Async implementation of the server loop."""
        async for msg in self.read_messages():
            if self._output_handler:
                self._output_handler(msg.raw_message)
            # Dispatch to handler
            handler = self.message_handlers.get(msg.message_type)
            if handler:
                handler(msg)

    # ── Event handlers ───────────────────────────────────────────

    def _on_status(self, msg: ParsedMessage) -> None:
        """Handle STATUS_UPDATE messages - populate clients from status data."""
        if msg.data.get("status_complete"):
            for client_data in msg.data.get("clients", []):
                self._process_discovered_client(client_data)
        elif msg.data.get("client_data"):
            self._process_discovered_client(msg.data["client_data"])

    def _process_discovered_client(self, client_data: Dict[str, Any]) -> None:
        """Register a discovered client into network_manager."""
        client_id = client_data["client_id"]
        client_name = client_data.get("name", "")
        client_ip = client_data.get("ip", "")

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
