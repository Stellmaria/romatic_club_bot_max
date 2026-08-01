"""Deprecated import shim for explicit configuration models.

This module intentionally performs no environment bootstrap and exports no
runtime values. Use ``BotProcessSettings`` or ``UserbotProcessSettings`` in a
composition root; legacy application modules temporarily use the single adapter
in :mod:`bot.core.legacy_config`.
"""

from bot.core.environment import PROJECT_ROOT, load_project_environment, resolve_project_root
from bot.core.settings import (
    BidValidationMode,
    BotProcessSettings,
    BotSettings,
    CONFIG_SCHEMA,
    ConfigurationError,
    DatabaseSettings,
    LogLevel,
    Settings,
    SupervisorClientSettings,
    UserbotProcessSettings,
    UserbotSettings,
)

__all__ = (
    "BidValidationMode",
    "BotProcessSettings",
    "BotSettings",
    "CONFIG_SCHEMA",
    "ConfigurationError",
    "DatabaseSettings",
    "LogLevel",
    "PROJECT_ROOT",
    "Settings",
    "SupervisorClientSettings",
    "UserbotProcessSettings",
    "UserbotSettings",
    "load_project_environment",
    "resolve_project_root",
)
