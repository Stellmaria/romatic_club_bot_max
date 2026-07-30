"""Shared Telegram and Telethon delivery, cancellation and access helpers."""

from __future__ import annotations

import contextlib
import inspect
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Awaitable, Callable, Mapping, Optional, Union

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User
from telethon.tl.functions.messages import SendMessageRequest

from bot.core.settings import ADMIN_LOG_CHATS, ADMINS_OWNERS, LOG_CHAT_ID
from bot.core.time import to_moscow
from bot.handlers.admin.action_support.exchange import safe_answer_photo, tg_clean
from bot.handlers.admin.helper.admin_constants import ADMIN_MESSAGES, CANCEL_MSG, CANCEL_TEXTS
from bot.handlers.admin.helper.new.helper import normalize_chat_id
from bot.handlers.admin.helper.new.keyboards import menu_keyboard
from bot.security import admin_secret_matches
from bot.services.admin_logging import send_admin_log

def _safe_strip(s: Optional[str]) -> str:
    return s.strip() if isinstance(s, str) else ""


def parse_datetime_field(field: Any) -> Optional[datetime]:
    if isinstance(field, str):
        try:
            return datetime.fromisoformat(field)
        except (ValueError, TypeError):
            return None
    return field if isinstance(field, datetime) else None


def _to_msk(dt: Any) -> datetime | None:
    d = parse_datetime_field(dt)
    if not d:
        return None
    try:
        return to_moscow(d)
    except Exception:
        return d


def _human_wait(delta: timedelta) -> str:
    sec = int(delta.total_seconds())
    if sec < 0:
        sec = 0
    days, sec = divmod(sec, 86400)
    hours, sec = divmod(sec, 3600)
    mins, _ = divmod(sec, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    parts.append(f"{mins}м")
    return " ".join(parts)


def _resolve_bot_from_message(
        message: Message, given: Optional[Bot] = None
) -> Optional[Bot]:
    if isinstance(given, Bot):
        return given
    mb = getattr(message, "bot", None)
    return mb if isinstance(mb, Bot) else None


def _ensure_sender(message: Message) -> tuple[Optional[int], Optional[str]]:
    fu = getattr(message, "from_user", None)
    if isinstance(fu, User):
        return fu.id, fu.username
    return None, None


def as_message(obj: Union[Message, CallbackQuery]) -> Optional[Message]:
    if isinstance(obj, Message):
        return obj
    if isinstance(obj, CallbackQuery):
        m = obj.message
        return m if isinstance(m, Message) else None
    return None


def require_bot(obj: Union[Message, CallbackQuery]) -> Optional[Bot]:
    return getattr(obj, "bot", None)


async def _call_maybe_await(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    res = fn(*args, **kwargs)
    if inspect.isawaitable(res):
        return await res
    return res


def format_date_time_block(st: Any, et: Any) -> str:
    st_dt = parse_datetime_field(st)
    et_dt = parse_datetime_field(et)
    if isinstance(st_dt, datetime) and isinstance(et_dt, datetime):
        date = st_dt.strftime("%d.%m.%Y")
        return (
            f"<b>Назначен на:</b> {date} {st_dt:%H:%M}–{et_dt:%H:%M} (МСК)\n"
        )
    return ""


def owner_or_secret_required(
        func: Callable[..., Awaitable[Any]]
) -> Callable[..., Awaitable[Any]]:
    @wraps(func)
    async def wrapper(*args, **kwargs):
        msg: Optional[Message] = next(
            (a for a in args if isinstance(a, Message)), None
        )
        if msg is None:
            msg = kwargs.get("message")
        if msg is None:
            cq: Optional[CallbackQuery] = next(
                (a for a in args if isinstance(a, CallbackQuery)), None
            )
            msg = as_message(cq) if cq is not None else None
        if msg is None:
            return None

        text = _safe_strip(getattr(msg, "text", None))
        parts = text.split() if text else []

        uid = msg.from_user.id if msg.from_user else None
        is_owner = uid in ADMINS_OWNERS if uid is not None else False

        has_secret = len(parts) > 1 and admin_secret_matches(parts[-1])

        if is_owner or has_secret:
            return await func(*args, **kwargs)

        await msg.answer("Требуется пароль владельца.")
        return None

    return wrapper


async def safe_edit_message(call: CallbackQuery, new_text: str, reply_markup=None, silent: bool = False) -> None:
    m = as_message(call)
    if m is None:
        await call.answer(tg_clean(new_text)[:190], show_alert=True)
        return
    try:
        await m.edit_text(tg_clean(new_text), reply_markup=reply_markup, parse_mode="HTML")
        return
    except TelegramBadRequest:
        pass
    try:
        await m.edit_caption(tg_clean(new_text), reply_markup=reply_markup, parse_mode="HTML")
        return
    except TelegramBadRequest:
        pass
    try:
        await m.delete()
    except TelegramAPIError as e:
        if not silent:
            print(f"[SAFE EDIT] Не удалось удалить сообщение: {e}")

    bot: Optional[Bot] = require_bot(call)
    chat_id: Optional[Union[int, str]] = getattr(getattr(m, "chat", None), "id", None)
    if bot is not None and chat_id is not None:
        await bot.send_message(chat_id, tg_clean(new_text), reply_markup=reply_markup, parse_mode="HTML")


async def notify_owners(bot: Bot, text: str, silent: bool = False) -> None:
    for owner_id in ADMINS_OWNERS:
        try:
            await bot.send_message(owner_id, tg_clean(text), parse_mode="HTML")
        except TelegramAPIError as e:
            if not silent:
                print(f"[OWNER NOTIFY ERROR] {owner_id}: {e}")


async def send_log_to_chats(client_api, text: str) -> None:
    for chat_id in ADMIN_LOG_CHATS:
        try:
            entity = await client_api.get_entity(chat_id)
            await client_api(SendMessageRequest(peer=entity, message=text))
            print(f"[LOG OK] Сообщение отправлено в лог-чат {chat_id}")
        except Exception as e:
            print(f"[LOG ERROR] {e}")


async def verify_log_chats(bot: Bot) -> None:
    for name, raw in {"LOG_CHAT_ID": LOG_CHAT_ID}.items():
        cid = normalize_chat_id(raw)
        if cid is None:
            print(f"[BOOT] {name} пуст/некорректен: {raw!r}")
            continue
        try:
            chat = await bot.get_chat(cid)
            print(f"[BOOT] {name} ок: {getattr(chat, 'id', cid)}")
        except TelegramAPIError as e:
            print(f"[BOOT] {name} недоступен ({raw!r}): {e}")


def get_cancel_text(state_name: Optional[str]) -> str:
    cancel_map: dict[str, str] = {
        "ModActionFSM:waiting_for_unluxury_user": (
            CANCEL_TEXTS["removeluxury_cancel"][0]
        ),
        "ModActionFSM:waiting_for_luxury_user": (
            CANCEL_TEXTS["giveluxury_cancel"][0]
        ),
        "ModActionFSM:waiting_for_admin_user": (
            CANCEL_TEXTS["addadmin_cancel"][0]
        ),
        "ModActionFSM:waiting_for_admin_remove_user": (
            CANCEL_TEXTS["removeadmin_cancel"][0]
        ),
        "ModActionFSM:waiting_for_trusted_user": "Выдача доверия отменена.",
        "ModActionFSM:waiting_for_untrusted_user": (
            "Снятие доверия отменена."
        ),
    }
    key = state_name if isinstance(state_name, str) else ""
    default_msg = str(CANCEL_MSG)
    return cancel_map.get(key, default_msg)


async def process_universal_cancel_text(
        message: Message, state: FSMContext
) -> None:
    cancel_text = get_cancel_text(await state.get_state())
    await state.clear()
    await message.answer(
        f"{cancel_text}\n\n"
        f"{ADMIN_MESSAGES.get('admin_panel_greeting', 'Добро пожаловать в админ-панель!')}"
    )


async def process_universal_cancel_callback(
        call: CallbackQuery, state: FSMContext
) -> None:
    cancel_text = get_cancel_text(await state.get_state())
    await state.clear()

    msg: Optional[Message] = getattr(call, "message", None)
    if msg is not None:
        with contextlib.suppress(TelegramAPIError):
            await msg.delete()

    bot = call.bot if isinstance(getattr(call, "bot", None), Bot) else None

    chat_id: Optional[int] = getattr(getattr(msg, "chat", None), "id", None)
    if chat_id is None:
        fu = getattr(call, "from_user", None)
        if isinstance(fu, User):
            chat_id = fu.id

    if bot is not None and chat_id is not None:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"{cancel_text}\n\n"
                f"{ADMIN_MESSAGES.get('admin_panel_greeting', 'Добро пожаловать в админ-панель!')}"
            ),
            reply_markup=menu_keyboard(
                ["⚙️ Модерация", "👥 Пользователи", "🎴 Карты"],
                ["📊 Статистика", "📣 Рассылка", "🚫 Логи"],
            ),
        )
    await call.answer()


