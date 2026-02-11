"""Test AMP API client and adapter."""

import asyncio
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.adapters.amp.amp_api_client import AMPAPIClient, AMPAPIError
from core.adapters.amp.adapter import AMPGameAdapter
from core.adapters.base import GameAdapterConfig


logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def test_api_client(
    url: str, username: str, password: str, totp: str = "", instance_id: str = ""
):
    """Test the AMP API client directly."""
    logger.info("=" * 60)
    logger.info("Testing AMP API Client")
    logger.info("=" * 60)

    client = AMPAPIClient(
        base_url=url,
        username=username,
        password=password,
        instance_id=instance_id,
    )

    try:
        # Test login
        logger.info("Testing login...")
        if instance_id:
            logger.info(f"Will login to instance: {instance_id}")
        success = await client.login(two_factor_token=totp)
        logger.info(f"Login successful: {success}")
        logger.info(
            f"ADS Session: {client._session_id[:20]}..."
            if client._session_id
            else "No ADS session"
        )

        # Show instance session status (login() auto-logs into instance if instance_id is set)
        if instance_id:
            if client._instance_session_id:
                logger.info(f"Instance Session: {client._instance_session_id[:20]}...")
            else:
                logger.warning("No instance session - instance API calls may fail")

        # Test get_status
        logger.info("\nTesting get_status...")
        status = await client.get_status()
        logger.info(f"Status: {status}")

        # Test get_updates
        logger.info("\nTesting get_updates (polling for 10 seconds)...")
        start_time = datetime.now()
        message_count = 0

        while (datetime.now() - start_time).seconds < 10:
            updates = await client.get_updates()

            if updates.console_entries:
                for entry in updates.console_entries:
                    message_count += 1
                    logger.info(
                        f"[{entry.timestamp}] {entry.source}: {entry.contents[:100]}"
                    )

            if updates.status:
                state = updates.status.get("State", "Unknown")
                logger.info(f"Server state: {state}")

            await asyncio.sleep(2)

        logger.info(f"\nReceived {message_count} console entries in 10 seconds")

        # Test send_console_message
        logger.info("\nTesting send_console_message...")
        await client.send_console_message("status")
        logger.info("Sent 'status' command")

        # Wait for response
        await asyncio.sleep(2)
        updates = await client.get_updates()
        logger.info(f"Got {len(updates.console_entries)} entries after command")

    except AMPAPIError as e:
        logger.error(f"API Error: {e}")
        return False

    finally:
        await client.close()
        logger.info("Client closed")

    return True


async def test_adapter(url: str, username: str, password: str, totp: str = ""):
    """Test the AMP game adapter."""
    logger.info("\n" + "=" * 60)
    logger.info("Testing AMP Game Adapter")
    logger.info("=" * 60)

    # Create config - password format is "username:password:totp"
    password_field = f"{username}:{password}"
    if totp:
        password_field += f":{totp}"

    config = GameAdapterConfig(
        game_type="amp",
        host=url,
        password=password_field,
        poll_interval=2.0,
    )

    adapter = AMPGameAdapter(config)

    try:
        # Test connect
        logger.info("Testing connect...")
        success = await adapter.connect()
        logger.info(f"Connected: {success}")

        if not success:
            logger.error("Failed to connect")
            return False

        logger.info(f"is_connected: {adapter.is_connected}")
        logger.info(f"connection_type: {adapter.connection_type}")

        # Test send_command
        logger.info("\nTesting send_command...")
        result = await adapter.send_command("status")
        logger.info(f"Command result: {result}")

        # Test read_messages
        logger.info("\nTesting read_messages (10 seconds)...")
        start_time = datetime.now()
        message_count = 0

        async for message in adapter.read_messages():
            message_count += 1
            logger.info(f"Message: {str(message)[:100]}...")

            if (datetime.now() - start_time).seconds >= 10:
                break

        logger.info(f"\nReceived {message_count} messages")

        # Test get_server_status
        logger.info("\nTesting get_server_status...")
        status = await adapter.get_server_status()
        logger.info(f"Server status: {status}")

    except Exception as e:
        logger.error(f"Error: {e}")
        return False

    finally:
        await adapter.disconnect()
        logger.info("Adapter disconnected")

    return True


async def main():
    parser = argparse.ArgumentParser(description="Test AMP API integration")
    parser.add_argument(
        "--url", required=True, help="AMP panel URL (e.g., http://localhost:8080)"
    )
    parser.add_argument("--username", required=True, help="AMP username")
    parser.add_argument("--password", required=True, help="AMP password")
    parser.add_argument("--totp", default="", help="2FA/TOTP token (if 2FA enabled)")
    parser.add_argument(
        "--instance",
        default="",
        help="Instance ID/hash to connect to specific instance",
    )
    parser.add_argument(
        "--mode",
        choices=["client", "adapter", "both"],
        default="both",
        help="Test mode: client, adapter, or both",
    )

    args = parser.parse_args()

    # If instance ID provided, modify URL to use instance endpoint
    url = args.url
    if args.instance:
        logger.info(f"Using instance ID: {args.instance}")

    results = {}

    if args.mode in ("client", "both"):
        results["client"] = await test_api_client(
            url, args.username, args.password, args.totp, args.instance
        )

    if args.mode in ("adapter", "both"):
        results["adapter"] = await test_adapter(
            url, args.username, args.password, args.totp
        )

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("Test Summary")
    logger.info("=" * 60)
    for test_name, passed in results.items():
        status = "PASSED" if passed else "FAILED"
        logger.info(f"  {test_name}: {status}")


if __name__ == "__main__":
    asyncio.run(main())
