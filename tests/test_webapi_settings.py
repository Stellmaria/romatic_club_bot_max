from __future__ import annotations

from webapi.settings import WebAppSettings


def test_webapp_settings_load_luxury_and_channel_configuration() -> None:
    settings = WebAppSettings.from_env(
        {
            "BOT_TOKEN": "123456:" + "test-token",
            "DATABASE_URL": "postgresql://user:password@localhost/app",
            "LUXURY_CHAT_ID": "-100100",
            "LUXURY_CHAT_ID_LVL2": "-100200",
            "AUCTION_CHANNEL_USERNAME": "@card_house",
            "WEBAPP_LUXURY_CONTACT_URL": "https://t.me/velassya",
        }
    )

    assert settings.luxury_chat_id == -100100
    assert settings.luxury_chat_id_lvl2 == -100200
    assert settings.auction_channel_username == "card_house"
    assert settings.luxury_contact_url == "https://t.me/velassya"
