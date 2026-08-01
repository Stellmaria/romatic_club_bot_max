"""User-facing FSM flow for submitting an auction lot."""

import html
import logging
import re
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram import types, Bot
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

from bot.handlers.admin.helper.admin_constants import load_full_auction_ctx
from bot.core.time import to_moscow
from bot.services.admin_logging import send_admin_log
from bot.handlers.constants import USER_MESSAGES
from bot.handlers.helper.helpers_users import _emoji_by_currency
from bot.handlers.auction.kinds import auction_kind_keyboard
from bot.handlers.auction.exchange import (
    clean_telegram_text as _tg_clean,
    exchange_deck_id_from_row as _deck_id_from_row,
    exchange_deck_keyboard,
    get_exchange_deck_ids as _get_exchange_deck_ids,
    get_exchange_decks_for_menu as _get_exchange_decks_for_menu,
)
from bot.keyboards.keyboards import craft_uid_kb
from bot.domain.auctions import (
    AuctionAccessDenied,
    AuctionKind,
    currency_choices_label,
)
from bot.services.auction_workflows import (
    AuctionCreationService,
)
from bot.services.luxury import get_user_luxury_level
from bot.telegram.media import answer_media_any as _answer_media_any
from bot.core.legacy_config import legacy_config
from db.cards import (
    get_all_decks,
    get_cards_by_deck,
    get_card_by_id,
)
from db.admin import log_admin_action
from db.users import (
    is_luxury_user,
    get_user,
)
from db.auctions import (
    get_lots_by_owner,
    has_pending_lot,
    count_sold_by_card_id,
    count_sold_same_card,
)
from bot.telegram.states import ExchangeFSM, UserAddLotFSM

from bot.features.auction_submission import (
    ANY_CARD_VIDEO_ID,
    ANY_DECK_PHOTO_ID,
    ANY_RARITY_VIDEO_ID,
    SPINS_VIDEO_BY_QTY,
    TREASURES_LOCKED,
    TREASURES_LOCK_REASON,
    _cur_emoji,
    _cur_step,
    _currency_label,
    _ensure_membership,
    _exchange_deck_cover_id,
    _get_rarity_from_state_or_db,
    _norm_currency,
    _norm_rarity,
    _service_media_file_id,
    _subscription_title,
    compute_start_price_limits,
    currency_kb,
    auction_currency_kb,
    log,
)
from bot.telegram.callback_parser import split_callback_data

router = Router(name=__name__)
submission_feature = router


async def _ask_for_currency(message: types.Message, state: FSMContext) -> None:
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


@router.message(F.text.regexp(r"^/addlot(?:@\w+)?(?:\s|$)"))
async def addlot_regex_entry(message: types.Message, state: FSMContext, bot: Bot):
    await addlot_start(message, state, bot)


async def addlot_start(message: types.Message, state: FSMContext, bot: Bot) -> None:
    if message.chat.type != "private":
        me = await bot.me()
        url = f"https://t.me/{me.username}?start=addlot"
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="Открыть диалог с ботом", url=url)]]
        )
        await message.reply("Эту команду нужно запускать в личке с ботом.", reply_markup=kb)
        return

    await state.clear()

    try:
        u = await get_user(message.from_user.id)
        is_lux = bool(u and (u.get("is_luxury") or u.get("is_lux")))
        is_trusted = bool(u and u.get("is_trusted"))
    except Exception:
        is_lux = False
        is_trusted = False

    try:
        await _ensure_membership(bot, message.from_user.id, legacy_config.AUCTION_CHANNEL_ID, legacy_config.DISCUSSION_CHAT_ID)
    except PermissionError:
        await message.answer(
            USER_MESSAGES.get(
                "auction_access_denied",
                "❗ Для того чтобы выставить карту на аукцион, нужно выполнить следующие пункты:\n"
                "1️⃣ Быть подписанным на наш канал https://t.me/karty_kr, а также состоять в чате https://t.me/karta_kr\n"
                "2️⃣ Иметь именно подарочную копию выставляемой карты\n"
                "3️⃣ Знать номер колоды и имя персонажа вашей карты\n"
                "4️⃣ Указать стартовую цену (Самая лучшая от 90-300💎)\n"
                "5️⃣ Оставить эту карту у себя до выхода аукциона",
            )
        )
        return
    except Exception:
        await message.answer("Не удалось проверить подписку. Попробуй позже или напиши админам.")
        return

    luxury_level = await get_user_luxury_level(bot, message.from_user.id)

    await state.update_data(
        is_lux=is_lux,
        is_trusted=is_trusted,
        luxury_level=luxury_level,
        auction_kind=None,
    )

    await state.set_state(UserAddLotFSM.waiting_for_auction_kind)

    await message.answer(
        "Выберите вид аукциона:",
        reply_markup=auction_kind_keyboard(luxury_level),
    )


@router.callback_query(
    UserAddLotFSM.waiting_for_auction_kind, F.data.startswith("auk_kind_locked:")
)
async def auk_kind_locked(call: types.CallbackQuery) -> None:
    await call.answer("Этот тип доступен только по уровню Лакшери.", show_alert=True)


@router.callback_query(UserAddLotFSM.waiting_for_auction_kind, F.data.startswith("auk_kind:"))
async def auk_kind_selected(call: types.CallbackQuery, state: FSMContext) -> None:
    _, kind = split_callback_data(call.data, ":", 1)

    try:
        selected_kind = AuctionKind.from_raw(kind)
    except ValueError:
        await call.answer("Неизвестный тип аукциона.", show_alert=True)
        return

    data = await state.get_data()
    luxury_level = int(data.get("luxury_level") or 0)
    if luxury_level < selected_kind.minimum_luxury_level:
        await call.answer(
            f"Этот тип доступен с уровня Лакшери {selected_kind.minimum_luxury_level}.",
            show_alert=True,
        )
        return

    # ✅ БИРЖА
    if selected_kind is AuctionKind.EXCHANGE:
        await state.update_data(auction_kind=selected_kind.value)
        await state.set_state(ExchangeFSM.waiting_for_deck)

        decks = await _get_exchange_decks_for_menu()
        deck_ids_label = " / ".join(str(_deck_id_from_row(d)) for d in decks)

        await call.message.answer(
            f"🛒 Биржа: выбери колоду ({deck_ids_label}):",
            reply_markup=exchange_deck_keyboard(decks),
        )
        await call.answer()
        return

    is_lux = bool(data.get("is_lux", False))
    is_trusted = bool(data.get("is_trusted", False))
    await state.update_data(auction_kind=selected_kind.value)
    await call.answer()
    await _start_deck_choice(call.message, state, is_lux, is_trusted)


