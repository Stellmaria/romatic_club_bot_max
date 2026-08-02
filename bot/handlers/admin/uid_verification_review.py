"""UID verification review handlers.

The historical blocked-approval callback was registered after WHOIS.  It uses
a second router so the aggregate facade can preserve that exact position.
"""

from __future__ import annotations

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.admin.logs_admin import send_admin_log
from bot.handlers.admin.uid_admin_presentation import (
    kb_req_list,
    kb_verif_menu,
    render_req,
    render_uid_verif_view,
    send_media_any,
    safe_call_answer,
    safe_edit,
)
from bot.handlers.admin.uid_admin_shared import (
    REQUIRED_CONFIRMS,
    uidv_counts,
    uidv_user_line,
)
from bot.services.admin_thanks import admin_tag, build_thanks_kb
from bot.services.uid_verification import (
    UIDVerificationService,
    get_uid_verification_request,
    list_uid_verification_requests,
)
from bot.use_cases.common import (
    ApplicationConflict,
    ApplicationInvalidState,
    ApplicationNotFound,
)
from bot.use_cases.uid_moderation import (
    ApproveUidVerificationUseCase,
    ModerateUidCommand,
    RejectUidVerificationUseCase,
)
from bot.telegram.states import ModActionFSM
from bot.telegram.callback_parser import split_callback_data


router = Router(name=f"{__name__}.review")
late_router = Router(name=f"{__name__}.approve_blocked")


@router.message(Command("verif"), F.chat.type == "private")
@admin_only
async def verif_menu_cmd(message: types.Message) -> None:
    await message.answer("🧾 Меню UID-верификаций:", reply_markup=kb_verif_menu())


@router.message(F.text == "🧾 Верификация", F.chat.type == "private")
@admin_only
async def verif_menu_button(message: types.Message) -> None:
    await message.answer("🧾 Меню UID-верификаций:", reply_markup=kb_verif_menu())


@router.callback_query(F.data == "uidv|menu")
@admin_only
async def verif_menu_cb(call: types.CallbackQuery) -> None:
    await safe_edit(call, "🧾 Меню UID-верификаций:", reply_markup=kb_verif_menu())
    await call.answer()


@router.callback_query(F.data.startswith("uidv|list|"))
@admin_only
async def verif_list(call: types.CallbackQuery) -> None:
    parts = split_callback_data(call.data or "", "|")
    if len(parts) != 4:
        await call.answer("Кривые данные.", show_alert=True)
        return

    status = parts[2]
    page = int(parts[3])
    limit = 10
    offset = page * limit

    title_map = {
        "awaiting": "Ожидают подтверждений",
        "ready": "Готовы к проверке",
        "revision": "На доработке",
        "conflict": "Конфликты",
        "pending": "Ожидают подтверждений",
        "approved": "Одобрены",
        "rejected": "Отклонены",
    }
    title = title_map.get(status, status)

    items: list[dict]
    has_more = False

    if status in ("awaiting", "ready"):
        raw = await list_uid_verification_requests(status="pending", limit=500, offset=0)

        def _ok(it: dict) -> bool:
            cc = int(it.get("confirmed_cnt") or 0)
            if status == "awaiting":
                return cc < REQUIRED_CONFIRMS
            return cc >= REQUIRED_CONFIRMS

        filtered = [it for it in raw if _ok(it)]
        items = filtered[offset: offset + limit]
        has_more = len(filtered) > offset + limit

    else:
        # conflict / approved / rejected / pending
        items = await list_uid_verification_requests(status=status, limit=limit, offset=offset)
        has_more = len(items) == limit

    if not items:
        await safe_edit(call, f"Пусто: <b>{title}</b>.", reply_markup=kb_verif_menu())
        await call.answer()
        return

    await safe_edit(
        call,
        f"🧾 Заявки: <b>{title}</b>\nВыбери заявку:",
        reply_markup=kb_req_list(items, status=status, page=page, has_more=has_more),
    )
    await call.answer()


