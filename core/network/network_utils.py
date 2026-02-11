"""Network utilities for latency control.

This module provides secure utilities for applying network latency rules
using tc (traffic control) and nftables. All input is validated to prevent
shell injection attacks.

Security features:
- Uses subprocess.run with list arguments (no shell execution)
- Input validation for interface names, IP addresses, and latency values
- Comprehensive logging of operations and errors
- Graceful error handling
"""

import subprocess
import logging
import re
from typing import Dict

logger = logging.getLogger(__name__)

# Maximum reasonable latency in milliseconds (10 seconds)
MAX_LATENCY_MS = 10000


def _validate_interface(interface: str) -> bool:
    """Validate network interface name.

    Interface names must be alphanumeric with optional underscores and hyphens.
    This prevents shell injection via malicious interface names.

    Args:
        interface: The network interface name to validate.

    Returns:
        True if the interface name is valid, False otherwise.
    """
    if not interface:
        return False
    # Only allow alphanumeric characters, underscores, and hyphens
    # No spaces, semicolons, backticks, $(), pipes, etc.
    return bool(re.match(r"^[a-zA-Z0-9_-]+$", interface))


def _validate_ip(ip: str) -> bool:
    """Validate IPv4 address format.

    Validates that the IP address has exactly 4 octets, each between 0-255.
    This prevents shell injection via malicious IP strings.

    Args:
        ip: The IP address string to validate.

    Returns:
        True if the IP address is valid, False otherwise.
    """
    if not ip:
        return False

    # Match exactly 4 octets of digits separated by dots
    pattern = r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$"
    match = re.match(pattern, ip)

    if not match:
        return False

    # Validate each octet is in range 0-255
    return all(0 <= int(octet) <= 255 for octet in match.groups())


def _validate_latency(latency: int) -> bool:
    """Validate latency value.

    Latency must be a non-negative integer within a reasonable range.

    Args:
        latency: The latency value in milliseconds.

    Returns:
        True if the latency value is valid, False otherwise.
    """
    if not isinstance(latency, int):
        return False
    if latency < 0:
        return False
    if latency > MAX_LATENCY_MS:
        return False
    return True


