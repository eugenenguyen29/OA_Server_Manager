"""Tests for DisplayUtils Protocol typing with ClientTracker."""

from unittest.mock import Mock

from core.adapters.base import ClientTracker
from core.network.network_manager import NetworkManager
from core.utils.display_utils import DisplayUtils


def test_display_client_table_accepts_client_tracker():
    """display_client_table should accept a ClientTracker-typed argument."""
    display_utils = DisplayUtils()
    mock_tracker = Mock(spec=ClientTracker)
    mock_tracker.get_client_info_table.return_value = [
        [1, "192.168.1.100", "Human", 50, "Connected", "Player1"]
    ]
    mock_tracker.get_human_count.return_value = 1
    mock_tracker.get_bot_count.return_value = 0

    # Should not raise
    display_utils.display_client_table(mock_tracker, "TEST")

    mock_tracker.get_client_info_table.assert_called_once()


def test_display_client_table_with_network_manager():
    """NetworkManager satisfies the ClientTracker Protocol."""
    assert isinstance(NetworkManager(), ClientTracker)

    display_utils = DisplayUtils()
    network_manager = NetworkManager()

    # Should not raise - NetworkManager satisfies ClientTracker
    display_utils.display_client_table(network_manager, "TEST")


def test_display_client_table_with_mock_client_tracker():
    """Any object implementing ClientTracker methods should work."""

    class FakeTracker:
        def set_obs_status(self, ip: str, connected: bool) -> None:
            pass

        def get_client_id_by_ip(self, ip: str):
            return None

        def get_client_info_table(self):
            return []

        def get_human_count(self) -> int:
            return 0

        def get_bot_count(self) -> int:
            return 0

    tracker = FakeTracker()
    assert isinstance(tracker, ClientTracker)

    display_utils = DisplayUtils()
    display_utils.display_client_table(tracker, "TEST")


def test_display_client_table_calls_get_client_info_table():
    """display_client_table must call get_client_info_table on the tracker."""
    display_utils = DisplayUtils()
    mock_tracker = Mock(spec=ClientTracker)
    mock_tracker.get_client_info_table.return_value = [
        [1, "10.0.0.1", "Human", 20, "Connected", "Alice"]
    ]
    mock_tracker.get_human_count.return_value = 1
    mock_tracker.get_bot_count.return_value = 0

    display_utils.display_client_table(mock_tracker, "STATUS")

    mock_tracker.get_client_info_table.assert_called_once()
    mock_tracker.get_human_count.assert_called_once()
    mock_tracker.get_bot_count.assert_called_once()
