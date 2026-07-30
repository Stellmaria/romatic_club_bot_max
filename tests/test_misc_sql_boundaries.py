from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path

from bot.services.appeals import AppealService
from bot.services.auction_comments import AuctionCommentService
from bot.services.custom_emojis import CustomEmojiService
from bot.services.warnings import WarningService

ROOT = Path(__file__).resolve().parents[1]

HANDLER_BOUNDARIES = (
    "bot/handlers/auction/warnings.py",
    "bot/handlers/emoji_setup.py",
    "bot/handlers/helper/appeals_service.py",
    "bot/handlers/auction_comments.py",
)
SERVICE_BOUNDARIES = (
    "bot/services/warnings.py",
    "bot/services/custom_emojis.py",
    "bot/services/appeals.py",
    "bot/services/auction_comments.py",
)

SQL = re.compile(
    r"\b(?:SELECT\s+.+\s+FROM|INSERT\s+INTO|UPDATE\s+public\.|"
    r"DELETE\s+FROM|CREATE\s+TABLE)\b",
    re.IGNORECASE | re.DOTALL,
)


def _tree(relative: str) -> ast.Module:
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _imports(relative: str) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree(relative)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_legacy_handlers_and_facade_contain_no_sql_or_asyncpg() -> None:
    for relative in HANDLER_BOUNDARIES:
        tree = _tree(relative)
        imports = _imports(relative)
        assert "asyncpg" not in imports, relative
        assert "db.core" not in imports, relative
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not SQL.search(node.value), (relative, node.lineno)


def test_services_use_pool_boundary_and_do_not_import_handlers() -> None:
    for relative in SERVICE_BOUNDARIES:
        imports = _imports(relative)
        assert "db.pool" in imports, relative
        assert not any(name.startswith("bot.handlers") for name in imports), relative

    bridge_imports = _imports("bot/bridges/legacy_http.py")
    assert "bot.presentation.warnings" in bridge_imports
    assert not any(name.startswith("bot.handlers") for name in bridge_imports)


def test_admin_warning_text_compatibility_export_uses_presentation_module() -> None:
    imports = _imports("bot/handlers/admin/helper/admin_constants.py")
    assert "bot.presentation.warnings" in imports
    source = (ROOT / "bot/handlers/admin/helper/admin_constants.py").read_text(encoding="utf-8")
    assert "WARN_TEXTS = [" not in source


class FakeWarningRepository:
    def __init__(self) -> None:
        self.username: str | None = None
        self.prune_values: dict[str, object] | None = None

    async def find_user_id_by_username(self, username: str) -> int | None:
        self.username = username
        return 71

    async def prune_old(self, **values):
        self.prune_values = values
        return [{"user_id": 71, "removed": 2}]


def test_warning_service_normalizes_targets_and_delegates_retention() -> None:
    async def scenario() -> None:
        repository = FakeWarningRepository()
        service = WarningService(repository)  # type: ignore[arg-type]

        assert await service.resolve_user_id(" @Alice ") == 71
        assert repository.username == "Alice"
        assert await service.resolve_user_id(" 42 ") == 42
        assert await service.resolve_user_id("invalid") is None
        result = await service.prune_old(
            maximum_warning_count=4,
            age_days=30,
            target_user_id=71,
            dry_run=True,
        )
        assert result == [{"user_id": 71, "removed": 2}]
        assert repository.prune_values == {
            "maximum_warning_count": 4,
            "age_days": 30,
            "target_user_id": 71,
            "dry_run": True,
        }

    asyncio.run(scenario())


class FakeEmojiRepository:
    def __init__(self) -> None:
        self.saved: tuple[str, str] | None = None
        self.deleted: str | None = None

    async def upsert(self, *, name: str, emoji_id: str) -> None:
        self.saved = (name, emoji_id)

    async def delete(self, name: str) -> str | None:
        self.deleted = name
        return name


def test_custom_emoji_service_normalizes_names() -> None:
    async def scenario() -> None:
        repository = FakeEmojiRepository()
        service = CustomEmojiService(repository)  # type: ignore[arg-type]
        await service.save("  Fire ", " emoji-id ")
        assert repository.saved == ("fire", "emoji-id")
        assert await service.delete(" FIRE ") == "fire"
        assert repository.deleted == "fire"

    asyncio.run(scenario())


class FakeAppealRepository:
    def __init__(self) -> None:
        self.created: dict[str, object] | None = None
        self.statuses: list[dict[str, object]] = []

    async def create(self, **values) -> int:
        self.created = values
        return 19

    async def set_status(self, **values) -> bool:
        self.statuses.append(values)
        return True


def test_appeal_service_preserves_comment_update_semantics() -> None:
    async def scenario() -> None:
        repository = FakeAppealRepository()
        service = AppealService(repository)  # type: ignore[arg-type]
        appeal_id = await service.create_appeal(
            user_id=5,
            username=" Alice ",
            topic=" Question ",
            description=" Details ",
            participants=" Bob ",
            media_message_ids=["7"],  # type: ignore[list-item]
            origin_chat_id=-100,
        )
        assert appeal_id == 19
        assert repository.created == {
            "user_id": 5,
            "username": "Alice",
            "topic": "Question",
            "description": "Details",
            "participants": "Bob",
            "media_message_ids": [7],
            "origin_chat_id": -100,
        }

        await service.set_status(
            appeal_id=19,
            status=" RESOLVED ",
            moderator_id=2,
            moderator_username=" Admin ",
        )
        await service.set_status(
            appeal_id=19,
            status="resolved",
            moderator_id=2,
            moderator_username="Admin",
            comment="   ",
        )
        assert repository.statuses[0]["update_comment"] is False
        assert repository.statuses[1]["update_comment"] is True
        assert repository.statuses[1]["comment"] is None

    asyncio.run(scenario())


class FakeAuctionCommentRepository:
    async def is_active_lot_owner(self, *, user_id: int, username: str) -> bool:
        return user_id == 3 and username == "alice"


def test_auction_comment_service_normalizes_owner_lookup() -> None:
    async def scenario() -> None:
        service = AuctionCommentService(FakeAuctionCommentRepository())  # type: ignore[arg-type]
        assert await service.is_active_lot_owner(user_id=3, username=" Alice ")

    asyncio.run(scenario())
