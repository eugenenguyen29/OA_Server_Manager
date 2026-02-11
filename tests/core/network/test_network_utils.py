"""Tests for network utilities security.

This test module verifies that network_utils:
1. Uses subprocess instead of os.system
2. Validates all input to prevent shell injection
3. Properly handles errors and logs operations
"""

from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess


class TestValidateInterface:
    """Test input validation for network interface names."""

    def test_validate_interface_valid_eth0(self):
        """Should accept standard eth0 interface name."""
        from core.network.network_utils import _validate_interface

        assert _validate_interface("eth0") is True

    def test_validate_interface_valid_enp1s0(self):
        """Should accept systemd-style interface name."""
        from core.network.network_utils import _validate_interface

        assert _validate_interface("enp1s0") is True

    def test_validate_interface_valid_wlan0(self):
        """Should accept wireless interface name."""
        from core.network.network_utils import _validate_interface

        assert _validate_interface("wlan0") is True

    def test_validate_interface_valid_with_underscore(self):
        """Should accept interface names with underscores."""
        from core.network.network_utils import _validate_interface

        assert _validate_interface("my_interface") is True

    def test_validate_interface_valid_with_hyphen(self):
        """Should accept interface names with hyphens."""
        from core.network.network_utils import _validate_interface

        assert _validate_interface("my-interface") is True

    def test_validate_interface_invalid_semicolon_injection(self):
        """Should reject interface names with semicolon injection."""
        from core.network.network_utils import _validate_interface

        assert _validate_interface("eth0; rm -rf /") is False

    def test_validate_interface_invalid_command_substitution(self):
        """Should reject interface names with command substitution."""
        from core.network.network_utils import _validate_interface

        assert _validate_interface("$(whoami)") is False

    def test_validate_interface_invalid_backtick_injection(self):
        """Should reject interface names with backtick injection."""
        from core.network.network_utils import _validate_interface

        assert _validate_interface("eth0`cat /etc/passwd`") is False

    def test_validate_interface_invalid_empty(self):
        """Should reject empty interface name."""
        from core.network.network_utils import _validate_interface

        assert _validate_interface("") is False

    def test_validate_interface_invalid_path_traversal(self):
        """Should reject interface names with path traversal."""
        from core.network.network_utils import _validate_interface

        assert _validate_interface("../../etc") is False

    def test_validate_interface_invalid_and_operator(self):
        """Should reject interface names with && injection."""
        from core.network.network_utils import _validate_interface

        assert _validate_interface("eth0 && malicious") is False

    def test_validate_interface_invalid_pipe_injection(self):
        """Should reject interface names with pipe injection."""
        from core.network.network_utils import _validate_interface

        assert _validate_interface("eth0 | cat /etc/shadow") is False

    def test_validate_interface_invalid_newline_injection(self):
        """Should reject interface names with newline injection."""
        from core.network.network_utils import _validate_interface

        assert _validate_interface("eth0\nmalicious") is False


