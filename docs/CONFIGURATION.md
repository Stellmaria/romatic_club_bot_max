# Configuration architecture

Runtime configuration is loaded only in executable composition roots:

- `main.py` loads `.env`, constructs `BotProcessSettings`, and passes it to `run_bot`.
- `userbot/entrypoint.py` loads `.env`, constructs `UserbotProcessSettings`, and passes it to the userbot application.
- maintenance commands construct only the strict models they use.

Importing `bot.core.settings`, `bot.application`, `userbot.application`, database modules, or the Supervisor client does not read environment variables and does not create a settings singleton.

## Process boundaries

`BotProcessSettings` contains `BotSettings`, `DatabaseSettings`, and `SupervisorClientSettings`. It validates bot-only variables and never requires userbot credentials.

`UserbotProcessSettings` contains `UserbotSettings` and `DatabaseSettings`. It validates userbot-only variables and never requires `BOT_TOKEN` or Supervisor variables.

The smaller models also expose `from_env()` for focused maintenance and contract checks. Multiple models can be created from different mappings in one Python process; no cache or singleton is involved.

## Validation rules

Missing required values and malformed integers, booleans, enums, lists, paths, pool bounds, Fernet keys, or Supervisor secrets raise `ConfigurationError`. Every issue names the environment variable, but error messages never include its value.

Accepted booleans are `true/false`, `1/0`, `yes/no`, and `on/off` (plus Russian `да/нет`). `BID_VALIDATION_MODE` accepts only `bot`, `userbot`, or `db`.

Secrets are marked in `CONFIG_SCHEMA`. The canonical inventory is checked against `.env.example` by `tests/test_env_example_contract.py`.

## Compatibility adapter

Legacy handlers still importing historical constants use the single inert adapter in `bot.core.legacy_config`. Composition roots configure it with an already validated process model before importing those handlers. It performs no environment reads. New modules must accept typed settings explicitly instead.
