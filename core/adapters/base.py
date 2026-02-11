"""Abstract base classes for game adapters."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

if TYPE_CHECKING:
    from core.game.game_manager import GameManager
    from core.game.state_manager import GameStateManager
    from core.network.network_manager import NetworkManager
    from core.obs.connection_manager import OBSConnectionManager


@runtime_checkable
class ClientTracker(Protocol):
    """Protocol for client tracking used by OBS and display utilities.

    Any object implementing these methods can be used where client
    tracking is needed, decoupling OBS/display from NetworkManager.
    """

    def set_obs_status(self, ip: str, connected: bool) -> None: ...
    def get_client_id_by_ip(self, ip: str) -> Optional[int]: ...
    def get_client_info_table(self) -> List[List[Any]]: ...
    def get_human_count(self) -> int: ...
    def get_bot_count(self) -> int: ...


class ConnectionType(Enum):
    """Type of connection to game server."""

    SUBPROCESS = "subprocess"  # stdin/stdout/stderr (OpenArena)
    RCON = "rcon"  # Source RCON TCP protocol (Dota 2, CS2)
    WEBSOCKET = "websocket"  # For future games


class MessageType(Enum):
    """Game-agnostic message types.

    This enum provides a unified set of message types for all game adapters.
    Legacy aliases are provided for backward compatibility during migration
    from the old messaging system.
    """

    # Core adapter types
    CLIENT_CONNECT = "client_connect"
    CLIENT_DISCONNECT = "client_disconnect"
    GAME_START = "game_start"
    GAME_END = "game_end"
    WARMUP_START = "warmup_start"
    WARMUP_END = "warmup_end"
    PLAYER_KILL = "player_kill"
    CHAT_MESSAGE = "chat_message"
    STATUS_UPDATE = "status_update"
    SERVER_SHUTDOWN = "server_shutdown"
    GAME_INITIALIZATION = "game_initialization"
    UNKNOWN = "unknown"

    # Legacy aliases (for migration from core/messaging/message_processor.py)
    # These map legacy names to their new adapter equivalents
    CLIENT_CONNECTING = "client_connect"  # -> CLIENT_CONNECT
    MATCH_END_FRAGLIMIT = "game_end"  # -> GAME_END
    MATCH_END_TIMELIMIT = "game_end"  # -> GAME_END
    WARMUP_STATE = "warmup_start"  # -> WARMUP_START
    SHUTDOWN_GAME = "server_shutdown"  # -> SERVER_SHUTDOWN
    STATUS_LINE = "status_update"  # -> STATUS_UPDATE


@dataclass
class ParsedMessage:
    """Normalized parsed message structure."""

    message_type: MessageType
    raw_message: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: Optional[float] = None


@dataclass
class GameAdapterConfig:
    """Configuration for game adapter initialization."""

    game_type: str
    host: str = "localhost"
    port: int = 27015
    password: Optional[str] = None
    binary_path: Optional[str] = None
    startup_args: Optional[List[str]] = None
    poll_interval: float = 5.0


class GameAdapter(ABC):
    """
    Abstract interface for game server communication.

    Responsibilities:
    - Server lifecycle (start, stop, connect, disconnect)
    - Command sending (game-specific encoding)
    - Message reading (async generator pattern)
    """

    @property
    @abstractmethod
    def connection_type(self) -> ConnectionType:
        """Return the connection type for this adapter."""
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if adapter is connected to game server."""
        pass

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to game server. Returns success status."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully disconnect from game server."""
        pass

    @abstractmethod
    async def send_command(self, command: str) -> Optional[str]:
        """
        Send command to game server.
        Returns response for RCON, None for subprocess (async response via read).
        """
        pass

    @abstractmethod
    async def read_messages(self) -> AsyncIterator[ParsedMessage]:
        """
        Async generator yielding parsed server messages.

        Each adapter parses internally and yields ``ParsedMessage`` objects.
        For subprocess: reads from stderr, parses each line.
        For HTTP/polling: fetches updates, parses each entry.
        """
        pass

    @abstractmethod
    def start_server(self) -> bool:
        """
        Start the game server process (if applicable).
        For RCON: may be no-op if connecting to existing server.
        """
        pass

    @abstractmethod
    def stop_server(self) -> None:
        """Stop/terminate the game server."""
        pass

    @abstractmethod
    async def kick_client(self, client_id: int) -> None:
        """Kick a client from the server by client ID."""
        pass

    # ── Manager ownership (compositional) ──────────────────────────

    @property
    @abstractmethod
    def network_manager(self) -> NetworkManager:
        """Return the NetworkManager instance owned by this adapter."""
        ...

    @property
    @abstractmethod
    def game_state_manager(self) -> GameStateManager:
        """Return the GameStateManager instance owned by this adapter."""
        ...

    @property
    @abstractmethod
    def game_manager(self) -> GameManager:
        """Return the GameManager instance owned by this adapter."""
        ...

    @property
    @abstractmethod
    def obs_connection_manager(self) -> OBSConnectionManager:
        """Return the OBSConnectionManager instance owned by this adapter."""
        ...

    @property
    @abstractmethod
    def message_processor(self) -> BaseMessageProcessor:
        """Return the BaseMessageProcessor instance owned by this adapter."""
        ...

    @property
    @abstractmethod
    def insufficient_humans(self) -> bool:
        """Whether the server has fewer humans than the required threshold."""
        ...

    @property
    @abstractmethod
    def clients(self) -> List[Dict[str, Any]]:
        """Return list of currently connected clients."""
        ...

    @property
    @abstractmethod
    def server_state(self) -> str:
        """Return current server/game state as a string."""
        ...

    # ── Message processing ───────────────────────────────────────

    @abstractmethod
    def process_server_message(self, raw_message: str) -> None:
        """Parse raw message and dispatch to the appropriate handler."""
        ...

    @abstractmethod
    def set_output_handler(self, handler: Callable[[str], None]) -> None:
        """Set a callback that receives every raw server message."""
        ...

    @abstractmethod
    def set_async_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Provide an async event loop for scheduling coroutines."""
        ...

    @abstractmethod
    def run_server_loop(self) -> None:
        """Blocking loop: read messages, forward to output handler, dispatch."""
        ...

    def send_command_sync(self, command: str) -> None:
        """
        Synchronous command wrapper for callback compatibility.

        This default implementation handles various event loop scenarios:
        - If a loop is already running, schedules via ``run_coroutine_threadsafe``
        - Otherwise, creates a temporary loop with ``asyncio.run()``

        Args:
            command: The command string to send to the game server.

        Returns:
            None (fire-and-forget pattern for callbacks).
        """
        try:
            loop = asyncio.get_running_loop()
            # Loop is running — schedule thread-safely and keep reference
            asyncio.run_coroutine_threadsafe(self.send_command(command), loop)
        except RuntimeError:
            # No running event loop — create a temporary one
            asyncio.run(self.send_command(command))


class BaseMessageProcessor(ABC):
    """
    Abstract interface for parsing game server output.
    Each game adapter implements game-specific regex/parsing.
    """

    def __init__(self, send_command_callback: Optional[Callable[[str], None]] = None):
        self.send_command = send_command_callback

    @abstractmethod
    def process_message(self, raw_message: str) -> ParsedMessage:
        """Parse raw server output into normalized ParsedMessage."""
        pass

    @abstractmethod
    def get_supported_message_types(self) -> List[MessageType]:
        """Return list of message types this processor can detect."""
        pass


class BaseGameManager(ABC):
    """
    Abstract interface for game-specific operations.
    Encapsulates commands like adding bots, setting game rules, etc.
    """

    def __init__(self, send_command_callback: Callable[[str], None]):
        self.send_command = send_command_callback

    @abstractmethod
    def apply_startup_config(self) -> Dict[str, str]:
        """Return game-specific startup configuration."""
        pass

    @abstractmethod
    def apply_default_config(self) -> bool:
        """Apply default game configuration."""
        pass

    @abstractmethod
    async def add_bots(self, count: int, difficulty: int = 1) -> bool:
        """Add AI bots to the game."""
        pass

    @abstractmethod
    def kick_player(self, player_id: Any) -> bool:
        """Kick a player from the server."""
        pass

    @abstractmethod
    def broadcast_message(self, message: str) -> bool:
        """Send message to all players."""
        pass

    @abstractmethod
    def parse_status_response(self, response: str) -> List[Dict[str, Any]]:
        """Parse status command response into player list."""
        pass
