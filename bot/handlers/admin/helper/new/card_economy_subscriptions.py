"""Subscription confirmation broadcasts, callbacks and unsubscribe actions."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from html import escape
from math import ceil
from typing import Optional

from aiogram import Bot, F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot.auction_notify import _kb_equal
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.logs_admin import send_admin_log as _send_admin_log
from bot.handlers.card_subscribe import _decks_keyboard, _presets_manage_keyboard
from bot.services.card_economy import CardEconomyService
from bot.services.card_subscriptions import CardSubscriptionsService
from bot.telegram.callbacks import safe_callback_answer
from bot.core.legacy_config import legacy_config
from db.cards import (
    get_card,
    get_deck,
    norm_obtain_type,
    set_card_obtain,
    set_deck_type,
    get_all_decks,
)
from db.users import (
    get_user_id_by_username,
    is_luxury_user,
)
from db.subscriptions import (
    list_broadcast_targets,
    list_user_card_subs,
    mark_subscription_confirmed,
    mark_unreachable_user,
    unsubscribe_subscription,
)
from db.auctions import get_auction_winner
from bot.telegram.states import CardSubscribeFSM, EconomyFSM

# ---------------------------------------------------------------------------
# Router / constants
# ---------------------------------------------------------------------------

from bot.handlers.admin.helper.new.card_economy_shared import (
    CHUNK_LIMIT,
    CONF_CB_PREFIX,
    SEND_HTML_KW,
    SUBS_CONFIRM_CB,
    UNSUB_CB_PREFIX,
    _safe_edit,
    _subs_word,
)
from bot.telegram.callback_parser import split_callback_data

router = Router(name="admin_card_economy_subscriptions")


def _build_text(subs: list[dict]) -> str:
    """
    Строит читаемый список подписок пользователя + краткие инструкции.

    Ожидается структура элемента:
    {
        "sub_id": int,
        "card_name": str,
        "hero_name": str | None,
        "deck_id": int | None,
        "last_confirmed_at": datetime | None
    }
    """
    total = len(subs)
    lines: list[str] = []
    lines.append("🔔 <b>Ваши активные подписки</b>")
    lines.append(f"Всего: <b>{total}</b> {_subs_word(total)}")
    lines.append("")
    lines.append(
        "Нажмите на название, чтобы отметить подписку подтверждённой, "
        "или на «Отписаться», если она больше не нужна."
    )
    lines.append("Если сообщений будет много, пришлю их частями.")
    lines.append("")

    for i, s in enumerate(subs, 1):
        name = escape(str(s.get("card_name") or "-").strip())
        hero = escape(str(s.get("hero_name") or "").strip())
        deck_id = s.get("deck_id")
        deck_txt = f"№{deck_id}" if deck_id is not None else "—"
        ok_mark = " ✅" if s.get("last_confirmed_at") else ""
        title = f"{name} — {hero}" if hero else name
        lines.append(f"{i}. <b>{title}</b> · 📚 колода {deck_txt}{ok_mark}")

    lines.append("")
    lines.append("Готово. Проверьте список и обновите то, что нужно.")
    return "\n".join(lines)


def _build_keyboard(subs: list[dict]) -> InlineKeyboardMarkup:
    """Build a keyboard whose callbacks match the registered handlers."""
    rows: list[list[InlineKeyboardButton]] = []
    for subscription in subs or []:
        sub_id = subscription.get("sub_id")
        if sub_id is None:
            continue
        hero = subscription.get("hero_name") or "—"
        card = subscription.get("card_name") or "—"
        confirmed = bool(subscription.get("last_confirmed_at"))
        prefix = "✅" if confirmed else "Подтвердить"
        title = f"{prefix}: {hero} — {card}"[:56]
        rows.append([
            InlineKeyboardButton(text=title, callback_data=f"{CONF_CB_PREFIX}{sub_id}"),
            InlineKeyboardButton(text="Отписаться", callback_data=f"{UNSUB_CB_PREFIX}{sub_id}"),
        ])

    if rows:
        rows.append([InlineKeyboardButton(text="✅ Подтвердить всё", callback_data="sc:ok_all")])
    else:
        rows.append([InlineKeyboardButton(text="Нет активных подписок", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="Закрыть", callback_data="sc:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_confirm_text() -> str:
    return (
        "<b>Нужно обновить подписки</b>\n\n"
        "Мы чистим и актуализируем ваши подписки на карты.\n"
        "Если хотите подтвердить актуальность и обновить настройки, "
        "нажмите кнопку ниже. Тогда пришлю ваш список подписок "
        "и клавиатуру для быстрого редактирования.\n\n"
        "Если подписок много, сообщения придут частями."
    )


def _build_confirm_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, показать список",
                    callback_data=f"{SUBS_CONFIRM_CB}:yes:{user_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🙅‍♀️ Нет, оставить как есть",
                    callback_data=f"{SUBS_CONFIRM_CB}:no:{user_id}",
                )
            ],
        ]
    )


def _split_with_parts(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Режем длинный текст на части и маркируем «Часть i/n»."""
    chunks = [text[i: i + limit] for i in range(0, len(text), limit)] or [text]
    n = len(chunks)
    if n == 1:
        return chunks
    labeled: list[str] = []
    for i, c in enumerate(chunks, 1):
        labeled.append(f"<b>Часть {i}/{n}</b>\n\n{c}")
    return labeled


