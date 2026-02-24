"""Dota 2 status parser (reuses AMP parser for identical status format)."""

from core.adapters.amp.status_parser import AMPStatusParser as Dota2StatusParser

__all__ = ["Dota2StatusParser"]
