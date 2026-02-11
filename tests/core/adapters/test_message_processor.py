"""Tests for message processor migration.

This test module verifies:
1. OAMessageProcessor uses StatusParser internally
2. Legacy state variables have been removed
3. All message parsing functionality works correctly
4. Legacy message processor module has been deleted
"""

import pytest
from unittest.mock import MagicMock


class TestOAMessageProcessorUsingStatusParser:
    """Test OAMessageProcessor uses StatusParser."""

    def test_uses_status_parser_internally(self):
        """OAMessageProcessor should use StatusParser for status parsing."""
        from core.adapters.openarena.message_processor import OAMessageProcessor
        from core.adapters.status_parser import StatusParser

        processor = OAMessageProcessor()
        assert hasattr(processor, "_status_parser")
        assert isinstance(processor._status_parser, StatusParser)

    def test_no_legacy_parsing_status_variable(self):
        """Should not have legacy _parsing_status variable."""
        from core.adapters.openarena.message_processor import OAMessageProcessor

        processor = OAMessageProcessor()
        assert not hasattr(processor, "_parsing_status")

    def test_no_legacy_status_lines_variable(self):
        """Should not have legacy _status_lines variable."""
        from core.adapters.openarena.message_processor import OAMessageProcessor

        processor = OAMessageProcessor()
        assert not hasattr(processor, "_status_lines")

    def test_no_legacy_status_line_count_variable(self):
        """Should not have legacy _status_line_count variable."""
        from core.adapters.openarena.message_processor import OAMessageProcessor

        processor = OAMessageProcessor()
        assert not hasattr(processor, "_status_line_count")

    def test_no_legacy_status_header_detected_variable(self):
        """Should not have legacy _status_header_detected variable."""
        from core.adapters.openarena.message_processor import OAMessageProcessor

        processor = OAMessageProcessor()
        assert not hasattr(processor, "_status_header_detected")

    def test_status_client_count_is_logging_only(self):
        """_status_client_count should only track count for logging, not parsing state."""
        from core.adapters.openarena.message_processor import OAMessageProcessor

        processor = OAMessageProcessor()
        # This variable is allowed as it's for logging only, not parsing state
        # The 6 legacy state variables were: _parsing_status, _status_lines,
        # _status_line_count, _status_header_detected, _status_client_count, _seen_separator
        # Now replaced by StatusParser with StatusParseContext
        # _status_client_count is retained for logging client extraction count
        assert hasattr(processor, "_status_client_count")
        assert processor._status_client_count == 0

    def test_no_legacy_seen_separator_variable(self):
        """Should not have legacy _seen_separator variable."""
        from core.adapters.openarena.message_processor import OAMessageProcessor

        processor = OAMessageProcessor()
        assert not hasattr(processor, "_seen_separator")


class TestOAMessageProcessorParsing:
    """Test OAMessageProcessor parsing functionality."""

    def test_parse_client_connecting(self):
        """Should parse client connecting message."""
        from core.adapters.openarena.message_processor import OAMessageProcessor
        from core.adapters.base import MessageType

        processor = OAMessageProcessor()
        result = processor.process_message(
            "Client 0 connecting with 100 challenge ping"
        )

        assert result.message_type == MessageType.CLIENT_CONNECT
        assert result.data["client_id"] == 0
        assert result.data["challenge_ping"] == 100

    def test_parse_client_disconnect(self):
        """Should parse client disconnect message."""
        from core.adapters.openarena.message_processor import OAMessageProcessor
        from core.adapters.base import MessageType

        processor = OAMessageProcessor()
        result = processor.process_message("ClientDisconnect: 2")

        assert result.message_type == MessageType.CLIENT_DISCONNECT
        assert result.data["client_id"] == 2

    def test_parse_game_initialization(self):
        """Should parse game initialization message."""
        from core.adapters.openarena.message_processor import OAMessageProcessor
        from core.adapters.base import MessageType

        processor = OAMessageProcessor()
        result = processor.process_message("------- Game Initialization -------")

        assert result.message_type == MessageType.GAME_INITIALIZATION

    def test_parse_fraglimit(self):
        """Should parse fraglimit hit message."""
        from core.adapters.openarena.message_processor import OAMessageProcessor
        from core.adapters.base import MessageType

        processor = OAMessageProcessor()
        result = processor.process_message("Exit: Fraglimit hit.")

        assert result.message_type == MessageType.GAME_END
        assert result.data["reason"] == "fraglimit"

    def test_parse_timelimit(self):
        """Should parse timelimit hit message."""
        from core.adapters.openarena.message_processor import OAMessageProcessor
        from core.adapters.base import MessageType

        processor = OAMessageProcessor()
        result = processor.process_message("Exit: Timelimit hit.")

        assert result.message_type == MessageType.GAME_END
        assert result.data["reason"] == "timelimit"

    def test_parse_warmup(self):
        """Should parse warmup message."""
        from core.adapters.openarena.message_processor import OAMessageProcessor
        from core.adapters.base import MessageType

        processor = OAMessageProcessor()
        result = processor.process_message("Warmup:")

        assert result.message_type == MessageType.WARMUP_START

    def test_parse_shutdown(self):
        """Should parse shutdown message."""
        from core.adapters.openarena.message_processor import OAMessageProcessor
        from core.adapters.base import MessageType

        processor = OAMessageProcessor()
        result = processor.process_message("ShutdownGame:")

        assert result.message_type == MessageType.SERVER_SHUTDOWN

    def test_parse_unknown(self):
        """Should return UNKNOWN for unrecognized messages."""
        from core.adapters.openarena.message_processor import OAMessageProcessor
        from core.adapters.base import MessageType

        processor = OAMessageProcessor()
        result = processor.process_message("Some random server output")

        assert result.message_type == MessageType.UNKNOWN


