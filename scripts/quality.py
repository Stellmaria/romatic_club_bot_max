from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOTS = ("bot/", "database/", "db/", "userbot/")
RUFF_RULES = "E,F,I,B,ASYNC,SIM,UP,C4,PIE,RUF"


def run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)


def git_output(*args: str) -> str:
    return subprocess.check_output(("git", *args), cwd=ROOT, text=True).strip()


def resolve_base(explicit_base: str | None) -> str:
    if explicit_base:
        return explicit_base
    event_name = os.getenv("GITHUB_EVENT_NAME")
    base_ref = os.getenv("GITHUB_BASE_REF")
    if event_name == "pull_request" and base_ref:
        candidate = f"origin/{base_ref}"
        subprocess.run(
            ("git", "fetch", "--no-tags", "--depth=1", "origin", base_ref),
            cwd=ROOT,
            check=True,
        )
        return candidate
    return "HEAD^"


def changed_python_files(base: str) -> list[str]:
    merge_base = git_output("merge-base", base, "HEAD")
    output = git_output(
        "diff",
        "--name-only",
        "--diff-filter=ACMR",
        f"{merge_base}...HEAD",
        "--",
        "*.py",
    )
    return [path for path in output.splitlines() if path and (ROOT / path).is_file()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run quality gates on changed Python files.")
    parser.add_argument("--base", help="Git ref used as the comparison base.")
    args = parser.parse_args()

    base = resolve_base(args.base)
    files = changed_python_files(base)
    if not files:
        print("No changed Python files; quality ratchet has nothing to check.")
        return 0

    run(sys.executable, "-m", "black", "--check", *files)
    run(sys.executable, "-m", "ruff", "check", "--select", RUFF_RULES, *files)

    runtime_files = [path for path in files if path.startswith(RUNTIME_ROOTS)]
    if runtime_files:
        run(
            sys.executable,
            "-m",
            "mypy",
            "--follow-imports=silent",
            "--no-error-summary",
            *runtime_files,
        )
    else:
        print("No changed runtime modules; mypy ratchet skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
