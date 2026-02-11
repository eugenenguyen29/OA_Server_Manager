"""
Test real-time console streaming from AMP instance.

This test connects to a specific AMP instance and streams console
output in real-time (via polling).

Usage:
    python tests/test_amp_console_stream.py \
        --url https://your-amp-panel.com \
        --username admin \
        --password yourpassword \
        --instance c6f3276b

Environment variables (optional):
    AMP_URL, AMP_USERNAME, AMP_PASSWORD, AMP_INSTANCE_ID
"""

import asyncio
import argparse
import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.adapters.amp.amp_api_client import AMPAPIClient, AMPAPIError, ConsoleEntry

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Reduce noise from aiohttp
logging.getLogger("aiohttp").setLevel(logging.WARNING)


class ConsoleStreamer:
    """Real-time console streamer for AMP instances."""

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        instance_id: str,
        poll_interval: float = 1.0,
    ):
        self.client = AMPAPIClient(
            base_url=url,
            username=username,
            password=password,
            instance_id=instance_id,
        )
        self.poll_interval = poll_interval
        self.running = False
        self.seen_entries: set[str] = set()
        self.entry_count = 0

    def _entry_key(self, entry: ConsoleEntry) -> str:
        """Create unique key for deduplication."""
        return f"{entry.timestamp.isoformat()}:{entry.contents}"

    def _format_entry(self, entry: ConsoleEntry) -> str:
        """Format console entry for display."""
        ts = entry.timestamp.strftime("%H:%M:%S")
        src = entry.source[:12].ljust(12) if entry.source else "SERVER".ljust(12)
        return f"[{ts}] {src} │ {entry.contents}"

    async def connect(self) -> bool:
        """Authenticate with AMP."""
        try:
            logger.info("Connecting to AMP...")
            await self.client.login()
            logger.info(f"✓ ADS session acquired")

            if self.client._instance_session_id:
                logger.info(f"✓ Instance session acquired")
            else:
                logger.warning("✗ No instance session - may fail")

            return True
        except AMPAPIError as e:
            logger.error(f"✗ Login failed: {e}")
            return False

    async def stream_console(self, duration: int = 0) -> None:
        """
        Stream console messages.

        Args:
            duration: How long to stream (seconds). 0 = indefinite.
        """
        self.running = True
        start_time = datetime.now()

        logger.info("─" * 60)
        logger.info("Console stream started (Ctrl+C to stop)")
        logger.info("─" * 60)

        try:
            while self.running:
                # Check duration limit
                if duration > 0:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    if elapsed >= duration:
                        logger.info(f"\n⏱ Duration limit ({duration}s) reached")
                        break

                # Poll for updates
                try:
                    updates = await self.client.get_updates()

                    # Process console entries
                    for entry in updates.console_entries:
                        key = self._entry_key(entry)
                        if key not in self.seen_entries:
                            self.seen_entries.add(key)
                            self.entry_count += 1
                            print(self._format_entry(entry))

                    # Show status changes
                    if updates.status:
                        state = updates.status.get("State")
                        if state:
                            logger.debug(f"Server state: {state}")

                except AMPAPIError as e:
                    logger.warning(f"Poll error: {e}")
                    # Try to reconnect
                    try:
                        await self.client.login()
                        logger.info("Reconnected")
                    except AMPAPIError:
                        logger.error("Reconnection failed")
                        break

                await asyncio.sleep(self.poll_interval)

        except asyncio.CancelledError:
            logger.info("\nStream cancelled")

        finally:
            logger.info("─" * 60)
            logger.info(f"Stream ended. Total entries: {self.entry_count}")

    async def send_command(self, command: str) -> None:
        """Send a command to the console."""
        try:
            await self.client.send_console_message(command)
            logger.info(f"→ Sent: {command}")
        except AMPAPIError as e:
            logger.error(f"✗ Command failed: {e}")

    async def close(self) -> None:
        """Clean up."""
        self.running = False
        await self.client.close()
        logger.info("Connection closed")


