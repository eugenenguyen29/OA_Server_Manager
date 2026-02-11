# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ASTRID Framework is a multi-game server management framework with:
- **Game-agnostic adapter layer** supporting multiple games (OpenArena, AMP)
- Dedicated game server lifecycle management
- Real-time client connection handling (human vs bot detection)
- OBS Studio integration for automated recording via WebSocket
- Network latency simulation using Linux TC (traffic control) + nftables
- Terminal UI (TUI) for administration

**Supported Games:**
- **OpenArena** - Subprocess-based (stdin/stderr), manages `oa_ded` binary
- **AMP (CubeCoders)** - HTTP API-based, connects to AMP panel for game server control

## Commands

**Development Environment:**
```bash
# Enter Nix development shell (auto-loads via direnv)
nix develop

# Install Python dependencies
uv sync
```

**Running:**
```bash
uv run main.py          # CLI server management
uv run tui_main.py      # TUI admin interface
```

**Linting & Type Checking:**
```bash
ruff check .            # Lint
ruff format .           # Format
ty check .              # Type check
```

**Testing:**
```bash
# OBS connection test
uv run tests/test_obs_connection.py

# OBS comprehensive test (see tests/README.md for options)
uv run tests/obs_test.py --host <IP> --port 4455 --password <pwd>

# TUI test
uv run tests/tui_test.py
```

**IMPORTANT: Always use `uv run` to execute Python scripts instead of `python` or `python3`. This ensures the correct virtual environment and dependencies are used.**

## Architecture

```
core/
├── adapters/                      # Game-agnostic adapter layer
│   ├── base.py                    # Abstract interfaces (GameAdapter, BaseMessageProcessor, BaseGameManager, ClientTracker Protocol)
│   ├── registry.py                # GameAdapterRegistry factory
│   ├── status_parser.py           # Base StatusParser class with FSM
│   ├── openarena/                 # OpenArena implementation
│   │   ├── adapter.py             # Subprocess-based adapter (stdin/stderr)
│   │   ├── message_processor.py   # OA-specific regex parsing
│   │   ├── status_parser.py       # OA status output parser
│   │   └── game_manager.py        # OA commands (addbot, set, etc.)
│   └── amp/                       # AMP (CubeCoders) implementation
│       ├── amp_api_client.py      # HTTP API client for AMP panel
│       ├── adapter.py             # AMP-based adapter (HTTP polling)
│       ├── message_processor.py   # AMP console message parsing
│       └── status_parser.py       # AMP status output parser
├── server/
│   ├── server.py                  # [DEPRECATED] Legacy OA orchestrator — use adapters instead
│   └── shutdown_strategies.py     # Strategy pattern for server shutdown
├── network/
│   ├── network_manager.py         # Client tracking (human vs bot, IPs, latencies)
│   └── network_utils.py           # TC + nftables latency rules (requires sudo)
├── game/
│   ├── game_manager.py            # Bot management, game config
│   └── state_manager.py           # FSM: WAITING → WARMUP → RUNNING
├── obs/
│   ├── controller.py              # Layer 1: OBSWebSocketClient — WebSocket 5.x protocol, zero game coupling
│   ├── manager.py                 # Layer 2: OBSManager — Multi-client connection pool, zero game coupling
│   └── connection_manager.py      # Layer 3: OBSConnectionManager — Game integration via ClientTracker Protocol + kick_client_callback
└── utils/
    ├── settings.py                # .env configuration loader (game type selection)
    └── display_utils.py           # CLI output formatting (accepts ClientTracker Protocol)
```

**Game Adapter Pattern:**
- `GameAdapter` - Abstract interface for server communication (connect, send_command, read_messages)
- `BaseMessageProcessor` - Abstract interface for parsing game output
- `BaseGameManager` - Abstract interface for game-specific operations
- `GameAdapterRegistry` - Factory for creating adapters by game type

**OpenArena vs AMP:**
| Aspect | OpenArena | AMP |
|--------|-----------|-----|
| Connection | Subprocess stdin/stderr | HTTP API |
| Server Lifecycle | Starts oa_ded process | Connects to AMP panel |
| Commands | Fire-and-forget | Fire-and-forget (via API) |
| Messages | Continuous stderr stream | Polling via GetUpdates |
| Auth | None | Username/password |
| Kick Command | `clientkick {id}` | `kickid {id}` |

**Threading Model:**
- Main thread: Server process lifecycle
- Server thread: Message read loop (reads from oa_ded stderr)
- Async thread: Event loop for OBS WebSocket and AMP API operations
- TUI thread: Textual framework interactive UI

**Key Patterns:**
- Game adapters abstract connection type (subprocess vs HTTP API)
- Client discovery via "status" command parsing
- Latency control requires root/sudo for TC qdisc and nftables rules
- OBS uses WebSocket 5.x protocol with optional password auth
- `ClientTracker` Protocol (`@runtime_checkable`) — OBS and display code depend on a typed contract, not concrete `NetworkManager`
- `kick_client_callback` — each adapter injects its game-specific kick command into `OBSConnectionManager`, avoiding reverse dependencies
- Compositional ownership — each adapter owns its sub-managers (`_network_manager`, `_obs_connection_manager`, etc.)
- Shutdown strategies use the Strategy pattern, accessing adapter sub-managers for clean teardown

**OBS 3-Layer Architecture:**
```
Layer 1: OBSWebSocketClient (controller.py)       — WebSocket 5.x protocol, zero game coupling
Layer 2: OBSManager (manager.py)                   — Multi-client connection pool, zero game coupling
Layer 3: OBSConnectionManager (connection_manager.py) — Game integration via ClientTracker Protocol + kick_client_callback
```
Layers 1-2 are pure OBS concerns. Layer 3 bridges OBS to the game adapter through Protocol-based dependency inversion.

**Deprecated Code:**

| Module | Status | Replacement |
|--------|--------|-------------|
| `core/server/server.py` | Deprecated | Use adapters via `GameAdapterRegistry` |
| `main.py` (OA path) | Legacy | Use `tui_main.py` |

## Configuration

All settings via `.env` file:

**Game Selection:**
- `GAME_TYPE` - `openarena` or `amp`

**Common Settings:**
- `NPLAYERS_THRESHOLD` - Min humans to start match
- `LATENCIES` - Network latency values (ms) applied per-client
- `ENABLE_LATENCY_CONTROL` - Toggle TC-based latency simulation
- `INTERFACE` - Network interface for latency control

**OpenArena Settings:**
- `OA_BINARY_PATH`, `OA_PORT` - Server binary and port
- `BOT_ENABLE`, `BOT_COUNT`, `BOT_DIFFICULTY` - Bot configuration
- `TIMELIMIT`, `FLAGLIMIT`, `WARMUP_TIME`, `ENABLE_WARMUP` - Game rules

**AMP Settings:**
- `AMP_BASE_URL` - AMP panel URL (e.g., "http://localhost:8080")
- `AMP_USERNAME`, `AMP_PASSWORD` - AMP credentials
- `AMP_INSTANCE_ID` - Instance ID for multi-instance setups
- `AMP_POLL_INTERVAL` - Console polling interval (seconds, default 2.0)

**OBS Integration:**
- `OBS_PORT`, `OBS_PASSWORD`, `OBS_CONNECTION_TIMEOUT` - OBS WebSocket settings

## System Dependencies

**OpenArena:**
- `oa_ded` - OpenArena dedicated server binary

**AMP:**
- Running CubeCoders AMP panel with game server instance

**Network (optional):**
- `tc` (iproute2) - Traffic control for latency simulation
- `nftables` - Packet marking for traffic control
- `sudo` - Required for network rule management