@router.callback_query(F.data.startswith("uidv|view|") | F.data.startswith("uidv|view_one|"))
@admin_only
async def verif_view(call: types.CallbackQuery) -> None:
    parts = split_callback_data(call.data or "", "|")
    if len(parts) < 3:
        await call.answer("Кривые данные.", show_alert=True)
        return

    req_id = int(parts[2])
    req = await get_uid_verification_request(request_id=req_id)
    if not req:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    await render_req(call, req_id, req)
    await call.answer()


@router.callback_query(F.data.startswith("uidv|proof|"))
@admin_only
async def verif_send_proof(call: types.CallbackQuery, bot: Bot) -> None:
    parts = split_callback_data(call.data or "", "|")
    if len(parts) < 3:
        await safe_call_answer(call, "Кривые данные.", show_alert=True)
        return

    req_id = int(parts[2])
    req = await get_uid_verification_request(request_id=req_id)
    if not req:
        await safe_call_answer(call, "Заявка не найдена.", show_alert=True)
        return

    proofs: list[tuple[str, str]] = []
    profile = (req.get("profile_proof_file_id") or "").strip()
    uidp = (req.get("uid_proof_file_id") or "").strip()
    reg = (req.get("reg_date_proof_file_id") or "").strip()

    if profile:
        proofs.append(("Профиль (код + рег. дата)", profile))
    if uidp:
        proofs.append(("UID (если отдельно)", uidp))
    if reg and reg != profile:
        proofs.append(("Дата регистрации (если отдельно)", reg))

    if not proofs:
        await safe_call_answer(call, "Пруфы не найдены.", show_alert=True)
        return

    # ✅ СРАЗУ отвечаем на callback (иначе “query is too old”)
    await safe_call_answer(call, "Отправляю пруфы в ЛС…")

    to_chat = call.from_user.id
    sent = 0
    forbidden = False
    last_err: Exception | None = None

    for title, packed in proofs:
        try:
            await send_media_any(
                bot,
                to_chat,
                packed,
                caption=f"{title} • заявка #{req_id}",
                # protect_content=False,  # включи если надо запретить пересылку/сейв
            )
            sent += 1
        except TelegramForbiddenError as e:
            forbidden = True
            last_err = e
            break
        except Exception as e:
            last_err = e
            continue

    # лог оставляем как был
    try:
        await send_admin_log(
            bot,
            "👁 <b>Админ открыл пруфы UID-верификации</b>\n"
            f"Заявка: <code>{req_id}</code>\n"
            f"Админ: @{call.from_user.username or 'id' + str(call.from_user.id)}"
        )
    except Exception:
        pass

    # ✅ Делаем ВИДИМУЮ реакцию
    if sent > 0:
        # в приватке можно написать сообщение (не зависит от callback timeout)
        if call.message and call.message.chat.type == "private":
            await call.message.answer("✅ Пруфы отправлены.")
        else:
            await safe_call_answer(call, "✅ Пруфы отправлены.")
        return

    # ничего не отправилось
    if forbidden:
        await safe_call_answer(
            call,
            "⚠️ Не могу отправить в ЛС. Открой личку с ботом и нажми /start (или разблокируй бота).",
            show_alert=True,
        )
    else:
        await safe_call_answer(
            call,
            "⚠️ Не удалось отправить пруфы (ошибка отправки).",
            show_alert=True,
        )


