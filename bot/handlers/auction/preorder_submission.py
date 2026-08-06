"""Priority handlers that complete and persist structured preorder drafts."""

from __future__ import annotations

# fmt: off
import html
import logging
import secrets

from aiogram import F, Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot.core.legacy_config import legacy_config
from bot.domain.auctions import AuctionKind, Currency
from bot.domain.preorders import (
    PREORDER_MAX_START_PRICE,
    PREORDER_MIN_START_PRICE,
    PREORDER_MODE_WHOLE_DECK,
    build_preorder_title,
    format_preorder_composition,
    validate_preorder_selection,
    validate_preorder_start_price,
)
from bot.features.auction_submission import ANY_DECK_PHOTO_ID, _norm_currency
from bot.handlers.auction.preorder import PreorderDraftFilter
from bot.handlers.auction.submission_support import auction_currency_kb
from bot.services.admin_logging import send_admin_log
from bot.services.auction_submission import AuctionSubmissionCatalogService
from bot.services.luxury import get_user_luxury_level
from bot.services.preorder_submissions import (
    PreorderAccessDenied,
    PreorderDeckUnavailable,
    PreorderSubmissionError,
    PreorderSubmissionService,
)
from bot.telegram.states import UserAddLotFSM

log = logging.getLogger(__name__)
router = Router(name=__name__)

_CONFIRM_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Подтвердить")],
        [KeyboardButton(text="❌ Отмена")],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)


def _preorder_access_allowed(user_id: int, luxury_level: int) -> bool:
    return user_id in legacy_config.ADMINS or luxury_level >= AuctionKind.PREORDER.minimum_luxury_level


def _mode_label(mode: str) -> str:
    return "Целая колода" if mode == PREORDER_MODE_WHOLE_DECK else "Карты по редкостям"


def _composition_label(mode: str, items: dict[str, int]) -> str:
    if mode == PREORDER_MODE_WHOLE_DECK:
        return "вся будущая колода"
    return format_preorder_composition(items)