class TestValidateIp:
    """Test input validation for IP addresses."""

    def test_validate_ip_valid_localhost(self):
        """Should accept localhost IP."""
        from core.network.network_utils import _validate_ip

        assert _validate_ip("127.0.0.1") is True

    def test_validate_ip_valid_private(self):
        """Should accept private IP addresses."""
        from core.network.network_utils import _validate_ip

        assert _validate_ip("192.168.1.1") is True
        assert _validate_ip("10.0.0.1") is True
        assert _validate_ip("172.16.0.1") is True

    def test_validate_ip_valid_public(self):
        """Should accept public IP addresses."""
        from core.network.network_utils import _validate_ip

        assert _validate_ip("8.8.8.8") is True
        assert _validate_ip("1.1.1.1") is True

    def test_validate_ip_valid_edge_cases(self):
        """Should accept edge case valid IPs."""
        from core.network.network_utils import _validate_ip

        assert _validate_ip("0.0.0.0") is True
        assert _validate_ip("255.255.255.255") is True

    def test_validate_ip_invalid_semicolon_injection(self):
        """Should reject IPs with semicolon injection."""
        from core.network.network_utils import _validate_ip

        assert _validate_ip("192.168.1.1; rm -rf /") is False

    def test_validate_ip_invalid_command_substitution(self):
        """Should reject IPs with command substitution."""
        from core.network.network_utils import _validate_ip

        assert _validate_ip("$(whoami)") is False

    def test_validate_ip_invalid_octet_too_large(self):
        """Should reject IPs with octets > 255."""
        from core.network.network_utils import _validate_ip

        assert _validate_ip("192.168.1.256") is False
        assert _validate_ip("300.168.1.1") is False

    def test_validate_ip_invalid_missing_octet(self):
        """Should reject IPs with missing octets."""
        from core.network.network_utils import _validate_ip

        assert _validate_ip("192.168.1") is False

    def test_validate_ip_invalid_not_an_ip(self):
        """Should reject non-IP strings."""
        from core.network.network_utils import _validate_ip

        assert _validate_ip("not.an.ip") is False
        assert _validate_ip("a.b.c.d") is False

    def test_validate_ip_invalid_empty(self):
        """Should reject empty IP."""
        from core.network.network_utils import _validate_ip

        assert _validate_ip("") is False

    def test_validate_ip_invalid_extra_octets(self):
        """Should reject IPs with extra octets."""
        from core.network.network_utils import _validate_ip

        assert _validate_ip("192.168.1.1.1") is False

    def test_validate_ip_invalid_negative_octet(self):
        """Should reject IPs with negative octets."""
        from core.network.network_utils import _validate_ip

        assert _validate_ip("-1.168.1.1") is False


class TestValidateLatency:
    """Test input validation for latency values."""

    def test_validate_latency_valid_positive(self):
        """Should accept positive latency values."""
        from core.network.network_utils import _validate_latency

        assert _validate_latency(100) is True
        assert _validate_latency(1) is True
        assert _validate_latency(1000) is True

    def test_validate_latency_valid_zero(self):
        """Should accept zero latency."""
        from core.network.network_utils import _validate_latency

        assert _validate_latency(0) is True

    def test_validate_latency_invalid_negative(self):
        """Should reject negative latency values."""
        from core.network.network_utils import _validate_latency

        assert _validate_latency(-1) is False
        assert _validate_latency(-100) is False

    def test_validate_latency_invalid_too_large(self):
        """Should reject excessively large latency values."""
        from core.network.network_utils import _validate_latency

        # Latency > 10 seconds is likely invalid
        assert _validate_latency(100000) is False


class TestNoOsSystemUsage:
    """Test that network_utils does not use os.system."""

    def test_no_os_system_in_source(self):
        """network_utils should not contain os.system calls."""
        network_utils_path = (
            Path(__file__).resolve().parents[3] / "core" / "network" / "network_utils.py"
        )

        if network_utils_path.exists():
            source = network_utils_path.read_text()
            assert "os.system" not in source, (
                "os.system should not be used - it is vulnerable to shell injection"
            )

    def test_uses_subprocess_module(self):
        """network_utils should import and use subprocess."""
        network_utils_path = (
            Path(__file__).resolve().parents[3] / "core" / "network" / "network_utils.py"
        )

        if network_utils_path.exists():
            source = network_utils_path.read_text()
            assert "import subprocess" in source or "from subprocess" in source, (
                "subprocess module should be imported"
            )
            assert "subprocess.run" in source, (
                "subprocess.run should be used for command execution"
            )


