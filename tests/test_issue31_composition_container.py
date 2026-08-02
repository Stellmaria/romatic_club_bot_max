from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bot.application_models import (
    AuctionRecord,
    RecordMappingError,
    map_auction,
)
from bot.application_ports import Clock, FileStoragePort
from bot.bootstrap.container import ApplicationContainer
from bot.domain.auctions import AuctionKind, Currency

ROOT = Path(__file__).resolve().parents[1]


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 3, tzinfo=timezone.utc)


class FakeFiles:
    async def put(self, key: str, content: bytes) -> str:
        return key

    async def get(self, key: str) -> bytes | None:
        return None

    async def delete(self, key: str) -> None:
        return None


class FakePool:
    pass


def _imports(relative: str) -> set[str]:
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_container_builds_one_repository_instance_per_aggregate(tmp_path: Path) -> None:
    clock = FakeClock()
    files = FakeFiles()
    container = ApplicationContainer.build(
        pool=FakePool(),
        storage_root=tmp_path,
        clock=clock,
        file_storage=files,
    )

    assert container.auction_creation._repository is container.auction_repository
    assert container.auction_moderation._repository is container.auction_repository
    assert container.auction_owner._repository is container.auction_repository
    assert container.auction_lifecycle._repository is container.auction_repository
    assert container.auction_publication._repository is container.auction_repository
    assert container.exchange._repository is container.exchange_repository
    assert container.clock is clock
    assert container.file_storage is files
    assert isinstance(clock, Clock)
    assert isinstance(files, FileStoragePort)


def test_row_mapper_rejects_missing_required_field() -> None:
    with pytest.raises(RecordMappingError, match="auction_kind"):
        map_auction(
            {
                "auction_id": 10,
                "status": "pending",
                "currency": "diamonds",
                "start_price": 10,
            }
        )


def test_row_mapper_returns_typed_entity() -> None:
    entity = map_auction(
        {
            "auction_id": 10,
            "status": "pending",
            "auction_kind": "standard",
            "currency": "diamonds",
            "start_price": 10,
        }
    )

    assert entity == AuctionRecord(
        auction_id=10,
        status="pending",
        auction_kind=AuctionKind.STANDARD,
        currency=Currency.DIAMONDS,
        start_price=10,
    )


def test_application_models_and_ports_are_framework_neutral() -> None:
    forbidden = {"aiogram", "asyncpg", "telethon", "db"}
    for relative in ("bot/application_models.py", "bot/application_ports.py"):
        roots = {name.split(".", 1)[0] for name in _imports(relative)}
        assert not roots & forbidden, relative


def test_composition_root_injects_container_into_dispatcher() -> None:
    source = (ROOT / "bot/application.py").read_text(encoding="utf-8")
    assert "ApplicationContainer.build(" in source
    assert "pool=database_runtime.require_pool()" in source
    assert "application_container=container" in source


def test_concrete_adapter_construction_is_confined_to_container() -> None:
    application = (ROOT / "bot/application.py").read_text(encoding="utf-8")
    container = (ROOT / "bot/bootstrap/container.py").read_text(encoding="utf-8")

    assert "AuctionWorkflowRepository(" not in application
    assert "ExchangeRepository(" not in application
    assert "AuctionWorkflowRepository(pool)" in container
    assert "ExchangeRepository(pool)" in container