def _run_cmd(cmd: list, check: bool = False) -> subprocess.CompletedProcess:
    """Execute command safely with subprocess.

    Uses subprocess.run with list arguments to prevent shell injection.
    Captures stdout and stderr for logging and debugging.

    Args:
        cmd: Command as a list of strings (e.g., ["/usr/bin/tc", "qdisc", "show"]).
        check: If True, raise CalledProcessError on non-zero exit.

    Returns:
        CompletedProcess instance with returncode, stdout, and stderr.

    Raises:
        subprocess.CalledProcessError: If check=True and command fails.
        FileNotFoundError: If the command executable is not found.
        PermissionError: If permission is denied to execute the command.
    """
    logger.debug(f"Executing: {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def apply_latency_rules(ip_latency_map: Dict[str, int], interface: str) -> bool:
    """Apply latency rules to network traffic for specific IP addresses.

    Uses tc (traffic control) and nftables to apply per-IP latency rules.
    All input is validated before any commands are executed.

    Args:
        ip_latency_map: Mapping of IP addresses to latency values in milliseconds.
        interface: Network interface name (e.g., "eth0", "enp1s0").

    Returns:
        True if all rules were applied successfully, False otherwise.
    """
    # Validate interface
    if not _validate_interface(interface):
        logger.error(f"Invalid interface name: {interface}")
        return False

    # Validate all IPs and latencies
    for ip, latency in ip_latency_map.items():
        if not _validate_ip(ip):
            logger.error(f"Invalid IP address: {ip}")
            return False
        if not _validate_latency(latency):
            logger.error(f"Invalid latency value: {latency}")
            return False

    try:
        # Clear existing tc rules
        logger.info("Clearing existing tc rules...")
        result = _run_cmd(
            ["/usr/bin/sudo", "/sbin/tc", "qdisc", "del", "dev", interface, "root"]
        )
        if result.returncode != 0 and "No such file or directory" not in result.stderr:
            logger.debug(f"tc qdisc del returned: {result.stderr}")

        # Create nftables table if it doesn't exist
        logger.info("Setting up nftables...")
        _run_cmd(["/usr/bin/sudo", "nft", "add", "table", "ip", "netem"])
        _run_cmd(
            [
                "/usr/bin/sudo",
                "nft",
                "add",
                "chain",
                "ip",
                "netem",
                "output",
                "{ type filter hook output priority 0; }",
            ]
        )

        # Set up htb qdisc
        logger.info("Setting up htb qdisc...")
        result = _run_cmd(
            [
                "/usr/bin/sudo",
                "/sbin/tc",
                "qdisc",
                "add",
                "dev",
                interface,
                "root",
                "handle",
                "1:",
                "htb",
                "default",
                "1",
            ]
        )
        if result.returncode != 0:
            logger.error(f"Failed to set up htb qdisc: {result.stderr}")
            return False

        # Apply rules for each IP
        for i, (ip, latency) in enumerate(ip_latency_map.items(), start=1):
            class_id = f"1:{i + 10}"
            mark_id = str(i * 100)

            logger.info(f"Applying {latency}ms latency to {ip}...")

            # Create a class under htb
            result = _run_cmd(
                [
                    "/usr/bin/sudo",
                    "/sbin/tc",
                    "class",
                    "add",
                    "dev",
                    interface,
                    "parent",
                    "1:",
                    "classid",
                    class_id,
                    "htb",
                    "rate",
                    "1000mbit",
                ]
            )
            if result.returncode != 0:
                logger.error(f"Failed to create tc class: {result.stderr}")
                return False

            # Apply netem to this class
            result = _run_cmd(
                [
                    "/usr/bin/sudo",
                    "/sbin/tc",
                    "qdisc",
                    "add",
                    "dev",
                    interface,
                    "parent",
                    class_id,
                    "handle",
                    f"{i + 10}:",
                    "netem",
                    "delay",
                    f"{latency}ms",
                ]
            )
            if result.returncode != 0:
                logger.error(f"Failed to apply netem: {result.stderr}")
                return False

            # Use tc filter to assign marked packets to the correct class
            result = _run_cmd(
                [
                    "/usr/bin/sudo",
                    "/sbin/tc",
                    "filter",
                    "add",
                    "dev",
                    interface,
                    "protocol",
                    "ip",
                    "parent",
                    "1:",
                    "prio",
                    "1",
                    "handle",
                    mark_id,
                    "fw",
                    "classid",
                    class_id,
                ]
            )
            if result.returncode != 0:
                logger.error(f"Failed to add tc filter: {result.stderr}")
                return False

            # Use nftables to mark packets based on destination IP
            result = _run_cmd(
                [
                    "/usr/bin/sudo",
                    "nft",
                    "add",
                    "rule",
                    "ip",
                    "netem",
                    "output",
                    "ip",
                    "daddr",
                    ip,
                    "meta",
                    "mark",
                    "set",
                    mark_id,
                ]
            )
            if result.returncode != 0:
                logger.error(f"Failed to add nftables rule: {result.stderr}")
                return False

        logger.info("Latency rules applied successfully.")
        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed with exit code {e.returncode}: {e.stderr}")
        return False
    except FileNotFoundError as e:
        logger.error(f"Command not found: {e}")
        return False
    except PermissionError as e:
        logger.error(f"Permission denied: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return False


def dispose(interface: str) -> bool:
    """Remove latency rules and restore default qdisc.

    Replaces the tc qdisc on the interface with pfifo_fast (default).

    Args:
        interface: Network interface name to clean up.

    Returns:
        True if successful, False otherwise.
    """
    # Validate interface
    if not _validate_interface(interface):
        logger.error(f"Invalid interface name: {interface}")
        return False

    try:
        logger.info(f"Disposing latency rules on {interface}...")
        result = _run_cmd(
            [
                "/usr/bin/sudo",
                "/sbin/tc",
                "qdisc",
                "replace",
                "dev",
                interface,
                "root",
                "pfifo_fast",
            ]
        )
        if result.returncode != 0:
            logger.error(f"Failed to dispose qdisc: {result.stderr}")
            return False

        logger.info("Latency rules disposed successfully.")
        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed with exit code {e.returncode}: {e.stderr}")
        return False
    except FileNotFoundError as e:
        logger.error(f"Command not found: {e}")
        return False
    except PermissionError as e:
        logger.error(f"Permission denied: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return False


# Legacy compatibility - keep NetworkUtils class for backward compatibility
class NetworkUtils:
    """Legacy wrapper class for backward compatibility.

    New code should use the module-level functions directly:
    - apply_latency_rules()
    - dispose()
    """

    @staticmethod
    def apply_latency_rules(ip_latency_map: Dict[str, int], interface: str) -> bool:
        """Apply latency rules. See module-level function for documentation."""
        return apply_latency_rules(ip_latency_map, interface)

    @staticmethod
    def dispose(interface: str) -> bool:
        """Dispose latency rules. See module-level function for documentation."""
        return dispose(interface)