async def _load_future_deck(deck_id: int) -> dict[str, object] | None:
    service = await AuctionSubmissionCatalogService.create()
    return await service.future_empty_deck(deck_id)


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant),
    F.data == "preorder:finish",
)
async def preorder_finish_with_fixed_price(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    message = call.message
    if not isinstance(message, types.Message):
        await call.answer("Сообщение недоступно. Откройте меню заново.", show_alert=True)
        return

    data = await state.get_data()
    user_id = call.from_user.id
    luxury_level = int(data.get("luxury_level") or 0)
    if not _preorder_access_allowed(user_id, luxury_level):
        await call.answer(
            "Предзаказ доступен администраторам и пользователям Лакшери 1–2.",
            show_alert=True,
        )
        return

    deck_id = int(data.get("preorder_deck_id") or 0)
    if deck_id <= 0:
        await call.answer("Сначала выберите будущую колоду.", show_alert=True)
        return

    try:
        mode, items = validate_preorder_selection(
            mode=data.get("preorder_mode"),
            items=data.get("preorder_items"),
        )
    except ValueError:
        await call.answer(
            "Добавьте хотя бы одну карту или выберите целую колоду.",
            show_alert=True,
        )
        return

    try:
        deck = await _load_future_deck(deck_id)
    except Exception:  # noqa: BLE001 - infrastructure failure becomes a stable UI response.
        log.exception("failed to revalidate preorder deck=%s", deck_id)
        await call.answer("Не удалось проверить будущую колоду.", show_alert=True)
        return
    if not deck:
        await call.answer(
            "Колода уже получила карты или была удалена. Выберите другую.",
            show_alert=True,
        )
        return

    deck_name = str(deck.get("deck_name") or data.get("preorder_deck_name") or "")
    title = build_preorder_title(
        deck_id=deck_id,
        deck_name=deck_name,
        mode=mode,
        items=items,
    )
    request_key = str(data.get("preorder_request_key") or "").strip()
    if not request_key:
        request_key = f"preorder:{user_id}:{secrets.token_hex(16)}"

    await state.update_data(
        auction_kind=AuctionKind.PREORDER.value,
        preorder_deck_id=deck_id,
        preorder_deck_name=deck_name,
        preorder_mode=mode,
        preorder_items=items,
        preorder_request_key=request_key,
        deck_id=deck_id,
        card_id=None,
        card_name=title,
        hero_name=f"Предзаказ колоды №{deck_id}",
        rarity="any",
        deck_type=str(deck.get("deck_type") or "card"),
        service="preorder",
        lot_scope="deck",
        is_whole_deck=mode == PREORDER_MODE_WHOLE_DECK,
        image_id=ANY_DECK_PHOTO_ID,
        image_file_id=ANY_DECK_PHOTO_ID,
    )
    await state.set_state(UserAddLotFSM.waiting_for_currency)
    await message.answer(
        "Выберите валюту стартовой цены предзаказа:",
        reply_markup=auction_currency_kb(AuctionKind.PREORDER.value),
    )
    await call.answer()


@router.message(
    StateFilter(UserAddLotFSM.waiting_for_currency),
    PreorderDraftFilter(),
    F.text,
)
async def preorder_currency_selected(message: types.Message, state: FSMContext) -> None:
    currency = _norm_currency(message.text)
    if not currency:
        await message.answer(
            "Выберите валюту кнопкой.",
            reply_markup=auction_currency_kb(AuctionKind.PREORDER.value),
        )
        return

    parsed_currency = Currency.from_raw(currency)
    await state.update_data(
        auction_kind=AuctionKind.PREORDER.value,
        currency=parsed_currency.value,
        accepted_currencies=[parsed_currency.value],
        custom_offer_terms=None,
        min_start=PREORDER_MIN_START_PRICE,
        max_start=PREORDER_MAX_START_PRICE,
    )
    await state.set_state(UserAddLotFSM.waiting_for_start_price)
    await message.answer(
        "Допустимая стартовая цена для любого предзаказа: "
        f"<b>{PREORDER_MIN_START_PRICE}–{PREORDER_MAX_START_PRICE} "
        f"{parsed_currency.emoji}</b>\n"
        f"Шаг цены: {parsed_currency.bid_step}.\n\n"
        "Введите стартовую цену целым числом:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(
    StateFilter(UserAddLotFSM.waiting_for_start_price),
    PreorderDraftFilter(),
    F.text,
)
async def preorder_start_price_received(
    message: types.Message,
    state: FSMContext,
) -> None:
    try:
        price = validate_preorder_start_price((message.text or "").strip())
    except ValueError:
        await message.answer(
            "Введите целое число от "
            f"{PREORDER_MIN_START_PRICE} до {PREORDER_MAX_START_PRICE}."
        )
        return

    await state.update_data(start_price=price)
    await state.set_state(UserAddLotFSM.waiting_for_comment)
    await message.answer(
        "Введите комментарий к предзаказу или '-' если он не нужен:",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(
    StateFilter(UserAddLotFSM.waiting_for_comment),
    PreorderDraftFilter(),
    F.text,
)
async def preorder_comment_received(message: types.Message, state: FSMContext) -> None:
    raw_comment = (message.text or "").strip()
    comment = "" if raw_comment == "-" else raw_comment[:2000]
    data = await state.get_data()

    try:
        mode, items = validate_preorder_selection(
            mode=data.get("preorder_mode"),
            items=data.get("preorder_items"),
        )
        price = validate_preorder_start_price(data.get("start_price"))
        currency = Currency.from_raw(data.get("currency"))
    except (TypeError, ValueError) as exc:
        log.warning("invalid preorder draft before preview: %s", exc)
        await message.answer("Черновик предзаказа повреждён. Откройте создание лота заново.")
        await state.clear()
        return

    deck_id = int(data.get("preorder_deck_id") or 0)
    deck_name = str(data.get("preorder_deck_name") or "Будущая колода")
    title = build_preorder_title(
        deck_id=deck_id,
        deck_name=deck_name,
        mode=mode,
        items=items,
    )
    await state.update_data(comment=comment, card_name=title)
    await state.set_state(UserAddLotFSM.waiting_for_confirmation)

    comment_line = html.escape(comment) if comment else "-"
    await message.answer(
        "<b>Лот:</b> "
        f"{html.escape(title)}\n"
        f"<b>Режим:</b> {_mode_label(mode)}\n"
        f"<b>Состав:</b> {html.escape(_composition_label(mode, items))}\n"
        f"<b>Стартовая цена:</b> {price} {currency.emoji}\n"
        f"<b>Комментарий:</b> {comment_line}\n"
        "Всё верно? Отправить заявку на модерацию?",
        parse_mode="HTML",
        reply_markup=_CONFIRM_KEYBOARD,
    )


@router.message(
    StateFilter(UserAddLotFSM.waiting_for_confirmation),
    PreorderDraftFilter(),
    F.text.in_(["❌ Отмена", "нет", "Нет"]),
)
async def preorder_submission_cancelled(
    message: types.Message,
    state: FSMContext,
) -> None:
    await state.clear()
    await message.answer(
        "Создание предзаказа отменено.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(
    StateFilter(UserAddLotFSM.waiting_for_confirmation),
    PreorderDraftFilter(),
    F.text.in_(["✅ Подтвердить", "да", "Да"]),
)
async def preorder_submission_confirmed(
    message: types.Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    user_id = message.from_user.id
    luxury_level = await get_user_luxury_level(message.bot, user_id)
    request_key = str(data.get("preorder_request_key") or "").strip()
    if not request_key:
        request_key = f"preorder:{user_id}:{secrets.token_hex(16)}"
        await state.update_data(preorder_request_key=request_key)

    try:
        submitted = await (await PreorderSubmissionService.create()).submit(
            owner_id=user_id,
            luxury_level=luxury_level,
            is_admin=user_id in legacy_config.ADMINS,
            deck_id=int(data.get("preorder_deck_id") or 0),
            deck_name=str(data.get("preorder_deck_name") or ""),
            mode=data.get("preorder_mode"),
            items=data.get("preorder_items"),
            request_key=request_key,
            start_price=data.get("start_price"),
            currency=data.get("currency"),
            comment=str(data.get("comment") or ""),
            image_id=str(data.get("image_id") or ANY_DECK_PHOTO_ID),
        )
    except PreorderAccessDenied as exc:
        await message.answer(str(exc), reply_markup=ReplyKeyboardRemove())
        await state.clear()
        return
    except PreorderDeckUnavailable as exc:
        await message.answer(str(exc), reply_markup=ReplyKeyboardRemove())
        await state.clear()
        return
    except (TypeError, ValueError, PreorderSubmissionError) as exc:
        log.warning("preorder submission rejected user=%s: %s", user_id, exc)
        await message.answer(
            "Не удалось проверить данные предзаказа. Исправьте черновик и повторите.",
            reply_markup=_CONFIRM_KEYBOARD,
        )
        return
    except Exception:  # noqa: BLE001 - keep the draft available after infrastructure failures.
        log.exception("failed to create preorder application user=%s", user_id)
        await message.answer(
            "❌ Не удалось создать заявку. Черновик сохранён, попробуйте подтвердить ещё раз.",
            reply_markup=_CONFIRM_KEYBOARD,
        )
        return

    action = "уже существовала" if submitted.was_existing else "создана"
    await send_admin_log(
        message.bot,
        "🗓 <b>Заявка предзаказа "
        f"№{submitted.auction_id} {action}</b>\n"
        f"Пользователь: <code>{user_id}</code>\n"
        f"Колода: №{html.escape(str(data.get('preorder_deck_id') or '-'))}\n"
        f"Режим: {html.escape(str(data.get('preorder_mode') or '-'))}",
    )
    await state.clear()
    if submitted.was_existing:
        result_text = (
            f"✅ Заявка №{submitted.auction_id} уже была создана ранее. "
            "Дубликат не добавлен."
        )
    else:
        result_text = (
            f"✅ Заявка №{submitted.auction_id} создана и отправлена на модерацию."
        )
    await message.answer(result_text, reply_markup=ReplyKeyboardRemove())
# fmt: on
