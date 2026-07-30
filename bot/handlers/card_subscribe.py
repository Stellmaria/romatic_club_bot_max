# src/app/bot/handlers/users/card_subscribe.py
from __future__ import annotations

import asyncio
import html
from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.filters import Command

from bot.auction_notify import _extract_gifts
from bot.handlers.admin.services.market_utils import safe_edit_reply_markup
from bot.handlers.helper.helpers_card_subscribe import (
    subscribe_to_card,
    get_subscriptions,
    unsubscribe_from_card,
)
from db.legacy import (
    get_all_decks,
    get_cards_by_deck,
    list_my_preset_subs,
    fetch,
    execute,
)
from bot.legacy_fsm import CardSubscribeFSM
from bot.services.card_subscriptions import CardSubscriptionsService


# ======================
# Helpers
# ======================
async def tg_retry(callable_coro, *, attempts: int = 3, base_delay: float = 0.8):
    last_exc = None
    for i in range(attempts):
        try:
            return await callable_coro()
        except TelegramNetworkError as e:
            last_exc = e
            await asyncio.sleep(base_delay * (2 ** i))
    raise last_exc

def _rarity_emoji(val: str | None) -> str:
    r = (val or "").strip().lower()
    mapping = {
        # русские прилагательные
        "бронзовая": "🥉", "серебряная": "🥈", "золотая": "🥇",
        "алмазная": "💎", "алмазный": "💎", "алмазные": "💎",
        # русские существительные
        "бронза": "🥉", "серебро": "🥈", "золото": "🥇",
        "алмазы": "💎", "алмаз": "💎",
        # английские
        "bronze": "🥉", "silver": "🥈", "gold": "🥇",
        "diamond": "💎", "diamonds": "💎",
    }
    return mapping.get(r, "")


def _gift_badge(card: dict) -> str:
    cups, dias = _extract_gifts(card)
    bits: list[str] = []
    if cups > 0:
        bits.append(f"☕{cups}")
    if dias > 0:
        bits.append(f"💎{dias}")
    return " ".join(bits)


def _label_limit(s: str, max_len: int = 64) -> str:
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _make_card_btn_label(card: dict) -> str:
    """
    Приоритет: полное имя героя → эмодзи редкости → бейдж подарков.
    Если длинно, режем сначала подарки, потом эмодзи; имя не трогаем.
    """
    hero = (card.get("hero_name") or card.get("card_name") or "").strip() or f"#{card.get('card_id')}"
    r_emoji = _rarity_emoji(card.get("rarity") or card.get("tier") or card.get("nominal"))
    gifts = _gift_badge(card)

    label = " ".join(p for p in (hero, r_emoji, gifts) if p)
    if len(label) > 34 and gifts:
        label = " ".join(p for p in (hero, r_emoji) if p)
    if len(label) > 40 and r_emoji:
        label = hero
    return _label_limit(label, 64)


def _extract_subscribed_card_ids(subs: List[Dict[str, Any]]) -> Set[int]:
    out: Set[int] = set()
    for s in subs or []:
        try:
            cid = int(s.get("card_id"))
            out.add(cid)
        except Exception:
            continue
    return out


def _short(text: str, limit: int = 26) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _cards_keyboard(cards: list[dict], subscribed: set[int], deck_id: int) -> InlineKeyboardMarkup:
    items: list[tuple[str, int]] = []
    for c in cards:
        cid = int(c["card_id"])
        if cid in subscribed:
            continue
        items.append((_make_card_btn_label(c), cid))

    kb = InlineKeyboardBuilder()
    if not items:
        kb.row(InlineKeyboardButton(text="✅ Все карты этой колоды уже подписаны", callback_data="noop"))
    else:
        # формируем строки жадно: для каждой подписи решаем ширину ряда
        row: list[InlineKeyboardButton] = []
        current_width = 0

        def flush():
            nonlocal row, current_width
            if row:
                kb.row(*row)
                row = []
                current_width = 0

        for label, cid in items:
            L = len(label)
            # очень длинные — по одной в ряд
            if L >= 24:
                flush()
                kb.row(InlineKeyboardButton(text=label, callback_data=f"sub:card:{cid}"))
                continue
            # если уже 2 в строке или текущая ширина выходит — перенос
            if len(row) >= 2 and current_width + L > 18:
                flush()
            # компакт по 3 в ряд там, где реально коротко
            if len(row) >= 3:
                flush()
            row.append(InlineKeyboardButton(text=label, callback_data=f"sub:card:{cid}"))
            current_width += L
        flush()

    kb.row(InlineKeyboardButton(text=f"🗂 Вся колода {deck_id}", callback_data=f"sub:preset:deck_all_{deck_id}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад к колодам", callback_data="sub:back_decks"))
    return kb.as_markup()


