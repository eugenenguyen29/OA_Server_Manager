"""Tests for StatusParser class.

TDD Phase 2: Tests for the StatusParser abstraction that encapsulates
the 6-variable status parsing state machine into a clean interface.
"""

from core.adapters.status_parser import (
    StatusParser,
    StatusParseState,
    StatusParseContext,
)


class TestStatusParseState:
    """Test StatusParseState enum."""

    def test_idle_state_exists(self):
        """IDLE state should be defined."""
        assert StatusParseState.IDLE is not None

    def test_parsing_state_exists(self):
        """PARSING state should be defined."""
        assert StatusParseState.PARSING is not None


class TestStatusParseContext:
    """Test StatusParseContext dataclass."""

    def test_default_state_is_idle(self):
        """Default state should be IDLE."""
        ctx = StatusParseContext()
        assert ctx.state == StatusParseState.IDLE

    def test_default_lines_is_empty(self):
        """Default lines should be empty list."""
        ctx = StatusParseContext()
        assert ctx.lines == []

    def test_default_seen_separator_is_false(self):
        """Default seen_separator should be False."""
        ctx = StatusParseContext()
        assert ctx.seen_separator is False

    def test_reset_clears_all_state(self):
        """reset() should clear all state to defaults."""
        ctx = StatusParseContext()
        ctx.state = StatusParseState.PARSING
        ctx.lines = ["line1", "line2"]
        ctx.seen_separator = True

        ctx.reset()

        assert ctx.state == StatusParseState.IDLE
        assert ctx.lines == []
        assert ctx.seen_separator is False


class TestStatusParser:
    """Test base StatusParser functionality."""

    def test_initial_state_is_idle(self):
        """Parser should start in IDLE state."""
        parser = StatusParser()
        assert not parser.is_parsing
        assert parser._ctx.state == StatusParseState.IDLE

    def test_start_parsing_changes_state(self):
        """start_parsing() should change state to PARSING."""
        parser = StatusParser()

        parser.start_parsing()

        assert parser.is_parsing
        assert parser._ctx.state == StatusParseState.PARSING

    def test_start_parsing_resets_previous_state(self):
        """start_parsing() should reset any previous parsing state."""
        parser = StatusParser()
        parser.start_parsing()
        parser.add_line("old line")
        parser.mark_separator_seen()

        # Start a new parsing session
        parser.start_parsing()

        assert parser.is_parsing
        assert parser._ctx.lines == []
        assert not parser.seen_separator

    def test_add_line_stores_lines(self):
        """add_line() should accumulate lines."""
        parser = StatusParser()
        parser.start_parsing()

        parser.add_line("line 1")
        parser.add_line("line 2")
        parser.add_line("line 3")

        assert parser._ctx.lines == ["line 1", "line 2", "line 3"]

    def test_mark_separator_seen(self):
        """mark_separator_seen() should set separator flag."""
        parser = StatusParser()
        assert not parser.seen_separator

        parser.mark_separator_seen()

        assert parser.seen_separator

    def test_complete_returns_lines(self):
        """complete() should return collected lines."""
        parser = StatusParser()
        parser.start_parsing()
        parser.add_line("line 1")
        parser.add_line("line 2")

        result = parser.complete()

        assert result == ["line 1", "line 2"]

    def test_complete_resets_state(self):
        """complete() should reset state after returning lines."""
        parser = StatusParser()
        parser.start_parsing()
        parser.add_line("line 1")
        parser.mark_separator_seen()

        parser.complete()

        assert not parser.is_parsing
        assert parser._ctx.lines == []
        assert not parser.seen_separator

    def test_complete_returns_copy_not_reference(self):
        """complete() should return a copy of lines, not the internal list."""
        parser = StatusParser()
        parser.start_parsing()
        parser.add_line("line 1")

        result = parser.complete()
        result.append("modified")

        # Internal state should not be affected
        assert "modified" not in parser._ctx.lines


