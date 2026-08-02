from __future__ import annotations
from bot.telegram.callback_parser import split_callback_data

"""Exchange flow component extracted during refactoring phase 7."""

import html
import logging
from datetime import datetime, timezone
from html import escape
from aiogram import Bot, F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.handlers.admin.action_support.compat import safe_user_mention, send_admin_log
from bot.handlers.admin.services.market_utils import safe_edit_text
from bot.services.exchanges import ExchangeService
from bot.use_cases.common import ApplicationError
from bot.use_cases.exchange_submission import SubmitExchangeCommand, SubmitExchangeUseCase
from bot.services.exchange_submission import ExchangeSubmissionQueries
from bot.telegram.media import answer_media_any as _answer_media_any
from bot.services.handler_persistence import (
    get_card_by_id,
    get_cards_by_ids,
    get_cards_ids_by_deck,
    get_deck_by_id,
    is_luxury_user,
)
from bot.legacy_fsm import ExchangeFSM, UserAddLotFSM

router = Router(name="auction_exchange_submission")
log = logging.getLogger(__name__)

from .common import (
    GUIDE_UID_CRAFT_PHOTO_ID,
    GUIDE_UID_CRAFT_TEXT,
    cur_emoji,
    deck_price_for_deck,
    digits_int,
    exchange_cards_kb,
    exchange_deck_cover_id,
    exchange_gain_for_card,
    exchange_key_for_card,
    exchange_price_for_card,
    fmt_dt_msk,
    format_gain_line,
    get_exchange_deck_ids,
    gift_emoji,
    load_full_cards_for_deck,
    normalize_card_ids,
    sum_gains,
    compute_start_price_limits,
    exchange_copies_keyboard,
    exchange_mode_keyboard,
    tg_clean,
)

from .notifications import (
    send_user_exchange_confirmation,
    send_user_exchange_confirmation_copies,
    send_user_exchange_confirmation_deck_split,
)

@router.callback_query(ExchangeFSM.waiting_for_deck, F.data.startswith("ex_deck:"))
async def ex_deck_selected(call: types.CallbackQuery, state: FSMContext) -> None:
    deck_id = int(split_callback_data(call.data, ":", 1)[1])
    if deck_id not in await get_exchange_deck_ids():
        await call.answer("Эта колода недоступна для биржи.", show_alert=True)
        return

    await state.update_data(ex_deck_id=deck_id)
    await state.set_state(ExchangeFSM.waiting_for_mode)

    await call.message.answer(
        f"🛒 Биржа\nКолода {deck_id}. Что выставляем?",
        reply_markup=exchange_mode_keyboard()
    )
    await call.answer()


def _format_exchange_cards_list(full_cards: list[dict]) -> str:
    lines: list[str] = []
    for i, c in enumerate(full_cards, start=1):
        hero = (c.get("hero_name") or "—").strip()
        card = (c.get("card_name") or "—").strip()
        rarity = (c.get("rarity") or "").strip()
        rarity_txt = f" • {rarity}" if rarity else ""
        lines.append(f"{i}. {hero} — {card}{rarity_txt}")
    return "\n".join(lines)


