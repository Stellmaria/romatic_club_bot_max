from __future__ import annotations

import importlib.metadata
import re
import subprocess
import sys
from pathlib import Path

DENIED_LICENSE_MARKERS = {
    "agpl",
    "gnu affero general public license",
    "sspl",
    "server side public license",
}

ALLOWED_UNKNOWN = {
    # pymorphy2 metadata is incomplete on some distributions; the project is MIT licensed.
    "pymorphy2",
    "pymorphy2-dicts-ru",
}


def requirement_names(lock_path: Path) -> list[str]:
    names: list[str] = []
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)", line)
        if match:
            names.append(match.group(1))
    return sorted(set(names), key=str.casefold)


def normalized(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def license_text(distribution: importlib.metadata.Distribution) -> str:
    metadata = distribution.metadata
    values = [metadata.get("License", "")]
    values.extend(metadata.get_all("Classifier") or [])
    return "\n".join(values).strip().lower()


def main() -> int:
    lock_path = Path(sys.argv[1] if len(sys.argv) > 1 else "requirements.lock")
    if not lock_path.is_file():
        print(f"lock file not found: {lock_path}", file=sys.stderr)
        return 2

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(lock_path)],
        check=True,
    )

    denied: list[str] = []
    unknown: list[str] = []
    for name in requirement_names(lock_path):
        distribution = importlib.metadata.distribution(name)
        text = license_text(distribution)
        if any(marker in text for marker in DENIED_LICENSE_MARKERS):
            denied.append(f"{name}: {text or 'missing metadata'}")
        elif not text and normalized(name) not in {normalized(item) for item in ALLOWED_UNKNOWN}:
            unknown.append(name)

    if denied:
        print("Denied dependency licenses detected:", file=sys.stderr)
        print("\n".join(f"- {item}" for item in denied), file=sys.stderr)
        return 1
    if unknown:
        print("Dependencies with missing license metadata require explicit review:", file=sys.stderr)
        print("\n".join(f"- {item}" for item in unknown), file=sys.stderr)
        return 1

    print("Dependency license policy passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
