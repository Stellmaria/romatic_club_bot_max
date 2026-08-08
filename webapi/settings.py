"""Process-scoped settings for the Telegram Mini App web process."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from bot.core.settings import ConfigurationError, ConfigurationIssue, DatabaseSettings


@dataclass(frozen=True, slots=True)
class WebAppSettings:
    bot_token: str
    database: DatabaseSettings
    host: str = "0.0.0.0"
    port: int = 8080
    auth_max_age_seconds: int = 3600

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        project_root: str | Path | None = None,
    ) -> WebAppSettings:
        env = os.environ if environ is None else environ
        issues: list[ConfigurationIssue] = []

        bot_token = str(env.get("BOT_TOKEN", "")).strip()
        if not bot_token:
            issues.append(ConfigurationIssue("BOT_TOKEN", "is required"))

        host = str(env.get("WEBAPP_HOST", "0.0.0.0")).strip() or "0.0.0.0"
        port = _read_positive_int(env, "WEBAPP_PORT", 8080, issues, maximum=65535)
        auth_max_age_seconds = _read_positive_int(
            env,
            "WEBAPP_AUTH_MAX_AGE_SECONDS",
            3600,
            issues,
        )

        try:
            database = DatabaseSettings.from_env(env, project_root=project_root)
        except ConfigurationError as error:
            issues.extend(error.issues)
            database = DatabaseSettings(url="")

        if issues:
            raise ConfigurationError(issues)

        return cls(
            bot_token=bot_token,
            database=database,
            host=host,
            port=port,
            auth_max_age_seconds=auth_max_age_seconds,
        )


def _read_positive_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    issues: list[ConfigurationIssue],
    *,
    maximum: int | None = None,
) -> int:
    raw = str(environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        issues.append(ConfigurationIssue(name, "must be an integer"))
        return default
    if value <= 0:
        issues.append(ConfigurationIssue(name, "must be greater than zero"))
    if maximum is not None and value > maximum:
        issues.append(ConfigurationIssue(name, f"must be at most {maximum}"))
    return value


__all__ = ["WebAppSettings"]
