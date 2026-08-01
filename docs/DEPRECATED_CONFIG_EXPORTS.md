# Deprecated configuration exports

The former module-level exports from `config.py` and `bot.core.settings` were removed. Temporary access is isolated in `bot.core.legacy_config.legacy_config`.

| Legacy name | Typed replacement |
|---|---|
| `BOT_TOKEN` | `BotSettings.bot_token` |
| `ADMINS`, `ADMINS_OWNERS` | `BotSettings` / `UserbotSettings` admin fields |
| `AUCTION_CHANNEL_ID`, `AUCTION_CHANNEL_USERNAME`, `DISCUSSION_CHAT_ID` | process Telegram settings |
| `DATABASE_URL`, pool sizes, `DB_AUTO_MIGRATE` | `DatabaseSettings` |
| `USERBOT_API_ID`, `USERBOT_API_HASH`, `USERBOT_SESSION` | `UserbotSettings` |
| `TG_API_ID`, `TG_API_HASH`, `TG_SESSION`, `BACKFILL_LIMIT_POSTS` | `UserbotSettings` backfill fields |
| Supervisor variables | `SupervisorClientSettings` |
| schedule announcement variables | `UserbotSettings` schedule fields |

The adapter is a migration boundary, not a supported configuration API. Remove an entry from `DEPRECATION_INVENTORY` when its final consumer accepts a typed dependency.
