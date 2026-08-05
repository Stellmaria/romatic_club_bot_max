# ruff: noqa: RUF001
"""Recovery layer for legacy UID verification requests and revision flow."""

from __future__ import annotations

import html
import logging
import re
import secrets
from collections.abc import Iterable

from aiogram import Bot, F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.logs_admin import send_admin_log
from bot.handlers.admin.uid_verification_review import verif_approve as legacy_verif_approve
from bot.repositories.uid_verification_recovery import (
    ensure_request_uid,
    prepare_revision_profile,
    replace_revision_profile,
    replace_revision_uid,
    save_revision_other_response,
)
from bot.services import uid_verification as uid_verification_service
from bot.telegram.callback_parser import split_callback_data
from bot.telegram.states import UIDVerificationFixFSM
from bot.uid_crypto import norm_uid

logger = logging.getLogger(__name__)
router = Router(name=__name__)

UID_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)
CODE_RE = re.compile(r"^MX-[0-9]{5}$", re.IGNORECASE)
MAX_OTHER_RESPONSE_LENGTH = 3000

_REV_FLAG_TITLES: dict[str, str] = {
    "profile": "📷 Профиль + код",
    "deal1_screen": "🤝 Сделка 1: скрин",
    "deal1_username": "🤝 Сделка 1: ник",
    "deal2_screen": "🤝 Сделка 2: скрин",
    "deal2_username": "🤝 Сделка 2: ник",
    "deal3_screen": "🤝 Сделка 3: скрин",
    "deal3_username": "🤝 Сделка 3: ник",
    "deal4_screen": "🤝 Сделка 4: скрин",
    "deal4_username": "🤝 Сделка 4: ник",
    "deal5_screen": "🤝 Сделка 5: скрин",
    "deal5_username": "🤝 Сделка 5: ник",
    "extra": "➕ Доп. пруфы",
    "other": "📝 Другое (по комментарию модератора)",
}


class UIDRevisionRecoveryFSM(StatesGroup):
    """Temporary states used by legacy revision recovery and completion."""

    waiting_uid = State()
    waiting_profile = State()
    waiting_other = State()


def _valid_uid(value: str | None) -> str | None:
    normalized = norm_uid(value or "")
    return normalized if UID_RE.fullmatch(normalized) else None


def _new_revision_code() -> str:
    return f"MX-{secrets.randbelow(90_000) + 10_000:05d}"


def _pack_media(message: types.Message) -> str | None:
    if message.photo:
        return f"p:{message.photo[-1].file_id}"
    if message.video:
        return f"v:{message.video.file_id}"
    if message.document:
        return f"d:{message.document.file_id}"
    return None


