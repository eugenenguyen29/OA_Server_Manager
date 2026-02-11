import os
import asyncio
import logging
import signal
import sys
import threading
import time

import core.utils.settings as settings
from core.network.network_utils import NetworkUtils
from core.server.server import Server
from core.adapters import (
    GameAdapterConfig,
    GameAdapterRegistry,
    register_default_adapters,
)

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger("ASTRIDServerUtil")

# Register available game adapters
register_default_adapters()


def create_adapter_config() -> GameAdapterConfig:
    """Create adapter configuration based on game type."""
    game_type = settings.game_type

    if game_type == "dota2":
        return GameAdapterConfig(
            game_type="dota2",
            host=settings.dota2_rcon_host,
            port=settings.dota2_rcon_port,
            password=settings.dota2_rcon_password,
            poll_interval=settings.dota2_poll_interval,
        )
    else:  # Default to OpenArena
        return GameAdapterConfig(
            game_type="openarena",
            host="localhost",
            port=settings.oa_port,
            binary_path=settings.oa_binary_path,
        )


# For now, use the existing Server class for OpenArena
# Dota 2 mode will use the adapter directly
server = Server()
game_adapter = None
async_loop = None
interface = settings.interface


def cleanup():
    """Centralized cleanup function."""
    if async_loop and async_loop.is_running():
        try:
            future = asyncio.run_coroutine_threadsafe(
                server.cleanup_obs_async(), async_loop
            )
            future.result(timeout=5)
        except Exception as e:
            logger.error(f"Error cleaning up OBS connections: {e}")

    try:
        NetworkUtils.dispose(interface)
        logger.info("Network rules cleaned up")
    except Exception as e:
        logger.error(f"Error cleaning up network rules: {e}")

    server.dispose()

    if async_loop and async_loop.is_running():
        async_loop.call_soon_threadsafe(async_loop.stop)


def signal_handler(sig, frame):
    """Handle exit signals (SIGINT, SIGTERM) cleanly."""
    signal_name = "SIGTERM" if sig == signal.SIGTERM else "SIGINT"
    logger.warning(f"{signal_name} received. Starting graceful shutdown...")

    def force_exit():
        time.sleep(10)  # 10 second timeout
        logger.error("Forced shutdown after timeout")
        os._exit(1)

    timeout_thread = threading.Thread(target=force_exit, daemon=True)
    timeout_thread.start()

    try:
        cleanup()
        logger.info("Graceful shutdown completed")
        sys.exit(0)

    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
        sys.exit(1)


def run_async_loop():
    """Run the async event loop in a separate thread."""
    global async_loop
    async_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(async_loop)

    server.set_async_loop(async_loop)

    def exception_handler(loop, context):
        """Handle unhandled exceptions in the async loop."""
        exception = context.get("exception")
        if exception:
            logger.error(
                f"Unhandled exception in async loop: {exception}", exc_info=True
            )
        else:
            logger.error(f"Async loop error: {context['message']}")

    async_loop.set_exception_handler(exception_handler)

    try:
        logger.info("Starting async event loop")
        async_loop.run_forever()
    except Exception as e:
        logger.error(f"Fatal async loop error: {e}", exc_info=True)
    finally:
        logger.info("Async event loop closing")
        async_loop.close()


def run_server_thread():
    """Run server in background thread."""
    try:
        server.start_server()
        logger.info("Server process started successfully")
        server.run_server_loop()
    except Exception as e:
        logger.error(f"Server thread error: {e}")
        cleanup()


async def run_dota2_adapter():
    """Run Dota 2 adapter in async mode."""
    global game_adapter

    config = create_adapter_config()
    game_adapter = GameAdapterRegistry.create(config)

    logger.info(f"Connecting to Dota 2 server at {config.host}:{config.port}...")

    if not await game_adapter.connect():
        logger.error("Failed to connect to Dota 2 server")
        return

    logger.info("Connected to Dota 2 server, starting message loop...")

    try:
        async for message in game_adapter.read_messages():
            if message:
                logger.info(f"[DOTA2] {message[:100]}...")
    except Exception as e:
        logger.error(f"Dota 2 adapter error: {e}")
    finally:
        await game_adapter.disconnect()


def run_dota2_thread():
    """Run Dota 2 adapter in a thread with its own event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_dota2_adapter())
    except Exception as e:
        logger.error(f"Dota 2 thread error: {e}")
    finally:
        loop.close()


def main():
    """Main execution function."""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    game_type = settings.game_type
    logger.info(f"Starting ASTRID Server Management System")
    logger.info(f"Game type: {game_type.upper()}")
    logger.info(f"Available adapters: {GameAdapterRegistry.get_available_games()}")

    if game_type == "dota2":
        # Dota 2 mode - use RCON adapter
        logger.info("Running in Dota 2 RCON mode")

        dota2_thread = threading.Thread(target=run_dota2_thread, name="Dota2Thread")
        dota2_thread.start()

        try:
            dota2_thread.join()
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            if game_adapter:
                game_adapter.request_shutdown()

    else:
        # OpenArena mode - use existing Server class
        logger.info("Running in OpenArena mode")

        async_thread = threading.Thread(target=run_async_loop, daemon=True)
        async_thread.start()

        time.sleep(0.2)

        server_thread = threading.Thread(target=run_server_thread, name="ServerThread")
        server_thread.start()

        try:
            while not server.is_shutdown_requested():
                time.sleep(1)
        except Exception as e:
            logger.critical(f"An unhandled exception occurred: {e}", exc_info=True)
        finally:
            logger.info("Application is shutting down.")
            cleanup()
            if async_loop and async_loop.is_running():
                async_thread.join(timeout=2)
            server_thread.join(timeout=5)


if __name__ == "__main__":
    main()
