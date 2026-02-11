"""Tests for unified MessageType enum.

This module tests that the MessageType enum in core/adapters/base.py
has all required adapter message types plus legacy aliases for backward
compatibility during migration.
"""

from enum import Enum

from core.adapters.base import MessageType


class TestMessageTypeIsEnum:
    """Test that MessageType is a proper Enum."""

    def test_message_type_is_enum_class(self):
        """Verify MessageType is a subclass of Enum."""
        assert issubclass(MessageType, Enum)

    def test_message_type_instances_are_enum_members(self):
        """Verify MessageType members are Enum instances."""
        assert isinstance(MessageType.CLIENT_CONNECT, MessageType)
        assert isinstance(MessageType.UNKNOWN, MessageType)


class TestAdapterMessageTypesExist:
    """Test that all adapter message types are defined."""

    def test_client_connect_exists(self):
        """Verify CLIENT_CONNECT is defined."""
        assert hasattr(MessageType, "CLIENT_CONNECT")
        assert MessageType.CLIENT_CONNECT.value == "client_connect"

    def test_client_disconnect_exists(self):
        """Verify CLIENT_DISCONNECT is defined."""
        assert hasattr(MessageType, "CLIENT_DISCONNECT")
        assert MessageType.CLIENT_DISCONNECT.value == "client_disconnect"

    def test_game_start_exists(self):
        """Verify GAME_START is defined."""
        assert hasattr(MessageType, "GAME_START")
        assert MessageType.GAME_START.value == "game_start"

    def test_game_end_exists(self):
        """Verify GAME_END is defined."""
        assert hasattr(MessageType, "GAME_END")
        assert MessageType.GAME_END.value == "game_end"

    def test_warmup_start_exists(self):
        """Verify WARMUP_START is defined."""
        assert hasattr(MessageType, "WARMUP_START")
        assert MessageType.WARMUP_START.value == "warmup_start"

    def test_warmup_end_exists(self):
        """Verify WARMUP_END is defined."""
        assert hasattr(MessageType, "WARMUP_END")
        assert MessageType.WARMUP_END.value == "warmup_end"

    def test_player_kill_exists(self):
        """Verify PLAYER_KILL is defined."""
        assert hasattr(MessageType, "PLAYER_KILL")
        assert MessageType.PLAYER_KILL.value == "player_kill"

    def test_chat_message_exists(self):
        """Verify CHAT_MESSAGE is defined."""
        assert hasattr(MessageType, "CHAT_MESSAGE")
        assert MessageType.CHAT_MESSAGE.value == "chat_message"

    def test_status_update_exists(self):
        """Verify STATUS_UPDATE is defined."""
        assert hasattr(MessageType, "STATUS_UPDATE")
        assert MessageType.STATUS_UPDATE.value == "status_update"

    def test_server_shutdown_exists(self):
        """Verify SERVER_SHUTDOWN is defined."""
        assert hasattr(MessageType, "SERVER_SHUTDOWN")
        assert MessageType.SERVER_SHUTDOWN.value == "server_shutdown"

    def test_game_initialization_exists(self):
        """Verify GAME_INITIALIZATION is defined."""
        assert hasattr(MessageType, "GAME_INITIALIZATION")
        assert MessageType.GAME_INITIALIZATION.value == "game_initialization"

    def test_unknown_exists(self):
        """Verify UNKNOWN is defined."""
        assert hasattr(MessageType, "UNKNOWN")
        assert MessageType.UNKNOWN.value == "unknown"


class TestLegacyAliasesExist:
    """Test that legacy message type aliases are defined."""

    def test_client_connecting_alias_exists(self):
        """Verify CLIENT_CONNECTING legacy alias is defined."""
        assert hasattr(MessageType, "CLIENT_CONNECTING")

    def test_match_end_fraglimit_alias_exists(self):
        """Verify MATCH_END_FRAGLIMIT legacy alias is defined."""
        assert hasattr(MessageType, "MATCH_END_FRAGLIMIT")

    def test_match_end_timelimit_alias_exists(self):
        """Verify MATCH_END_TIMELIMIT legacy alias is defined."""
        assert hasattr(MessageType, "MATCH_END_TIMELIMIT")

    def test_warmup_state_alias_exists(self):
        """Verify WARMUP_STATE legacy alias is defined."""
        assert hasattr(MessageType, "WARMUP_STATE")

    def test_shutdown_game_alias_exists(self):
        """Verify SHUTDOWN_GAME legacy alias is defined."""
        assert hasattr(MessageType, "SHUTDOWN_GAME")

    def test_status_line_alias_exists(self):
        """Verify STATUS_LINE legacy alias is defined."""
        assert hasattr(MessageType, "STATUS_LINE")