async def _start_deck_choice(
    message: types.Message, state: FSMContext, is_lux: bool, is_trusted: bool
):
    await state.update_data(is_lux=is_lux, is_trusted=is_trusted)

    decks = await get_all_decks()
    if not decks:
        await message.answer("Пока нет доступных колод.")
        return

    keyboard = [
        [
            types.InlineKeyboardButton(
                text=f"{deck['deck_id']}. {deck.get('deck_name') or deck.get('title') or 'Колода'}",
                callback_data=f"user_deck_{deck['deck_id']}",
            )
        ]
        for deck in decks
    ]

    keyboard.append(
        [types.InlineKeyboardButton(text="Другие лоты", callback_data="user_own_custom")]
    )
    # ✅ кнопка “Типы аукционов” (как просила)
    keyboard.append(
        [types.InlineKeyboardButton(text="📁 Типы аукционов", callback_data="user_auk_types")]
    )

    # ✅ назад к выбору аукциона
    keyboard.append(
        [
            types.InlineKeyboardButton(
                text="⬅️ Назад к выбору аукциона", callback_data="user_back_to_auction_kind"
            )
        ]
    )

    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.answer("Выберите колоду:", reply_markup=kb)

    # ❗ВАЖНО: строка должна заканчиваться тут. НИКАКИХ декораторов на этой же строке.
    await state.set_state(UserAddLotFSM.waiting_for_deck)


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_back_to_auction_kind"
)
async def cb_user_back_to_auction_kind(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    luxury_level = int(data.get("luxury_level") or 0)

    await state.set_state(UserAddLotFSM.waiting_for_auction_kind)
    await call.message.answer(
        "Выберите вид аукциона:",
        reply_markup=auction_kind_keyboard(luxury_level),
    )
    await call.answer()


def kb_decks(decks: list[dict]) -> types.InlineKeyboardMarkup:
    rows: list[list[types.InlineKeyboardButton]] = []

    for d in decks:
        deck_id = d.get("deck_id") or d.get("id")
        if not deck_id:
            continue

        num = d.get("num")
        if num is None:
            num = deck_id

        name = d.get("name") or d.get("deck_name") or d.get("title") or "—"

        rows.append(
            [
                types.InlineKeyboardButton(
                    text=f"{num}. {name}",
                    callback_data=f"user_deck_{int(deck_id)}",
                )
            ]
        )

    rows.append([types.InlineKeyboardButton(text="Друзья+", callback_data="user_friends_plus")])
    rows.append(
        [types.InlineKeyboardButton(text="Слоты прогресса", callback_data="user_progress_slots")]
    )
    rows.append([types.InlineKeyboardButton(text="Пропуски", callback_data="user_subscription")])
    rows.append([types.InlineKeyboardButton(text="Кручения", callback_data="user_spins")])
    rows.append(
        [
            types.InlineKeyboardButton(
                text="Колода-конструктор", callback_data="user_deck_constructor"
            )
        ]
    )
    rows.append([types.InlineKeyboardButton(text="Другие лоты", callback_data="user_own_variant")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def kb_presets_menu() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="Друзья+", callback_data="user_friends_plus")],
            [
                types.InlineKeyboardButton(
                    text="Слоты прогресса", callback_data="user_progress_slots"
                )
            ],
            [types.InlineKeyboardButton(text="Пропуски", callback_data="user_subscription")],
            [types.InlineKeyboardButton(text="Кручения", callback_data="user_spins")],
            [
                types.InlineKeyboardButton(
                    text="Колода-конструктор", callback_data="user_deck_constructor"
                )
            ],
            [types.InlineKeyboardButton(text="Любая бронза", callback_data="user_any_bronze")],
            [types.InlineKeyboardButton(text="Любое серебро", callback_data="user_any_silver")],
            [types.InlineKeyboardButton(text="Любая золотая", callback_data="user_any_gold")],
            [types.InlineKeyboardButton(text="Любая алмазная", callback_data="user_any_diamond")],
            [types.InlineKeyboardButton(text="Любая карта", callback_data="user_any_card")],
            [types.InlineKeyboardButton(text="Любая колода", callback_data="user_any_deck")],
            [
                types.InlineKeyboardButton(
                    text="Алмазы за чай", callback_data="user_res_diamonds_for_tea"
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Чай за алмазы", callback_data="user_res_tea_for_diamonds"
                )
            ],
            [types.InlineKeyboardButton(text="Назад", callback_data="user_deck_back")],
        ]
    )


def kb_subscription_types(back_cb: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="🥇 Золотой пропуск", callback_data="user_subscription_gold"
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="💎 Премиум пропуск", callback_data="user_subscription_premium"
                )
            ],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)],
        ]
    )


def kb_subscription_periods(plan: str, back_cb: str) -> types.InlineKeyboardMarkup:
    title = "Золотой пропуск" if plan == "gold" else "Премиум пропуск"
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=f"{title} • 1 месяц", callback_data=f"user_subscription_period:{plan}:1"
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=f"{title} • 3 месяца", callback_data=f"user_subscription_period:{plan}:3"
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=f"{title} • 6 месяцев", callback_data=f"user_subscription_period:{plan}:6"
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=f"{title} • 12 месяцев",
                    callback_data=f"user_subscription_period:{plan}:12",
                )
            ],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)],
        ]
    )


async def _start_subscription_period_step(
    call: types.CallbackQuery,
    state: FSMContext,
    *,
    plan: str,
    back_cb: str,
) -> None:
    title = "Золотой пропуск" if plan == "gold" else "Премиум пропуск"

    await state.update_data(
        deck_id=None,
        card_id=None,
        hero_name=None,
        rarity="any",
        deck_type=None,
        subscription_plan=plan,
        subscription_months=None,
        card_name=title,  # пока базовое название
        service=None,  # сервис зафиксируем после выбора срока
        image_id=None,
        image_file_id=None,
    )

    await call.message.answer(
        "Выберите срок подписки:",
        reply_markup=kb_subscription_periods(plan, back_cb),
    )
    await state.set_state(UserAddLotFSM.waiting_for_subscription)

    try:
        await call.answer()
    except Exception:
        pass


async def _check_service_addlot_access(call: types.CallbackQuery, state: FSMContext) -> bool:
    user_id = call.from_user.id
    is_lux = await is_luxury_user(user_id)

    if is_lux:
        return True

    if await has_pending_lot(user_id):
        await call.message.answer(
            "❗ У вас уже есть заявка в ожидании рассмотрения или на модерации!\n"
            "Дождитесь её обработки, чтобы отправить новую.\n\n"
            "Хотите выставлять несколько карт? Получите лакшери-статус!"
        )
        await state.clear()
        try:
            await call.answer()
        except Exception:
            pass
        return False

    user_lots = await get_lots_by_owner(user_id)
    scheduled_user_lots = [
        a for a in (user_lots or []) if (a.get("status") or "") in {"scheduled", "active"}
    ]

    if scheduled_user_lots:
        last_lot = max(
            scheduled_user_lots, key=lambda x: (x.get("end_time") or x.get("start_time"))
        )
        date_str = to_moscow(last_lot["end_time"]).strftime("%d.%m.%Y")
        start = to_moscow(last_lot["start_time"]).strftime("%H:%M")
        end = to_moscow(last_lot["end_time"]).strftime("%H:%M")
        next_possible_time = to_moscow(last_lot["end_time"]) + timedelta(minutes=1)

        msg = (
            "❗ У вас уже есть запланированный лот в аукционе.\n\n"
            f"Лот: <b>{last_lot['card_name']}</b>\n"
            f"Дата: <b>{date_str}</b>\n"
            f"Время: <b>{start}–{end}</b>\n\n"
            f"<i>Вы сможете снова подать заявку после окончания этого аукциона — "
            f"{next_possible_time.strftime('%d.%m.%Y %H:%M')}</i>\n"
            "Хотите выставлять несколько карт? Получите лакшери-статус!"
        )
        await call.message.answer(msg, parse_mode="HTML")
        await state.clear()
        try:
            await call.answer()
        except Exception:
            pass
        return False

    return True


