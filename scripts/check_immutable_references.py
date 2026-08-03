from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTION = re.compile(r"^\s*-?\s*uses:\s*([^#\s]+)@([^#\s]+)", re.MULTILINE)
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")


def main() -> int:
    failures: list[str] = []
    for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
        source = workflow.read_text(encoding="utf-8")
        for action, ref in ACTION.findall(source):
            if action.startswith("./"):
                continue
            if not FULL_SHA.fullmatch(ref):
                failures.append(
                    f"{workflow.relative_to(ROOT)}: {action}@{ref} is not pinned to a full commit SHA"
                )

    for name in ("Dockerfile", "Dockerfile.server-supervisor-proxy"):
        first = (ROOT / name).read_text(encoding="utf-8").splitlines()[0]
        if not first.startswith("FROM ") or not DIGEST.search(first):
            failures.append(f"{name}: base image is not pinned by sha256 digest")

    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    postgres = (
        "postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
    )
    if postgres not in compose:
        failures.append("compose.yaml: PostgreSQL image is not pinned by the approved digest")

    inventory = (ROOT / "security/base-image-digests.txt").read_text(encoding="utf-8")
    for expected in (
        "python:3.13.13-slim@sha256:aa938a849bcb82dce8f49480f056ab82bf5c1c3ebc294f0430f37b6820e7f286",
        "python:3.13-alpine@sha256:399babc8b49529dabfd9c922f2b5eea81d611e4512e3ed250d75bd2e7683f4b0",
        postgres,
    ):
        if expected not in inventory:
            failures.append(f"security/base-image-digests.txt: missing {expected}")

    if failures:
        raise SystemExit("Immutable reference policy failed:\n- " + "\n- ".join(failures))
    print("Immutable GitHub Actions and container references verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