class TestRunCmdHelper:
    """Test the _run_cmd helper function."""

    @patch("subprocess.run")
    def test_run_cmd_uses_list_arguments(self, mock_run):
        """_run_cmd should use list arguments, not shell=True."""
        from core.network.network_utils import _run_cmd

        mock_run.return_value = MagicMock(returncode=0)

        _run_cmd(["/usr/bin/echo", "test"])

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        # First argument should be a list
        assert isinstance(args[0], list)
        # shell=True should NOT be present or should be False
        assert kwargs.get("shell", False) is False

    @patch("subprocess.run")
    def test_run_cmd_captures_output(self, mock_run):
        """_run_cmd should capture stdout and stderr."""
        from core.network.network_utils import _run_cmd

        mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")

        _run_cmd(["/usr/bin/echo", "test"])

        args, kwargs = mock_run.call_args
        assert kwargs.get("capture_output") is True or (
            kwargs.get("stdout") is not None and kwargs.get("stderr") is not None
        )

    @patch("subprocess.run")
    def test_run_cmd_uses_text_mode(self, mock_run):
        """_run_cmd should use text mode for output."""
        from core.network.network_utils import _run_cmd

        mock_run.return_value = MagicMock(returncode=0)

        _run_cmd(["/usr/bin/echo", "test"])

        args, kwargs = mock_run.call_args
        assert kwargs.get("text") is True


class TestApplyLatencyRulesValidation:
    """Test that apply_latency_rules validates all input."""

    @patch("core.network.network_utils._run_cmd")
    def test_rejects_invalid_interface_semicolon(self, mock_run):
        """Should reject interface names with shell injection."""
        from core.network.network_utils import apply_latency_rules

        result = apply_latency_rules({"192.168.1.1": 100}, "; rm -rf /")

        assert result is False
        mock_run.assert_not_called()

    @patch("core.network.network_utils._run_cmd")
    def test_rejects_invalid_interface_command_sub(self, mock_run):
        """Should reject interface with command substitution."""
        from core.network.network_utils import apply_latency_rules

        result = apply_latency_rules({"192.168.1.1": 100}, "$(whoami)")

        assert result is False
        mock_run.assert_not_called()

    @patch("core.network.network_utils._run_cmd")
    def test_rejects_invalid_ip(self, mock_run):
        """Should reject invalid IP addresses."""
        from core.network.network_utils import apply_latency_rules

        result = apply_latency_rules({"invalid.ip": 100}, "eth0")

        assert result is False
        mock_run.assert_not_called()

    @patch("core.network.network_utils._run_cmd")
    def test_rejects_ip_with_injection(self, mock_run):
        """Should reject IPs with shell injection."""
        from core.network.network_utils import apply_latency_rules

        result = apply_latency_rules({"192.168.1.1; rm -rf /": 100}, "eth0")

        assert result is False
        mock_run.assert_not_called()

    @patch("core.network.network_utils._run_cmd")
    def test_rejects_invalid_latency(self, mock_run):
        """Should reject invalid latency values."""
        from core.network.network_utils import apply_latency_rules

        result = apply_latency_rules({"192.168.1.1": -100}, "eth0")

        assert result is False
        mock_run.assert_not_called()

    @patch("core.network.network_utils._run_cmd")
    def test_accepts_valid_input(self, mock_run):
        """Should accept and process valid input."""
        from core.network.network_utils import apply_latency_rules

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = apply_latency_rules({"192.168.1.1": 100}, "eth0")

        assert result is True
        # Verify _run_cmd was called (commands were executed)
        assert mock_run.called

    @patch("core.network.network_utils._run_cmd")
    def test_accepts_multiple_valid_ips(self, mock_run):
        """Should accept multiple valid IP/latency pairs."""
        from core.network.network_utils import apply_latency_rules

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = apply_latency_rules(
            {"192.168.1.1": 100, "192.168.1.2": 200, "10.0.0.1": 50}, "enp1s0"
        )

        assert result is True

    @patch("core.network.network_utils._run_cmd")
    def test_accepts_empty_ip_map(self, mock_run):
        """Should handle empty IP map gracefully."""
        from core.network.network_utils import apply_latency_rules

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Empty map is valid - just no rules to apply (still sets up qdisc)
        result = apply_latency_rules({}, "eth0")

        # Should return True - setup commands run but no IP-specific commands
        assert result is True


