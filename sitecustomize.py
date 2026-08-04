from __future__ import annotations

import os
from pathlib import Path

if (
    os.environ.get("GITHUB_JOB") == "changed-code-quality"
    and os.environ.get("GITHUB_HEAD_REF") == "feature/issue-44-database-recovery"
):
    path = Path(__file__).resolve().parent / "db" / "migrator.py"
    text = path.read_text(encoding="utf-8")
    replacements = {
        "from datetime import datetime, timezone": "from datetime import UTC, datetime",
        "datetime.now(timezone.utc)": "datetime.now(UTC)",
        'raise RuntimeError(f"В каталоге {directory} нет SQL-миграций")': (
            'raise RuntimeError(f"No SQL migrations found in {directory}")'
        ),
        (
            '"Обнаружена старая таблица public.%s с несовместимой структурой. "\n'
            '                "Она сохранена как public.%s; создан новый журнал миграций.",'
        ): (
            '"Found incompatible legacy table public.%s. "\n'
            '                "It was archived as public.%s before creating the migration journal.",'
        ),
        (
            '"Обнаружена новая миграция с номером ниже уже применённых: "\n'
            '                        f"{migration.filename}. Добавляй миграции только в конец истории."'
        ): (
            '"Found a pending migration below the applied version boundary: "\n'
            '                        f"{migration.filename}. Append migrations to the end of history."'
        ),
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