import re

BASE_PRESET_ORDER = {
    "any_card": 10,
    "any_deck": 20,
    "any_bronze": 30,
    "any_silver": 40,
    "any_gold": 50,
    "any_diamond": 60,

    "friends_plus": 100,
    "progress_slots": 110,
    "spins_10": 120,
    "spins_50": 130,
    "spins_100": 140,

    "subscription_gold_1": 150,
    "subscription_gold_3": 151,
    "subscription_gold_6": 152,
    "subscription_gold_12": 153,

    "subscription_premium_1": 160,
    "subscription_premium_3": 161,
    "subscription_premium_6": 162,
    "subscription_premium_12": 163,
}


def _preset_sort_key(item: dict) -> tuple[int, str]:
    key = str(item.get("key") or "")
    title = str(item.get("title") or "")

    if key in BASE_PRESET_ORDER:
        return BASE_PRESET_ORDER[key], title

    m = re.fullmatch(r"deck_all_(\d+)", key)
    if m:
        return 1000 + int(m.group(1)), title

    return 999999, title


async def _fetch_all_presets_rows() -> List[Dict[str, Any]]:
    items = await (await CardSubscriptionsService.from_runtime()).list_presets()
    items.sort(key=_preset_sort_key)
    return items


async def _presets_manage_keyboard(user_id: int, back: str) -> InlineKeyboardMarkup:
    presets = await _fetch_all_presets_rows()
    my = await list_my_preset_subs(user_id)
    have = {s["key"] for s in my}

    kb = InlineKeyboardBuilder()
    for p in presets:
        key = p["key"]
        title = p["title"]
        if key in have:
            kb.row(
                InlineKeyboardButton(text=f"✅ {title}", callback_data=f"sub:preset:{key}"),
                InlineKeyboardButton(text="❌", callback_data=f"sub:preset_unsub:{key}"),
            )
        else:
            kb.row(InlineKeyboardButton(text=title, callback_data=f"sub:preset:{key}"))

    if back == "cards":
        kb.row(InlineKeyboardButton(text="⬅️ Назад к картам", callback_data="sub:back_cards"))
    else:
        kb.row(InlineKeyboardButton(text="⬅️ Назад к колодам", callback_data="sub:back_decks"))
    return kb.as_markup()


async def _toggle_preset(user_id: int, key: str) -> Tuple[bool, str]:
    """
    Тумблер подписки на пресет по ключу.
    Возвращает (is_on_now, toast_message).
    """
    return await (await CardSubscriptionsService.from_runtime()).toggle_preset(user_id, key)


