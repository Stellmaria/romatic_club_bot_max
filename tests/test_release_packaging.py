from __future__ import annotations

from pathlib import Path

from scripts.build_release import should_include


def test_release_excludes_credentials_and_personal_data() -> None:
    excluded = (
        ".env",
        ".envrc",
        "db.sql",
        "userbot_session.session",
        "userbot_session.session-journal",
        "userbot_session.session-wal",
        "private/legacy_database_dump.sql",
        "var/userbot_session.session",
        "backfill_users.csv",
        "dump/users.sql",
        "snapshot.sql",
        "backup.sql.gz",
        "original.zip",
        "auction_bot.whl",
        ".ENV",
        "TOKEN.SESSION",
        "backup.DUMP",
        "Private/runtime.txt",
        "database/users.sql",
        "database/migrations/users.sql",
        "auction_telegram_bot.egg-info/PKG-INFO",
        "Auction_Telegram_Bot.EGG-INFO/entry_points.txt",
    )
    for name in excluded:
        assert not should_include(Path(name)), name


def test_release_keeps_configuration_template_and_source() -> None:
    assert should_include(Path(".env.example"))
    assert should_include(Path("bot/core/settings.py"))
    assert should_include(Path("database/migrations/006_outbox_delivery_control.sql"))
    assert should_include(Path("database/pgadmin_schema.sql"))
