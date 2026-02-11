"""Base status parser for game server status output.

This module provides the abstract StatusParser class that encapsulates
status parsing state, replacing the 6-variable state machine with a
clean, testable interface.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List


class StatusParseState(Enum):
    """Status parsing state machine states."""

    IDLE = auto()
    PARSING = auto()


@dataclass
class StatusParseContext:
    """Encapsulates all status parsing state.

    Attributes:
        state: Current parsing state (IDLE or PARSING).
        lines: Accumulated lines during parsing.
        seen_separator: Whether the separator line has been encountered.
    """

    state: StatusParseState = StatusParseState.IDLE
    lines: List[str] = field(default_factory=list)
    seen_separator: bool = False

    def reset(self) -> None:
        """Reset all state to initial values."""
        self.state = StatusParseState.IDLE
        self.lines.clear()
        self.seen_separator = False


class StatusParser:
    """Base class for game-specific status parsing.

    This class encapsulates the status parsing state machine that was
    previously spread across 6 instance variables. It provides a clean
    interface for:
    - Starting a new parsing session
    - Accumulating lines
    - Tracking separator detection
    - Completing parsing and retrieving results

    Game-specific subclasses should extend this with parsing logic
    specific to their server's status output format.
    """

    def __init__(self) -> None:
        """Initialize the status parser with idle state."""
        self._ctx = StatusParseContext()

    @property
    def is_parsing(self) -> bool:
        """Check if currently in parsing state.

        Returns:
            True if parsing is in progress, False otherwise.
        """
        return self._ctx.state == StatusParseState.PARSING

    @property
    def seen_separator(self) -> bool:
        """Check if separator line has been seen.

        Returns:
            True if separator has been encountered, False otherwise.
        """
        return self._ctx.seen_separator

    @property
    def line_count(self) -> int:
        """Get the number of accumulated lines.

        Returns:
            The count of lines accumulated during parsing.
        """
        return len(self._ctx.lines)

    @property
    def lines(self) -> list[str]:
        """Get a copy of accumulated lines.

        Returns:
            A copy of the list of accumulated lines.
        """
        return self._ctx.lines.copy()

    def start_parsing(self) -> None:
        """Start a new status parsing session.

        Resets any previous state and sets the parser to PARSING state.
        """
        self._ctx.reset()
        self._ctx.state = StatusParseState.PARSING

    def add_line(self, line: str) -> None:
        """Add a line to the parsing session.

        Args:
            line: The line to add to accumulated lines.
        """
        self._ctx.lines.append(line)

    def mark_separator_seen(self) -> None:
        """Mark that the separator line has been encountered."""
        self._ctx.seen_separator = True

    def complete(self) -> List[str]:
        """Complete parsing and return accumulated lines.

        Returns a copy of the accumulated lines and resets all state
        back to IDLE.

        Returns:
            List of accumulated lines during the parsing session.
        """
        lines = self._ctx.lines.copy()
        self._ctx.reset()
        return lines