class TestOAStatusParser:
    """Test OpenArena-specific status parsing."""

    def test_inherits_from_status_parser(self):
        """OAStatusParser should inherit from StatusParser."""
        from core.adapters.openarena.status_parser import OAStatusParser

        parser = OAStatusParser()
        assert isinstance(parser, StatusParser)

    def test_parse_client_line_human(self):
        """Should parse human player line correctly."""
        from core.adapters.openarena.status_parser import OAStatusParser

        parser = OAStatusParser()
        # Format: num score ping name lastmsg address qport rate
        line = "  0    5  100 Player1      0 192.168.1.100:27961   12345 25000"
        result = parser.parse_client_line(line)

        assert result is not None
        assert result["client_id"] == 0
        assert result["score"] == 5
        assert result["ping"] == 100
        assert result["name"] == "Player1"
        assert result["ip"] == "192.168.1.100"
        assert result["type"] == "HUMAN"

    def test_parse_client_line_bot(self):
        """Should parse bot player line correctly."""
        from core.adapters.openarena.status_parser import OAStatusParser

        parser = OAStatusParser()
        # Bot lines have "bot" as address
        line = "  1    3   50 BotPlayer    0 bot                   0 25000"
        result = parser.parse_client_line(line)

        assert result is not None
        assert result["client_id"] == 1
        assert result["name"] == "BotPlayer"
        assert result["ip"] == "bot"
        assert result["type"] == "BOT"

    def test_parse_client_line_insufficient_parts(self):
        """Should return None for lines with insufficient parts."""
        from core.adapters.openarena.status_parser import OAStatusParser

        parser = OAStatusParser()
        line = "  0    5  100"  # Only 3 parts
        result = parser.parse_client_line(line)

        assert result is None

    def test_parse_client_line_invalid_format(self):
        """Should return None for lines with invalid format."""
        from core.adapters.openarena.status_parser import OAStatusParser

        parser = OAStatusParser()
        line = "not a valid client line at all"
        result = parser.parse_client_line(line)

        assert result is None

    def test_detect_status_header(self):
        """Should detect status header line."""
        from core.adapters.openarena.status_parser import OAStatusParser

        parser = OAStatusParser()
        header_line = (
            "num score ping name            lastmsg address               qport rate"
        )

        assert parser.is_status_header(header_line)

    def test_detect_non_header(self):
        """Should not detect non-header lines as header."""
        from core.adapters.openarena.status_parser import OAStatusParser

        parser = OAStatusParser()

        assert not parser.is_status_header("map: oasago2")
        assert not parser.is_status_header("some random line")
        assert not parser.is_status_header("---")

    def test_detect_separator(self):
        """Should detect separator line."""
        from core.adapters.openarena.status_parser import OAStatusParser

        parser = OAStatusParser()

        assert parser.is_separator("---")
        assert parser.is_separator("-------")
        assert parser.is_separator("--- separator ---")
        assert not parser.is_separator("not a separator")

    def test_validate_ip_valid(self):
        """Should accept valid IP addresses."""
        from core.adapters.openarena.status_parser import OAStatusParser

        parser = OAStatusParser()

        assert parser._is_valid_ip("192.168.1.1")
        assert parser._is_valid_ip("10.0.0.1")
        assert parser._is_valid_ip("255.255.255.255")
        assert parser._is_valid_ip("0.0.0.0")

    def test_validate_ip_invalid(self):
        """Should reject invalid IP addresses."""
        from core.adapters.openarena.status_parser import OAStatusParser

        parser = OAStatusParser()

        assert not parser._is_valid_ip("256.1.1.1")
        assert not parser._is_valid_ip("192.168.1")
        assert not parser._is_valid_ip("192.168.1.1.1")
        assert not parser._is_valid_ip("abc.def.ghi.jkl")
        assert not parser._is_valid_ip("")
        assert not parser._is_valid_ip("bot")

    def test_validate_ip_edge_cases(self):
        """Should handle edge cases in IP validation."""
        from core.adapters.openarena.status_parser import OAStatusParser

        parser = OAStatusParser()

        assert not parser._is_valid_ip("-1.0.0.0")
        assert not parser._is_valid_ip("192.168.1.-1")
