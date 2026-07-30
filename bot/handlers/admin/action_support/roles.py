"""Administrator-role and trusted-user workflows."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping, Optional, Tuple

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, User

from bot.core.settings import ADMINS_OWNERS
from bot.handlers.admin.action_support.transport import (
    _ensure_sender,
    _resolve_bot_from_message,
    _safe_strip,
    notify_owners,
    require_bot,
)
from bot.handlers.admin.helper.admin_constants import ADMIN_MESSAGES, SYSTEM_MESSAGES
from bot.handlers.admin.helper.new.formatting import format_admin_action_log
from bot.handlers.admin.helper.user_helpers import format_user_ref
from bot.security import admin_secret_matches
from bot.services.admin_logging import send_admin_log
from db.admin import add_admin, is_admin, log_admin_action, remove_admin
from db.users import set_trusted_status

async def add_admin_role(
        user_id: int,
        username: Optional[str],
        by_admin_id: int,
        *,
        bot: Optional[Bot] = None,
        admin_username: Optional[str] = None,
) -> None:
    if username is not None and not isinstance(username, str):
        username = None
    await add_admin(user_id, username, by_admin_id)
    if bot:
        text = format_admin_action_log(
            action="add_admin",
            admin={"id": by_admin_id, "username": admin_username},
            target={"user_id": user_id, "username": username},
        )
        await send_admin_log(bot, text)


async def remove_admin_role(
        user_id: int,
        by_admin_id: int,
        *,
        username: Optional[str] = None,
        bot: Optional[Bot] = None,
        admin_username: Optional[str] = None,
) -> None:
    await remove_admin(user_id)
    if bot:
        text = format_admin_action_log(
            action="remove_admin",
            admin={"id": by_admin_id, "username": admin_username},
            target={"user_id": user_id, "username": username},
        )
        await send_admin_log(bot, text)


def _parse_admin_command_args(
        message: Message, is_owner: bool
) -> Tuple[Optional[str], Optional[str]]:
    text = _safe_strip(getattr(message, "text", None))
    parts = text.split()
    if parts and parts[0].startswith("/"):
        parts = parts[1:]
    if not parts:
        return None, None
    if is_owner:
        who = parts[0]
        password = parts[1] if len(parts) > 1 else None
        return who, password
    if len(parts) < 2:
        return None, None
    return parts[0], parts[1]


async def _ensure_bot_or_fail(
        message: Message, state: Optional[FSMContext]
) -> Optional[Bot]:
    bot: Optional[Bot] = require_bot(message)
    if bot is None:
        await message.answer(
            "Техническая пауза: бот недоступен. Повторите позже."
        )
        if state:
            await state.clear()
        return None
    return bot


def _admin_link_text(
        by_admin_id: int, by_admin_username: Optional[str]
) -> str:
    return (
        f"<a href='tg://user?id={by_admin_id}'>"
        f"{by_admin_username or by_admin_id}</a>"
    )


async def _remove_admin_flow(
        *,
        who_id: int,
        who_username: Optional[str],
        by_admin_id: int,
        by_admin_username: Optional[str],
        message: Message,
        bot: Bot,
) -> None:
    await remove_admin_role(
        user_id=who_id,
        by_admin_id=by_admin_id,
        username=who_username,
        bot=bot,
        admin_username=by_admin_username,
    )
    await message.answer(
        ADMIN_MESSAGES["user_removed_admin"].format(user_id=who_id)
    )
    await log_admin_action(
        user_id=by_admin_id,
        action_type="remove_admin",
        auction_id=None,
        details=f"Удалён админ {who_id} (@{who_username or 'no_username'})",
    )


async def _add_admin_flow(
        *,
        who_id: int,
        who_username: Optional[str],
        by_admin_id: int,
        by_admin_username: Optional[str],
        message: Message,
        bot: Bot,
        state: Optional[FSMContext],
) -> None:
    if await is_admin(who_id):
        await message.answer("Пользователь уже является администратором.")
        if state:
            await state.clear()
        return
    await add_admin_role(
        user_id=who_id,
        username=who_username,
        by_admin_id=by_admin_id,
        bot=bot,
        admin_username=by_admin_username,
    )
    await message.answer(
        ADMIN_MESSAGES["user_now_admin"].format(user_id=who_id),
        parse_mode="HTML",
    )
    await log_admin_action(
        user_id=by_admin_id,
        action_type="add_admin",
        auction_id=None,
        details=f"Добавлен админ {who_id} (@{who_username or 'no_username'})",
    )


async def do_admin_add_remove(
        who_id: int,
        who_username: Optional[str],
        by_admin_id: int,
        by_admin_username: Optional[str],
        is_remove: bool,
        message: Message,
        state: Optional[FSMContext] = None,
) -> None:
    bot = await _ensure_bot_or_fail(message, state)
    if bot is None:
        return

    if is_remove and who_id in ADMINS_OWNERS:
        admin_link = _admin_link_text(by_admin_id, by_admin_username)
        await log_admin_action(
            user_id=by_admin_id,
            action_type="remove_owner_attempt",
            auction_id=None,
            details=f"Попытка удалить владельца {who_id}",
        )
        await notify_owners(
            bot, f"🚫 Попытка удалить владельца! Попытался: {admin_link}"
        )
        await message.answer("Нельзя удалить владельца.")
        if state:
            await state.clear()
        return

    if is_remove and who_id == by_admin_id:
        await message.answer(SYSTEM_MESSAGES["cannot_delete_self"])
        if state:
            await state.clear()
        return

    if is_remove:
        await _remove_admin_flow(
            who_id=who_id,
            who_username=who_username,
            by_admin_id=by_admin_id,
            by_admin_username=by_admin_username,
            message=message,
            bot=bot,
        )
    else:
        await _add_admin_flow(
            who_id=who_id,
            who_username=who_username,
            by_admin_id=by_admin_id,
            by_admin_username=by_admin_username,
            message=message,
            bot=bot,
            state=state,
        )

    if state:
        await state.clear()


async def admin_add_remove(
        message: Message, state: FSMContext, is_remove: bool = False
) -> None:
    fu = getattr(message, "from_user", None)
    if not isinstance(fu, User):
        await message.answer("Не могу определить отправителя команды.")
        return

    is_owner = fu.id in ADMINS_OWNERS
    who, password = _parse_admin_command_args(message, is_owner)
    if not who:
        await message.answer(
            SYSTEM_MESSAGES["syntax_error"].format(
                example="Пример: /add_admin @username пароль"
            )
        )
        return
    if not is_owner and not admin_secret_matches(password):
        await message.answer(SYSTEM_MESSAGES["invalid_password"])
        return

    from bot.handlers.helper.helpers_users import (
        resolve_user_identifier,
    )

    user = await resolve_user_identifier(who)
    if not user:
        await message.answer(SYSTEM_MESSAGES["user_not_found"])
        return

    await do_admin_add_remove(
        who_id=user["user_id"],
        who_username=user.get("username"),
        by_admin_id=fu.id,
        by_admin_username=fu.username,
        is_remove=is_remove,
        message=message,
        state=state,
    )


async def give_trusted_status(
        user_id: int,
        by_admin_id: int,
        *,
        username: Optional[str] = None,
        bot: Optional[Bot] = None,
        admin_username: Optional[str] = None,
) -> None:
    await set_trusted_status(user_id, True)
    if bot:
        text = format_admin_action_log(
            action="give_trusted",
            admin={"id": by_admin_id, "username": admin_username},
            target={"user_id": user_id, "username": username},
        )
        await send_admin_log(bot, text)
    await log_admin_action(
        user_id=by_admin_id,
        action_type="give_trusted",
        auction_id=None,
        details=f"Выдан trusted @{username or user_id} (id {user_id})",
    )


async def remove_trusted_status(
        user_id: int,
        by_admin_id: int,
        *,
        username: Optional[str] = None,
        bot: Optional[Bot] = None,
        admin_username: Optional[str] = None,
) -> None:
    await set_trusted_status(user_id, False)
    if bot:
        text = format_admin_action_log(
            action="remove_trusted",
            admin={"id": by_admin_id, "username": admin_username},
            target={"user_id": user_id, "username": username},
        )
        await send_admin_log(bot, text)
    await log_admin_action(
        user_id=by_admin_id,
        action_type="remove_trusted",
        auction_id=None,
        details=f"Снят trusted @{username or user_id} (id {user_id})",
    )


async def _resolve_user_or_error(
        who: str, answer: Callable[[str], Awaitable[Any]]
) -> Optional[dict]:
    from bot.handlers.admin.helper.user_helpers import (
        ensure_user_by_username_or_id,
    )

    user = await ensure_user_by_username_or_id(who)
    if not user:
        await answer(SYSTEM_MESSAGES["user_not_found"])
        return None
    return user


def _extract_who_text(who: Optional[str], message: Message) -> str:
    if isinstance(who, str) and who.strip():
        return who.strip()
    raw_text = getattr(message, "text", None)
    return raw_text.strip() if isinstance(raw_text, str) else ""


def _trusted_result_text(grant: bool, user: Mapping[str, Any]) -> str:
    action = "выдан" if grant else "снят"
    return (
        f"Статус 'Доверенный' {action} у пользователя "
        f"{format_user_ref(dict(user))}"
    )


async def _actor_and_bot_or_fail(
        message: Message, state: Optional[FSMContext], bot: Optional[Bot]
) -> Optional[Tuple[int, Optional[str], Bot]]:
    by_admin_id, admin_username = _ensure_sender(message)
    if by_admin_id is None:
        await message.answer("Не могу определить отправителя команды.")
        if state:
            await state.clear()
        return None
    bot_resolved = _resolve_bot_from_message(message, bot)
    if bot_resolved is None:
        await message.answer(
            "Техническая пауза: бот недоступен. Повторите позже."
        )
        if state:
            await state.clear()
        return None
    return by_admin_id, admin_username, bot_resolved


async def _do_trusted_action(
        *,
        message: Message,
        state: Optional[FSMContext],
        who: Optional[str],
        bot: Optional[Bot],
        grant: bool,
) -> None:
    who_text = _extract_who_text(who, message)
    user: Optional[Mapping[str, Any]] = await _resolve_user_or_error(
        who_text, message.answer
    )
    if not user:
        return

    actor = await _actor_and_bot_or_fail(message, state, bot)
    if actor is None:
        return
    by_admin_id, admin_username, bot_resolved = actor

    if grant:
        await give_trusted_status(
            user_id=user["user_id"],
            by_admin_id=by_admin_id,
            username=user.get("username"),
            bot=bot_resolved,
            admin_username=admin_username,
        )
    else:
        await remove_trusted_status(
            user_id=user["user_id"],
            by_admin_id=by_admin_id,
            username=user.get("username"),
            bot=bot_resolved,
            admin_username=admin_username,
        )

    await message.answer(_trusted_result_text(grant, user))

    if state:
        await state.clear()


__all__ = (
    'add_admin_role',
    'remove_admin_role',
    '_parse_admin_command_args',
    '_ensure_bot_or_fail',
    '_admin_link_text',
    '_remove_admin_flow',
    '_add_admin_flow',
    'do_admin_add_remove',
    'admin_add_remove',
    'give_trusted_status',
    'remove_trusted_status',
    '_resolve_user_or_error',
    '_extract_who_text',
    '_trusted_result_text',
    '_actor_and_bot_or_fail',
    '_do_trusted_action',
)