class TestLegacyAliasesMapCorrectly:
    """Test that legacy aliases map to correct adapter types."""

    def test_client_connecting_maps_to_client_connect(self):
        """Verify CLIENT_CONNECTING maps to CLIENT_CONNECT value."""
        assert MessageType.CLIENT_CONNECTING.value == MessageType.CLIENT_CONNECT.value
        assert MessageType.CLIENT_CONNECTING.value == "client_connect"

    def test_match_end_fraglimit_maps_to_game_end(self):
        """Verify MATCH_END_FRAGLIMIT maps to GAME_END value."""
        assert MessageType.MATCH_END_FRAGLIMIT.value == MessageType.GAME_END.value
        assert MessageType.MATCH_END_FRAGLIMIT.value == "game_end"

    def test_match_end_timelimit_maps_to_game_end(self):
        """Verify MATCH_END_TIMELIMIT maps to GAME_END value."""
        assert MessageType.MATCH_END_TIMELIMIT.value == MessageType.GAME_END.value
        assert MessageType.MATCH_END_TIMELIMIT.value == "game_end"

    def test_warmup_state_maps_to_warmup_start(self):
        """Verify WARMUP_STATE maps to WARMUP_START value."""
        assert MessageType.WARMUP_STATE.value == MessageType.WARMUP_START.value
        assert MessageType.WARMUP_STATE.value == "warmup_start"

    def test_shutdown_game_maps_to_server_shutdown(self):
        """Verify SHUTDOWN_GAME maps to SERVER_SHUTDOWN value."""
        assert MessageType.SHUTDOWN_GAME.value == MessageType.SERVER_SHUTDOWN.value
        assert MessageType.SHUTDOWN_GAME.value == "server_shutdown"

    def test_status_line_maps_to_status_update(self):
        """Verify STATUS_LINE maps to STATUS_UPDATE value."""
        assert MessageType.STATUS_LINE.value == MessageType.STATUS_UPDATE.value
        assert MessageType.STATUS_LINE.value == "status_update"


class TestEnumBehavior:
    """Test Enum-specific behavior of MessageType."""

    def test_aliases_are_same_enum_member(self):
        """Verify that aliases with same value are the same Enum member.

        Python Enum treats members with the same value as aliases.
        The second member becomes an alias of the first.
        """
        # CLIENT_CONNECTING should be an alias of CLIENT_CONNECT
        assert MessageType.CLIENT_CONNECTING is MessageType.CLIENT_CONNECT

        # MATCH_END_FRAGLIMIT and MATCH_END_TIMELIMIT should both be aliases of GAME_END
        assert MessageType.MATCH_END_FRAGLIMIT is MessageType.GAME_END
        assert MessageType.MATCH_END_TIMELIMIT is MessageType.GAME_END

        # WARMUP_STATE should be an alias of WARMUP_START
        assert MessageType.WARMUP_STATE is MessageType.WARMUP_START

        # SHUTDOWN_GAME should be an alias of SERVER_SHUTDOWN
        assert MessageType.SHUTDOWN_GAME is MessageType.SERVER_SHUTDOWN

        # STATUS_LINE should be an alias of STATUS_UPDATE
        assert MessageType.STATUS_LINE is MessageType.STATUS_UPDATE

    def test_lookup_by_value(self):
        """Verify MessageType can be looked up by value."""
        assert MessageType("client_connect") is MessageType.CLIENT_CONNECT
        assert MessageType("game_end") is MessageType.GAME_END
        assert MessageType("server_shutdown") is MessageType.SERVER_SHUTDOWN

    def test_all_core_types_in_members(self):
        """Verify all core types are in __members__ (aliases excluded from iteration)."""
        members = list(MessageType)
        member_names = [m.name for m in members]

        # Core types should be present
        assert "CLIENT_CONNECT" in member_names
        assert "GAME_END" in member_names
        assert "WARMUP_START" in member_names
        assert "SERVER_SHUTDOWN" in member_names
        assert "STATUS_UPDATE" in member_names

        # Aliases should NOT be in iteration (they are aliases, not primary members)
        # But they should be accessible via __members__
        assert "CLIENT_CONNECTING" in MessageType.__members__
        assert "MATCH_END_FRAGLIMIT" in MessageType.__members__
        assert "MATCH_END_TIMELIMIT" in MessageType.__members__
        assert "WARMUP_STATE" in MessageType.__members__
        assert "SHUTDOWN_GAME" in MessageType.__members__
        assert "STATUS_LINE" in MessageType.__members__