def _decks_keyboard(decks: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """
    Меню выбора колоды:
    • каждая кнопка: 'Колода {id} — {название}'
    • ниже: 'Подписка на категорию любой карты' и 'Мои подписки'
    """
    kb = InlineKeyboardBuilder()
    for d in decks:
        # пытаемся быть терпимыми к разным схемам
        did = d.get("deck_id") or d.get("id") or d.get("deckId")
        deck_id = int(did)
        name = (
                d.get("deck_name")
                or d.get("name")
                or f"Колода {deck_id}"
        )
        label = f"Колода {deck_id} — {name}"
        kb.button(text=label, callback_data=f"admin_deck_{deck_id}")
    kb.adjust(1)
    kb.row(
        InlineKeyboardButton(
            text="🔔 Пресеты уведомлений",
            callback_data="sub:presets_open"
        )
    )
    kb.row(InlineKeyboardButton(text="🧾 Мои подписки", callback_data="sub:list"))
    return kb.as_markup()


# --- PUBLIC: старт мастера подписки (вызывается из /start subs и по команде) ---
from aiogram import types
from aiogram.fsm.context import FSMContext


async def start_subscribe_card(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    decks = await get_all_decks()
    if not decks:
        await message.answer("Пока нет доступных колод.")
        return
    await message.answer("Выбери колоду для подписки:", reply_markup=_decks_keyboard(decks))
    await state.set_state(CardSubscribeFSM.waiting_for_deck)


async def safe_call_answer(
        call: CallbackQuery,
        text: str | None = None,
        show_alert: bool = False,
):
    try:
        await call.answer(text=text or None, show_alert=show_alert)
    except TelegramBadRequest as e:
        msg = str(e)
        # Игнорируем именно "query is too old / query ID is invalid"
        if "query is too old" in msg or "query ID is invalid" in msg:
            return
        # Всё остальное пусть падает, чтобы не прятать реальные баги
        raise


def register_card_subscribe_handlers(router: Router) -> None:
    # -------- callback handlers --------

    async def choose_deck(call: types.CallbackQuery, state: FSMContext):
        deck_id = int(call.data.split("_")[-1])
        await state.update_data(deck_id=deck_id)

        cards = await get_cards_by_deck(deck_id)
        if not cards:
            await _safe_edit_msg(
                call.message,
                "В этой колоде пока нет карт.",
                _decks_keyboard(await get_all_decks()),
            )
            await safe_call_answer(call)
            return

        subs = await get_subscriptions(call.from_user.id)
        subscribed_ids = _extract_subscribed_card_ids(subs)

        kb = _cards_keyboard(cards, subscribed_ids, deck_id)
        await _safe_edit_msg(call.message, f"Выбери карту для подписки (колода №{deck_id}):", kb)
        await state.set_state(CardSubscribeFSM.waiting_for_card)
        await safe_call_answer(call)

    async def back_to_decks(call: types.CallbackQuery, state: FSMContext):
        decks = await get_all_decks()
        await _safe_edit_msg(call.message, "Выбери колоду для подписки:", _decks_keyboard(decks))
        await state.set_state(CardSubscribeFSM.waiting_for_deck)
        await safe_call_answer(call)

    async def choose_card(call: types.CallbackQuery, state: FSMContext):
        card_id = int(call.data.split(":")[-1])
        card = await subscribe_to_card(call.from_user.id, card_id)
        if not card:
            await call.answer("Карта не найдена", show_alert=True)
            return

        data = await state.get_data()
        deck_id = int(data["deck_id"])
        cards = await get_cards_by_deck(deck_id)

        subs = await get_subscriptions(call.from_user.id)
        subscribed_ids = _extract_subscribed_card_ids(subs)

        kb = _cards_keyboard(cards, subscribed_ids, deck_id)
        await safe_call_answer(call, "Подписка оформлена")
        await safe_edit_reply_markup(call.message, reply_markup=kb)

    @router.callback_query(CardSubscribeFSM.waiting_for_deck, F.data == "sub:presets_open")
    async def open_presets_manager_from_decks(call: types.CallbackQuery, state: FSMContext):
        await state.update_data(presets_back="decks")  # чтобы потом "Назад" не скакал
        kb = await _presets_manage_keyboard(call.from_user.id, back="decks")
        await _safe_edit_msg(call.message, "Пресеты уведомлений по расписанию:", kb)
        await safe_call_answer(call)

    @router.callback_query(F.data.startswith("sub:preset:"))
    async def toggle_any_preset(call: types.CallbackQuery, state: FSMContext):
        key = call.data.split(":", 2)[-1]
        _, toast = await _toggle_preset(call.from_user.id, key)

        text_now = (call.message.text or "").lower()
        if "пресеты уведомлений" in text_now:
            data = await state.get_data()
            back = data.get("presets_back") or ("cards" if "deck_id" in data else "decks")
            kb = await _presets_manage_keyboard(call.from_user.id, back)
            await safe_edit_reply_markup(call.message, reply_markup=kb)
        await safe_call_answer(call, toast)

    @router.callback_query(F.data.startswith("sub:preset_unsub:"))
    async def handle_preset_unsub(call: types.CallbackQuery, state: FSMContext):
        key = call.data.split(":", 2)[-1]
        await (await CardSubscriptionsService.from_runtime()).unsubscribe_preset(
            call.from_user.id, key
        )

        text_now = (call.message.text or "").lower()
        if "пресеты уведомлений" in text_now:
            data = await state.get_data()
            back = data.get("presets_back") or ("cards" if "deck_id" in data else "decks")
            kb = await _presets_manage_keyboard(call.from_user.id, back)
            await safe_edit_reply_markup(call.message, reply_markup=kb)
        await safe_call_answer(call, "Отключено")

    async def preset_unsub(call: types.CallbackQuery, state: FSMContext):
        key = call.data.split(":", 2)[-1]
        await (await CardSubscriptionsService.from_runtime()).unsubscribe_preset(
            call.from_user.id, key
        )

        text_now = (call.message.text or "").lower()
        if "пресеты уведомлений" in text_now:
            data = await state.get_data()
            back = "cards" if "deck_id" in data else "decks"
            kb = await _presets_manage_keyboard(call.from_user.id, back)
            await safe_edit_reply_markup(call.message, reply_markup=kb)
        await call.answer("Отключено")

    async def _safe_edit_msg(msg: types.Message, text: str, kb: InlineKeyboardMarkup) -> None:
        """
        Аккуратно редактируем сообщение:
        - если текст и разметка те же — выходим
        - если текст тот же, но клава другая — edit_reply_markup
        - иначе — edit_text
        """
        current_text = msg.html_text or msg.text or ""
        current_kb = msg.reply_markup

        def _same_kb(a, b) -> bool:
            try:
                if a is b:
                    return True
                if a is None or b is None:
                    return False
                return a.model_dump(exclude_none=True) == b.model_dump(exclude_none=True)
            except Exception:
                return str(a) == str(b)

        same_text = (current_text == text)
        same_markup = _same_kb(current_kb, kb)

        if same_text and same_markup:
            return

        try:
            if same_text and not same_markup:
                await msg.edit_reply_markup(reply_markup=kb)
            else:
                await msg.edit_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
        except TelegramBadRequest as e:
            # Телега иногда всё равно ноет — игнорируем именно этот кейс
            if "message is not modified" not in str(e):
                raise

    async def back_from_presets_to_cards(call: types.CallbackQuery, state: FSMContext):
        data = await state.get_data()
        deck_id = int(data["deck_id"])
        cards = await get_cards_by_deck(deck_id)
        subs = await get_subscriptions(call.from_user.id)
        subscribed_ids = _extract_subscribed_card_ids(subs)
        kb = _cards_keyboard(cards, subscribed_ids, deck_id)
        await _safe_edit_msg(call.message, f"Выбери карту для подписки (колода №{deck_id}):", kb)
        await safe_call_answer(call)

    async def my_all_subs_callback(call: types.CallbackQuery):
        await _send_my_subs(call)

    async def unsubscribe_card_cb(call: types.CallbackQuery):
        sub_id = int(call.data.split("_")[1])
        await unsubscribe_from_card(sub_id, call.from_user.id)
        await safe_call_answer(call, "Подписка удалена")

    @router.callback_query(F.data == "sub:back_cards")
    async def back_cards_universal(call: types.CallbackQuery, state: FSMContext):
        """
        Возвращаем список карт даже если состояние поехало.
        Если deck_id нет в FSM — уводим на список колод.
        """
        data = await state.get_data()
        deck_id = data.get("deck_id")
        if not deck_id:
            # Фоллбек: назад к колодам
            decks = await get_all_decks()
            await _safe_edit_msg(call.message, "Выбери колоду для подписки:", _decks_keyboard(decks))
            await state.set_state(CardSubscribeFSM.waiting_for_deck)
            await safe_call_answer(call)
            return

        try:
            deck_id = int(deck_id)
        except Exception:
            decks = await get_all_decks()
            await call.message.edit_text("Выбери колоду для подписки:", reply_markup=_decks_keyboard(decks))
            await state.set_state(CardSubscribeFSM.waiting_for_deck)
            await safe_call_answer(call)
            return

        cards = await get_cards_by_deck(deck_id)
        subs = await get_subscriptions(call.from_user.id)
        subscribed_ids = _extract_subscribed_card_ids(subs)
        kb = _cards_keyboard(cards, subscribed_ids, deck_id)

        await _safe_edit_msg(call.message, "Выбери карту для подписки:", kb)
        await state.set_state(CardSubscribeFSM.waiting_for_card)
        await safe_call_answer(call)

    @router.callback_query(F.data == "noop")
    async def noop(call: types.CallbackQuery):
        # чтобы пустые «заглушки» в сетке пресетов не сыпали в логи «not handled»
        await safe_call_answer(call)

    # -------- shared: render "My subs" --------

    MAX_TG = 4096
    SAFE = 3500  # запас, потому что HTML-теги тоже считаютcя символами

    async def _send_with_kb(event: types.CallbackQuery | types.Message, text: str, buttons: list[InlineKeyboardButton]):
        kb = InlineKeyboardBuilder()
        for b in buttons:
            kb.row(b)
        kb = kb.as_markup()
        if isinstance(event, types.CallbackQuery):
            await event.message.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        else:
            await event.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

    async def _send_group_chunked(event: types.CallbackQuery | types.Message, title: str,
                                  items: list[tuple[str, InlineKeyboardButton]]):
        """
        items: список (строка_для_текста, кнопка_для_отписки)
        Шлём несколько сообщений, не превышая лимит Телеги.
        """
        if not items:
            return

        header = f"{title}:\n"
        acc_lines: list[str] = []
        acc_buttons: list[InlineKeyboardButton] = []
        acc_len = len(header)

        def flush():
            nonlocal acc_lines, acc_buttons, acc_len
            if not acc_lines:
                return
            text = header + "\n".join(acc_lines)
            # отправляем и обнуляем аккумулированное
            yield text, acc_buttons
            acc_lines, acc_buttons, acc_len = [], [], len(header)

        for line, btn in items:
            line_len = len(line) + 1  # + '\n'
            # если текущая строка уже не влезает — отправляем накопленное
            if acc_len + line_len > SAFE:
                for text, btns in flush():
                    await _send_with_kb(event, text, btns)
            acc_lines.append(line)
            acc_buttons.append(btn)
            acc_len += line_len

        # отправляем остаток
        for text, btns in flush():
            await _send_with_kb(event, text, btns)

    async def _load_card_meta(card_ids: list[int]) -> dict[int, dict]:
        if not card_ids:
            return {}

        return await (await CardSubscriptionsService.from_runtime()).card_metadata(card_ids)

    async def _deck_name_map() -> dict[int, str]:
        decks = await get_all_decks()
        out: dict[int, str] = {}
        for d in decks or []:
            try:
                did = int(d.get("deck_id") or d.get("id"))
            except Exception:
                continue
            name = (d.get("deck_name") or d.get("name") or "").strip()
            out[did] = name
        return out

    def _preset_pretty_title(key: str, title_raw: str, deck_names: dict[int, str]) -> str:
        key = str(key or "").strip()
        title = str(title_raw or "").strip() or key or "—"

        m = re.fullmatch(r"deck_all_(\d+)", key)
        if m:
            deck_id = int(m.group(1))
            deck_name = deck_names.get(deck_id, "")
            if deck_name:
                return f"Вся колода {deck_id} — {deck_name}"
            return f"Вся колода {deck_id}"

        return title
    async def my_subscriptions_cmd(message: types.Message):
        await _send_my_subs(message)

    async def _send_my_subs(event: types.CallbackQuery | types.Message):
        uid = event.from_user.id

        try:
            card_subs = await get_subscriptions(uid)
        except Exception:
            card_subs = []

        try:
            preset_subs = await list_my_preset_subs(uid)
        except Exception:
            preset_subs = []

        if not card_subs and not preset_subs:
            empty = "Пусто. Ни карт, ни пресетов."
            if isinstance(event, types.CallbackQuery):
                await event.message.answer(empty)
                try:
                    await event.answer()
                except Exception:
                    pass
            else:
                await event.answer(empty)
            return

        # -------------------------
        # Карточные подписки: грузим мету и группируем по колодам
        # -------------------------
        card_ids: list[int] = []
        for s in card_subs:
            try:
                cid = int(s.get("card_id"))
                card_ids.append(cid)
            except Exception:
                continue

        card_meta = await _load_card_meta(card_ids)
        deck_names = await _deck_name_map()

        grouped_cards: dict[tuple[int, str], list[tuple[str, InlineKeyboardButton]]] = defaultdict(list)

        for s in card_subs:
            sub_raw = s.get("id", s.get("sub_id"))
            if sub_raw is None:
                continue

            try:
                sub_id = int(sub_raw)
            except (TypeError, ValueError):
                continue

            try:
                card_id = int(s.get("card_id"))
            except Exception:
                card_id = 0

            meta = card_meta.get(card_id, {})
            hero = html.escape((meta.get("hero_name") or s.get("hero_name") or "").strip())
            card = html.escape((meta.get("card_name") or s.get("card_name") or "").strip())
            deck_id = int(meta.get("deck_id") or s.get("deck_id") or 0)
            deck_name = (meta.get("deck_name") or deck_names.get(deck_id) or "").strip()

            line_main = " — ".join(x for x in [hero, card] if x)
            if not line_main:
                line_main = f"#{card_id}" if card_id else "—"

            btn_label = hero or card or (f"#{card_id}" if card_id else "—")

            btn = InlineKeyboardButton(
                text=f"❌ {btn_label}",
                callback_data=f"unsubscribe_{sub_id}",
            )

            grouped_cards[(deck_id, deck_name)].append((f"• <b>{line_main}</b>", btn))

        # -------------------------
        # Пресеты: красиво показываем колоды и сервисные лоты
        # -------------------------
        preset_items: list[tuple[str, InlineKeyboardButton]] = []
        for p in preset_subs:
            raw_title = p.get("title") or p.get("key") or "—"
            key = str(p.get("key") or raw_title)
            pretty_title = _preset_pretty_title(key, str(raw_title), deck_names)
            safe_title = html.escape(pretty_title)

            btn = InlineKeyboardButton(
                text=f"❌ {pretty_title}",
                callback_data=f"sub:preset_unsub:{key}",
            )
            preset_items.append((f"• <b>{safe_title}</b>", btn))

        # -------------------------
        # Отправка
        # -------------------------
        if grouped_cards:
            for (deck_id, deck_name) in sorted(
                    grouped_cards.keys(),
                    key=lambda x: (999999 if not x[0] else x[0], x[1].lower()),
            ):
                if deck_id:
                    title = f"🃏 <b>Колода {deck_id}</b>"
                    if deck_name:
                        title += f" — {html.escape(deck_name)}"
                else:
                    title = "🃏 <b>Подписки на карты</b>"

                await _send_group_chunked(event, title, grouped_cards[(deck_id, deck_name)])

        if preset_items:
            await _send_group_chunked(event, "📦 <b>Подписки на пресеты</b>", preset_items)

        if isinstance(event, types.CallbackQuery):
            try:
                await event.answer()
            except Exception:
                pass
    # ======================
    # Actual registrations
    # ======================

    router.message.register(start_subscribe_card, Command("subscribe_card"))
    router.message.register(start_subscribe_card, F.text == "/subscribe_card")

    router.callback_query.register(choose_deck, CardSubscribeFSM.waiting_for_deck, F.data.startswith("admin_deck_"))
    router.callback_query.register(back_to_decks, CardSubscribeFSM.waiting_for_card, F.data == "sub:back_decks")
    router.callback_query.register(back_to_decks, CardSubscribeFSM.waiting_for_deck, F.data == "sub:back_decks")
    router.callback_query.register(choose_card, CardSubscribeFSM.waiting_for_card, F.data.startswith("sub:card:"))

    # менеджер пресетов
    router.callback_query.register(open_presets_manager_from_decks, CardSubscribeFSM.waiting_for_deck,
                                   F.data == "sub:preset:any_card")
    router.callback_query.register(toggle_any_preset, F.data.startswith("sub:preset:"))
    router.callback_query.register(preset_unsub, F.data.startswith("sub:preset_unsub:"))
    router.callback_query.register(back_from_presets_to_cards, CardSubscribeFSM.waiting_for_card,
                                   F.data == "sub:back_cards")

    # список подписок и отписка
    router.callback_query.register(my_all_subs_callback, F.data == "sub:list")
    router.callback_query.register(unsubscribe_card_cb, F.data.startswith("unsubscribe_"))

    router.message.register(my_subscriptions_cmd, Command("my_subscriptions"))
    router.message.register(my_subscriptions_cmd, Command("my_subs"))
