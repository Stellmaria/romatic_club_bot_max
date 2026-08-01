from __future__ import annotations

import ast

from scripts.migrate_callback_splits import migrate_source


def test_migrates_callback_split_and_preserves_arguments() -> None:
    source = '''"""Example."""\n\nfrom __future__ import annotations\n\n\ndef handle(call):\n    prefix, item_id = call.data.split("|", 1)\n    return prefix, item_id\n'''

    migrated, count = migrate_source(source)

    assert count == 1
    assert "call.data.split" not in migrated
    assert 'split_callback_data(call.data, "|", 1)' in migrated
    assert (
        "from bot.telegram.callback_parser import split_callback_data" in migrated
    )
    ast.parse(migrated)


def test_migrates_nested_and_reverse_splits_until_stable() -> None:
    source = '''def handle(callback_data):\n    return callback_data.split("|", 1)[1].rsplit(":", 1)\n'''

    migrated, count = migrate_source(source)
    second_pass, second_count = migrate_source(migrated)

    assert count == 2
    assert "split_callback_data" in migrated
    assert "rsplit_callback_data" in migrated
    assert ".split(" not in migrated
    assert ".rsplit(" not in migrated
    assert second_pass == migrated
    assert second_count == 0
    ast.parse(migrated)


def test_does_not_touch_non_callback_text_parsing() -> None:
    source = '''def handle(message):\n    return message.text.split(",")\n'''

    migrated, count = migrate_source(source)

    assert migrated == source
    assert count == 0