def _kb_exchange_cards_numbers(full_cards: list[dict]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i, c in enumerate(full_cards, start=1):
        card_id = c.get("card_id")
        if card_id is None:
            continue
        b.button(text=str(i), callback_data=f"ex_card:{int(card_id)}")
    b.adjust(5)  # 5 кнопок в ряд
    return b.as_markup()


@router.callback_query(ExchangeFSM.waiting_for_mode, F.data.startswith("ex_mode:"))
@router.callback_query(ExchangeFSM.waiting_for_card, F.data.startswith("ex_mode:"))
async def ex_mode_selected(call: CallbackQuery, state: FSMContext):
    """
    Выбор режима биржи: карточка / колода / сплит-колода.
    Здесь же сохраняем card_ids и фикс. стоимость в state.
    """
    data_raw = (call.data or "").strip()

    # payload после ":" или "|"
    if ":" in data_raw:
        _, mode = data_raw.split(":", 1)
    else:
        parts = data_raw.split("|", 1)
        mode = parts[1] if len(parts) == 2 else "card"

    mode = (mode or "card").strip() or "card"

    st = await state.get_data()
    deck_id = st.get("ex_deck_id") or st.get("deck_id")
    if not deck_id:
        await state.clear()
        await safe_edit_text(call.message, "⚠️ Не смог определить колоду. Попробуй заново.")
        await call.answer()
        return

    try:
        deck_id_i = int(deck_id)
    except (TypeError, ValueError):
        await state.clear()
        await safe_edit_text(call.message, "⚠️ Колода указана некорректно. Попробуй заново.")
        await call.answer()
        return

    # 1) Одна карта
    if mode == "card":
        await state.update_data(exchange_kind="card", mode="card")

        cards = await load_full_cards_for_deck(deck_id_i)
        if not cards:
            await state.clear()
            await safe_edit_text(call.message, "⚠️ В этой колоде нет карт. Попробуй заново.")
            await call.answer()
            return

        await state.update_data(ex_cards_cache=cards)
        await state.set_state(ExchangeFSM.waiting_for_card)

        kb = exchange_cards_kb(cards, deck_id=deck_id_i)
        await safe_edit_text(
            call.message,
            "Выберите карту или «Вся колода»:",
            reply_markup=kb,
        )
        await call.answer()
        return

    # 2) Колода / Сплит (берём все карты колоды)
    try:
        full_cards = await load_full_cards_for_deck(deck_id_i)
        if not full_cards:
            await state.clear()
            await safe_edit_text(call.message, "⚠️ Не нашёл карты этой колоды. Попробуй заново.")
            await call.answer()
            return

        card_ids: list[int] = [
            int(c["card_id"])
            for c in full_cards
            if c and c.get("card_id") is not None
        ]

        # фикс. цена: считаем детерминированно
        if mode == "deck":
            price_i = int(await deck_price_for_deck(deck_id_i))  # ВАЖНО: await
            title = "🛒 Биржа"
        else:  # deck_split
            price_i = int(sum(exchange_price_for_card(c) for c in full_cards))
            title = "🛒 Биржа (Сплит)"

        # ✅ правильный профит (по всем картам и с учётом типа валют)
        diamonds_sum, cups_sum, treasures_sum = sum_gains(full_cards)
        gain_line = format_gain_line(diamonds_sum, cups_sum, treasures_sum)

        await state.update_data(
            exchange_kind=mode,
            mode=mode,
            split_mode=("per_card" if mode == "deck_split" else "many_in_one"),
            ex_card_ids=card_ids,
            ex_price=price_i,
            ex_price_diamonds=price_i,
            # чтобы не ломать старые места (если где-то есть), храним алмазы отдельно
            ex_gain=int(diamonds_sum),
            ex_gain_line=gain_line,
            currency="алмазы",
        )
        await state.set_state(ExchangeFSM.waiting_for_comment)

        deck_title = f"{deck_id_i} колода"
        try:
            d = await get_deck_by_id(int(deck_id_i))
            nm = (d.get("name") or "").strip() if d else ""
            if nm:
                deck_title = nm if nm.lower().startswith(str(int(deck_id_i))) else f"{deck_id_i} колода — {nm}"
        except Exception:
            pass

        text_to_user = (
            f"{title}\n"
            f"Колода: {deck_title} ({len(full_cards)} карт)\n"
            f"Стоимость: {price_i} 💎 (фикс.)\n"
            f"Колода даёт: {gain_line}\n\n"
            "Комментарий (если не нужен, отправь 0):"
        )

        # ✅ заставка по колоде 16/18/20
        cover_id = await exchange_deck_cover_id(deck_id_i)

        sent = None
        if cover_id:
            try:
                sent = await _answer_media_any(call.message, cover_id, caption=text_to_user, reply_markup=None)
            except Exception:
                sent = None

        if not sent:
            await safe_edit_text(call.message, text_to_user)

        await call.answer()
        return

    except Exception:
        await state.clear()
        await safe_edit_text(call.message, "⚠️ Ошибка при расчёте стоимости. Попробуй заново.")
        await call.answer()
        return


@router.callback_query(
    ExchangeFSM.waiting_for_card,
    (F.data.startswith("ex_card:") | F.data.startswith("ex_card|")),
)
async def ex_card_selected(call: CallbackQuery, state: FSMContext):
    data_raw = (call.data or "").strip()

    if ":" in data_raw:
        _, card_id_s = data_raw.split(":", 1)
    else:
        _, card_id_s = data_raw.split("|", 1)

    try:
        card_id = int(card_id_s)
    except Exception:
        await call.answer("Некорректный card_id.", show_alert=True)
        return

    card = await get_card_by_id(card_id)
    if not card:
        await call.answer("Карта не найдена.", show_alert=True)
        return

    try:
        price = int(exchange_price_for_card(card))
        _t, amt = exchange_gain_for_card(card)
        gift = int(amt or 0)
    except Exception:
        await state.clear()
        await call.message.answer("⚠️ Ошибка при выборе карты. Попробуй заново.")
        await call.answer()
        return

    await state.update_data(
        exchange_kind="card",
        mode="card",
        split_mode="one",
        copies=1,
        ex_card_id=card_id,
        ex_card_ids=[card_id],
        ex_price=price,
        ex_price_diamonds=int(price),
        ex_gain=gift,
        currency="алмазы",
    )

    hero = escape((card.get("hero_name") or "").strip(), quote=False)
    name = escape((card.get("card_name") or "").strip(), quote=False)

    await call.message.answer(
        "🛒 <b>Биржа</b>\n"
        f"Карта: <b>{hero} — {name}</b>\n"
        f"Стоимость: <b>{price}</b> 💎 (фикс.)\n"
        f"Карта даёт: <b>+{gift}</b> 💎\n\n"
        "Сколько таких карт выставляем?",
        parse_mode="HTML",
        reply_markup=exchange_copies_keyboard(),
    )
    await state.set_state(ExchangeFSM.waiting_for_copies)
    await call.answer()


@router.message(ExchangeFSM.waiting_for_card)
async def ex_card_by_number(message: Message, state: FSMContext):
    t = (message.text or "").strip()
    if not t.isdigit():
        return

    card_id = int(t)
    card = await get_card_by_id(card_id)
    if not card:
        await message.answer("⚠️ Карта не найдена. Выбери кнопкой из списка.")
        return

    price = exchange_price_for_card(card)
    gain_type, gain_amount = exchange_gain_for_card(card)
    emoji = gift_emoji(gain_type)

    await state.update_data(
        mode="card",
        exchange_kind="card",
        split_mode="one",
        copies=1,
        ex_card_ids=[card_id],
        ex_price=int(price),
        ex_price_diamonds=int(price),
        ex_gain=int(gain_amount),
        ex_gift=(gain_type, int(gain_amount)),
        currency="алмазы",
    )

    hero = escape((card.get("hero_name") or "—").strip(), quote=False)
    name = escape((card.get("card_name") or "—").strip(), quote=False)

    await message.answer(
        "🛒 <b>Биржа</b>\n"
        f"Карта: <b>{hero} — {name}</b>\n"
        f"Стоимость (фикс.): <b>{int(price)}</b> 💎\n"
        f"Карта даёт: <b>+{int(gain_amount)}</b> {gift_emoji(gain_type)}\n\n"
        "Сколько таких карт выставляем?",
        parse_mode="HTML",
        reply_markup=exchange_copies_keyboard(),
    )
    await state.set_state(ExchangeFSM.waiting_for_copies)


_RARITY_EMOJI = {
    "эпик": "💠",
    "легендар": "👑",
    "редк": "🔷",
    "обыч": "🔹",
    "ивент": "🎟️",
}


def _safe_int(v: object, default: int = 0) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except Exception:
        return default


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_res_diamonds_for_tea")
async def user_preset_res_diamonds_for_tea(call: types.CallbackQuery, state: FSMContext):
    # “получаем 💎, платят 🍵”
    await state.update_data(
        service=None,
        deck_type="resource",
        rarity="any",
        forced_obtain_type="алмазы",  # для потолков RESOURCE_CAP_BY_OBTAIN["алмазы"]
        currency="чашки",
        card_id=None,
        card_name="Ресурсная карта (💎 за 🍵)",
        hero_name="Ресурсная карта",
    )

    min_allowed, max_allowed, hint = await compute_start_price_limits(state, "🍵")
    await state.update_data(min_start=min_allowed, max_start=max_allowed)
    await call.message.answer(
        f"Формат: <b>алмазы за чай</b>\n"
        f"Допустимая стартовая цена: <b>{min_allowed}–{max_allowed} 🍵</b>\n"
        f"({hint})\n\n"
        f"Введите стартовую цену:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(UserAddLotFSM.waiting_for_start_price)
    await call.answer()


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_own_variant), F.data == "user_res_tea_for_diamonds")
async def user_preset_res_tea_for_diamonds(call: types.CallbackQuery, state: FSMContext):
    # “получаем 🍵, платят 💎”
    await state.update_data(
        service=None,
        deck_type="resource",
        rarity="any",
        forced_obtain_type="чашки",  # для потолков RESOURCE_CAP_BY_OBTAIN["чашки"]
        currency="алмазы",
        card_id=None,
        card_name="Ресурсная карта (🍵 за 💎)",
        hero_name="Ресурсная карта",
    )

    min_allowed, max_allowed, hint = await compute_start_price_limits(state, "💎")
    await state.update_data(min_start=min_allowed, max_start=max_allowed)
    await call.message.answer(
        f"Формат: <b>чай за алмазы</b>\n"
        f"Допустимая стартовая цена: <b>{min_allowed}–{max_allowed} 💎</b>\n"
        f"({hint})\n\n"
        f"Введите стартовую цену:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(UserAddLotFSM.waiting_for_start_price)
    await call.answer()


async def _db_create_exchange_batch(
        *,
        user_id: int,
        username: str,  # можно не использовать, но оставим сигнатуру как у тебя
        deck_id: int,
        mode: str,
        card_ids: list[int],
        price: int,
        currency: str,
        comment: str,
        proof_photo_id: str,
) -> int:
    """
    Создаёт batch в БД и привязывает к нему список card_ids.
    Возвращает batch_id.
    """
    service = await ExchangeService.create()
    batch = await service.submit(
        user_id=int(user_id),
        deck_id=int(deck_id),
        mode=(mode or "card").strip() or "card",
        currency=(currency or "алмазы").strip() or "алмазы",
        price=int(price or 0),
        comment=(comment or "-").strip() or "-",
        proof_photo_id=(proof_photo_id or "NO_PROOF").strip() or "NO_PROOF",
        card_ids=[int(cid) for cid in (card_ids or [])],
    )
    return int(batch["batch_id"])


async def _finalize_exchange_request(
        message: Message,
        state: FSMContext,
        bot: Bot,
        proof_photo_id: str | None = None,
) -> None:
    data = await state.get_data()
    user_id = int(message.from_user.id)
    deck_id = data.get("ex_deck_id") or data.get("deck_id")
    if not deck_id:
        await state.clear()
        await message.answer("⚠️ Не смог определить колоду. Попробуй заново.")
        return
    deck_id_i = int(deck_id)
    mode = (data.get("mode") or data.get("exchange_kind") or "card").strip() or "card"
    currency = (data.get("currency") or "алмазы").strip()
    comment = ((data.get("ex_comment") or "") or (data.get("comment") or "")).strip()
    split_mode = (data.get("split_mode") or ("per_card" if mode == "deck_split" else "one")).strip()
    copies = max(1, min(int(data.get("copies") or 1), 20))
    proof_photo_id = (proof_photo_id or "").strip() or "NO_PROOF"
    card_ids = normalize_card_ids(data.get("ex_card_ids") or data.get("card_ids"))
    if not card_ids and data.get("ex_card_id"):
        card_ids = [int(data["ex_card_id"])]

    service = await ExchangeService.create()
    use_case = SubmitExchangeUseCase(
        get_card_ids_by_deck=get_cards_ids_by_deck,
        get_cards=get_cards_by_ids,
        price_for_card=exchange_price_for_card,
        price_for_deck=deck_price_for_deck,
        submit_many=service.submit_many,
    )
    try:
        result = await use_case.execute(
            SubmitExchangeCommand(
                user_id=user_id,
                deck_id=deck_id_i,
                mode=mode,
                currency=currency,
                comment=comment,
                proof_photo_id=proof_photo_id,
                card_ids=tuple(card_ids),
                split_mode=split_mode,
                copies=copies,
                explicit_price=digits_int(data.get("ex_price") or data.get("ex_price_diamonds") or 0),
            )
        )
    except ApplicationError as exc:
        await state.clear()
        await message.answer(f"⚠️ {exc}")
        return
    except Exception:
        await state.clear()
        log.exception("exchange submission failed")
        await message.answer("⚠️ Не удалось создать заявку биржи. Попробуй позже.")
        return

    full_cards = list(result.cards)
    by_id = {int(card["card_id"]): card for card in full_cards}

    async def _send_exchange_log_one(batch_id: int, *, items_count: int, price: int) -> None:
        try:
            deck_name = await (await ExchangeSubmissionQueries.create()).deck_name(deck_id_i)
        except Exception:
            deck_name = None
        log_text = format_exchange_new_request_log(
            batch_id=int(batch_id),
            created_at_msk=fmt_dt_msk(datetime.now(timezone.utc)),
            sender_username=message.from_user.username,
            sender_id=message.from_user.id,
            deck_id=deck_id_i,
            deck_name=deck_name,
            mode=mode,
            items_count=int(items_count),
            price=int(price),
            currency=currency,
            has_proof=str(proof_photo_id).upper() != "NO_PROOF",
            comment=comment,
        )
        try:
            await send_admin_log(message.bot, log_text)
        except Exception:
            log.exception("Could not deliver exchange submission log for batch %s", batch_id)

    for item in result.items:
        await _send_exchange_log_one(
            item.batch_id, items_count=len(item.card_ids), price=item.price
        )

    if split_mode == "per_card" or mode == "deck_split":
        created = [
            (item.batch_id, by_id[item.card_ids[0]], item.price)
            for item in result.items
        ]
        await send_user_exchange_confirmation_deck_split(
            message, created=created, user_id=user_id, deck_id=deck_id_i
        )
    elif len(result.items) > 1 and len(result.items[0].card_ids) == 1:
        first = result.items[0]
        await send_user_exchange_confirmation_copies(
            message,
            batch_ids=[item.batch_id for item in result.items],
            user_id=user_id,
            card=by_id[first.card_ids[0]],
            price=first.price,
            currency=currency,
            comment=comment,
            deck_id=deck_id_i,
        )
    else:
        item = result.items[0]
        await send_user_exchange_confirmation(
            message,
            batch_id=item.batch_id,
            user_id=user_id,
            cards=full_cards,
            price=item.price,
            currency=currency,
            comment=comment,
            deck_id=deck_id_i,
        )
    await state.clear()


def format_exchange_new_request_log(*,
                                    batch_id: int,
                                    created_at_msk: str,
                                    sender_username: str | None,
                                    sender_id: int | None,
                                    deck_id: int | None,
                                    deck_name: str | None,
                                    mode: str,
                                    items_count: int,
                                    price: int | None,
                                    currency: str,
                                    has_proof: bool,
                                    comment: str | None) -> str:
    # отправитель
    if sender_id:
        sender = safe_user_mention(sender_id, sender_username)
    else:
        sender = f"@{sender_username}" if sender_username else "—"

    deck_title = (deck_name or "").strip()
    deck_part = deck_title if deck_title else (f"{deck_id}" if deck_id else "—")

    cur_print = (currency or "алмазы").strip()
    cur = cur_print.lower()
    currency_icon = cur_emoji(cur)

    mode_key = (mode or "").strip().lower()
    mode_lbl = {
        "card": "Одна карта",
        "deck": "Колода целиком",
        "deck_split": "Разбор колоды",
    }.get(mode_key, (mode or "—"))

    proof_line = "✅ Да" if has_proof else "❌ Нет"
    price_line = f"{int(price)} {currency_icon} ({cur_print})" if price is not None else f"— {currency_icon} ({cur_print})"

    cmt = (comment or "").strip()
    if not cmt:
        cmt = "-"

    return (
        "🛒 <b>Новая заявка на биржу</b>\n"
        f"🕒 {created_at_msk} (МСК)\n"
        f"👤 Отправитель: {sender}\n"
        f"🆔 Batch: <code>{batch_id}</code>\n\n"
        f"📚 Колода: <b>{tg_clean(str(deck_part))}</b>\n"
        f"🎛 Режим: <b>{tg_clean(mode_lbl)}</b>\n"
        f"🃏 Карт: <b>{items_count}</b>\n"
        f"💰 Цена: <b>{tg_clean(price_line)}</b>\n"
        f"📸 Пруф: <b>{proof_line}</b>\n"
        f"💬 Комментарий: <i>{tg_clean(cmt)}</i>\n\n"
        "Действие: <code>exchange_add_request</code>"
    )


@router.message(ExchangeFSM.waiting_for_comment)
async def ex_comment_input(message: Message, state: FSMContext, bot: Bot) -> None:
    text = (message.text or "").strip()
    text_low = text.lower()

    exit_texts = {"🏠 меню", "меню", "/start", "🛒 биржа", "биржа", "📦 аукцион", "аукцион"}
    if text_low in exit_texts:
        await state.clear()
        await message.answer("Ок, выхожу из оформления заявки биржи.")
        return

    # Команды не жрём FSM-ом
    if text.startswith("/"):
        raise SkipHandler()

    # "0" = без комментария
    if text == "0":
        text = ""

    await state.update_data(ex_comment=text)

    # лакшери = без пруфа
    user_id = int(message.from_user.id)
    try:
        lux = bool(await is_luxury_user(user_id))
    except Exception:
        lux = False

    if lux:
        await _finalize_exchange_request(message, state, bot, proof_photo_id="NO_PROOF")
        return

    await ex_request_proof(message, state)


_EXIT_TEXTS = {
    "🏠 меню",
    "меню",
    "/start",
    "🛒 биржа",
    "биржа",
    "📦 аукцион",
    "аукцион",
}


async def ex_request_proof(message: Message, state: FSMContext) -> None:
    """
    Переводит пользователя на шаг пруфа.
    """
    await state.set_state(ExchangeFSM.waiting_for_proof)
    await message.answer(
        "📸 Пришли, пожалуйста, <b>пруф</b> (фото).\n"
        "Если передумал — /cancel",
        parse_mode="HTML",
    )


@router.message(ExchangeFSM.waiting_for_proof)
async def ex_proof_any(message: Message, state: FSMContext, bot: Bot) -> None:
    proof_photo_id: str | None = None

    # фото
    if message.photo:
        proof_photo_id = message.photo[-1].file_id

    # документ (скрин)
    elif message.document:
        proof_photo_id = message.document.file_id

    # видео
    elif message.video:
        proof_photo_id = message.video.file_id

    # гиф/анимация
    elif message.animation:
        proof_photo_id = message.animation.file_id

    # текст
    else:
        t = (message.text or "").strip().lower()
        if t in {"0", "нет", "-", "skip", "пропуск"}:
            proof_photo_id = "NO_PROOF"
        else:
            await message.answer(
                "Нужно <b>фото/скрин</b> пруфа или <b>0</b> (если пруфа нет).",
                parse_mode="HTML",
            )
            return

    await _finalize_exchange_request(message, state, bot, proof_photo_id=proof_photo_id)


def _sum_exchange_prices(cards: list[dict]) -> tuple[int, list[tuple[tuple[str, str, int], int, str, str]]]:
    """
    Сумма фикс-цен (в 💎) по списку карт.
    missing: [(exchange_key, card_id, hero_name, card_name), ...] для карт, где цена не определилась.
    """
    total = 0
    missing: list[tuple[tuple[str, str, int], int, str, str]] = []

    for card in cards or []:
        price = int(exchange_price_for_card(card) or 0)
        if price > 0:
            total += price
            continue

        key = exchange_key_for_card(card)
        cid = int(card.get("card_id") or 0)
        hero = str(card.get("hero_name") or "").strip()
        name = str(card.get("card_name") or "").strip()
        missing.append((key, cid, hero, name))

    return int(total), missing


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_craft_uid), F.data == "craft_uid:help")
async def addlot_craft_uid_help(call: CallbackQuery):
    await call.answer()

    await call.message.answer_photo(
        GUIDE_UID_CRAFT_PHOTO_ID,
        caption="🆔 <b>Гайд</b>: крафт по UID",
        parse_mode="HTML",
    )
    await call.message.answer(
        GUIDE_UID_CRAFT_TEXT,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(StateFilter(UserAddLotFSM.waiting_for_craft_uid), F.data.startswith("craft_uid:"))
async def addlot_craft_uid_answer(call: CallbackQuery, state: FSMContext):
    raw = split_callback_data(call.data or "", ":", 1)[-1].strip().lower()
    craft_ok = raw in {"yes", "1", "true", "да"}

    await state.update_data(craft_uid_possible=craft_ok)

    # убираем кнопки, чтобы не тыкали повторно
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    d = await state.get_data()
    currency = d.get("currency", "алмазы")
    emoji = cur_emoji(currency)

    craft_text = "✅ Да" if craft_ok else "❌ Нет"
    comment = (d.get("comment") or "").strip()

    preview = (
        f"<b>Лот:</b> {html.escape(str(d.get('card_name') or '-'))}\n"
        f"Валюта: {emoji}\n"
        f"Минимальная ставка: {d.get('start_price')} {emoji}\n"
        f"Крафт на UID: {craft_text}\n"
        f"Комментарий: {html.escape(comment or '-')}\n"
        "Всё верно? Отправить заявку на модерацию?"
    )

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Подтвердить"), KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await call.message.answer(preview, reply_markup=kb, parse_mode="HTML")
    await state.set_state(UserAddLotFSM.waiting_for_confirmation)
    await call.answer()


@router.callback_query(ExchangeFSM.waiting_for_copies, F.data.startswith("ex_copies:"))
async def ex_copies_selected(call: CallbackQuery, state: FSMContext) -> None:
    payload = split_callback_data(call.data or "", ":", 1)[1].strip()

    if payload == "other":
        await call.message.answer("Введи число (например 2). Минимум 1, максимум 50.")
        await call.answer()
        return

    try:
        copies = int(payload)
    except Exception:
        await call.answer("Некорректное число.", show_alert=True)
        return

    copies = max(1, min(copies, 50))
    await state.update_data(copies=copies)

    st = await state.get_data()
    price = int(st.get("ex_price") or st.get("ex_price_diamonds") or 0)

    await state.set_state(ExchangeFSM.waiting_for_comment)
    await call.message.answer(
        "🛒 <b>Биржа</b>\n"
        f"Экземпляров: <b>{copies}</b>\n"
        f"Стоимость (фикс.) за 1: <b>{price}</b> 💎\n\n"
        "Комментарий (если не нужен, отправь 0):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await call.answer()


@router.message(ExchangeFSM.waiting_for_copies)
async def ex_copies_input(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    text_low = text.lower()

    # выход
    if text_low in {"🏠 меню", "меню", "/start", "🛒 биржа", "биржа", "📦 аукцион", "аукцион"}:
        await state.clear()
        await message.answer("Ок, выхожу из оформления заявки биржи.")
        return

    # команды не жрём FSM-ом
    if text.startswith("/"):
        raise SkipHandler()

    if not text.isdigit():
        await message.answer("Нужно число. Например: 2")
        return

    copies = int(text)
    copies = max(1, min(copies, 50))
    await state.update_data(copies=copies)

    st = await state.get_data()
    price = int(st.get("ex_price") or st.get("ex_price_diamonds") or 0)

    await state.set_state(ExchangeFSM.waiting_for_comment)
    await message.answer(
        "🛒 <b>Биржа</b>\n"
        f"Экземпляров: <b>{copies}</b>\n"
        f"Стоимость (фикс.) за 1: <b>{price}</b> 💎\n\n"
        "Комментарий (если не нужен, отправь 0):",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