def _build_no_subs_text() -> str:
    return (
        "🔔 <b>У вас пока нет подписок</b>\n\n"
        "Нажмите кнопку ниже, чтобы выбрать карты и включить уведомления.\n"
        "В любой момент эту кнопку можно отправить вручную командой /subscribe_card."
    )


def _start_subscribe_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Начать подписку", callback_data="sub:open")]
    ])


def _start_subscribe_kb() -> ReplyKeyboardMarkup:
    # компактная одноразовая клавиатура, чтобы пользователь просто ткнул и отправил команду
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="/subscribe_card")]],
        resize_keyboard=True,
        one_time_keyboard=True,
        selective=True,
    )


from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter, TelegramForbiddenError


@router.message(Command("subs_confirm_broadcast"))
@admin_only
async def subs_confirm_broadcast(message: types.Message) -> None:
    bot = message.bot
    parts = (message.text or "").split(maxsplit=1)
    target_uid: int | None = None

    await message.answer("Стартую рассылку-подтверждение/приглашение.")

    # Опциональный таргет @user или id
    if len(parts) > 1:
        token = parts[1].strip()
        try:
            target_uid = int(token)
        except ValueError:
            handle = token if token.startswith("@") else f"@{token}"
            try:
                chat = await bot.get_chat(handle)
                if getattr(chat, "type", "private") == "private":
                    target_uid = int(chat.id)
            except TelegramAPIError:
                target_uid = None
            if target_uid is None:
                target_uid = await get_user_id_by_username(token)
        if target_uid is None:
            await message.answer(f"Не нашёл пользователя по «{token}». Пропускаю.")
            return

    targets = [target_uid] if target_uid is not None else await list_broadcast_targets()

    sent_confirms = sent_prompts = 0
    skipped_forbidden = skipped_bad_request = skipped_other = 0

    for uid in targets:
        try:
            subs = await list_user_card_subs(uid)
            subs = _normalize_sub_rows(subs)

            if subs:
                await bot.send_message(
                    uid,
                    _build_confirm_text(),
                    reply_markup=_build_confirm_kb(uid),
                    **SEND_HTML_KW,
                )
                sent_confirms += 1
            else:
                await bot.send_message(
                    uid,
                    _build_no_subs_text(),
                    reply_markup=_start_subscribe_inline_kb(),
                    **SEND_HTML_KW,
                )
                sent_prompts += 1

            await asyncio.sleep(0.05)

        except TelegramRetryAfter as e:
            await asyncio.sleep(getattr(e, "retry_after", 1.0))
        except TelegramForbiddenError:
            skipped_forbidden += 1
            with contextlib.suppress(Exception):
                await mark_unreachable_user(uid, "forbidden")
        except TelegramBadRequest:
            skipped_bad_request += 1
        except TelegramAPIError as e:
            skipped_other += 1
            with contextlib.suppress(Exception):
                await mark_unreachable_user(uid, f"api:{type(e).__name__}")
        except Exception:
            skipped_other += 1

    total_skipped = skipped_forbidden + skipped_bad_request + skipped_other
    await message.answer(
        "Готово. "
        f"Запросов подтверждения: {sent_confirms}, "
        f"приглашений: {sent_prompts}, "
        f"пропущено: {total_skipped} "
        f"(403: {skipped_forbidden}, 400: {skipped_bad_request}, прочее: {skipped_other})."
    )


