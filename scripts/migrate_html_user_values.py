"""Escape the audited direct Telegram user values in HTML messages."""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "bot/handlers/auction/warnings.py": (
        "{message.from_user.username or 'user'}",
        "{escape_html(message.from_user.username or 'user')}",
    ),
    "bot/handlers/profile.py": (
        "{message.from_user.full_name}",
        "{escape_html(message.from_user.full_name)}",
    ),
    "bot/handlers/users.py": (
        "{message.from_user.full_name}",
        "{escape_html(message.from_user.full_name)}",
    ),
}
IMPORT = "from bot.telegram.boundary import escape_html\n"


def migrate(path: Path, old: str, new: str) -> bool:
    source = path.read_text(encoding="utf-8")
    migrated = source.replace(old, new)
    if migrated == source:
        return False
    if IMPORT not in migrated:
        lines = migrated.splitlines(keepends=True)
        insertion = 0
        while insertion < len(lines):
            stripped = lines[insertion].strip()
            if (
                not stripped
                or stripped.startswith('"""')
                or stripped.startswith("from __future__ import")
                or stripped.startswith("import ")
                or stripped.startswith("from ")
                or stripped.startswith(")")
                or stripped.startswith("(")
                or stripped.endswith("(")
                or stripped.endswith(",")
            ):
                insertion += 1
                continue
            break
        lines.insert(insertion, IMPORT)
        migrated = "".join(lines)
    path.write_text(migrated, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    changed: list[str] = []
    for relative, (old, new) in TARGETS.items():
        path = ROOT / relative
        source = path.read_text(encoding="utf-8")
        if old not in source:
            continue
        changed.append(relative)
        if args.write:
            migrate(path, old, new)
    if changed and not args.write:
        print("Unsafe HTML user values remain:")
        for relative in changed:
            print(f"- {relative}")
        return 1
    print(f"Escaped direct user values in {len(changed)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