@router.callback_query(F.data.startswith("uidv|deals|"))
@admin_only
async def verif_send_deals(call: types.CallbackQuery, bot: Bot) -> None:
    parts = split_callback_data(call.data or "", "|")
    if len(parts) < 3:
        await safe_call_answer(call, "Кривые данные.", show_alert=True)
        return

    req_id = int(parts[2])
    req = await get_uid_verification_request(request_id=req_id)
    if not req:
        await safe_call_answer(call, "Заявка не найдена.", show_alert=True)
        return

    deals: list[str] = list(req.get("deal_file_ids") or [])
    names: list[str] = list(req.get("counterparty_usernames") or [])

    if not deals:
        await safe_call_answer(call, "Сделки не найдены.", show_alert=True)
        return

    await safe_call_answer(call, "Отправляю сделки в ЛС…")

    to_chat = call.from_user.id
    sent = 0
    forbidden = False
    last_err: Exception | None = None

    for i, packed in enumerate(deals, start=1):
        uname = names[i - 1] if i - 1 < len(names) else "—"
        uname_disp = f"@{str(uname).lstrip('@')}" if uname and uname != "—" else "—"
        try:
            await send_media_any(
                bot,
                to_chat,
                packed,
                caption=f"Сделка {i} • {uname_disp} • заявка #{req_id}",
                # protect_content=False,
            )
            sent += 1
        except TelegramForbiddenError as e:
            forbidden = True
            last_err = e
            break
        except Exception as e:
            last_err = e
            continue

    try:
        await send_admin_log(
            bot,
            "👁 <b>Админ открыл сделки UID-верификации</b>\n"
            f"Заявка: <code>{req_id}</code>\n"
            f"Админ: @{call.from_user.username or 'id' + str(call.from_user.id)}"
        )
    except Exception:
        pass

    if sent > 0:
        if call.message and call.message.chat.type == "private":
            await call.message.answer("✅ Сделки отправлены.")
        else:
            await safe_call_answer(call, "✅ Сделки отправлены.")
        return

    if forbidden:
        await safe_call_answer(
            call,
            "⚠️ Не могу отправить в ЛС. Открой личку с ботом и нажми /start (или разблокируй бота).",
            show_alert=True,
        )
    else:
        await safe_call_answer(
            call,
            "⚠️ Не удалось отправить сделки (ошибка отправки).",
            show_alert=True,
        )


async def _approve_uid_use_case() -> ApproveUidVerificationUseCase:
    service = await UIDVerificationService.create()
    return ApproveUidVerificationUseCase(
        get_request=service.get_request,
        decide=service.approve_request,
        required_confirmations=REQUIRED_CONFIRMS,
    )


async def _reject_uid_use_case() -> RejectUidVerificationUseCase:
    service = await UIDVerificationService.create()
    return RejectUidVerificationUseCase(
        get_request=service.get_request,
        decide=service.reject_request,
    )


@router.callback_query(F.data.startswith("uidv|approve|"))
@admin_only
async def verif_approve(call: types.CallbackQuery, bot: Bot):
    parts = split_callback_data(call.data or "", "|")
    if len(parts) < 3:
        await call.answer("Кривые данные.", show_alert=True)
        return
    req_id = int(parts[2])
    try:
        result = await (await _approve_uid_use_case()).execute(
            ModerateUidCommand(
                request_id=req_id,
                admin_id=call.from_user.id,
                admin_username=call.from_user.username or call.from_user.full_name,
            )
        )
    except ApplicationConflict as exc:
        confirmed = int(exc.details.get("confirmed") or 0)
        required = int(exc.details.get("required") or REQUIRED_CONFIRMS)
        await call.answer(
            f"Нельзя одобрить: подтверждений {confirmed}/{required}.",
            show_alert=True,
        )
        try:
            await render_uid_verif_view(call, req_id)
        except Exception:
            pass
        return
    except ApplicationNotFound:
        await call.answer("Заявка не найдена.", show_alert=True)
        return
    except ApplicationInvalidState as exc:
        await call.answer(
            f"Не удалось: {exc.details.get('reason') or 'уже обработано'}",
            show_alert=True,
        )
        return

    req_after = result.request
    moderator = admin_tag(call.from_user)
    try:
        thanks_kb = await build_thanks_kb(req_id, moderator)
    except Exception:
        thanks_kb = None
    try:
        await bot.send_message(
            chat_id=int(req_after["user_id"]),
            protect_content=False,
            text=(
                "✅ <b>UID-верификация одобрена</b>\n\n"
                f"Заявка: <code>#{req_id}</code>\n"
                f"Модератор: {moderator}\n\n"
                "Если хочешь, можешь поблагодарить модератора кнопкой ниже."
            ),
            reply_markup=thanks_kb,
            parse_mode="HTML",
        )
    except Exception:
        pass

    confirmed, rejected, pending = uidv_counts(req_after)
    try:
        await send_admin_log(
            bot,
            "uidv",
            "✅ <b>UID-верификация одобрена</b>\n"
            f"Заявка: <code>#{req_id}</code>\n"
            f"Пользователь: {uidv_user_line(req_after)} "
            f"(id=<code>{req_after.get('user_id')}</code>)\n"
            f"Подтверждения: <b>{confirmed}/{REQUIRED_CONFIRMS}</b> "
            f"(pending={pending}, rejected={rejected})\n"
            f"Админ: {moderator}",
        )
    except Exception:
        pass
    await call.answer("✅ Одобрено")
    try:
        await render_uid_verif_view(call, req_id)
    except Exception:
        pass


