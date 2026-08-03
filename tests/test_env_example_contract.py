from __future__ import annotations

from pathlib import Path

from bot.core.settings import CONFIG_SCHEMA

ROOT = Path(__file__).resolve().parents[1]


def _env_values(filename: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in (ROOT / filename).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        assert separator, f"invalid {filename} line: {raw_line!r}"
        assert name not in values, f"duplicate {filename} variable: {name}"
        values[name] = value
    return values


def test_service_examples_contain_their_complete_process_schema() -> None:
    examples = {
        "bot": _env_values(".env.bot.example"),
        "userbot": _env_values(".env.userbot.example"),
    }
    for process, values in examples.items():
        missing = {
            field.name
            for field in CONFIG_SCHEMA
            if process in field.processes and field.name not in values
        }
        assert not missing, (process, missing)


def test_service_examples_do_not_leak_process_specific_secrets() -> None:
    bot = _env_values(".env.bot.example")
    userbot = _env_values(".env.userbot.example")
    assert "USERBOT_API_HASH" not in bot
    assert "TG_API_HASH" not in bot
    assert "BOT_TOKEN" not in userbot
    assert "SUPERVISOR_TOKEN" not in userbot
    assert "SUPERVISOR_TOKEN_FILE" not in userbot


def test_optional_defaults_match_each_process_schema() -> None:
    examples = {
        "bot": _env_values(".env.bot.example"),
        "userbot": _env_values(".env.userbot.example"),
    }
    mismatches: dict[tuple[str, str], tuple[str, str]] = {}
    for field in CONFIG_SCHEMA:
        if field.default is None:
            continue
        for process in field.processes:
            actual = examples[process][field.name]
            if actual != field.default:
                mismatches[(process, field.name)] = (field.default, actual)
    assert not mismatches


def test_host_example_contains_compose_environment_selection() -> None:
    host = _env_values(".env.example")
    assert host["BOT_ENV_FILE"] == ".env.bot"
    assert host["USERBOT_ENV_FILE"] == ".env.userbot"
    assert "BOT_TOKEN" not in host
    assert "USERBOT_API_HASH" not in host


def test_process_and_secret_metadata_is_explicit() -> None:
    names = [field.name for field in CONFIG_SCHEMA]
    assert len(names) == len(set(names))
    assert all(field.processes for field in CONFIG_SCHEMA)
    assert {field.name for field in CONFIG_SCHEMA if field.secret} >= {
        "BOT_TOKEN",
        "DATABASE_URL",
        "UID_HASH_KEY",
        "UID_ENC_KEY",
        "USERBOT_API_HASH",
        "SUPERVISOR_TOKEN",
    }
