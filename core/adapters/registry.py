"""Game adapter registry and factory."""

import logging
from typing import Dict, Type

from core.adapters.base import GameAdapter, GameAdapterConfig

logger = logging.getLogger(__name__)


class GameAdapterRegistry:
    """
    Registry for game adapters.
    Allows runtime selection of game adapter based on configuration.
    """

    _adapters: Dict[str, Type[GameAdapter]] = {}

    @classmethod
    def register(cls, game_type: str, adapter_class: Type[GameAdapter]) -> None:
        """Register a game adapter class."""
        cls._adapters[game_type.lower()] = adapter_class
        logger.debug(f"Registered adapter for game type: {game_type}")

    @classmethod
    def create(cls, config: GameAdapterConfig) -> GameAdapter:
        """Create game adapter instance from config."""
        game_type = config.game_type.lower()

        if game_type not in cls._adapters:
            available = list(cls._adapters.keys())
            raise ValueError(f"Unknown game type: {game_type}. Available: {available}")

        adapter_class = cls._adapters[game_type]
        logger.info(f"Creating {game_type} adapter: {adapter_class.__name__}")

        return adapter_class(config)

    @classmethod
    def get_available_games(cls) -> list:
        """Return list of registered game types."""
        return list(cls._adapters.keys())

    @classmethod
    def is_registered(cls, game_type: str) -> bool:
        """Check if a game type is registered."""
        return game_type.lower() in cls._adapters


def register_default_adapters() -> None:
    """Register all default game adapters."""
    from core.adapters.amp.adapter import AMPGameAdapter
    from core.adapters.openarena.adapter import OAGameAdapter

    GameAdapterRegistry.register("openarena", OAGameAdapter)
    GameAdapterRegistry.register("amp", AMPGameAdapter)
    logger.info(f"Registered adapters: {GameAdapterRegistry.get_available_games()}")