async def send_lot_card_safe(message: Message, lot: Mapping[str, Any], text: str, kb: InlineKeyboardMarkup) -> None:
    image_id = lot.get("image_id") or lot.get("card_image_id")
    try:
        if image_id:
            await safe_answer_photo(
                message,
                image_id,
                caption=tg_clean(text),
                reply_markup=kb,
                parse_mode="HTML",
            )
        else:
            await message.answer(tg_clean(text), reply_markup=kb, parse_mode="HTML")
    except TelegramAPIError as e:
        bot = _resolve_bot_from_message(message)
        if bot is not None:
            await send_admin_log(bot, f"[ERROR] Не удалось отправить фото лота: {e}")
        else:
            with contextlib.suppress(TelegramAPIError):
                await message.answer(
                    tg_clean(text) + "\n[⚠️ Ошибка при отправке медиа]",
                    reply_markup=kb,
                    parse_mode="HTML",
                )


__all__ = (
    '_safe_strip',
    'parse_datetime_field',
    '_to_msk',
    '_human_wait',
    '_resolve_bot_from_message',
    '_ensure_sender',
    'as_message',
    'require_bot',
    '_call_maybe_await',
    'format_date_time_block',
    'owner_or_secret_required',
    'safe_edit_message',
    'notify_owners',
    'send_log_to_chats',
    'verify_log_chats',
    'get_cancel_text',
    'process_universal_cancel_text',
    'process_universal_cancel_callback',
    'send_lot_card_safe',
)

