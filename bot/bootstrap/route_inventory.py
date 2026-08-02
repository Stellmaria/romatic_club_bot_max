"""Print the validated route inventory for CI and operators."""

from __future__ import annotations

from bot.bootstrap.routers import route_inventory_json


def main() -> None:
    print(route_inventory_json())


if __name__ == "__main__":
    main()