class TestOAMessageProcessorStatusParsing:
    """Test OAMessageProcessor status parsing with StatusParser."""

    def test_status_header_starts_parsing(self):
        """Status header should start parsing via StatusParser."""
        from core.adapters.openarena.message_processor import OAMessageProcessor
        from core.adapters.base import MessageType

        processor = OAMessageProcessor()
        result = processor.process_message(
            "num score ping name            lastmsg address               qport rate"
        )

        assert result.message_type == MessageType.STATUS_UPDATE
        # Should be using StatusParser internally
        assert processor._status_parser.is_parsing

    def test_status_separator_line(self):
        """Separator line should be tracked via StatusParser."""
        from core.adapters.openarena.message_processor import OAMessageProcessor
        from core.adapters.base import MessageType

        processor = OAMessageProcessor()
        # Start parsing
        processor.process_message(
            "num score ping name            lastmsg address               qport rate"
        )
        # Process separator
        result = processor.process_message(
            "--- ----- ---- --------------- ------- --------------------- ----- -----"
        )

        assert result.message_type == MessageType.STATUS_UPDATE
        assert processor._status_parser.seen_separator

    def test_status_client_data_extraction(self):
        """Client data should be extracted during status parsing."""
        from core.adapters.openarena.message_processor import OAMessageProcessor
        from core.adapters.base import MessageType

        processor = OAMessageProcessor()
        # Start parsing
        processor.process_message(
            "num score ping name            lastmsg address               qport rate"
        )
        # Process separator
        processor.process_message(
            "--- ----- ---- --------------- ------- --------------------- ----- -----"
        )
        # Process client line
        result = processor.process_message(
            "  0    5  100 Player1              0 192.168.1.100:27961   12345 25000"
        )

        assert result.message_type == MessageType.STATUS_UPDATE
        assert "client_data" in result.data

    def test_status_parsing_completes_on_empty_line(self):
        """Empty line should complete status parsing."""
        from core.adapters.openarena.message_processor import OAMessageProcessor

        processor = OAMessageProcessor()
        # Start parsing
        processor.process_message(
            "num score ping name            lastmsg address               qport rate"
        )
        # Process separator
        processor.process_message(
            "--- ----- ---- --------------- ------- --------------------- ----- -----"
        )
        # Complete with empty line
        result = processor.process_message("")

        # After completion, parsing should be done
        assert not processor._status_parser.is_parsing


class TestLegacyMessageProcessorRemoved:
    """Test that legacy message processor is removed."""

    def test_legacy_module_not_importable(self):
        """Legacy message processor module should not exist."""
        with pytest.raises(ImportError):
            from core.messaging.message_processor import MessageProcessor

    def test_legacy_message_type_not_importable(self):
        """Legacy MessageType from messaging module should not exist."""
        with pytest.raises(ImportError):
            from core.messaging.message_processor import MessageType

    def test_legacy_parsed_message_not_importable(self):
        """Legacy ParsedMessage from messaging module should not exist."""
        with pytest.raises(ImportError):
            from core.messaging.message_processor import ParsedMessage


class TestServerImportsOAMessageProcessor:
    """Test that server uses OAMessageProcessor."""

    def test_server_uses_unified_message_type(self):
        """Server should use MessageType from core.adapters.base."""
        # This verifies the import path is correct
        from core.adapters.base import MessageType

        # All message types used by server handlers should exist
        assert hasattr(MessageType, "CLIENT_CONNECT")
        assert hasattr(MessageType, "CLIENT_DISCONNECT")
        assert hasattr(MessageType, "GAME_INITIALIZATION")
        assert hasattr(MessageType, "GAME_END")
        assert hasattr(MessageType, "WARMUP_START")
        assert hasattr(MessageType, "SERVER_SHUTDOWN")
        assert hasattr(MessageType, "STATUS_UPDATE")

    def test_oa_message_processor_importable(self):
        """OAMessageProcessor should be importable from openarena module."""
        from core.adapters.openarena.message_processor import OAMessageProcessor

        processor = OAMessageProcessor()
        assert processor is not None


class TestMessageProcessorCallbackCompatibility:
    """Test send_command callback compatibility."""

    def test_callback_is_optional(self):
        """send_command callback should be optional."""
        from core.adapters.openarena.message_processor import OAMessageProcessor

        # Should work without callback
        processor = OAMessageProcessor()
        assert processor.send_command is None

    def test_callback_can_be_set(self):
        """send_command callback should be settable."""
        from core.adapters.openarena.message_processor import OAMessageProcessor

        mock_callback = MagicMock()
        processor = OAMessageProcessor(send_command_callback=mock_callback)

        assert processor.send_command == mock_callback

    def test_callback_invoked_on_client_connect(self):
        """send_command should be called when client connects."""
        from core.adapters.openarena.message_processor import OAMessageProcessor

        mock_callback = MagicMock()
        processor = OAMessageProcessor(send_command_callback=mock_callback)

        processor.process_message("Client 0 connecting with 100 challenge ping")

        # Should send 'status' command to get client IP
        mock_callback.assert_called_once_with("status")
