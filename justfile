# ASTRID Framework

# Run the TUI
run:
    uv run tui_main.py

# Run the CLI server
run-cli:
    uv run main.py

# Lint and format
lint:
    ruff check .

format:
    ruff format .

check: lint
    ty check .

# Build astrid-tui binary
build-tui:
    uv run pyinstaller astrid_tui.spec --clean

# Build tcp-tui binary
build-tcp:
    uv run pyinstaller tcp_tui.spec --clean

# Build all binaries
build-all: build-tui build-tcp

# Clean build artifacts
clean:
    rm -rf build/ dist/

# Run tests
test:
    uv run pytest