async def interactive_mode(streamer: ConsoleStreamer) -> None:
    """Run interactive mode with console streaming and command input."""
    # Start streaming in background
    stream_task = asyncio.create_task(streamer.stream_console())

    # Simple command input loop
    logger.info("\nType commands to send (or 'quit' to exit):\n")

    try:
        while streamer.running:
            # Non-blocking input check
            await asyncio.sleep(0.1)

            # In a real implementation, you'd use aioconsole or similar
            # For now, this just streams without interactive input

    except KeyboardInterrupt:
        pass
    finally:
        streamer.running = False
        stream_task.cancel()
        try:
            await stream_task
        except asyncio.CancelledError:
            pass


async def main():
    parser = argparse.ArgumentParser(
        description="Stream console output from AMP instance in real-time",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Stream for 60 seconds
  python test_amp_console_stream.py --url https://amp.example.com \\
      --username admin --password secret --instance c6f3276b --duration 60

  # Stream indefinitely (Ctrl+C to stop)
  python test_amp_console_stream.py --url https://amp.example.com \\
      --username admin --password secret --instance c6f3276b

  # Use environment variables
  export AMP_URL=https://amp.example.com
  export AMP_USERNAME=admin
  export AMP_PASSWORD=secret
  export AMP_INSTANCE_ID=c6f3276b
  python test_amp_console_stream.py
        """,
    )

    # Connection arguments (can use env vars as defaults)
    parser.add_argument(
        "--url",
        default=os.environ.get("AMP_URL", ""),
        help="AMP panel URL (or AMP_URL env var)",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("AMP_USERNAME", ""),
        help="AMP username (or AMP_USERNAME env var)",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("AMP_PASSWORD", ""),
        help="AMP password (or AMP_PASSWORD env var)",
    )
    parser.add_argument(
        "--instance",
        default=os.environ.get("AMP_INSTANCE_ID", "c6f3276b"),
        help="Instance ID (default: c6f3276b)",
    )

    # Streaming options
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=0,
        help="Duration to stream in seconds (0 = indefinite)",
    )

    # Commands to run
    parser.add_argument(
        "--command",
        action="append",
        dest="commands",
        help="Send command(s) before streaming (can repeat)",
    )

    args = parser.parse_args()

    # Validate required args
    if not args.url:
        parser.error("--url required (or set AMP_URL environment variable)")
    if not args.username:
        parser.error("--username required (or set AMP_USERNAME environment variable)")
    if not args.password:
        parser.error("--password required (or set AMP_PASSWORD environment variable)")

    # Create streamer
    streamer = ConsoleStreamer(
        url=args.url,
        username=args.username,
        password=args.password,
        instance_id=args.instance,
        poll_interval=args.poll_interval,
    )

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        logger.info("\nInterrupt received, shutting down...")
        streamer.running = False

    signal.signal(signal.SIGINT, signal_handler)

    try:
        # Connect
        if not await streamer.connect():
            sys.exit(1)

        # Get initial status
        try:
            status = await streamer.client.get_status()
            state = status.get("State", "Unknown")
            metrics = status.get("Metrics", {})

            logger.info(f"Server State: {state}")
            if metrics:
                cpu = metrics.get("CPU Usage", {}).get("Percent", "?")
                mem = metrics.get("Memory Usage", {}).get("Percent", "?")
                logger.info(f"CPU: {cpu}% | Memory: {mem}%")
        except AMPAPIError as e:
            logger.warning(f"Could not get initial status: {e}")

        # Send any initial commands
        if args.commands:
            for cmd in args.commands:
                await streamer.send_command(cmd)
                await asyncio.sleep(0.5)

        # Stream console
        await streamer.stream_console(duration=args.duration)

    finally:
        await streamer.close()


if __name__ == "__main__":
    asyncio.run(main())
