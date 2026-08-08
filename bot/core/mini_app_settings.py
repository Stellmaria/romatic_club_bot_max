"""Configuration for the optional Telegram Mini App launch surface."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit


class MiniAppConfigurationError(ValueError):
    """Raised when the public Mini App URL is malformed."""


@dataclass(frozen=True, slots=True)
class MiniAppSettings:
    """Bot-side settings used to expose the Mini App in Telegram."""

    public_url: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.public_url)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> MiniAppSettings:
        env = os.environ if environ is None else environ
        public_url = str(env.get("WEBAPP_PUBLIC_URL", "")).strip()
        if not public_url:
            return cls()

        parsed = urlsplit(public_url)
        if parsed.scheme.casefold() != "https" or not parsed.hostname:
            raise MiniAppConfigurationError("WEBAPP_PUBLIC_URL: must be an absolute HTTPS URL")
        if parsed.username is not None or parsed.password is not None:
            raise MiniAppConfigurationError("WEBAPP_PUBLIC_URL: must not contain embedded credentials")
        if parsed.fragment:
            raise MiniAppConfigurationError("WEBAPP_PUBLIC_URL: must not contain a URL fragment")
        return cls(public_url=public_url)


__all__ = ["MiniAppConfigurationError", "MiniAppSettings"]