async def _safe_edit_msg(
        msg: types.Message,
        text: str,
        kb: Optional[InlineKeyboardMarkup] = None,
        parse_mode: Optional[str] = None,
) -> None:
    """
    Пытается отредактировать исходное сообщение.
    Если редактировать нельзя/нечего — отправляет новое.
    """
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode=parse_mode)
    except TelegramBadRequest:
        # случаи: "message is not modified", "message can't be edited", "not enough rights"
        await msg.answer(text, reply_markup=kb, parse_mode=parse_mode)


def _normalize_sub_rows(rows) -> list[dict]:
    """
    Приводит записи подписок к единому виду.
    Принимает любые словари / Record'ы, вытаскивает sub_id из известных названий.
    Отбрасывает мусор без айди.
    """
    normalized: list[dict] = []
    for r in rows or []:
        d = dict(r)
        sid = d.get("sub_id") or d.get("id") or d.get("subscription_id")
        if sid is None:
            continue
        d["sub_id"] = int(sid)
        # подстрахуем ключи для названия карточки
        d.setdefault("hero_name", d.get("hero") or d.get("hero_title"))
        d.setdefault("card_name", d.get("card") or d.get("card_title") or d.get("title"))
        normalized.append(d)
    return normalized


@router.callback_query(CardSubscribeFSM.waiting_for_deck, F.data.in_({"sub:presets_open", "sub:preset:any_card"}))
async def open_presets_manager_from_decks(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(presets_back="decks")
    kb = await _presets_manage_keyboard(call.from_user.id, back="decks")
    await _safe_edit_msg(call.message, "Пресеты уведомлений по расписанию:", kb)
    await call.answer()


async def open_subscribe_from_broadcast(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    decks = await get_all_decks()
    if not decks:
        await call.message.answer("Пока нет доступных колод.")
        await call.answer()
        return
    await call.message.answer("Выбери колоду для подписки:", reply_markup=_decks_keyboard(decks))
    await state.set_state(CardSubscribeFSM.waiting_for_deck)
    await call.answer()


router.callback_query.register(open_subscribe_from_broadcast, F.data == "sub:open")

# почини несовпадение ключа пресетов; оставим оба алиаса
router.callback_query.register(
    open_presets_manager_from_decks,
    CardSubscribeFSM.waiting_for_deck,
    F.data.in_({"sub:presets_open", "sub:preset:any_card"}),
)


async def open_subscribe_from_button(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    decks = await get_all_decks()
    if not decks:
        await call.message.answer("Пока нет доступных колод.")
        await call.answer()
        return
    await call.message.answer("Выбери колоду для подписки:", reply_markup=_decks_keyboard(decks))
    await state.set_state(CardSubscribeFSM.waiting_for_deck)
    await call.answer()


@router.callback_query(F.data.startswith(f"{SUBS_CONFIRM_CB}:"))
async def subs_confirm_callback(call: types.CallbackQuery) -> None:
    """Обрабатывает подтверждение от пользователя."""
    bot = call.message.bot
    try:
        _, action, sid = split_callback_data(call.data, ":")
        uid = int(sid)
    except Exception:
        await call.answer("Неверные данные.", show_alert=False)
        return

    # Немного безопасности: реагируем только если нажимает сам пользователь
    if call.from_user and call.from_user.id != uid:
        await call.answer("Это не для вас.", show_alert=False)
        return

    if action == "no":
        try:
            await call.message.edit_text(
                "Окей, ничего не меняем. Если передумаете — "
                "зайдите в профиль и обновите подписки."
            )
        except TelegramAPIError:
            await call.message.answer(
                "Окей, ничего не меняем. Если передумаете — "
                "зайдите в профиль и обновите подписки."
            )
        return

    if action != "yes":
        await call.answer("Неизвестное действие.", show_alert=False)
        return

    # 'yes' — достаём актуальные подписки и шлём списком
    try:
        subs = await list_user_card_subs(uid)
        subs = _normalize_sub_rows(subs)
    except Exception:
        subs = []

    if not subs:
        try:
            await call.message.edit_text(
                "Подписок не найдено. Добавьте сначала хотя бы одну."
            )
        except TelegramAPIError:
            await call.message.answer(
                "Подписок не найдено. Добавьте сначала хотя бы одну."
            )
        return

    try:
        await call.message.edit_text("Окей, присылаю ваш список подписок…")
    except TelegramAPIError:
        pass

    full_text = _build_text(subs)
    parts = _split_with_parts(full_text, limit=CHUNK_LIMIT)

    # отправляем частями; клавиатура кладётся в последний блок
    for i, chunk in enumerate(parts, 1):
        markup = _build_keyboard(subs) if i == len(parts) else None
        try:
            await bot.send_message(uid, chunk, reply_markup=markup, **SEND_HTML_KW)
            await asyncio.sleep(0.05)
        except TelegramRetryAfter as e:
            await asyncio.sleep(getattr(e, "retry_after", 1.0))
            try:
                await bot.send_message(
                    uid, chunk, reply_markup=markup, **SEND_HTML_KW
                )
            except TelegramAPIError:
                break
        except TelegramAPIError:
            break


@router.message(Command("subs_confirm_test"))
@admin_only
async def subs_confirm_test(message: types.Message) -> None:
    """Тестовая отправка списка и клавиатуры одному пользователю."""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Укажи @username или user_id. Пример: "
            "/subs_confirm_test @aam_cheshire"
        )
        return

    token = parts[1].strip()
    bot = message.bot

    await message.answer(
        "Стартую рассылку подтверждений (только указанному пользователю)."
    )

    target_uid: int | None = None
    try:
        target_uid = int(token)
    except ValueError:
        handle = token if token.startswith("@") else f"@{token}"
        try:
            chat = await bot.get_chat(handle)
            if getattr(chat, "type", "private") == "private":
                target_uid = int(chat.id)
        except TelegramAPIError:
            target_uid = None
        if target_uid is None:
            target_uid = await get_user_id_by_username(token)

    if target_uid is None:
        await message.answer(f"Не нашёл пользователя по «{token}». Пропускаю.")
        return

    sent, skipped = 0, 0
    try:
        subs = await list_user_card_subs(target_uid)
        if not subs:
            skipped += 1
        else:
            await bot.send_message(
                target_uid,
                _build_text(subs),
                reply_markup=_build_keyboard(subs),
                **SEND_HTML_KW,
            )
            sent += 1
            await asyncio.sleep(0.06)
    except TelegramRetryAfter as e:
        await asyncio.sleep(getattr(e, "retry_after", 1.0))
    except TelegramAPIError as e:
        await mark_unreachable_user(target_uid, str(e))
        skipped += 1

    await message.answer(f"Готово. Отправлено: {sent}, пропущено: {skipped}.")


# ---------------------------------------------------------------------------
# Колбэки подтверждения/отписки (клавиатура списка подписок)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "sc:ok_all")
async def sc_confirm_all(call: types.CallbackQuery) -> None:
    service = await CardSubscriptionsService.from_runtime()
    count = await service.confirm_all(int(call.from_user.id))

    if call.message and call.message.reply_markup:
        new_rows: list[list[InlineKeyboardButton]] = []
        for row in call.message.reply_markup.inline_keyboard:
            updated: list[InlineKeyboardButton] = []
            for button in row:
                callback_data = getattr(button, "callback_data", None)
                text = button.text or ""
                if callback_data and callback_data.startswith(CONF_CB_PREFIX):
                    suffix = text.split(":", 1)[-1].strip()
                    text = f"✅: {suffix}"[:56]
                updated.append(InlineKeyboardButton(text=text, callback_data=callback_data))
            new_rows.append(updated)
        new_kb = InlineKeyboardMarkup(inline_keyboard=new_rows)
        if not _kb_equal(call.message.reply_markup, new_kb):
            current_text = call.message.html_text or call.message.text or ""
            await _safe_edit(call, current_text, new_kb)

    await call.answer(f"Подтверждено: {count}")


@router.callback_query(F.data == "sc:close")
async def sc_close(call: types.CallbackQuery) -> None:
    try:
        if call.message:
            await call.message.delete()
    finally:
        await call.answer()


# --- ЗАМЕНИ хендлер подтверждения на этот ---

@router.callback_query(F.data.startswith(CONF_CB_PREFIX))
async def sc_confirm(call: types.CallbackQuery) -> None:
    data = call.data or ""
    try:
        sub_id = int(data.split(":", 2)[-1])
    except Exception:
        await call.answer("Ошибка данных", show_alert=True)
        return

    ok = await mark_subscription_confirmed(sub_id, call.from_user.id)
    if not ok:
        await call.answer("Подписка не найдена", show_alert=True)
        return

    # Текущая и новая клавиатуры
    old_kb = call.message.reply_markup
    new_rows: list[list[InlineKeyboardButton]] = []
    target_cd = f"{CONF_CB_PREFIX}{sub_id}"
    changed = False

    if old_kb and old_kb.inline_keyboard:
        for row in old_kb.inline_keyboard:
            new_row: list[InlineKeyboardButton] = []
            for btn in row:
                if getattr(btn, "callback_data", None) == target_cd:
                    text = btn.text or ""
                    if not text.startswith("✅"):
                        text = f"✅ {text}"
                        changed = True
                    new_row.append(
                        InlineKeyboardButton(text=text, callback_data=btn.callback_data)
                    )
                else:
                    new_row.append(btn)
            new_rows.append(new_row)
    else:
        # На всякий: если клавиатуры не было, нечего редактировать
        await call.answer("Отмечено")
        return

    new_kb = InlineKeyboardMarkup(inline_keyboard=new_rows)

    # Если итог совпадает с тем, что уже стоит — не редактируем
    if not changed or _kb_equal(old_kb, new_kb):
        await call.answer("Уже отмечено")
        return

    # Безопасное редактирование (не свалимся на 'message is not modified')
    current_text = call.message.html_text or call.message.text or ""
    await _safe_edit(call, current_text, new_kb)
    await call.answer("Отмечено")


@router.message(Command("id"))
async def cmd_id(message: types.Message):
    tgt = message.reply_to_message or message

    if tgt.photo:
        p = tgt.photo[-1]
        text = (
            f"file_id: <code>{escape(p.file_id)}</code>\n"
            f"file_unique_id: <code>{escape(p.file_unique_id)}</code>\n"
            f"size: {p.width}x{p.height}"
        )
        await message.answer(text, parse_mode="HTML")
        return

    if tgt.document and str(tgt.document.mime_type or "").startswith("image/"):
        d = tgt.document
        text = (
            f"file_id: <code>{escape(d.file_id)}</code>\n"
            f"file_unique_id: <code>{escape(d.file_unique_id)}</code>\n"
            f"name: <code>{escape(d.file_name or '')}</code>"
        )
        await message.answer(text, parse_mode="HTML")
        return

    if tgt.sticker:
        s = tgt.sticker
        text = (
            f"sticker file_id: <code>{escape(s.file_id)}</code>\n"
            f"unique: <code>{escape(s.file_unique_id)}</code>"
        )
        await message.answer(text, parse_mode="HTML")
        return

    await message.answer("Пришли фото или ответь командой на сообщение с фото. Документы image/* тоже ок.")


# --- ЗАМЕНИ хендлер отписки на этот ---

@router.callback_query(F.data.startswith(UNSUB_CB_PREFIX))
async def sc_unsubscribe(call: types.CallbackQuery) -> None:
    data = call.data or ""
    try:
        sub_id = int(data.split(":", 2)[-1])
    except Exception:
        await call.answer("Ошибка данных", show_alert=True)
        return

    ok = await unsubscribe_subscription(sub_id, call.from_user.id)
    if not ok:
        await call.answer("Уже отписан или подписка не найдена", show_alert=True)
        return

    old_kb = call.message.reply_markup
    if not (old_kb and old_kb.inline_keyboard):
        await safe_callback_answer(call, "Отписано")
        return

    target_ok = f"{CONF_CB_PREFIX}{sub_id}"
    target_rm = f"{UNSUB_CB_PREFIX}{sub_id}"

    new_rows: list[list[InlineKeyboardButton]] = []
    for row in old_kb.inline_keyboard:
        cds = [getattr(btn, "callback_data", None) for btn in row]
        if target_ok in cds or target_rm in cds:
            # выкидываем строку с этой подпиской
            continue
        new_rows.append(row)

    # Если всё выпилили — покажем заглушку + Закрыть
    rows_wo_close = [
        r for r in new_rows
        if not (len(r) == 1 and getattr(r[0], "callback_data", "") == "sc:close")
    ]
    if not rows_wo_close:
        new_rows = [
            [InlineKeyboardButton(text="Нет активных подписок", callback_data="noop")],
            [InlineKeyboardButton(text="Закрыть", callback_data="sc:close")],
        ]
    else:
        if not any(
                len(r) == 1 and getattr(r[0], "callback_data", "") == "sc:close"
                for r in new_rows
        ):
            new_rows.append([InlineKeyboardButton(text="Закрыть", callback_data="sc:close")])

    new_kb = InlineKeyboardMarkup(inline_keyboard=new_rows)

    # Если по факту ничего не изменилось — не трогаем сообщение
    if _kb_equal(old_kb, new_kb):
        await safe_callback_answer(call, "Уже отписан")
        return

    current_text = call.message.html_text or call.message.text or ""
    await _safe_edit(call, current_text, new_kb)
    await safe_callback_answer(call, "Отписано")