def deck_constructor_bronze_kb() -> types.ReplyKeyboardMarkup:
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="4 бронзы")],
            [types.KeyboardButton(text="5 бронз")],
            [types.KeyboardButton(text="6 бронз")],
            [types.KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def spins_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="10 кручений")],
            [KeyboardButton(text="50 кручений")],
            [KeyboardButton(text="100 кручений")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_spins")
async def cb_spins_from_decks(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(
        deck_id=None,
        card_id=None,
        rarity="any",
        deck_type=None,
        card_name="Кручения",
        service="spins",
        spins_qty=None,
    )
    await call.message.answer("Выберите количество кручений:", reply_markup=spins_kb())
    # будем ловить число в том же состоянии, где обычно ловим валюту
    await state.set_state(UserAddLotFSM.waiting_for_currency)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_spins")
async def cb_spins_from_presets(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(card_id=None, card_name="Кручения", service="spins", spins_qty=None)
    await call.message.answer("Выберите количество кручений:", reply_markup=spins_kb())
    await state.set_state(UserAddLotFSM.waiting_for_currency)
    await call.answer()


@router.message(StateFilter(UserAddLotFSM.waiting_for_currency))
async def addlot_currency_or_spins(message: types.Message, state: FSMContext):
    data = await state.get_data()
    service = (data.get("service") or "").strip()

    # 1) Ветка КРУЧЕНИЙ: сначала выбираем количество
    if service == "spins" and not data.get("spins_qty"):
        m = re.search(r"\d+", message.text or "")
        qty = int(m.group()) if m else 0
        if qty not in (10, 50, 100):
            await message.answer("Выберите кнопкой: 10, 50 или 100 кручений.")
            return

        video_id = (SPINS_VIDEO_BY_QTY.get(qty) or "").strip()
        if not video_id or video_id.startswith("PASTE_"):
            await message.answer(
                "Видео для кручений ещё не настроено.\n"
                "Нужно вставить Telegram file_id в SPINS_VIDEO_10_ID / 50 / 100."
            )
            return

        await state.update_data(
            spins_qty=qty,
            card_name=f"Кручения ({qty} шт.)",
            image_id=video_id,
            image_file_id=video_id,
        )
        await _ask_for_currency(message, state)
        return

    # 2) Выбор валюты с учётом типа аукциона.
    auction_kind = str(data.get("auction_kind") or "standard").strip().lower()
    raw_currency = (message.text or "").strip().lower()
    is_reverse = auction_kind == AuctionKind.REVERSE.value
    is_free = auction_kind == AuctionKind.FREE.value

    if is_free and ("комби" in raw_currency or "свои вариант" in raw_currency):
        await state.update_data(
            currency="чашки",
            accepted_currencies=["чашки", "алмазы"],
            custom_offer_terms=None,
            start_price=0,
            min_start=None,
            max_start=None,
        )
        await message.answer(
            "Опишите свои варианты оплаты или обмена одним сообщением.\n"
            "Например: <code>2 чая + карта из КР</code> или "
            "<code>алмазы + обмен на другую карту</code>.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.set_state(UserAddLotFSM.waiting_for_custom_offer_terms)
        return

    has_tea = "🍵" in raw_currency or "чай" in raw_currency or "чаш" in raw_currency
    has_diamonds = "💎" in raw_currency or "алмаз" in raw_currency
    accepted_currencies: list[str]
    if (is_reverse or is_free) and has_tea and has_diamonds:
        currency = "чашки"  # compatibility scalar for old code paths
        accepted_currencies = ["чашки", "алмазы"]
    else:
        currency = _norm_currency(message.text)
        accepted_currencies = [currency] if currency else []

    if not currency:
        await message.answer(
            "Выберите валюту кнопкой.",
            reply_markup=auction_currency_kb(auction_kind),
        )
        return

    if is_reverse or is_free:
        if currency not in {"чашки", "алмазы"}:
            await message.answer(
                "Для этого типа доступны только 🍵 чай, 💎 алмазы или оба варианта.",
                reply_markup=auction_currency_kb(auction_kind),
            )
            return
    elif TREASURES_LOCKED and currency == "сокровища":
        await message.answer(TREASURES_LOCK_REASON, reply_markup=currency_kb())
        return

    await state.update_data(
        currency=currency,
        accepted_currencies=accepted_currencies,
        custom_offer_terms=None,
    )

    if is_free:
        await state.update_data(start_price=0, min_start=None, max_start=None)
        await message.answer(
            USER_MESSAGES.get(
                "add_comment",
                "Введите комментарий к лоту или '-' если не нужен:",
            ),
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.set_state(UserAddLotFSM.waiting_for_comment)
        return

    if is_reverse:
        min_allowed, max_allowed, hint = await compute_start_price_limits(state, currency)
        max_allowed = max(min_allowed, max_allowed)
        emoji = _cur_emoji(currency)
        step = _cur_step(currency)
        mixed_note = (
            "\nДля смешанного лота это потолок в чае; "
            "ставки сравниваются по курсу 1 🍵 = 10 💎."
            if len(accepted_currencies) > 1
            else ""
        )
        await message.answer(
            f"Стартовый потолок обратного аукциона: "
            f"<b>{min_allowed}–{max_allowed} {emoji}</b>\n"
            f"({hint})\nШаг: {step}.{mixed_note}\n\n"
            "Введите стартовый потолок целым числом:",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.update_data(min_start=min_allowed, max_start=max_allowed)
        await state.set_state(UserAddLotFSM.waiting_for_start_price)
        return

    min_allowed, max_allowed, hint = await compute_start_price_limits(state, currency)
    max_allowed = max(min_allowed, max_allowed)

    emoji = _cur_emoji(currency)
    step = _cur_step(currency)

    await message.answer(
        f"Допустимая стартовая цена: <b>{min_allowed}–{max_allowed} {emoji}</b>\n"
        f"({hint})\n"
        f"Шаг цены: {step} ({_currency_label(currency)})\n\n"
        f"Введите стартовую цену (целое число):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.update_data(min_start=min_allowed, max_start=max_allowed)
    await state.set_state(UserAddLotFSM.waiting_for_start_price)


@submission_feature.message(StateFilter(UserAddLotFSM.waiting_for_custom_offer_terms), F.text)
async def addlot_custom_offer_terms(message: types.Message, state: FSMContext):
    terms = (message.text or "").strip()
    if terms.lower() in {"отмена", "cancel", "/cancel"}:
        await state.clear()
        await message.answer("Создание лота отменено.", reply_markup=ReplyKeyboardRemove())
        return
    if len(terms) < 3:
        await message.answer("Опишите варианты подробнее, минимум 3 символа.")
        return
    if len(terms) > 500:
        await message.answer("Описание слишком длинное. Максимум 500 символов.")
        return

    await state.update_data(
        currency="чашки",
        accepted_currencies=["чашки", "алмазы"],
        custom_offer_terms=terms,
        start_price=0,
        min_start=None,
        max_start=None,
    )
    await message.answer(
        USER_MESSAGES.get(
            "add_comment",
            "Введите комментарий к лоту или '-' если не нужен:",
        ),
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(UserAddLotFSM.waiting_for_comment)


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_subscription")
async def cb_subscription_menu_from_decks(call: types.CallbackQuery, state: FSMContext):
    try:
        await call.answer()
    except Exception:
        pass
    await call.message.answer(
        "Выберите тип подписки:",
        reply_markup=kb_subscription_types("user_subscription_back_decks"),
    )


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_subscription_back_decks"
)
async def cb_subscription_back_decks(call: types.CallbackQuery, state: FSMContext):
    decks = await get_all_decks()
    await call.message.answer("Выберите колоду:", reply_markup=kb_decks(decks))
    try:
        await call.answer()
    except Exception:
        pass


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_deck),
    F.data.in_(["user_subscription_gold", "user_subscription_premium"]),
)
async def cb_subscription_choose_type_from_decks(call: types.CallbackQuery, state: FSMContext):
    allowed = await _check_service_addlot_access(call, state)
    if not allowed:
        return

    plan = "gold" if call.data == "user_subscription_gold" else "premium"
    await _start_subscription_period_step(
        call,
        state,
        plan=plan,
        back_cb="user_subscription_back_decks",
    )


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_friends_plus")
async def cb_friends_plus_from_decks(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id

    if not await is_luxury_user(user_id):
        if await has_pending_lot(user_id):
            await call.message.answer(
                "❗ У вас уже есть заявка в ожидании рассмотрения или на модерации!\n"
                "Дождитесь её обработки, чтобы отправить новую.\n\n"
                "Хотите выставлять несколько карт? Получите лакшери-статус!"
            )
            await state.clear()
            await call.answer()
            return
        user_lots = await get_lots_by_owner(user_id)
        scheduled_user_lots = [
            a for a in user_lots or [] if (a.get("status") or "") in {"scheduled", "active"}
        ]
        if scheduled_user_lots:
            last_lot = max(
                scheduled_user_lots, key=lambda x: (x.get("end_time") or x.get("start_time"))
            )
            date_str = to_moscow(last_lot["end_time"]).strftime("%d.%m.%Y")
            start = to_moscow(last_lot["start_time"]).strftime("%H:%M")
            end = to_moscow(last_lot["end_time"]).strftime("%H:%M")
            next_possible_time = to_moscow(last_lot["end_time"]) + timedelta(minutes=1)
            msg = (
                "❗ У вас уже есть запланированный лот в аукционе.\n\n"
                f"Карта: <b>{last_lot['card_name']}</b>\n"
                f"Дата: <b>{date_str}</b>\n"
                f"Время: <b>{start}–{end}</b>\n\n"
                f"<i>Вы сможете снова подать заявку после окончания этого аукциона — "
                f"{next_possible_time.strftime('%d.%m.%Y %H:%M')}</i>\n"
                "Хотите выставлять несколько карт? Получите лакшери-статус!"
            )
            await call.message.answer(msg, parse_mode="HTML")
            await state.clear()
            await call.answer()
            return

    media_id = _service_media_file_id("friends_plus")
    await state.update_data(
        deck_id=None,
        card_id=None,
        rarity="any",
        deck_type=None,
        card_name="Друзья+",
        service="friends_plus",
        image_id=media_id,
        image_file_id=media_id,
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_progress_slots")
async def cb_progress_slots_from_decks(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id

    if not await is_luxury_user(user_id):
        if await has_pending_lot(user_id):
            await call.message.answer(
                "❗ У вас уже есть заявка в ожидании рассмотрения или на модерации!\n"
                "Дождитесь её обработки, чтобы отправить новую.\n\n"
                "Хотите выставлять несколько карт? Получите лакшери-статус!"
            )
            await state.clear()
            await call.answer()
            return
        user_lots = await get_lots_by_owner(user_id)
        scheduled_user_lots = [
            a for a in user_lots or [] if (a.get("status") or "") in {"scheduled", "active"}
        ]
        if scheduled_user_lots:
            last_lot = max(
                scheduled_user_lots, key=lambda x: (x.get("end_time") or x.get("start_time"))
            )
            date_str = to_moscow(last_lot["end_time"]).strftime("%d.%m.%Y")
            start = to_moscow(last_lot["start_time"]).strftime("%H:%M")
            end = to_moscow(last_lot["end_time"]).strftime("%H:%M")
            next_possible_time = to_moscow(last_lot["end_time"]) + timedelta(minutes=1)
            msg = (
                "❗ У вас уже есть запланированный лот в аукционе.\n\n"
                f"Карта: <b>{last_lot['card_name']}</b>\n"
                f"Дата: <b>{date_str}</b>\n"
                f"Время: <b>{start}–{end}</b>\n\n"
                f"<i>Вы сможете снова подать заявку после окончания этого аукциона — "
                f"{next_possible_time.strftime('%d.%m.%Y %H:%M')}</i>\n"
                "Хотите выставлять несколько карт? Получите лакшери-статус!"
            )
            await call.message.answer(msg, parse_mode="HTML")
            await state.clear()
            await call.answer()
            return

    media_id = _service_media_file_id("progress_slots")
    await state.update_data(
        deck_id=None,
        card_id=None,
        rarity="any",
        deck_type=None,
        card_name="Слоты прогресса",
        service="progress_slots",
        image_id=media_id,
        image_file_id=media_id,
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data == "user_own_custom")
async def cb_show_presets(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Выберите один из вариантов:", reply_markup=kb_presets_menu())
    await state.set_state(UserAddLotFSM.waiting_for_own_variant)
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_deck_back"
)
async def cb_presets_back(call: types.CallbackQuery, state: FSMContext):
    # подставь свою функцию получения колод
    decks = await get_all_decks()  # или твой источник
    await call.message.answer("Выберите колоду:", reply_markup=kb_decks(decks))
    await state.set_state(UserAddLotFSM.waiting_for_deck)
    await call.answer()


# 3) ВЫБОР КОНКРЕТНОЙ КОЛОДЫ (строго фильтруем начало)
@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_deck), F.data.startswith("user_deck_"))
async def user_choose_deck(call: types.CallbackQuery, state: FSMContext):
    # тут твоя старая логика, НО без ветки own_custom
    deck_id = int(split_callback_data(call.data, "_")[2])
    user_id = call.from_user.id

    if not await is_luxury_user(user_id):
        if await has_pending_lot(user_id):
            await call.message.answer(
                "❗ У вас уже есть заявка в ожидании рассмотрения или на модерации!\n"
                "Дождитесь её обработки, чтобы отправить новую.\n\n"
                "Хотите выставлять несколько карт? Получите лакшери-статус!"
            )
            await state.clear()
            await call.answer()
            return

        user_lots = await get_lots_by_owner(user_id)
        scheduled_user_lots = [
            a for a in user_lots or [] if (a.get("status") or "") in {"scheduled", "active"}
        ]
        if scheduled_user_lots:
            last_lot = max(
                scheduled_user_lots, key=lambda x: (x.get("end_time") or x.get("start_time"))
            )
            date_str = to_moscow(last_lot["end_time"]).strftime("%d.%m.%Y")
            start = to_moscow(last_lot["start_time"]).strftime("%H:%M")
            end = to_moscow(last_lot["end_time"]).strftime("%H:%M")
            next_possible_time = to_moscow(last_lot["end_time"]) + timedelta(minutes=1)
            msg = (
                "❗ У вас уже есть запланированный лот в аукционе.\n\n"
                f"Карта: <b>{last_lot['card_name']}</b>\n"
                f"Дата: <b>{date_str}</b>\n"
                f"Время: <b>{start}–{end}</b>\n\n"
                f"<i>Вы сможете снова подать заявку после окончания этого аукциона — "
                f"{next_possible_time.strftime('%d.%m.%Y %H:%M')}</i>\n"
                "Хотите выставлять несколько карт? Получите лакшери-статус!"
            )
            await call.message.answer(msg, parse_mode="HTML")
            await state.clear()
            await call.answer()
            return

    await state.update_data(deck_id=deck_id)
    cards = await get_cards_by_deck(deck_id)
    keyboard = [
        [
            types.InlineKeyboardButton(
                text=f"{c['num']}. {c['hero_name']} ({c['rarity']})",
                callback_data=f"user_card_{c['card_id']}",
            )
        ]
        for c in (cards or [])
    ]
    keyboard.append(
        [
            types.InlineKeyboardButton(
                text=f"Вся колода №{deck_id}", callback_data=f"user_all_deck_{deck_id}"
            )
        ]
    )
    kb = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await call.message.answer("Выберите карту или «Вся колода»:", reply_markup=kb)
    await state.set_state(UserAddLotFSM.waiting_for_card)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_card), F.data.startswith("user_card_"))
async def user_choose_concrete_card(call: types.CallbackQuery, state: FSMContext):
    card_id = int(split_callback_data(call.data, "_")[-1])
    card = await get_card_by_id(card_id)
    if not card:
        await call.answer("Карта не найдена", show_alert=True)
        return

    try:
        await call.answer()
    except Exception:
        pass

    card_name = card.get("card_name") or card.get("hero_name") or f"Card #{card_id}"
    deck_id = int(card.get("deck_id")) if card.get("deck_id") else None
    rarity = _norm_rarity(card.get("rarity"))
    deck_type = (card.get("deck_type") or "").strip().lower()

    if deck_type not in {"roulette", "resource"} and deck_id:
        decks = await get_all_decks()
        for d in decks or []:
            if int(d.get("deck_id")) == deck_id:
                dt = (d.get("deck_type") or "").strip().lower()
                if dt in {"roulette", "resource"}:
                    deck_type = dt
                break

    await state.update_data(
        card_id=card_id, card_name=card_name, deck_id=deck_id, deck_type=deck_type, rarity=rarity
    )

    await _ask_for_currency(call.message, state)


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_card), F.data.startswith("user_all_deck_")
)
async def user_choose_all_deck(call: types.CallbackQuery, state: FSMContext):
    deck_id = int(split_callback_data(call.data, "_")[-1])
    deck_type = None
    decks = await get_all_decks()
    for d in decks or []:
        if int(d.get("deck_id")) == deck_id:
            dt = (d.get("deck_type") or "").strip().lower()
            if dt in {"roulette", "resource"}:
                deck_type = dt
            break
    exchange_deck_ids = await _get_exchange_deck_ids(decks)
    cover_id = _exchange_deck_cover_id(deck_id) if deck_id in exchange_deck_ids else None

    await state.update_data(
        card_id=None,
        card_name=f"Вся колода №{deck_id}",
        hero_name=f"Вся колода №{deck_id}",
        deck_id=deck_id,
        deck_type=deck_type,
        image_id=cover_id,
        image_file_id=cover_id,
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_any_bronze"
)
async def user_choose_any_bronze(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(
        card_name="Любая бронзовая",
        hero_name="Лот от игрока",
        rarity="bronze",
        card_id=None,
        image_id=ANY_RARITY_VIDEO_ID["bronze"],
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_any_silver"
)
async def user_choose_any_silver(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(
        card_name="Любая серебряная",
        hero_name="Лот от игрока",
        rarity="silver",
        card_id=None,
        image_id=ANY_RARITY_VIDEO_ID["silver"],
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_any_gold"
)
async def user_choose_any_gold(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(
        card_name="Любая золотая",
        hero_name="Лот от игрока",
        rarity="gold",
        card_id=None,
        image_id=ANY_RARITY_VIDEO_ID["gold"],
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_any_diamond"
)
async def user_choose_any_diamond(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(
        card_name="Любая алмазная",  # если хочешь — переименуешь в "Любой эпик", но это уже не про фото
        hero_name="Лот от игрока",
        rarity="diamond",
        card_id=None,
        image_id=ANY_RARITY_VIDEO_ID["diamond"],
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_any_card"
)
async def user_choose_any_card(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(
        card_name="Любая карта",
        hero_name="Лот от игрока",
        rarity="any",
        card_id=None,
        image_id=ANY_CARD_VIDEO_ID,  # ← ВОТ ЭТОГО У ТЕБЯ НЕ ХВАТАЛО
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_any_deck"
)
async def user_choose_any_deck(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(
        card_name="Любая колода",
        hero_name="Лот от игрока",
        service="deck",
        rarity="any",
        card_id=None,
        image_id=ANY_DECK_PHOTO_ID,  # ← И ЭТОГО ТОЖЕ
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_any_custom"
)
async def user_choose_custom(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Напишите вручную, какой лот вы хотите выставить (текстом):")
    await state.set_state(UserAddLotFSM.waiting_for_custom_card)
    await call.answer()


@router.message(StateFilter(UserAddLotFSM.waiting_for_custom_card))
async def user_process_custom_card(message: types.Message, state: FSMContext):
    name = message.text.strip()
    rarity = _norm_rarity(name)
    await state.update_data(card_id=None, card_name=name, rarity=rarity)
    await _ask_for_currency(message, state)


@router.message(StateFilter(UserAddLotFSM.waiting_for_start_price), F.text.regexp(r"^\d+$"))
async def addlot_start_price(message: types.Message, state: FSMContext):
    price = int(message.text)
    data = await state.get_data()

    min_start = int(data.get("min_start", 2))
    max_start = max(min_start, int(data.get("max_start", 30**9)))
    currency = data.get("currency", "алмазы")
    emoji = _cur_emoji(currency)
    step = _cur_step(currency)

    if not (min_start <= price <= max_start):
        if min_start == max_start:
            await message.answer(
                f"Недопустимая цена. Разрешённое значение: <b>{min_start} {emoji}</b>.",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                f"Недопустимая цена. Разрешённый диапазон: <b>{min_start}–{max_start} {emoji}</b>.",
                parse_mode="HTML",
            )
        return

    if price % step != 0:
        await message.answer(f"Цена должна быть кратна {step}.")
        return

    await state.update_data(start_price=price)
    await message.answer(
        USER_MESSAGES.get("add_comment", "Введите комментарий к лоту или '-' если не нужен:")
    )
    await state.set_state(UserAddLotFSM.waiting_for_comment)


@router.message(StateFilter(UserAddLotFSM.waiting_for_start_price))
async def addlot_price_invalid(message: types.Message):
    await message.answer("Введите целое число без пробелов и символов.")


@router.message(StateFilter(UserAddLotFSM.waiting_for_comment), F.text)
async def addlot_comment(message: types.Message, state: FSMContext):
    comment = "" if (message.text or "").strip() == "-" else (message.text or "").strip()
    await state.update_data(comment=comment)
    d = await state.get_data()

    # сервисные лоты (кручения/услуги) — вопрос про крафт не нужен
    if d.get("service"):
        await state.update_data(craft_uid_possible=None)

        currency = d.get("currency", "алмазы")
        emoji = _cur_emoji(currency)
        kind_key = str(d.get("auction_kind") or "standard").strip().lower()
        accepted_label = html.escape(
            currency_choices_label(d.get("accepted_currencies"), fallback=currency, custom_terms=d.get("custom_offer_terms"))
        )

        if d.get("service") == "spins":
            lot_title = f"Кручения ({d.get('spins_qty')} шт.)"
        else:
            lot_title = str(d.get("card_name") or "Лот")

        if kind_key == AuctionKind.REVERSE.value:
            price_line = (
                f"Валюта ставок: {accepted_label}\n"
                f"Стартовый потолок: {d.get('start_price')} {emoji}\n"
                "Побеждает минимальная ставка.\n"
            )
        elif kind_key == AuctionKind.FREE.value:
            price_line = f"Принимаются предложения: {accepted_label}\n"
        else:
            price_line = f"Минимальная ставка: {d.get('start_price')} {emoji}\n"

        preview = (
            f"<b>Лот:</b> {html.escape(lot_title)}\n"
            f"{price_line}"
            f"Комментарий: {html.escape(comment or '-')}\n"
            "Всё верно? Отправить заявку на модерацию?"
        )

        kb = types.ReplyKeyboardMarkup(
            keyboard=[
                [
                    types.KeyboardButton(text="✅ Подтвердить"),
                    types.KeyboardButton(text="❌ Отмена"),
                ]
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await message.answer(preview, reply_markup=kb, parse_mode="HTML")
        await state.set_state(UserAddLotFSM.waiting_for_confirmation)
        return

    # обычный лот (карта) — спрашиваем про крафт на UID
    await message.answer(
        "Возможен ли <b>крафт на UID</b> для этого лота?\nВыберите кнопку ниже:",
        reply_markup=craft_uid_kb(),
        parse_mode="HTML",
    )
    await state.set_state(UserAddLotFSM.waiting_for_craft_uid)


@router.message(
    StateFilter(UserAddLotFSM.waiting_for_confirmation), F.text.in_(["✅ Подтвердить", "да"])
)
async def user_addlot_confirm(message: types.Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    is_lux = await is_luxury_user(user_id)
    user = await get_user(user_id)
    is_trusted = bool(user and user.get("is_trusted"))

    data = await state.get_data()  # <— добавили
    if not (is_lux or is_trusted) and not data.get("service"):
        await message.answer(
            "Почти готово! Пришлите ОДНО фото вашей подарочной карты или целой КОЛОДЫ одним фото для подтверждения:",
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.set_state(UserAddLotFSM.waiting_for_proof_photo_final)
    else:
        comment = remove_usernames((data.get("comment") or "").strip())
        await _final_addlot_create(
            message,
            user_id=user_id,
            card_id=data.get("card_id"),
            hero_name=data.get("hero_name"),
            card_name=data.get("card_name"),
            start_price=int(data.get("start_price") or 0),
            currency=data.get("currency") or "алмазы",
            accepted_currencies=data.get("accepted_currencies"),
            custom_offer_terms=data.get("custom_offer_terms"),
            comment=comment,
            image_file_id=data.get("image_id") or data.get("image_file_id"),
            auction_kind=data.get("auction_kind") or "standard",
            craft_uid_possible=data.get("craft_uid_possible"),
            proof_photo_id=None,
        )
        await state.clear()
        await message.answer(
            USER_MESSAGES.get("commands_info", "Главное меню:"),
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )


@router.message(
    StateFilter(UserAddLotFSM.waiting_for_confirmation), F.text.in_(["❌ Отмена", "нет"])
)
async def user_addlot_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        USER_MESSAGES.get("lot_creation_canceled", "Создание лота отменено."),
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(USER_MESSAGES.get("commands_info", "Главное меню:"), parse_mode="HTML")


@router.message(StateFilter(UserAddLotFSM.waiting_for_confirmation))
async def addlot_confirm_invalid(message: types.Message):
    await message.answer("Пожалуйста, выберите действие кнопкой.")


@router.message(StateFilter(UserAddLotFSM.waiting_for_proof_photo_final), F.photo)
async def user_addlot_proof_final(message: types.Message, state: FSMContext, bot: Bot):
    proof_photo_id = message.photo[-1].file_id
    data = await state.get_data()
    comment = remove_usernames((data.get("comment") or "").strip())

    await _final_addlot_create(
        message,
        user_id=message.from_user.id,
        card_id=data.get("card_id"),
        hero_name=data.get("hero_name"),
        card_name=data.get("card_name"),
        start_price=int(data.get("start_price") or 0),
        currency=data.get("currency") or "алмазы",
        accepted_currencies=data.get("accepted_currencies"),
        custom_offer_terms=data.get("custom_offer_terms"),
        comment=comment,
        image_file_id=data.get("image_id") or data.get("image_file_id"),
        auction_kind=data.get("auction_kind") or "standard",
        craft_uid_possible=data.get("craft_uid_possible"),
        proof_photo_id=proof_photo_id,
    )

    await state.clear()
    await message.answer(
        USER_MESSAGES.get("commands_info", "Главное меню:"),
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(StateFilter(UserAddLotFSM.waiting_for_proof_photo_final))
async def user_addlot_proof_required(message: types.Message):
    await message.answer("Пожалуйста, пришлите фото подарочной карты (без него заявку не примут).")


async def _send_user_pending_lot_preview(
    message: types.Message,
    *,
    auction_id: int,
    auction_kind: str,
    start_price: int,
    currency: str,
    accepted_currencies: list[str] | tuple[str, ...] | None,
    custom_offer_terms: str | None,
    image_file_id: str | None,
    comment: str | None = None,
) -> None:
    """Красивое подтверждение пользователю: фото + инфо по заявке."""

    # лейблы типов (как у админки)
    kind_map = {
        "standard": "⭐️ Стандартный",
        "reverse": "✨ Обратный",
        "fast": "⚡️ Быстрый",
        "free": "🪶 Свободный",
        "black": "👑 Чёрный",
        "exchange": "🛒 Биржа",
    }
    kind_key = (auction_kind or "").strip().lower()
    kind_label = kind_map.get(kind_key, auction_kind)
    currencies_preview = currency_choices_label(
        accepted_currencies,
        fallback=currency,
        custom_terms=custom_offer_terms,
    )
    if kind_key == AuctionKind.REVERSE.value:
        price_preview = (
            f"Валюта ставок: <b>{html.escape(currencies_preview)}</b>\n"
            f"Стартовый потолок: <b>{int(start_price)}</b> {_emoji_by_currency(currency)}\n"
            "Ставки идут на понижение"
        )
    elif kind_key == AuctionKind.FREE.value:
        price_preview = (
            "Принимаются предложения: "
            f"<b>{html.escape(currencies_preview)}</b>"
        )
    else:
        price_preview = (
            f"Цена старта: <b>{int(start_price)}</b> (мин. ставка) {_emoji_by_currency(currency)}"
        )

    # подтянем расширенный контекст по лоту (карта/колода/редкость/цитата и т.д.)
    ctx = await load_full_auction_ctx(int(auction_id))
    luxury_level = 0
    try:
        luxury_level = await get_user_luxury_level(message.bot, message.from_user.id)
    except Exception:
        luxury_level = 0

    user_status = "🙂 Обычный"
    if luxury_level >= 2:
        user_status = "👑 Лакшери 2"
    elif luxury_level == 1:
        user_status = "👑 Лакшери 1"
    auction = (ctx or {}).get("auction") or {}
    card = (ctx or {}).get("card") or {}
    deck = (ctx or {}).get("deck") or {}

    hero = (auction.get("hero_name") or card.get("hero_name") or "").strip()
    ctitle = (auction.get("card_name") or card.get("card_name") or "").strip()

    title_line = "—"
    if hero and ctitle:
        title_line = f"{html.escape(hero)} — {html.escape(ctitle)}"
    elif ctitle:
        title_line = html.escape(ctitle)
    elif hero:
        title_line = html.escape(hero)

    cur = (currency or auction.get("currency") or "").strip().lower()
    cur_emoji = _emoji_by_currency(cur)  # уже есть в imports
    start_i = int(start_price or auction.get("start_price") or 0)

    # “Продано ранее”
    sold_cnt = 0
    try:
        cid = card.get("card_id")
        if cid:
            sold_cnt = int(await count_sold_by_card_id(int(cid)))
        elif hero and ctitle:
            sold_cnt = int(await count_sold_same_card(hero_name=hero, card_name=ctitle))
    except Exception:
        sold_cnt = 0

    # Колода
    deck_id = deck.get("deck_id")
    deck_name = (deck.get("name") or "").strip()
    if deck_id and deck_name:
        deck_line = f"Колода: 🃏 {int(deck_id)} колода — {html.escape(deck_name)}"
    elif deck_id:
        deck_line = f"Колода: 🃏 {int(deck_id)} колода"
    else:
        deck_line = "Колода: —"

    rarity = (card.get("rarity") or "").strip()
    # Редкость
    if not rarity:
        try:
            inferred = await _get_rarity_from_state_or_db(
                {
                    "card_name": ctitle or auction.get("card_name") or "",
                    "hero_name": hero or auction.get("hero_name") or "",
                    "card_id": card.get("card_id"),
                }
            )
            if inferred:
                rarity = inferred
        except Exception:
            pass

    if rarity:
        rkey = str(rarity).strip().lower()
        rarity_ru = {
            "bronze": "бронза",
            "silver": "серебро",
            "gold": "золото",
            "diamond": "эпик",
            "epic": "эпик",
            "алмазная": "эпик",
            "серебряная": "серебро",
            "бронзовая": "бронза",
            "золотая": "золото",
        }.get(rkey, rarity)

        rarity_emoji = {
            "bronze": "🟫",
            "silver": "🟦",
            "gold": "🟨",
            "diamond": "🔷",
            "epic": "🔷",
        }.get(rkey, "")

        rarity_line = f"Редкость: 🏷️ {html.escape((rarity_emoji + ' ' + rarity_ru).strip())}"
    else:
        rarity_line = "Редкость: 🏷️ —"

    craft_val = auction.get("craft_uid_possible")
    if craft_val is True:
        craft_line = "Крафт на UID возможен: 🆔 ✅ Да"
    elif craft_val is False:
        craft_line = "Крафт на UID возможен: 🆔 ❌ Нет"
    else:
        craft_line = "Крафт на UID возможен: 🆔 —"

    # Подарок (obtain_type/obtain_amount)
    gift_line = "При получении в подарок даёт: 🎁 —"
    try:
        ot = str(card.get("obtain_type") or "").strip().lower()
        amt = int(card.get("obtain_amount") or 0)
        if ot and amt > 0:
            em = {"diamonds": "💎", "cups": "🍵", "treasures": "🪙"}.get(ot, "💰")
            gift_line = f"При получении в подарок даёт: 🎁 +{amt} {em}"
    except Exception:
        pass

    story = (card.get("story") or "").strip()
    quote = (card.get("quote") or "").strip()

    story_line = f"История: 📜 {html.escape(story)}" if story else "История: 📜 —"
    quote_line = f"Цитата: 💬 {html.escape(quote)}" if quote else ""

    # Коммент (если есть)
    clean_comment = _tg_clean(comment or "").strip() if comment else ""
    comment_line = f"Комментарий: 💬 {html.escape(clean_comment)}" if clean_comment else ""

    # Короткий caption под фото (чтобы не упереться в лимит)
    caption = (
        "✅ <b>Заявка отправлена на модерацию</b>\n\n"
        f"<b>Лот №{int(auction_id)}</b>\n"
        f"⚙️ Тип: {html.escape(kind_label)}\n\n"
        f"<b>{title_line}</b>\n"
        f"{(price_preview + chr(10) + chr(10)) if price_preview else ''}"
        "⏳ Дата и время: будет назначено после модерации"
    )

    # Полная инфа отдельным сообщением (чтобы и красиво, и без лимитов)
    details_lines = [
        f"👤 Статус пользователя: <b>{user_status}</b>",
        deck_line,
        rarity_line,
        craft_line,
        f"Продано ранее: 📊 <b>{int(sold_cnt)}</b>",
        gift_line,
        story_line,
    ]
    if quote_line:
        details_lines.append(quote_line)
    if comment_line:
        details_lines.append(comment_line)

    details_lines.append("Оплата ставки в течение месяца.")
    details_text = "\n".join(details_lines)

    photo_id = image_file_id or auction.get("image_id") or card.get("image_id")

    if photo_id:
        try:
            sent_ok = await _answer_media_any(
                message,
                file_id=photo_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=ReplyKeyboardRemove(),
            )
            if sent_ok:
                await message.answer(details_text, parse_mode="HTML")
                return
        except Exception:
            pass

    await message.answer(
        caption + "\n\n" + details_text,
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


async def _final_addlot_create(
    message: types.Message,
    *,
    user_id: int,
    card_id: int | None,
    hero_name: str | None,
    card_name: str | None,
    start_price: int,
    currency: str,
    accepted_currencies: list[str] | tuple[str, ...] | None,
    custom_offer_terms: str | None,
    comment: str,
    image_file_id: str | None = None,
    auction_kind: str,
    craft_uid_possible: bool | None = None,
    proof_photo_id: str | None = None,
) -> None:
    try:
        service = await AuctionCreationService.create()
        created = await service.submit(
            owner_id=user_id,
            luxury_level=await get_user_luxury_level(message.bot, user_id),
            card_id=card_id,
            hero_name=hero_name or "",
            card_name=card_name or "",
            start_price=start_price,
            currency=currency,
            accepted_currencies=accepted_currencies,
            custom_offer_terms=custom_offer_terms,
            comment=comment,
            image_id=image_file_id,
            auction_kind=auction_kind,
            proof_photo_id=proof_photo_id,
            craft_uid_possible=craft_uid_possible,
        )
        auction_id = int(created["auction_id"])
    except AuctionAccessDenied:
        await message.answer("❌ Этот тип аукциона недоступен для вашего уровня Лакшери.")
        return
    except (TypeError, ValueError) as exc:
        logging.getLogger(__name__).warning("invalid auction draft: %s", exc)
        await message.answer("❌ Не удалось проверить данные заявки. Начните создание лота заново.")
        return
    except Exception:
        logging.getLogger(__name__).exception("auction creation failed")
        await message.answer("❌ Не удалось создать заявку. Попробуйте позже.")
        return

    if not auction_id:
        await message.answer("❌ Не удалось создать заявку. Попробуйте позже.")
        return

    # 1) Лог в БД (audit_logs)
    try:
        await log_admin_action(
            user_id=user_id,
            action_type="add_lot",
            auction_id=int(auction_id),
            details=(
                f"kind={auction_kind} currency={currency} "
                f"accepted={accepted_currencies or [currency]} custom={custom_offer_terms or '-'} start={start_price} "
                f"card='{card_name or '-'}' hero='{hero_name or '-'}' comment='{comment}'"
            ),
        )
    except Exception:
        pass

    # 2) Лог в админ-лог чаты (как раньше)
    try:
        from bot.handlers.admin.helper.new.utils import auction_kind_label

        bot = message.bot
        uname = (message.from_user.username or "").strip() if message.from_user else ""
        user_ref = f"@{html.escape(uname)}" if uname else f"<code>{user_id}</code>"

        kind_label = html.escape(auction_kind_label(auction_kind))
        cur_emoji = _emoji_by_currency(currency)
        accepted_label = html.escape(
            currency_choices_label(accepted_currencies, fallback=currency, custom_terms=custom_offer_terms)
        )

        lot_title = html.escape(str(card_name or "-"))
        hero_title = html.escape(str(hero_name or ""))

        lot_line = f"🎴 Лот №{int(auction_id)}: {lot_title}"
        if hero_title:
            lot_line += f" — {hero_title}"

        craft_val = craft_uid_possible

        if craft_val is True:
            craft_txt = "✅ Да"
        elif craft_val is False:
            craft_txt = "❌ Нет"
        else:
            craft_txt = "—"

        kind_key = str(auction_kind or "").strip().lower()
        if kind_key == AuctionKind.REVERSE.value:
            price_log_line = (
                f"💱 Валюта ставок: <b>{accepted_label}</b>\n"
                f"💰 Стартовый потолок: <b>{int(start_price)} {cur_emoji}</b>\n"
                "📉 Побеждает минимальная ставка.\n"
            )
        elif kind_key == AuctionKind.FREE.value:
            price_log_line = f"💱 Принимаются предложения: <b>{accepted_label}</b>\n"
        else:
            price_log_line = f"💰 Старт: <b>{int(start_price)} {cur_emoji}</b>\n"

        log_text = (
            "🆕 <b>Новая заявка на лот</b>\n"
            f"🕒 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')} (МСК)\n"
            f"🙍‍♂️ Отправитель: {user_ref}\n"
            f"{lot_line}\n"
            f"⚙️ Тип: {kind_label}\n"
            f"{price_log_line}"
            f"🆔 Крафт на UID: {craft_txt}\n"
            f"📝 Комментарий: {_tg_clean(comment or '-')}\n"
            "Действие: add_lot через бота."
        )

        await send_admin_log(bot, log_text)
    except Exception:
        log.exception("add_lot admin-log failed")

    await _send_user_pending_lot_preview(
        message,
        auction_id=int(auction_id),
        auction_kind=auction_kind,
        start_price=start_price,
        currency=currency,
        accepted_currencies=accepted_currencies,
        custom_offer_terms=custom_offer_terms,
        image_file_id=image_file_id,
        comment=comment,
    )


@router.message(F.text.lower().in_(["отмена", "cancel", "/cancel"]))
async def cancel_any(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        USER_MESSAGES.get("action_cancelled", "Действие отменено."),
        reply_markup=ReplyKeyboardRemove(),
    )


def remove_usernames(comment: str) -> str:
    if not comment:
        return ""
    return re.sub(r"@\w+", "", comment).strip()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_subscription"
)
async def cb_subscription_menu_from_presets(call: types.CallbackQuery, state: FSMContext):
    try:
        await call.answer()
    except Exception:
        pass
    await call.message.answer(
        "Выберите тип подписки:",
        reply_markup=kb_subscription_types("user_subscription_back_presets"),
    )


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_subscription_back_presets"
)
async def cb_subscription_back_presets(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Выберите вариант:", reply_markup=kb_presets_menu())
    try:
        await call.answer()
    except Exception:
        pass


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant),
    F.data.in_(["user_subscription_gold", "user_subscription_premium"]),
)
async def user_choose_subscription(call: types.CallbackQuery, state: FSMContext):
    allowed = await _check_service_addlot_access(call, state)
    if not allowed:
        return

    plan = "gold" if call.data == "user_subscription_gold" else "premium"
    await _start_subscription_period_step(
        call,
        state,
        plan=plan,
        back_cb="user_subscription_back_presets",
    )


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_subscription),
    F.data.startswith("user_subscription_period:"),
)
async def cb_subscription_period_selected(call: types.CallbackQuery, state: FSMContext):
    try:
        _, plan, months_raw = split_callback_data(call.data, ":")
        months = int(months_raw)
    except Exception:
        await call.answer("Некорректный срок подписки.", show_alert=True)
        return

    if months not in (1, 3, 6, 12):
        await call.answer("Доступно только 1, 3, 6 или 12 месяцев.", show_alert=True)
        return

    service = f"subscription_{plan}"
    media_id = _service_media_file_id(service)

    await state.update_data(
        subscription_plan=plan,
        subscription_months=months,
        card_name=_subscription_title(plan, months),
        service=service,
        image_id=media_id,
        image_file_id=media_id,
    )

    await _ask_for_currency(call.message, state)

    try:
        await call.answer()
    except Exception:
        pass


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_subscription),
    F.data == "user_subscription_back_decks",
)
async def cb_subscription_period_back_decks(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer(
        "Выберите тип подписки:",
        reply_markup=kb_subscription_types("user_subscription_back_decks"),
    )
    await state.set_state(UserAddLotFSM.waiting_for_deck)
    try:
        await call.answer()
    except Exception:
        pass


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_subscription),
    F.data == "user_subscription_back_presets",
)
async def cb_subscription_period_back_presets(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer(
        "Выберите тип подписки:",
        reply_markup=kb_subscription_types("user_subscription_back_presets"),
    )
    await state.set_state(UserAddLotFSM.waiting_for_own_variant)
    try:
        await call.answer()
    except Exception:
        pass


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_friends_plus"
)
async def user_choose_friends_plus(call: types.CallbackQuery, state: FSMContext):
    media_id = _service_media_file_id("friends_plus")
    await state.update_data(
        card_id=None,
        card_name="Друзья+",
        service="friends_plus",
        image_id=media_id,
        image_file_id=media_id,
    )
    await _ask_for_currency(call.message, state)
    await call.answer()


@router.callback_query(
    StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_progress_slots"
)
async def user_choose_progress_slots(call: types.CallbackQuery, state: FSMContext):
    media_id = _service_media_file_id("progress_slots")
    await state.update_data(
        card_id=None,
        card_name="Слоты прогресса",
        service="progress_slots",
        image_id=media_id,
        image_file_id=media_id,
    )
    await _ask_for_currency(call.message, state)
    await call.answer()
