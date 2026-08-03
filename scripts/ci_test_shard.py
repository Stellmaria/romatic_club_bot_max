from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = ROOT / "tests"


def discover_test_files() -> tuple[Path, ...]:
    """Return every non-integration pytest file handled by matrix shards."""

    return tuple(
        sorted(
            path.relative_to(ROOT)
            for path in TESTS_ROOT.rglob("test_*.py")
            if path.is_file() and "integration" not in path.relative_to(TESTS_ROOT).parts
        )
    )


def partition_test_files(
    files: Sequence[Path],
    *,
    total: int,
) -> tuple[tuple[Path, ...], ...]:
    """Balance files deterministically using source size as a stable runtime proxy."""

    if total < 1:
        raise ValueError("total must be at least 1")

    buckets: list[list[Path]] = [[] for _ in range(total)]
    loads = [0 for _ in range(total)]
    weighted = sorted(
        files,
        key=lambda path: (-(ROOT / path).stat().st_size, path.as_posix()),
    )

    for path in weighted:
        shard = min(range(total), key=lambda index: (loads[index], index))
        buckets[shard].append(path)
        loads[shard] += max((ROOT / path).stat().st_size, 1)

    return tuple(tuple(sorted(bucket)) for bucket in buckets)


def verify_partitions(*, total: int) -> int:
    files = discover_test_files()
    partitions = partition_test_files(files, total=total)
    assigned = [path for partition in partitions for path in partition]
    counts = Counter(assigned)

    duplicates = sorted(path for path, count in counts.items() if count != 1)
    missing = sorted(set(files) - set(assigned))
    empty = [index for index, partition in enumerate(partitions) if not partition]

    if duplicates or missing or empty:
        print(f"duplicate assignments: {[path.as_posix() for path in duplicates]}")
        print(f"missing assignments: {[path.as_posix() for path in missing]}")
        print(f"empty shards: {empty}")
        return 1

    for index, partition in enumerate(partitions):
        byte_count = sum((ROOT / path).stat().st_size for path in partition)
        print(f"shard {index}: {len(partition)} files, {byte_count} source bytes")
    print(f"verified {len(files)} test files across {total} shards")
    return 0


def run_shard(
    *,
    index: int,
    total: int,
    failfast: bool,
    durations: int,
    list_only: bool,
) -> int:
    if not 0 <= index < total:
        raise ValueError(f"index must be between 0 and {total - 1}")

    selected = partition_test_files(discover_test_files(), total=total)[index]
    if not selected:
        print(f"shard {index} has no tests", file=sys.stderr)
        return 2

    print(f"shard {index}/{total - 1}: {len(selected)} test files", flush=True)
    for path in selected:
        print(path.as_posix(), flush=True)

    if list_only:
        return 0

    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-m",
        "not integration",
        "--cov=bot",
        "--cov=db",
        "--cov=userbot",
        "--cov-branch",
        "--cov-report=",
        f"--junitxml=shard-{index}.xml",
        "--durations",
        str(durations),
    ]
    if failfast:
        command.extend(("--maxfail", "1"))
    command.extend(path.as_posix() for path in selected)

    return subprocess.run(command, cwd=ROOT, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a deterministic pytest shard.")
    parser.add_argument("--index", type=int)
    parser.add_argument("--total", type=int, required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--list", action="store_true", dest="list_only")
    parser.add_argument("--failfast", action="store_true")
    parser.add_argument("--durations", type=int, default=25)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.verify:
        return verify_partitions(total=args.total)
    if args.index is None:
        raise SystemExit("--index is required unless --verify is used")
    return run_shard(
        index=args.index,
        total=args.total,
        failfast=args.failfast,
        durations=args.durations,
        list_only=args.list_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
