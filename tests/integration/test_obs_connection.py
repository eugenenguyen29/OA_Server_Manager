#!/usr/bin/env python3
"""
Test script to verify OBS WebSocket connection functionality.
"""

import asyncio
import logging
import sys
import os

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.obs.controller import OBSWebSocketClient

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


async def test_obs_connection():
    """Test basic OBS connection to localhost."""
    logger.info("Testing OBS WebSocket connection...")

    # Test connection to 192.168.0.128 (where OBS should be running)
    obs_client = OBSWebSocketClient(host="192.168.0.128", port=4455, password=None)

    try:
        logger.info("Attempting to connect...")
        connected = await obs_client.connect()

        if connected:
            logger.info("✓ OBS connection successful!")

            # Test getting recording status
            logger.info("Testing recording status...")
            status = await obs_client.get_record_status()
            logger.info(f"Recording status: {status}")

            # Test getting scene list
            logger.info("Testing scene list...")
            scenes = await obs_client.get_scene_list()
            logger.info(f"Available scenes: {scenes}")

        else:
            logger.error("✗ OBS connection failed!")
            return False

    except Exception as e:
        logger.error(f"✗ Connection error: {e}")
        return False
    finally:
        if obs_client.websocket:
            await obs_client.disconnect()

    return connected


async def main():
    """Main test function."""
    logger.info("OBS WebSocket Connection Test")
    logger.info("=" * 40)
    logger.info(
        "Make sure OBS Studio is running on 192.168.0.128 with WebSocket enabled!"
    )
    logger.info("OBS -> Tools -> WebSocket Server Settings")
    logger.info("Enable WebSocket server on port 4455")
    logger.info("=" * 40)

    result = await test_obs_connection()

    if result:
        logger.info("✓ Test PASSED - OBS connection working")
        sys.exit(0)
    else:
        logger.error("✗ Test FAILED - OBS connection not working")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
