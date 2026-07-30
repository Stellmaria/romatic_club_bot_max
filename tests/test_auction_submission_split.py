from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bot.handlers import auctions
from bot.handlers.auction import guides, luxury_admin, submission, submission_support
from bot.services.auction_submission import AuctionSubmissionCatalogService
from bot.services.guides import GuideThanksService
from bot.services.luxury_admin import LuxuryAdminService


ROOT = Path(__file__).resolve().parents[1]


def _decorated_handlers(relative: str) -> tuple[str, ...]:
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    return tuple(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any("router." in ast.unparse(decorator) for decorator in node.decorator_list)
    )


def test_legacy_router_composes_features_in_original_registration_order() -> None:
    assert auctions.router.sub_routers == [
        submission.router,
        guides.router,
        luxury_admin.router,
    ]

    handlers = (
        *_decorated_handlers("bot/handlers/auction/submission.py"),
        *_decorated_handlers("bot/handlers/auction/guides.py"),
        *_decorated_handlers("bot/handlers/auction/luxury_admin.py"),
    )
    assert handlers == (
        "addlot_regex_entry",
        "auk_kind_locked",
        "auk_kind_selected",
        "cb_user_back_to_auction_kind",
        "cb_spins_from_decks",
        "cb_spins_from_presets",
        "addlot_currency_or_spins",
        "cb_subscription_menu_from_decks",
        "cb_subscription_back_decks",
        "cb_subscription_choose_type_from_decks",
        "cb_friends_plus_from_decks",
        "cb_progress_slots_from_decks",
        "cb_show_presets",
        "cb_presets_back",
        "user_choose_deck",
        "user_choose_concrete_card",
        "user_choose_all_deck",
        "user_choose_any_bronze",
        "user_choose_any_silver",
        "user_choose_any_gold",
        "user_choose_any_diamond",
        "user_choose_any_card",
        "user_choose_any_deck",
        "user_choose_custom",
        "user_process_custom_card",
        "addlot_start_price",
        "addlot_price_invalid",
        "addlot_comment",
        "user_addlot_confirm",
        "user_addlot_cancel",
        "addlot_confirm_invalid",
        "user_addlot_proof_final",
        "user_addlot_proof_required",
        "cancel_any",
        "cb_subscription_menu_from_presets",
        "cb_subscription_back_presets",
        "user_choose_subscription",
        "cb_subscription_period_selected",
        "cb_subscription_period_back_decks",
        "cb_subscription_period_back_presets",
        "user_choose_friends_plus",
        "user_choose_progress_slots",
        "auk_admin_thanks_open",
        "auk_admin_thanks_page",
        "auk_admin_thanks_noop",
        "auk_guides_david_open",
        "auk_david_page",
        "auk_david_show",
        "msg_david_answer_call",
        "auk_david_noop",
        "auk_david_thanks",
        "auk_guides_open",
        "auk_guides_menu",
        "cb_user_auk_types_from_decks",
        "auk_guide_open",
        "auk_guides_thanks",
        "auk_guides_back",
        "cmd_remove_luxury",
    )


def test_legacy_symbols_resolve_to_new_owners() -> None:
    assert auctions.addlot_start is submission.addlot_start
    assert auctions.compute_start_price_limits is submission_support.compute_start_price_limits
    assert auctions.guides_kb is guides.guides_kb
    assert auctions.cmd_remove_luxury is luxury_admin.cmd_remove_luxury


def test_extracted_handlers_keep_sql_behind_application_boundaries() -> None:
    for relative in (
        "bot/handlers/auction/guides.py",
        "bot/handlers/auction/luxury_admin.py",
        "bot/handlers/auction/submission_support.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "from db.db import" not in source
        assert "SELECT " not in source
        assert "INSERT " not in source
        assert "UPDATE " not in source

    repository = (ROOT / "bot/repositories/guides.py").read_text(encoding="utf-8")
    assert "async with connection.transaction()" in repository


class _GuideRepositoryFake:
    def __init__(self) -> None:
        self.increment_call = None

    async def increment(self, **kwargs):
        self.increment_call = kwargs
        return 7, 3


@pytest.mark.asyncio
async def test_guide_service_normalizes_identifiers_at_its_boundary() -> None:
    repository = _GuideRepositoryFake()
    service = GuideThanksService(repository)  # type: ignore[arg-type]

    assert await service.increment(user_id="42", author="@Dear_Davidik") == (7, 3)
    assert repository.increment_call == {"user_id": 42, "author": "@Dear_Davidik"}


class _CatalogRepositoryFake:
    async def deck_type_for_identity(self, **kwargs):
        self.identity = kwargs
        return "roulette"


@pytest.mark.asyncio
async def test_submission_catalog_service_delegates_typed_identity() -> None:
    repository = _CatalogRepositoryFake()
    service = AuctionSubmissionCatalogService(repository)  # type: ignore[arg-type]

    result = await service.deck_type_for_identity(card_name="Card", hero_name="Hero")

    assert result == "roulette"
    assert repository.identity == {"card_name": "Card", "hero_name": "Hero"}


class _LuxuryRepositoryFake:
    async def find_by_username(self, username):
        self.username = username
        return {"user_id": 1, "username": "owner", "is_luxury": True}

    async def find_by_id(self, user_id):
        self.user_id = user_id
        return {"user_id": user_id, "username": None, "is_luxury": True}


@pytest.mark.asyncio
async def test_luxury_service_parses_user_references() -> None:
    repository = _LuxuryRepositoryFake()
    service = LuxuryAdminService(repository)  # type: ignore[arg-type]

    assert (await service.find_user("@Owner"))["user_id"] == 1
    assert repository.username == "@Owner"
    assert (await service.find_user("25"))["user_id"] == 25
    assert repository.user_id == 25
    with pytest.raises(ValueError):
        await service.find_user("not-an-id")
