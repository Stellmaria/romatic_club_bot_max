"""Preorder flow for composing one lot from cards of a future empty deck."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Filter, StateFilter
from aiogram.fsm.context import FSMContext

from bot.domain.auctions import AuctionKind
from bot.domain.preorders import (
    MAX_PREORDER_QUANTITY,
    PREORDER_MODE_ITEMS,
    PREORDER_MODE_WHOLE_DECK,
    PREORDER_RARITIES,
    PREORDER_RARITY_LABELS,
    build_preorder_title,
    change_preorder_quantity,
    format_preorder_composition,
    normalize_preorder_items,
    normalize_preorder_mode,
    preorder_total,
    validate_preorder_selection,
)
from bot.features.auction_submission import ANY_DECK_PHOTO_ID
from bot.handlers.auction.submission import user_addlot_confirm
from bot.handlers.auction.submission_support import auction_currency_kb
from bot.services.auction_submission import AuctionSubmissionCatalogService
from bot.telegram.boundary import (
    CallbackField,
    CallbackSchema,
    TelegramBoundaryError,
)
from bot.telegram.states import UserAddLotFSM

log = logging.getLogger(__name__)
router = Router(name=__name__)

_STALE_PREORDER_CALLBACKS = {
    "user_any_bronze",
    "user_any_silver",
    "user_any_gold",
    "user_any_diamond",
    "user_any_card",
    "user_any_deck",
}
_DECK_CALLBACK = CallbackSchema(
    namespace="preorder",
    action="deck",
    fields=(CallbackField("deck_id", kind="int", minimum=1),),
)
_ITEM_CALLBACK = CallbackSchema(
    namespace="preorder",
    action="item",
    fields=(
        CallbackField(
            "rarity",
            kind="enum",
            values=frozenset(PREORDER_RARITIES),
        ),
        CallbackField(
            "direction",
            kind="enum",
            values=frozenset({"inc", "dec"}),
        ),
    ),
)


class PreorderDraftFilter(Filter):
    async def __call__(
        self,
        _event: types.TelegramObject,
        state: FSMContext,
    ) -> bool:
        data = await state.get_data()
        return bool(data.get("preorder_deck_id"))


def preorder_menu_keyboard() -> types.InlineKeyboardMarkup:
    """Keep service lots, but replace legacy any-card presets with preorder."""

    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="🗓 Предзаказ будущей колоды",
                    callback_data="preorder:open",
                )
            ],
            [types.InlineKeyboardButton(text="Друзья+", callback_data="user_friends_plus")],
            [
                types.InlineKeyboardButton(
                    text="Слоты прогресса",
                    callback_data="user_progress_slots",
                )
            ],
            [types.InlineKeyboardButton(text="Пропуски", callback_data="user_subscription")],
            [types.InlineKeyboardButton(text="Кручения", callback_data="user_spins")],
            [
                types.InlineKeyboardButton(
                    text="Колода-конструктор",
                    callback_data="user_deck_constructor",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Алмазы за чай",
                    callback_data="user_res_diamonds_for_tea",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Чай за алмазы",
                    callback_data="user_res_tea_for_diamonds",
                )
            ],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="user_deck_back")],
        ]
    )


def future_decks_keyboard(decks: list[dict[str, Any]]) -> types.InlineKeyboardMarkup:
    rows: list[list[types.InlineKeyboardButton]] = []
    for deck in decks:
        deck_id = int(deck["deck_id"])
        deck_name = str(deck.get("deck_name") or "Будущая колода")
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=f"{deck_id}. {deck_name}",
                    callback_data=_DECK_CALLBACK.pack(deck_id=deck_id),
                )
            ]
        )
    rows.append(
        [
            types.InlineKeyboardButton(
                text="⬅️ К другим лотам",
                callback_data="preorder:back",
            )
        ]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def preorder_cart_keyboard(
    items: dict[str, int],
    *,
    mode: object = PREORDER_MODE_ITEMS,
) -> types.InlineKeyboardMarkup:
    normalized_mode = normalize_preorder_mode(mode)
    rows: list[list[types.InlineKeyboardButton]] = []

    if normalized_mode == PREORDER_MODE_WHOLE_DECK:
        rows.extend(
            [
                [
                    types.InlineKeyboardButton(
                        text="🃏 Целая колода выбрана",
                        callback_data="preorder:noop",
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text="🔄 Выбрать карты по редкостям",
                        callback_data="preorder:items",
                    )
                ],
            ]
        )
    else:
        normalized = normalize_preorder_items(items)
        for rarity in PREORDER_RARITIES:
            quantity = normalized.get(rarity, 0)
            rows.append(
                [
                    types.InlineKeyboardButton(
                        text="➖",
                        callback_data=_ITEM_CALLBACK.pack(
                            rarity=rarity,
                            direction="dec",
                        ),
                    ),
                    types.InlineKeyboardButton(
                        text=f"{PREORDER_RARITY_LABELS[rarity]}: {quantity}",
                        callback_data="preorder:noop",
                    ),
                    types.InlineKeyboardButton(
                        text="➕",
                        callback_data=_ITEM_CALLBACK.pack(
                            rarity=rarity,
                            direction="inc",
                        ),
                    ),
                ]
            )
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="🃏 Заказать целую колоду",
                    callback_data="preorder:whole",
                )
            ]
        )

    rows.extend(
        [
            [
                types.InlineKeyboardButton(
                    text="✅ Продолжить",
                    callback_data="preorder:finish",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="⬅️ Выбрать другую колоду",
                    callback_data="preorder:decks",
                )
            ],
        ]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def whole_deck_confirmation_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="🗑 Очистить и выбрать целую колоду",
                    callback_data="preorder:whole:confirm",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Отмена",
                    callback_data="preorder:whole:cancel",
                )
            ],
        ]
    )


def preorder_cart_text(
    *,
    deck_id: int,
    deck_name: str,
    items: dict[str, int],
    mode: object = PREORDER_MODE_ITEMS,
) -> str:
    normalized_mode = normalize_preorder_mode(mode)
    if normalized_mode == PREORDER_MODE_WHOLE_DECK:
        return (
            f"<b>Предзаказ колоды №{deck_id}</b>\n"
            f"{deck_name}\n\n"
            "<b>Режим:</b> целая колода.\n"
            "В заявку войдёт вся будущая колода одним лотом."
        )

    total = preorder_total(items)
    return (
        f"<b>Предзаказ колоды №{deck_id}</b>\n"
        f"{deck_name}\n\n"
        "<b>Режим:</b> карты по редкостям.\n"
        "Соберите состав будущего лота. Можно комбинировать редкости, "
        "например 2 бронзы и 1 золото.\n\n"
        f"Всего карт: <b>{total}</b>\n"
        f"Лимит каждой редкости: {MAX_PREORDER_QUANTITY}."
    )


def _callback_message(call: types.CallbackQuery) -> types.Message | None:
    message = call.message
    return message if isinstance(message, types.Message) else None


async def _catalog() -> AuctionSubmissionCatalogService:
    return await AuctionSubmissionCatalogService.create()


async def _ask_for_preorder_currency(
    message: types.Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    kind = str(data.get("auction_kind") or "standard").strip().lower()
    if kind == AuctionKind.FREE.value:
        prompt = "Выберите, в какой валюте принимать предложения:"
    elif kind == AuctionKind.REVERSE.value:
        prompt = "Выберите валюту обратного аукциона:"
    else:
        prompt = "Выберите валюту:"

    await state.set_state(UserAddLotFSM.waiting_for_currency)
    await message.answer(prompt, reply_markup=auction_currency_kb(kind))


async def _edit_or_answer(
    message: types.Message,
    text: str,
    *,
    reply_markup: types.InlineKeyboardMarkup,
    parse_mode: str | None = None,
) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest:
        await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)


async def _render_preorder_cart(
    message: types.Message,
    *,
    deck_id: int,
    deck_name: str,
    items: dict[str, int],
    mode: object,
) -> None:
    await _edit_or_answer(
        message,
        preorder_cart_text(
            deck_id=deck_id,
            deck_name=deck_name,
            items=items,
            mode=mode,
        ),
        reply_markup=preorder_cart_keyboard(items, mode=mode),
        parse_mode="HTML",
    )


async def _show_future_decks(message: types.Message, state: FSMContext) -> None:
    await state.update_data(
        preorder_deck_id=None,
        preorder_deck_name=None,
        preorder_mode=PREORDER_MODE_ITEMS,
        preorder_items={},
        deck_id=None,
        card_id=None,
        service=None,
    )
    try:
        decks = await (await _catalog()).future_empty_decks()
    except Exception:  # noqa: BLE001 - convert infrastructure failures to a stable bot response.
        log.exception("failed to load future empty decks for preorder")
        await message.answer("Не удалось загрузить будущие колоды. Попробуйте ещё раз.")
        return

    if not decks:
        await _edit_or_answer(
            message,
            "Сейчас нет будущих колод без карт. Предзаказ недоступен.",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="⬅️ К другим лотам",
                            callback_data="preorder:back",
                        )
                    ]
                ]
            ),
        )
        return

    await _edit_or_answer(
        message,
        "Выберите одну будущую колоду. Колоды, в которых уже появились карты, скрыты:",
        reply_markup=future_decks_keyboard(decks),
    )


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_deck),
    F.data == "user_own_custom",
)
async def show_clean_other_lots_menu(call: types.CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(call)
    if message is None:
        await call.answer("Сообщение недоступно. Откройте меню заново.", show_alert=True)
        return

    await state.set_state(UserAddLotFSM.waiting_for_own_variant)
    await state.update_data(
        preorder_deck_id=None,
        preorder_mode=PREORDER_MODE_ITEMS,
        preorder_items={},
    )
    await message.answer(
        "Выберите вариант лота:",
        reply_markup=preorder_menu_keyboard(),
    )
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant),
    F.data == "preorder:back",
)
async def preorder_back(call: types.CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(call)
    if message is None:
        await call.answer("Сообщение недоступно. Откройте меню заново.", show_alert=True)
        return

    await state.update_data(
        preorder_deck_id=None,
        preorder_mode=PREORDER_MODE_ITEMS,
        preorder_items={},
    )
    await _edit_or_answer(
        message,
        "Выберите вариант лота:",
        reply_markup=preorder_menu_keyboard(),
    )
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant),
    F.data == "preorder:open",
)
async def preorder_open(call: types.CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(call)
    if message is None:
        await call.answer("Сообщение недоступно. Откройте меню заново.", show_alert=True)
        return

    await _show_future_decks(message, state)
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant),
    F.data.in_(_STALE_PREORDER_CALLBACKS),
)
async def redirect_legacy_preorder_buttons(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    message = _callback_message(call)
    if message is None:
        await call.answer("Сообщение недоступно. Откройте меню заново.", show_alert=True)
        return

    await call.answer("Теперь предзаказ оформляется на будущую колоду.")
    await _show_future_decks(message, state)


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant),
    F.data == "preorder:decks",
)
async def preorder_choose_other_deck(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    message = _callback_message(call)
    if message is None:
        await call.answer("Сообщение недоступно. Откройте меню заново.", show_alert=True)
        return

    await _show_future_decks(message, state)
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant),
    F.data.startswith(_DECK_CALLBACK.prefix),
)
async def preorder_choose_deck(call: types.CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(call)
    if message is None:
        await call.answer("Сообщение недоступно. Откройте меню заново.", show_alert=True)
        return

    try:
        payload = _DECK_CALLBACK.unpack(call.data)
    except TelegramBoundaryError as exc:
        await call.answer(exc.user_message, show_alert=True)
        return

    raw_deck_id = payload["deck_id"]
    if not isinstance(raw_deck_id, int):
        await call.answer("Некорректная колода.", show_alert=True)
        return
    deck_id = raw_deck_id

    try:
        deck = await (await _catalog()).future_empty_deck(deck_id)
    except Exception:  # noqa: BLE001 - convert infrastructure failures to a stable bot response.
        log.exception("failed to revalidate preorder deck %s", deck_id)
        await call.answer("Не удалось проверить колоду.", show_alert=True)
        return

    if not deck:
        await call.answer(
            "В этой колоде уже появились карты, поэтому предзаказ закрыт.",
            show_alert=True,
        )
        await _show_future_decks(message, state)
        return

    deck_name = str(deck.get("deck_name") or "Будущая колода")
    await state.update_data(
        preorder_deck_id=deck_id,
        preorder_deck_name=deck_name,
        preorder_mode=PREORDER_MODE_ITEMS,
        preorder_items={},
    )
    await _render_preorder_cart(
        message,
        deck_id=deck_id,
        deck_name=deck_name,
        items={},
        mode=PREORDER_MODE_ITEMS,
    )
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant),
    F.data.startswith(_ITEM_CALLBACK.prefix),
)
async def preorder_change_item(call: types.CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(call)
    if message is None:
        await call.answer("Сообщение недоступно. Откройте меню заново.", show_alert=True)
        return

    try:
        payload = _ITEM_CALLBACK.unpack(call.data)
    except TelegramBoundaryError as exc:
        await call.answer(exc.user_message, show_alert=True)
        return

    rarity = str(payload["rarity"])
    direction = str(payload["direction"])
    data = await state.get_data()
    deck_id = int(data.get("preorder_deck_id") or 0)
    if deck_id <= 0:
        await call.answer("Сначала выберите будущую колоду.", show_alert=True)
        await _show_future_decks(message, state)
        return
    if normalize_preorder_mode(data.get("preorder_mode")) == PREORDER_MODE_WHOLE_DECK:
        await call.answer(
            "Сначала переключитесь на выбор карт по редкостям.",
            show_alert=True,
        )
        return

    delta = 1 if direction == "inc" else -1
    items = change_preorder_quantity(data.get("preorder_items"), rarity, delta)
    await state.update_data(
        preorder_mode=PREORDER_MODE_ITEMS,
        preorder_items=items,
    )
    await _render_preorder_cart(
        message,
        deck_id=deck_id,
        deck_name=str(data.get("preorder_deck_name") or "Будущая колода"),
        items=items,
        mode=PREORDER_MODE_ITEMS,
    )
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant),
    F.data == "preorder:whole",
)
async def preorder_select_whole_deck(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    message = _callback_message(call)
    if message is None:
        await call.answer("Сообщение недоступно. Откройте меню заново.", show_alert=True)
        return

    data = await state.get_data()
    deck_id = int(data.get("preorder_deck_id") or 0)
    if deck_id <= 0:
        await call.answer("Сначала выберите будущую колоду.", show_alert=True)
        await _show_future_decks(message, state)
        return

    items = normalize_preorder_items(data.get("preorder_items"))
    if items:
        composition = format_preorder_composition(items)
        await _edit_or_answer(
            message,
            "Вы уже выбрали карты по редкостям:\n"
            f"<b>{composition}</b>\n\n"
            "При переходе на целую колоду этот состав будет очищен. Продолжить?",
            reply_markup=whole_deck_confirmation_keyboard(),
            parse_mode="HTML",
        )
        await call.answer()
        return

    deck_name = str(data.get("preorder_deck_name") or "Будущая колода")
    await state.update_data(
        preorder_mode=PREORDER_MODE_WHOLE_DECK,
        preorder_items={},
    )
    await _render_preorder_cart(
        message,
        deck_id=deck_id,
        deck_name=deck_name,
        items={},
        mode=PREORDER_MODE_WHOLE_DECK,
    )
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant),
    F.data == "preorder:whole:confirm",
)
async def preorder_confirm_whole_deck(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    message = _callback_message(call)
    if message is None:
        await call.answer("Сообщение недоступно. Откройте меню заново.", show_alert=True)
        return

    data = await state.get_data()
    deck_id = int(data.get("preorder_deck_id") or 0)
    if deck_id <= 0:
        await call.answer("Сначала выберите будущую колоду.", show_alert=True)
        await _show_future_decks(message, state)
        return

    deck_name = str(data.get("preorder_deck_name") or "Будущая колода")
    await state.update_data(
        preorder_mode=PREORDER_MODE_WHOLE_DECK,
        preorder_items={},
    )
    await _render_preorder_cart(
        message,
        deck_id=deck_id,
        deck_name=deck_name,
        items={},
        mode=PREORDER_MODE_WHOLE_DECK,
    )
    await call.answer("Состав очищен. Выбрана целая колода.")


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant),
    F.data == "preorder:whole:cancel",
)
async def preorder_cancel_whole_deck(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    message = _callback_message(call)
    if message is None:
        await call.answer("Сообщение недоступно. Откройте меню заново.", show_alert=True)
        return

    data = await state.get_data()
    deck_id = int(data.get("preorder_deck_id") or 0)
    if deck_id <= 0:
        await call.answer("Сначала выберите будущую колоду.", show_alert=True)
        await _show_future_decks(message, state)
        return

    items = normalize_preorder_items(data.get("preorder_items"))
    await state.update_data(preorder_mode=PREORDER_MODE_ITEMS)
    await _render_preorder_cart(
        message,
        deck_id=deck_id,
        deck_name=str(data.get("preorder_deck_name") or "Будущая колода"),
        items=items,
        mode=PREORDER_MODE_ITEMS,
    )
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant),
    F.data == "preorder:items",
)
async def preorder_select_items_mode(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    message = _callback_message(call)
    if message is None:
        await call.answer("Сообщение недоступно. Откройте меню заново.", show_alert=True)
        return

    data = await state.get_data()
    deck_id = int(data.get("preorder_deck_id") or 0)
    if deck_id <= 0:
        await call.answer("Сначала выберите будущую колоду.", show_alert=True)
        await _show_future_decks(message, state)
        return

    await state.update_data(
        preorder_mode=PREORDER_MODE_ITEMS,
        preorder_items={},
    )
    await _render_preorder_cart(
        message,
        deck_id=deck_id,
        deck_name=str(data.get("preorder_deck_name") or "Будущая колода"),
        items={},
        mode=PREORDER_MODE_ITEMS,
    )
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant),
    F.data == "preorder:noop",
)
async def preorder_noop(call: types.CallbackQuery) -> None:
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant),
    F.data == "preorder:finish",
)
async def preorder_finish(call: types.CallbackQuery, state: FSMContext) -> None:
    message = _callback_message(call)
    if message is None:
        await call.answer("Сообщение недоступно. Откройте меню заново.", show_alert=True)
        return

    data = await state.get_data()
    deck_id = int(data.get("preorder_deck_id") or 0)
    mode = normalize_preorder_mode(data.get("preorder_mode"))
    if deck_id <= 0:
        await call.answer("Сначала выберите будущую колоду.", show_alert=True)
        return

    try:
        mode, items = validate_preorder_selection(
            mode=mode,
            items=data.get("preorder_items"),
        )
    except ValueError:
        if mode == PREORDER_MODE_WHOLE_DECK:
            await call.answer(
                "Целую колоду нельзя смешивать с отдельными картами.",
                show_alert=True,
            )
        else:
            await call.answer("Добавьте хотя бы одну карту.", show_alert=True)
        return

    try:
        deck = await (await _catalog()).future_empty_deck(deck_id)
    except Exception:  # noqa: BLE001 - convert infrastructure failures to a stable bot response.
        log.exception("failed to revalidate preorder deck %s before pricing", deck_id)
        await call.answer("Не удалось проверить колоду.", show_alert=True)
        return

    if not deck:
        await call.answer(
            "В колоде уже появились карты. Предзаказ на неё закрыт.",
            show_alert=True,
        )
        await _show_future_decks(message, state)
        return

    deck_name = str(deck.get("deck_name") or data.get("preorder_deck_name") or "")
    title = build_preorder_title(
        deck_id=deck_id,
        deck_name=deck_name,
        items=items,
        mode=mode,
    )
    hero_name = (
        "Предзаказ целой будущей колоды"
        if mode == PREORDER_MODE_WHOLE_DECK
        else "Предзаказ будущей колоды"
    )
    await state.update_data(
        deck_id=deck_id,
        card_id=None,
        card_name=title,
        hero_name=hero_name,
        rarity="any",
        deck_type=str(deck.get("deck_type") or "").strip().lower() or None,
        service="deck",
        lot_scope="deck",
        is_whole_deck=True,
        image_id=ANY_DECK_PHOTO_ID,
        image_file_id=ANY_DECK_PHOTO_ID,
        preorder_deck_id=deck_id,
        preorder_deck_name=deck_name,
        preorder_mode=mode,
        preorder_items=items,
    )
    await call.answer()
    await _ask_for_preorder_currency(message, state)


@router.message(
    StateFilter(UserAddLotFSM.waiting_for_confirmation),
    PreorderDraftFilter(),
    F.text.in_(["✅ Подтвердить", "да"]),
)
async def confirm_preorder_with_revalidation(
    message: types.Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()
    deck_id = int(data.get("preorder_deck_id") or 0)

    try:
        deck = await (await _catalog()).future_empty_deck(deck_id)
    except Exception:  # noqa: BLE001 - convert infrastructure failures to a stable bot response.
        log.exception("failed to revalidate preorder deck %s before submit", deck_id)
        await message.answer("Не удалось проверить будущую колоду. Заявка не отправлена.")
        return

    if not deck:
        await state.set_state(UserAddLotFSM.waiting_for_own_variant)
        await state.update_data(
            preorder_deck_id=None,
            preorder_deck_name=None,
            preorder_mode=PREORDER_MODE_ITEMS,
            preorder_items={},
            deck_id=None,
            card_id=None,
            card_name=None,
            service=None,
            lot_scope=None,
            is_whole_deck=None,
        )
        await message.answer(
            "В выбранной колоде уже появились карты. Предзаказ закрыт, заявка не отправлена.",
            reply_markup=preorder_menu_keyboard(),
        )
        return

    await user_addlot_confirm(message, state, bot)
