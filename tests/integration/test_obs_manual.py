#!/usr/bin/env python3
"""
OBS WebSocket POC Test Script

This script demonstrates basic OBS WebSocket functionality including:
- Connection and authentication
- Recording controls (start/stop/status)
- Scene management

Requirements:
- OBS Studio with WebSocket plugin enabled
- websockets library: pip install websockets

Usage:
python obs_test.py [--host HOST] [--port PORT] [--password PASSWORD]
"""

import asyncio
import logging
import argparse
import sys
import os

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.obs.controller import OBSWebSocketClient


async def test_obs_connection(host: str, port: int, password: str = None):
    """Test OBS WebSocket connection and basic functionality."""

    # Setup logging
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    logger = logging.getLogger(__name__)

    # Create OBS client
    obs = OBSWebSocketClient(host=host, port=port, password=password)

    try:
        # Test connection
        logger.info("=== Testing OBS WebSocket Connection ===")
        if not await obs.connect():
            logger.error("Failed to connect to OBS")
            return

        # Test recording status
        logger.info("\n=== Testing Recording Status ===")
        status = await obs.get_record_status()
        logger.info(f"Recording Status: {status}")

        # Test scene list
        logger.info("\n=== Testing Scene Management ===")
        scenes = await obs.get_scene_list()
        logger.info(f"Available scenes: {scenes}")

        if scenes:
            current_scene = scenes[0]
            logger.info(f"Setting current scene to: {current_scene}")
            await obs.set_current_scene(current_scene)

        # Test recording controls
        logger.info("\n=== Testing Recording Controls ===")

        # Check if already recording
        status = await obs.get_record_status()
        if status["active"]:
            logger.info("Recording is already active")
            logger.info("Stopping current recording...")
            await obs.stop_record()
            await asyncio.sleep(1)  # Give OBS time to stop

        # Start recording
        logger.info("Starting recording...")
        if await obs.start_record():
            logger.info("Recording started successfully")

            # Wait a bit
            await asyncio.sleep(3)

            # Check status while recording
            status = await obs.get_record_status()
            logger.info(f"Recording Status: {status}")

            # Stop recording
            logger.info("Stopping recording...")
            await obs.stop_record()
        else:
            logger.error("Failed to start recording")

        logger.info("\n=== Test completed successfully ===")

    except Exception as e:
        logger.error(f"Test failed: {e}")

    finally:
        # Cleanup
        await obs.disconnect()


async def interactive_mode(host: str, port: int, password: str = None):
    """Interactive mode for testing OBS controls."""

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(__name__)

    obs = OBSWebSocketClient(host=host, port=port, password=password)

    try:
        logger.info("Connecting to OBS...")
        if not await obs.connect():
            logger.error("Failed to connect to OBS")
            return

        logger.info("Connected! Available commands:")
        logger.info("  start    - Start recording")
        logger.info("  stop     - Stop recording")
        logger.info("  status   - Get recording status")
        logger.info("  scenes   - List available scenes")
        logger.info("  scene <name> - Switch to scene")
        logger.info("  quit     - Exit")

        while True:
            try:
                command = input("\nOBS> ").strip().lower()

                if command == "quit":
                    break
                elif command == "start":
                    success = await obs.start_record()
                    logger.info(
                        f"Start recording: {'Success' if success else 'Failed'}"
                    )
                elif command == "stop":
                    success = await obs.stop_record()
                    logger.info(f"Stop recording: {'Success' if success else 'Failed'}")
                elif command == "status":
                    status = await obs.get_record_status()
                    logger.info(f"Recording Status: {status}")
                elif command == "scenes":
                    scenes = await obs.get_scene_list()
                    logger.info(f"Available scenes: {', '.join(scenes)}")
                elif command.startswith("scene "):
                    scene_name = command[6:]
                    success = await obs.set_current_scene(scene_name)
                    logger.info(
                        f"Switch to scene '{scene_name}': {'Success' if success else 'Failed'}"
                    )
                else:
                    logger.info("Unknown command")

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Command failed: {e}")

    finally:
        await obs.disconnect()
        logger.info("Disconnected from OBS")


def main():
    parser = argparse.ArgumentParser(description="OBS WebSocket POC Test")
    parser.add_argument("--host", default="localhost", help="OBS WebSocket host")
    parser.add_argument("--port", type=int, default=4455, help="OBS WebSocket port")
    parser.add_argument("--password", help="OBS WebSocket password")
    parser.add_argument(
        "--interactive", "-i", action="store_true", help="Interactive mode"
    )

    args = parser.parse_args()

    if args.interactive:
        asyncio.run(interactive_mode(args.host, args.port, args.password))
    else:
        asyncio.run(test_obs_connection(args.host, args.port, args.password))


if __name__ == "__main__":
    main()