@router.callback_query(F.data.startswith("uidv|reject|"))
@admin_only
async def verif_reject(call: types.CallbackQuery, state: FSMContext) -> None:
    req_id = int(split_callback_data(call.data or "", "|")[2])
    await state.set_state(ModActionFSM.waiting_for_reject_uid_verification_reason)
    await state.update_data(uidv_reject_req_id=req_id)
    await call.message.answer(f"Напиши причину отклонения заявки #{req_id} текстом:")
    await call.answer()


@router.message(ModActionFSM.waiting_for_reject_uid_verification_reason, F.chat.type == "private")
@admin_only
async def verif_reject_reason(message: types.Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    req_id = int(data.get("uidv_reject_req_id") or 0)
    reason = (message.text or "").strip()
    if not req_id or not reason:
        await message.answer("Нужна причина текстом.")
        return

    try:
        result = await (await _reject_uid_use_case()).execute(
            ModerateUidCommand(
                request_id=req_id,
                admin_id=message.from_user.id,
                admin_username=(
                    message.from_user.username or message.from_user.full_name
                ),
                reason=reason,
            )
        )
    except ApplicationNotFound:
        await message.answer("Заявка не найдена.")
        await state.clear()
        return
    except ApplicationInvalidState as exc:
        await message.answer(
            f"Не удалось отклонить: {exc.details.get('reason') or 'уже обработано'}."
        )
        await state.clear()
        return

    await state.clear()
    req_after = result.request
    moderator = admin_tag(message.from_user)
    try:
        thanks_kb = await build_thanks_kb(req_id, moderator)
    except Exception:
        thanks_kb = None
    try:
        await bot.send_message(
            chat_id=int(req_after["user_id"]),
            protect_content=False,
            text=(
                "❌ <b>UID-верификация отклонена</b>\n\n"
                f"Заявка: <code>#{req_id}</code>\n"
                f"Модератор: {moderator}\n"
                f"Причина: {reason}\n\n"
                "Можешь отправить заявку заново, когда исправишь проблему."
            ),
            reply_markup=thanks_kb,
            parse_mode="HTML",
        )
    except Exception:
        pass

    confirmed, rejected, pending = uidv_counts(req_after)
    try:
        await send_admin_log(
            bot,
            "uidv",
            "❌ <b>UID-верификация отклонена</b>\n"
            f"Заявка: <code>#{req_id}</code>\n"
            f"Пользователь: {uidv_user_line(req_after)} "
            f"(id=<code>{req_after.get('user_id')}</code>)\n"
            f"Подтверждения: <b>{confirmed}/{REQUIRED_CONFIRMS}</b> "
            f"(pending={pending}, rejected={rejected})\n"
            f"Причина: {reason}\n"
            f"Админ: {moderator}",
        )
    except Exception:
        pass
    await message.answer(f"Отклонено ❌\nПричина: {reason}")


@late_router.callback_query(F.data.startswith("uidv|approve_blocked|"))
@admin_only
async def verif_approve_blocked(call: types.CallbackQuery) -> None:
    parts = split_callback_data(call.data or "", "|")
    if len(parts) < 3:
        await call.answer("Кривые данные.", show_alert=True)
        return
    req_id = int(parts[2])

    req = await get_uid_verification_request(request_id=req_id)
    if not req:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    confirmed, rejected, pending = uidv_counts(req)

    await call.answer(
        f"Нельзя одобрить: подтверждений {confirmed}/{REQUIRED_CONFIRMS}.",
        show_alert=True
    )


__all__ = ["router","late_router","verif_menu_cmd","verif_menu_button","verif_menu_cb","verif_list","verif_view","verif_send_proof","verif_send_deals","verif_approve","verif_reject","verif_reject_reason","verif_approve_blocked"]
