"""Dota 2 message processor (reuses AMP parser for identical console format)."""

from core.adapters.amp.message_processor import (
    AMPMessageProcessor as Dota2MessageProcessor,
)

__all__ = ["Dota2MessageProcessor"]
