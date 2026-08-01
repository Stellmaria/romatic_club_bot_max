from __future__ import annotations

import ast
from pathlib import Path

from bot.domain.media_assets import (
    infer_media_type,
    normalize_media_type,
    normalize_target_key,
    normalize_target_kind,
)

ROOT = Path(__file__).resolve().parents[1]


def test_media_registry_migration_is_persistent_and_unique() -> None:
    source = (ROOT / "migrations/008_auction_media_registry.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS public.auction_media_assets" in source
    assert "UNIQUE (target_kind, target_key)" in source
    assert "target_kind IN ('deck', 'card', 'auction', 'rarity', 'service', 'spins', 'default')" in source
    assert "media_type IN ('photo', 'video', 'animation', 'document')" in source


def test_admin_media_router_is_registered() -> None:
    source = (ROOT / "bot/bootstrap/routers.py").read_text(encoding="utf-8")
    assert "from bot.handlers.admin.media_assets import router as media_assets_router" in source
    assert "dispatcher.include_router(media_assets_router)" in source


def test_media_command_supports_direct_file_id_and_deck_alias() -> None:
    source = (ROOT / "bot/handlers/admin/media_assets.py").read_text(encoding="utf-8")
    assert 'Command("set_media", "setmedia", "deck_media")' in source
    assert 'F.caption.regexp(r"^/(?:set_media|setmedia|deck_media)' in source
    assert "/set_media deck 22 FILE_ID video" in source
    assert "/deck_media 22 FILE_ID video" in source
    assert "configure_media_asset(" in source


def test_deck_media_is_loaded_from_database_in_both_auction_flows() -> None:
    exchange = (ROOT / "bot/handlers/auction/exchange/common.py").read_text(encoding="utf-8")
    submission = (ROOT / "bot/handlers/auction/exchange/submission.py").read_text(encoding="utf-8")

    assert 'await resolve_media_file_id(\n            "deck"' in exchange
    assert "cover_id = await exchange_deck_cover_id(deck_id_i)" in submission


def test_media_normalization_and_video_file_id_inference() -> None:
    assert normalize_target_kind("колода") == "deck"
    assert normalize_target_kind("лот") == "auction"
    assert normalize_target_key("deck", "22") == "22"
    assert normalize_target_key("rarity", "золото") == "gold"
    assert normalize_media_type("картинка") == "photo"
    assert normalize_media_type("видео") == "video"
    assert infer_media_type("BAACAgIAAxkBAAERAAE1aldpUk9K4DEcKgWm3hXyf7IwoCcAAsKhAAL3JrlKJlfJWuoy7Ow9BA") == "video"


def test_media_repository_has_no_telegram_dependency() -> None:
    source = (ROOT / "db/repositories/media_assets.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    from_imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(name.startswith("aiogram") for name in imports | from_imports)
    assert "bot.handlers" not in source