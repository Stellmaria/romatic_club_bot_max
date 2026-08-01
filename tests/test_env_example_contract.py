from __future__ import annotations

from pathlib import Path

from bot.core.settings import CONFIG_SCHEMA

ROOT = Path(__file__).resolve().parents[1]


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        assert separator, f"invalid .env.example line: {raw_line!r}"
        assert name not in values, f"duplicate .env.example variable: {name}"
        values[name] = value
    return values


def test_env_example_contains_the_complete_canonical_schema() -> None:
    values = _env_example_values()
    missing = {field.name for field in CONFIG_SCHEMA} - values.keys()
    assert not missing


def test_optional_defaults_match_the_schema() -> None:
    values = _env_example_values()
    mismatches = {
        field.name: (field.default, values[field.name])
        for field in CONFIG_SCHEMA
        if field.default is not None and values[field.name] != field.default
    }
    assert not mismatches


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
