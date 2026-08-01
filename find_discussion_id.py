"""Deprecated userbot launcher kept for operational compatibility.

Use ``python -m userbot.entrypoint`` for new deployments.
"""

from userbot.entrypoint import run


if __name__ == "__main__":
    raise SystemExit(run())
