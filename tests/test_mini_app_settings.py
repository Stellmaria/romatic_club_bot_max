from __future__ import annotations

import pytest

from bot.core.mini_app_settings import MiniAppConfigurationError, MiniAppSettings


def test_mini_app_is_disabled_without_public_url() -> None:
    settings = MiniAppSettings.from_env({})

    assert settings.enabled is False
    assert settings.public_url == ""


def test_mini_app_accepts_https_public_url() -> None:
    settings = MiniAppSettings.from_env(
        {"WEBAPP_PUBLIC_URL": "https://app.example.com/telegram"}
    )

    assert settings.enabled is True
    assert settings.public_url == "https://app.example.com/telegram"


@pytest.mark.parametrize(
    "url",
    [
        "http://app.example.com",
        "app.example.com",
        "https://user:password@app.example.com",
        "https://app.example.com/#fragment",
    ],
)
def test_mini_app_rejects_unsafe_public_url(url: str) -> None:
    with pytest.raises(MiniAppConfigurationError):
        MiniAppSettings.from_env({"WEBAPP_PUBLIC_URL": url})
