# ASTRID Framework Tests

## Directory Structure

Tests mirror the source code structure in `core/`:

```
tests/
├── conftest.py                     # Shared pytest fixtures
├── core/
│   ├── adapters/
│   │   ├── amp/                    # AMP adapter tests
│   │   │   ├── test_message_processor.py
│   │   │   └── test_status_parser.py
│   │   ├── openarena/              # OpenArena adapter tests (placeholder)
│   │   ├── test_message_processor.py   # Base message processor tests
│   │   ├── test_message_type.py        # MessageType enum tests
│   │   ├── test_status_parser.py       # Base StatusParser tests
│   │   └── test_sync_async_pattern.py  # Async pattern tests
│   ├── game/
│   │   └── test_state_manager.py   # GameStateManager tests
│   └── network/
│       └── test_network_utils.py   # Network utilities tests
├── integration/                    # Integration/E2E tests (require external services)
│   ├── test_amp_api.py             # AMP API client integration tests
│   ├── test_amp_console_stream.py  # AMP console streaming tests
│   ├── test_amp_rcon_command.py    # AMP RCON command tests
│   ├── test_obs_connection.py      # OBS WebSocket connection tests
│   └── test_obs_manual.py          # Manual OBS testing with CLI options
└── tui/
    └── test_tui_main.py            # TUI tests
```

## Running Tests

```bash
# Run all unit tests (excludes integration tests that need external services)
uv run pytest tests/core/ -v

# Run all tests including integration
uv run pytest -v

# Run specific test file
uv run pytest tests/core/game/test_state_manager.py -v

# Run tests matching a pattern
uv run pytest -k "status_parser" -v

# Run with coverage
uv run pytest --cov=core tests/core/
```

## Test Categories

### Unit Tests (`tests/core/`)

Pure unit tests that don't require external services. These should run fast and reliably.

### Integration Tests (`tests/integration/`)

Tests that require external services (AMP panel, OBS Studio). These are skipped by default in CI and should be run manually:

```bash
# AMP integration tests (requires running AMP panel)
uv run pytest tests/integration/test_amp_api.py -v

# OBS integration tests (requires OBS with WebSocket enabled)
uv run pytest tests/integration/test_obs_connection.py -v

# Manual OBS test with custom connection settings
uv run python tests/integration/test_obs_manual.py --host 192.168.0.128 --port 4455
```

## Writing New Tests

1. Place tests in the directory that mirrors the source module being tested
2. Use `test_` prefix for test files (pytest convention)
3. Use descriptive test class and function names
4. Add fixtures to `conftest.py` for shared test utilities
