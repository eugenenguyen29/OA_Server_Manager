"""Display utilities for formatted console output."""

import logging
from tabulate import tabulate

from core.adapters.base import ClientTracker


class DisplayUtils:
    """Utility class for formatted display output."""

    @staticmethod
    def display_client_table(
        client_tracker: ClientTracker, title: str = "CLIENT INFORMATION"
    ) -> None:
        """Display formatted client information table.

        Args:
            client_tracker: Client tracking interface providing table data.
            title: Title for the table display.
        """
        logger = logging.getLogger(__name__)

        # Get table data from client tracker
        table_data = client_tracker.get_client_info_table()

        if not table_data:
            logger.info("No clients connected")
            return

        # Define headers
        headers = ["Client ID", "IP Address", "Type", "Latency", "OBS Status", "Name"]

        # Create formatted output
        print("\n" + "=" * 80)
        print(f"{title:^80}")
        print("=" * 80)
        print(tabulate(table_data, headers=headers, tablefmt="grid"))
        print("=" * 80)

        # Log summary
        human_count = client_tracker.get_human_count()
        bot_count = client_tracker.get_bot_count()
        logger.info(f"Total clients: {human_count} humans, {bot_count} bots")

    @staticmethod
    def display_match_start(round_num: int, max_rounds: int) -> None:
        """
        Display match start information.

        Args:
            round_num: Current round number
            max_rounds: Total number of rounds
        """
        print("\n" + "╔" + "═" * 48 + "╗")
        print(f"║{'MATCH STARTING':^48}║")
        print(f"║{'Round ' + str(round_num) + '/' + str(max_rounds):^48}║")
        print("╚" + "═" * 48 + "╝")

    @staticmethod
    def display_match_end(round_num: int, max_rounds: int) -> None:
        """
        Display match end information.

        Args:
            round_num: Current round number
            max_rounds: Total number of rounds
        """
        print("\n" + "╔" + "═" * 48 + "╗")
        print(f"║{'MATCH COMPLETED':^48}║")
        print(
            f"║{'Round ' + str(round_num) + '/' + str(max_rounds) + ' finished':^48}║"
        )
        print("╚" + "═" * 48 + "╝")