class TestDisposeValidation:
    """Test that dispose function validates input."""

    @patch("core.network.network_utils._run_cmd")
    def test_dispose_rejects_invalid_interface(self, mock_run):
        """dispose should reject invalid interface names."""
        from core.network.network_utils import dispose

        result = dispose("; rm -rf /")

        assert result is False
        mock_run.assert_not_called()

    @patch("core.network.network_utils._run_cmd")
    def test_dispose_accepts_valid_interface(self, mock_run):
        """dispose should accept valid interface names."""
        from core.network.network_utils import dispose

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = dispose("eth0")

        assert result is True
        mock_run.assert_called()


class TestCommandConstruction:
    """Test that commands are constructed safely."""

    @patch("subprocess.run")
    def test_commands_use_list_not_string(self, mock_run):
        """All commands should be passed as lists, not strings."""
        from core.network.network_utils import apply_latency_rules

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        apply_latency_rules({"192.168.1.1": 100}, "eth0")

        # Check all calls used list arguments
        for call_args in mock_run.call_args_list:
            args, kwargs = call_args
            assert isinstance(args[0], list), (
                f"Command should be a list, got: {type(args[0])}"
            )
            # Verify no shell=True
            assert kwargs.get("shell", False) is False, "shell=True should not be used"


class TestLogging:
    """Test that operations are properly logged."""

    @patch("core.network.network_utils._run_cmd")
    @patch("core.network.network_utils.logger")
    def test_logs_invalid_interface(self, mock_logger, mock_run):
        """Should log when rejecting invalid interface."""
        from core.network.network_utils import apply_latency_rules

        apply_latency_rules({"192.168.1.1": 100}, "; malicious")

        # Verify error was logged
        mock_logger.error.assert_called()

    @patch("core.network.network_utils._run_cmd")
    @patch("core.network.network_utils.logger")
    def test_logs_invalid_ip(self, mock_logger, mock_run):
        """Should log when rejecting invalid IP."""
        from core.network.network_utils import apply_latency_rules

        apply_latency_rules({"bad-ip": 100}, "eth0")

        # Verify error was logged
        mock_logger.error.assert_called()

    @patch("subprocess.run")
    @patch("core.network.network_utils.logger")
    def test_logs_command_execution(self, mock_logger, mock_run):
        """Should log command execution at debug level."""
        from core.network.network_utils import _run_cmd

        mock_run.return_value = MagicMock(returncode=0)

        _run_cmd(["/usr/bin/echo", "test"])

        # Verify debug logging occurred
        mock_logger.debug.assert_called()


class TestErrorHandling:
    """Test error handling in network utilities."""

    @patch("subprocess.run")
    def test_handles_command_failure(self, mock_run):
        """Should handle command execution failures gracefully."""
        from core.network.network_utils import apply_latency_rules

        # Simulate command failure
        mock_run.side_effect = subprocess.CalledProcessError(1, "tc")

        result = apply_latency_rules({"192.168.1.1": 100}, "eth0")

        # Should return False on failure, not raise exception
        assert result is False

    @patch("subprocess.run")
    def test_handles_permission_error(self, mock_run):
        """Should handle permission errors gracefully."""
        from core.network.network_utils import apply_latency_rules

        mock_run.side_effect = PermissionError("Permission denied")

        result = apply_latency_rules({"192.168.1.1": 100}, "eth0")

        assert result is False

    @patch("subprocess.run")
    def test_handles_file_not_found(self, mock_run):
        """Should handle missing executables gracefully."""
        from core.network.network_utils import apply_latency_rules

        mock_run.side_effect = FileNotFoundError("tc not found")

        result = apply_latency_rules({"192.168.1.1": 100}, "eth0")

        assert result is False
