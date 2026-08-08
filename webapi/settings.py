"""Process-scoped settings for the Telegram Mini App web process."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from bot.core.settings import ConfigurationError, ConfigurationIssue, DatabaseSettings

DEFAULT_WEBAPP_HOST = "0.0.0.0"  # noqa: S104 - container listener behind the host proxy
DEFAULT_LUXURY_CONTACT_URL = "https://t.me/velassya"


@dataclass(frozen=True, slots=True)
class WebAppSettings:
    bot_token: str
    database: DatabaseSettings
    host: str = DEFAULT_WEBAPP_HOST
    port: int = 8080
    auth_max_age_seconds: int = 3600
    luxury_chat_id: int = 0
    luxury_chat_id_lvl2: int = 0
    auction_channel_username: str = ""
    luxury_contact_url: str = DEFAULT_LUXURY_CONTACT_URL

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

        host = str(env.get("WEBAPP_HOST", DEFAULT_WEBAPP_HOST)).strip() or DEFAULT_WEBAPP_HOST
        port = _read_positive_int(env, "WEBAPP_PORT", 8080, issues, maximum=65535)
        auth_max_age_seconds = _read_positive_int(
            env,
            "WEBAPP_AUTH_MAX_AGE_SECONDS",
            3600,
            issues,
        )
        luxury_chat_id = _read_optional_int(env, "LUXURY_CHAT_ID", issues)
        luxury_chat_id_lvl2 = _read_optional_int(env, "LUXURY_CHAT_ID_LVL2", issues)
        auction_channel_username = str(env.get("AUCTION_CHANNEL_USERNAME", "")).strip().lstrip("@")
        luxury_contact_url = _read_https_url(
            env,
            "WEBAPP_LUXURY_CONTACT_URL",
            DEFAULT_LUXURY_CONTACT_URL,
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
            luxury_chat_id=luxury_chat_id,
            luxury_chat_id_lvl2=luxury_chat_id_lvl2,
            auction_channel_username=auction_channel_username,
            luxury_contact_url=luxury_contact_url,
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


def _read_optional_int(
    environ: Mapping[str, str],
    name: str,
    issues: list[ConfigurationIssue],
) -> int:
    raw = str(environ.get(name, "")).strip()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        issues.append(ConfigurationIssue(name, "must be an integer"))
        return 0


def _read_https_url(
    environ: Mapping[str, str],
    name: str,
    default: str,
    issues: list[ConfigurationIssue],
) -> str:
    value = str(environ.get(name, default)).strip() or default
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        issues.append(ConfigurationIssue(name, "is malformed"))
        return default
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        issues.append(ConfigurationIssue(name, "must be an absolute HTTPS URL"))
    if parsed.username is not None or parsed.password is not None:
        issues.append(ConfigurationIssue(name, "must not contain embedded credentials"))
    if parsed.fragment:
        issues.append(ConfigurationIssue(name, "must not contain a URL fragment"))
    return value


__all__ = ["WebAppSettings"]