def _normalize_other_response(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text or len(text) > MAX_OTHER_RESPONSE_LENGTH:
        return None
    return text


def _remaining_after_completion(remaining: Iterable[str], completed: str) -> list[str]:
    return [str(flag) for flag in remaining if str(flag) != completed]


def _revision_keyboard(
    request_id: int,
    remaining: Iterable[str],
) -> types.InlineKeyboardMarkup:
    flags = [str(flag) for flag in remaining]
    builder = InlineKeyboardBuilder()
    for flag in flags:
        builder.button(
            text=_REV_FLAG_TITLES.get(flag, flag),
            callback_data=f"uidv_fix_item|{request_id}|{flag}",
        )
    if not flags:
        builder.button(
            text="✅ Отправить на проверку",
            callback_data=f"uidv_fix_send|{request_id}",
        )
    builder.button(text="⬅️ Назад", callback_data="uidv|start")
    builder.adjust(1)
    return builder.as_markup()


async def _revision_flags(request_id: int) -> list[str]:
    service = await uid_verification_service.UIDVerificationService.create()
    flags = await service.revision_flags(request_id)
    banned = {"uid_proof", "reg_date"}
    return [value for raw in flags if (value := str(raw).strip()) and value not in banned]


def _revision_text(
    request_id: int,
    flags: list[str],
    reason: str,
) -> str:
    lines = "\n".join(f"• {html.escape(_REV_FLAG_TITLES.get(flag, flag))}" for flag in flags)
    reason_block = f"\n\n<b>Комментарий модератора:</b>\n{html.escape(reason)}" if reason else ""
    return (
        f"🔧 <b>Доработка заявки #{request_id}</b>\n\n"
        f"<b>Нужно исправить:</b>\n{lines}"
        f"{reason_block}\n\n"
        "Выбери пункт и пришли исправление. Уже принятые данные повторно "
        "отправлять не нужно."
    )


async def _show_revision_menu(
    message: types.Message,
    *,
    request_id: int,
    flags: list[str],
    reason: str,
) -> None:
    await message.answer(
        _revision_text(request_id, flags, reason),
        reply_markup=_revision_keyboard(request_id, flags),
        parse_mode="HTML",
        protect_content=False,
    )


async def _active_revision_data(
    state: FSMContext,
    *,
    request_id: int,
    required_flag: str,
) -> tuple[dict[str, object], list[str]] | None:
    data = await state.get_data()
    if int(data.get("uidv_fix_req_id") or 0) != int(request_id):
        return None
    remaining = [str(flag) for flag in (data.get("uidv_fix_remaining") or [])]
    if required_flag not in remaining:
        return None
    return data, remaining


@router.callback_query(F.data.startswith("uidv|approve|"))
@admin_only
async def approve_uid_with_legacy_recovery(
    call: types.CallbackQuery,
    bot: Bot,
) -> None:
    parts = split_callback_data(call.data or "", "|")
    if len(parts) < 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    try:
        request_id = int(parts[2])
    except (TypeError, ValueError):
        await call.answer("Некорректный номер заявки.", show_alert=True)
        return

    try:
        result = await ensure_request_uid(
            request_id,
            expected_user_id=None,
            allowed_statuses={"pending"},
        )
    except Exception:
        logger.exception(
            "Failed to preflight UID verification approval",
            extra={"request_id": request_id},
        )
        await call.answer(
            "Не удалось проверить UID заявки. Повтори попытку позже.",
            show_alert=True,
        )
        return

    if result == "needs_uid":
        await call.answer(
            "В старой заявке не сохранился UID. Отправь её на доработку: "
            "пользователь повторно введёт UID.",
            show_alert=True,
        )
        return
    if result == "forbidden":
        await call.answer("Нет доступа к заявке.", show_alert=True)
        return
    if result == "not_found":
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    await legacy_verif_approve(call, bot)


@router.callback_query(F.data.startswith("uidv_fix|"))
async def start_uid_revision_recovery(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    parts = split_callback_data(call.data or "", "|")
    if len(parts) < 2:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    try:
        request_id = int(parts[1])
    except (TypeError, ValueError):
        await call.answer("Некорректный номер заявки.", show_alert=True)
        return

    service = await uid_verification_service.UIDVerificationService.create()
    request = await service.get_request(request_id)
    if not request:
        await call.answer("Заявка не найдена.", show_alert=True)
        return
    if int(request.get("user_id") or 0) != int(call.from_user.id):
        await call.answer("Это не твоя заявка.", show_alert=True)
        return
    if str(request.get("status") or "").strip().lower() != "revision":
        await call.answer(
            "Эта заявка сейчас не находится на доработке.",
            show_alert=True,
        )
        return

    flags = await _revision_flags(request_id)
    if not flags:
        await call.answer(
            "Модератор не указал, что исправлять. Обратись к администрации.",
            show_alert=True,
        )
        return

    reason = str(request.get("revision_reason") or "").strip()
    await state.clear()
    await state.update_data(
        uidv_fix_req_id=request_id,
        uidv_fix_remaining=flags,
        uidv_fix_current=None,
        uidv_fix_extra=[],
        uidv_fix_reason=reason,
    )

    try:
        uid_state = await ensure_request_uid(
            request_id,
            expected_user_id=call.from_user.id,
            allowed_statuses={"revision"},
        )
    except Exception:
        logger.exception(
            "Failed to prepare UID verification revision",
            extra={"request_id": request_id, "user_id": call.from_user.id},
        )
        await call.answer(
            "Не удалось открыть доработку. Повтори попытку позже.",
            show_alert=True,
        )
        return

    if uid_state == "needs_uid":
        await state.set_state(UIDRevisionRecoveryFSM.waiting_uid)
        if isinstance(call.message, types.Message):
            await call.message.answer(
                "🆔 В старой заявке UID не сохранился после переноса данных.\n\n"
                "Пришли UID текстом: ровно 24 шестнадцатеричных символа. "
                "После этого откроется список исправлений.",
                protect_content=False,
            )
        await call.answer()
        return
    if uid_state != "ready":
        await call.answer(
            "Не удалось открыть эту заявку на доработку.",
            show_alert=True,
        )
        return

    await state.set_state(UIDVerificationFixFSM.choosing_item)
    if isinstance(call.message, types.Message):
        await _show_revision_menu(
            call.message,
            request_id=request_id,
            flags=flags,
            reason=reason,
        )
    await call.answer()


@router.message(
    UIDRevisionRecoveryFSM.waiting_uid,
    F.chat.type == "private",
)
async def save_missing_revision_uid(
    message: types.Message,
    state: FSMContext,
) -> None:
    user = message.from_user
    if user is None:
        await state.clear()
        await message.answer("Не удалось определить пользователя. Открой доработку заново.")
        return

    normalized = _valid_uid(message.text)
    if normalized is None:
        await message.answer("UID должен содержать ровно 24 символа: цифры 0–9 и буквы a–f.")
        return

    data = await state.get_data()
    request_id = int(data.get("uidv_fix_req_id") or 0)
    flags = [str(flag) for flag in (data.get("uidv_fix_remaining") or [])]
    reason = str(data.get("uidv_fix_reason") or "")

    if request_id <= 0 or not flags:
        await state.clear()
        await message.answer("Сессия доработки устарела. Открой кнопку «Исправить заявку» заново.")
        return

    try:
        result = await replace_revision_uid(
            request_id,
            user_id=user.id,
            uid=normalized,
        )
    except Exception:
        logger.exception(
            "Failed to restore UID for revision request",
            extra={"request_id": request_id, "user_id": user.id},
        )
        await message.answer("Не удалось сохранить UID. Повтори попытку позже.")
        return

    if result != "ready":
        await state.clear()
        await message.answer("Заявка уже изменила статус. Открой раздел верификации заново.")
        return

    await state.set_state(UIDVerificationFixFSM.choosing_item)
    await state.update_data(uidv_fix_current=None)
    await message.answer("✅ UID восстановлен.", protect_content=False)
    await _show_revision_menu(
        message,
        request_id=request_id,
        flags=flags,
        reason=reason,
    )


@router.callback_query(
    F.data.startswith("uidv_fix_item|"),
    F.data.endswith("|profile"),
)
async def start_revision_profile(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    parts = split_callback_data(call.data or "", "|")
    if len(parts) != 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    request_id = int(parts[1] or 0)
    session = await _active_revision_data(
        state,
        request_id=request_id,
        required_flag="profile",
    )
    if session is None:
        await call.answer("Сначала открой доработку заново.", show_alert=True)
        return

    code = _new_revision_code()
    try:
        result = await prepare_revision_profile(
            request_id,
            user_id=call.from_user.id,
            verification_code=code,
        )
    except Exception:
        logger.exception(
            "Failed to prepare replacement profile proof",
            extra={"request_id": request_id, "user_id": call.from_user.id},
        )
        await call.answer("Не удалось создать новый код. Повтори попытку позже.", show_alert=True)
        return

    if result != "ready":
        await call.answer("Заявка уже изменила статус. Открой доработку заново.", show_alert=True)
        return

    await state.set_state(UIDRevisionRecoveryFSM.waiting_profile)
    await state.update_data(uidv_fix_current="profile", uidv_fix_profile_code=code)
    if isinstance(call.message, types.Message):
        await call.message.answer(
            "📷 <b>Исправление профиля</b>\n\n"
            f"1. Временно добавь в ник новый код: <code>{code}</code>\n"
            "2. Сделай свежий скрин профиля, где видны UID, код и дата регистрации.\n"
            "3. Верни обычный ник и пришли скрин сюда как фото или файл.",
            parse_mode="HTML",
            protect_content=False,
        )
    await call.answer()


@router.message(
    UIDRevisionRecoveryFSM.waiting_profile,
    F.chat.type == "private",
)
async def save_revision_profile(
    message: types.Message,
    state: FSMContext,
) -> None:
    user = message.from_user
    if user is None:
        await state.clear()
        await message.answer("Не удалось определить пользователя. Открой доработку заново.")
        return

    packed = _pack_media(message)
    if packed is None:
        await message.answer("Нужно прислать свежий скрин профиля как фото, видео или файл.")
        return

    data = await state.get_data()
    request_id = int(data.get("uidv_fix_req_id") or 0)
    remaining = [str(flag) for flag in (data.get("uidv_fix_remaining") or [])]
    reason = str(data.get("uidv_fix_reason") or "")
    if request_id <= 0 or "profile" not in remaining:
        await state.clear()
        await message.answer("Сессия доработки устарела. Открой её заново.")
        return

    try:
        result = await replace_revision_profile(
            request_id,
            user_id=user.id,
            packed_file_id=packed,
        )
    except Exception:
        logger.exception(
            "Failed to save replacement profile proof",
            extra={"request_id": request_id, "user_id": user.id},
        )
        await message.answer("Не удалось сохранить скрин. Повтори попытку позже.")
        return

    if result != "ready":
        await state.clear()
        await message.answer("Заявка уже изменила статус. Открой раздел верификации заново.")
        return

    remaining = _remaining_after_completion(remaining, "profile")
    await state.set_state(UIDVerificationFixFSM.choosing_item)
    await state.update_data(
        uidv_fix_remaining=remaining,
        uidv_fix_current=None,
        uidv_fix_profile_code=None,
    )
    await message.answer(
        f"✅ Новый профиль и код сохранены. Осталось пунктов: <b>{len(remaining)}</b>",
        parse_mode="HTML",
        protect_content=False,
    )
    await _show_revision_menu(
        message,
        request_id=request_id,
        flags=remaining,
        reason=reason,
    )


@router.callback_query(
    F.data.startswith("uidv_fix_item|"),
    F.data.endswith("|other"),
)
async def start_revision_other(
    call: types.CallbackQuery,
    state: FSMContext,
) -> None:
    parts = split_callback_data(call.data or "", "|")
    if len(parts) != 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    request_id = int(parts[1] or 0)
    session = await _active_revision_data(
        state,
        request_id=request_id,
        required_flag="other",
    )
    if session is None:
        await call.answer("Сначала открой доработку заново.", show_alert=True)
        return

    data, _ = session
    reason = str(data.get("uidv_fix_reason") or "").strip()
    await state.set_state(UIDRevisionRecoveryFSM.waiting_other)
    await state.update_data(uidv_fix_current="other")
    if isinstance(call.message, types.Message):
        reason_block = f"\n\nКомментарий модератора:\n{html.escape(reason)}" if reason else ""
        await call.message.answer(
            "📝 <b>Ответ по пункту «Другое»</b>\n\n"
            "Напиши одним сообщением, что исправлено или что нужно добавить к заявке. "
            f"Текст будет сохранён в истории заявки.{reason_block}",
            parse_mode="HTML",
            protect_content=False,
        )
    await call.answer()


@router.message(
    UIDRevisionRecoveryFSM.waiting_other,
    F.chat.type == "private",
)
async def save_revision_other(
    message: types.Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    user = message.from_user
    if user is None:
        await state.clear()
        await message.answer("Не удалось определить пользователя. Открой доработку заново.")
        return

    response = _normalize_other_response(message.text)
    if response is None:
        await message.answer(
            f"Нужен непустой текст длиной не более {MAX_OTHER_RESPONSE_LENGTH} символов."
        )
        return

    data = await state.get_data()
    request_id = int(data.get("uidv_fix_req_id") or 0)
    remaining = [str(flag) for flag in (data.get("uidv_fix_remaining") or [])]
    reason = str(data.get("uidv_fix_reason") or "")
    if request_id <= 0 or "other" not in remaining:
        await state.clear()
        await message.answer("Сессия доработки устарела. Открой её заново.")
        return

    try:
        result = await save_revision_other_response(
            request_id,
            user_id=user.id,
            response=response,
        )
    except Exception:
        logger.exception(
            "Failed to persist free-form UID revision response",
            extra={"request_id": request_id, "user_id": user.id},
        )
        await message.answer("Не удалось сохранить комментарий. Повтори попытку позже.")
        return

    if result != "ready":
        await state.clear()
        await message.answer("Заявка уже изменила статус. Открой раздел верификации заново.")
        return

    remaining = _remaining_after_completion(remaining, "other")
    await state.set_state(UIDVerificationFixFSM.choosing_item)
    await state.update_data(uidv_fix_remaining=remaining, uidv_fix_current=None)

    await message.answer(
        f"✅ Комментарий сохранён в истории заявки. Осталось пунктов: <b>{len(remaining)}</b>",
        parse_mode="HTML",
        protect_content=False,
    )
    await _show_revision_menu(
        message,
        request_id=request_id,
        flags=remaining,
        reason=reason,
    )

    try:
        await send_admin_log(
            bot,
            "📝 <b>UID-верификация: ответ пользователя по доработке</b>\n"
            f"Заявка: <code>#{request_id}</code>\n"
            f"Пользователь: <code>{user.id}</code>\n\n"
            f"{html.escape(response)}",
        )
    except Exception:
        logger.exception(
            "Failed to send UID revision response to admin log",
            extra={"request_id": request_id, "user_id": user.id},
        )


__all__ = [
    "MAX_OTHER_RESPONSE_LENGTH",
    "UIDRevisionRecoveryFSM",
    "approve_uid_with_legacy_recovery",
    "router",
    "save_missing_revision_uid",
    "save_revision_other",
    "save_revision_profile",
    "start_revision_other",
    "start_revision_profile",
    "start_uid_revision_recovery",
]
