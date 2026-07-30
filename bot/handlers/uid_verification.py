import asyncio
import html
import random
import re

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin.logs_admin import send_admin_log
from bot.uid_crypto import uid_decrypt
from db.legacy import (
    create_uid_verification_request,
    add_uid_verification_confirmation,
    set_uid_verification_confirmation_message,
    set_uid_verification_confirmation_status,
    get_user_basic_info_by_username,
    get_verified_uid_for_user,
    fetch,
    fetchrow,
    execute,
    clear_uid_verification_request_revision,
    replace_uid_verification_request_extra_proofs,
    set_uid_verification_request_deal_media,
    set_uid_verification_request_deal_username,
    set_uid_verification_request_profile_proof,
    set_uid_verification_request_reg_date_proof,
    set_uid_verification_request_uid_proof, get_uid_verification_request,
)

from bot.legacy_fsm import UIDVerificationFSM, UIDVerificationFixFSM

router = Router()

UID_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)
CODE_RE = re.compile(r"^MX-[0-9]{5}$", re.IGNORECASE)
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")

MIN_REQUIRED = 3
MAX_REQUESTS = 5
CONFIRM_TTL_HOURS = 72


# -------------------- helpers --------------------

def _tag_user(u: types.User) -> str:
    uname = (u.username or "").strip()
    return f"@{uname}" if uname else f"id{u.id}"


def _parse_username(text: str) -> str | None:
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("@"):
        t = t[1:]
    t = t.split()[0]
    if not USERNAME_RE.fullmatch(t):
        return None
    return t.lower()


def _pack_media(msg: types.Message) -> str | None:
    if msg.photo:
        return f"p:{msg.photo[-1].file_id}"
    if msg.video:
        return f"v:{msg.video.file_id}"
    if msg.document:
        return f"d:{msg.document.file_id}"
    return None


def _gen_code() -> str:
    return f"MX-{random.randint(10000, 99999)}"


def _kb_done() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Готово", callback_data="uidv|done")
    return kb.as_markup()


def _kb_confirm(conf_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"uidc|{conf_id}|ok"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"uidc|{conf_id}|no"),
            ]
        ]
    )


async def _send_media_any(
        bot,
        chat_id: int,
        packed_media: str,
        *,
        caption: str | None = None,
        reply_markup=None,
        parse_mode: str = "HTML",
        protect_content: bool = False,
):
    packed_media = (packed_media or "").strip()
    if not packed_media or ":" not in packed_media:
        raise ValueError("packed_media must be like 'p:<file_id>' / 'd:<file_id>' / 'v:<file_id>'")

    kind, file_id = packed_media.split(":", 1)
    kind = kind.strip().lower()
    file_id = file_id.strip()

    kind = {
        "p": "photo",
        "photo": "photo",
        "image": "photo",
        "img": "photo",
        "d": "document",
        "doc": "document",
        "document": "document",
        "v": "video",
        "video": "video",
    }.get(kind, kind)

    if kind == "photo":
        return await bot.send_photo(chat_id, file_id, caption=caption,
                                    reply_markup=reply_markup, parse_mode=parse_mode,
                                    protect_content=protect_content)

    if kind == "video":
        return await bot.send_video(chat_id, file_id, caption=caption,
                                    reply_markup=reply_markup, parse_mode=parse_mode,
                                    protect_content=protect_content)

    return await bot.send_document(chat_id, file_id, caption=caption,
                                   reply_markup=reply_markup, parse_mode=parse_mode,
                                   protect_content=protect_content)


async def _progress(request_id: int) -> dict:
    rows = await fetch(
        """
        SELECT r.user_id,
               r.created_at,
               (SELECT count(*)
                FROM uid_verification_confirmations c
                WHERE c.request_id = r.id
                  AND c.status = 'confirmed')                                          AS confirmed_cnt,
               (SELECT count(*)
                FROM uid_verification_confirmations c
                WHERE c.request_id = r.id
                  AND c.status IN ('pending', 'confirmed', 'rejected', 'unreachable')) AS total_cnt
        FROM uid_verification_requests r
        WHERE r.id = $1
        """,
        int(request_id),
    )
    return dict(rows[0]) if rows else {}


async def _notify_request_owner(bot: Bot, request_id: int, actor: types.User, action: str) -> None:
    """Оповещает автора заявки о подтверждении/отклонении сделки."""
    try:
        prog = await _progress(int(request_id))
    except Exception:
        return
    if not prog:
        return

    owner_id = int(prog.get("user_id") or 0)
    if not owner_id:
        return

    confirmed = int(prog.get("confirmed_cnt") or 0)
    left = max(MIN_REQUIRED - confirmed, 0)
    actor_tag = _tag_user(actor)

    if action == "confirmed":
        text = (
            f"✅ {actor_tag} подтвердил сделку.\n"
            f"Итого: {confirmed}/{MIN_REQUIRED}.\n"
            f"Осталось: {left}."
        )
        try:
            await bot.send_message(owner_id, text, protect_content=False)
        except Exception:
            pass

        if confirmed == MIN_REQUIRED:
            try:
                await bot.send_message(
                    owner_id,
                    f"🎉 Набрано {MIN_REQUIRED} подтверждения. Теперь заявку проверит администрация.",
                    protect_content=False,
                )
            except Exception:
                pass

    elif action == "rejected":
        text = (
            f"❌ {actor_tag} отклонил подтверждение.\n"
            f"Итого: {confirmed}/{MIN_REQUIRED}.\n"
            f"Осталось: {left}."
        )
        try:
            await bot.send_message(owner_id, text, protect_content=False)
        except Exception:
            pass


async def _finalize(bot: Bot, user: types.User, state: FSMContext, *, chat_id: int) -> None:
    data = await state.get_data()

    uid = (data.get("uid") or "").strip().lower()
    code = (data.get("code") or "").strip().upper()
    profile_proof = (data.get("profile_proof") or "").strip()
    deals: list[dict] = list(data.get("deals") or [])

    if not UID_RE.fullmatch(uid) or not CODE_RE.fullmatch(code) or not profile_proof:
        await bot.send_message(chat_id, "Данные заявки повреждены. Начни заново: /verify_uid", protect_content=False)
        await state.clear()
        return

    if len(deals) < MIN_REQUIRED:
        await bot.send_message(
            chat_id,
            f"Нужно минимум {MIN_REQUIRED} подтверждения. Сейчас: {len(deals)}.",
            protect_content=False,
        )
        return

    counterparty_usernames = [d["username"] for d in deals]
    deal_file_ids = [d["deal_file_id"] for d in deals]

    request_id = await create_uid_verification_request(
        user_id=int(user.id),
        uid=uid,
        verification_code=code,
        profile_proof_file_id=profile_proof,
        deal_file_ids=deal_file_ids,
        counterparty_usernames=counterparty_usernames,
        status="pending",
    )

    # Админ-лог (UID НЕ пишем)
    try:
        cps = ", ".join([f"@{u}" for u in counterparty_usernames])
        await send_admin_log(
            bot,
            "🆔 UID-верификация: новая заявка\n"
            f"ID: {request_id}\n"
            f"От: {_tag_user(user)} (id={user.id})\n"
            f"Контрагенты: {cps}\n"
            f"Таймер: {CONFIRM_TTL_HOURS}ч",
        )
    except Exception:
        pass

    # Шлём запросы подтверждающим (без UID)
    for d in deals:
        uname = d["username"]
        cp_id = int(d["user_id"])

        conf = await add_uid_verification_confirmation(
            request_id=int(request_id),
            counterparty_user_id=cp_id,
            counterparty_username=uname,
        )
        conf_id = int(conf["id"])

        caption = (
            f"🆔 <b>Подтверди сделку для UID-верификации</b>\n\n"
            f"Заявка <code>#{request_id}</code>\n"
            f"Если это реально ваша сделка с @{user.username or user.full_name} — жми «✅ Подтвердить».\n"
            f"Если нет — «❌ Отклонить».\n\n"
            f"⏳ Срок подтверждения: {CONFIRM_TTL_HOURS} часов."
        )

        try:
            sent = await _send_media_any(
                bot,
                cp_id,
                d["deal_file_id"],
                caption=caption,
                reply_markup=_kb_confirm(conf_id),
                parse_mode="HTML",
                protect_content=False,
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            await set_uid_verification_confirmation_status(conf_id=conf_id, status="unreachable")
            continue

        if sent:
            await set_uid_verification_confirmation_message(conf_id, int(sent.chat.id), int(sent.message_id))

    # Финальный текст как на твоём скрине
    await bot.send_message(
        chat_id,
        "✅ <b>Заявка на UID-верификацию отправлена. Что дальше?</b>\n\n"
        f"1) Ждём подтверждения от {MIN_REQUIRED} людей, которых ты отметил @\n"
        "2) После их подтверждения, твою заявку проверит администрация\n"
        "3) Одобрение или отказ в верификации\n\n"
        f"(Если {MIN_REQUIRED} человека не подтвердили вашу сделку в течение {CONFIRM_TTL_HOURS} часов, заявка удаляется автоматически)",
        parse_mode="HTML",
        protect_content=False,
    )

    await state.clear()


# -------------------- user flow --------------------

@router.message(Command("verify_uid"), F.chat.type == "private")
async def verify_uid_start(message: types.Message, state: FSMContext):
    await state.clear()

    existing = await get_verified_uid_for_user(int(message.from_user.id))
    if existing:
        await message.answer("У тебя уже есть верифицированный UID. Если нужно поменять, пиши админам.",
                             protect_content=False)
        return

    await state.set_state(UIDVerificationFSM.waiting_for_uid)
    await message.answer("1) Пришли юид", protect_content=False)


@router.message(UIDVerificationFSM.waiting_for_uid, F.chat.type == "private")
async def verify_uid_got_uid(message: types.Message, state: FSMContext):
    uid = (message.text or "").strip().lower()
    if not UID_RE.fullmatch(uid):
        await message.answer("Нужен UID из 24 символов (0-9, a-f). Пришли ещё раз.", protect_content=False)
        return

    code = _gen_code()
    await state.update_data(uid=uid, code=code)

    await state.set_state(UIDVerificationFSM.waiting_for_profile_with_code)
    await message.answer(
        "Теперь надо сделать скрин профиля, чтобы мы убедились, что это именно твой профиль, временно измени ник, "
        f"впиши рядом с ним или вместо него код: <code>{code}</code>\n"
        "По порядку твои действия:\n"
        "1. Открыть моя анкета;\n"
        "2. Изменить ник;\n"
        "3. Вставить код;\n"
        "4. Сделать скрин (где в профиле видно дату регистрации);\n"
        "5. Вернуть свой ник;\n"
        "6. Прислать скрин в бот.\n\n"
        "Можно фото или документ.",
        parse_mode="HTML",
        protect_content=False,
    )


@router.message(UIDVerificationFSM.waiting_for_profile_with_code, F.chat.type == "private")
async def verify_uid_got_profile(message: types.Message, state: FSMContext):
    packed = _pack_media(message)
    if not packed:
        await message.answer("Нужна картинка/документ со скрином профиля.", protect_content=False)
        return

    await state.update_data(profile_proof=packed, deals=[])
    await state.set_state(UIDVerificationFSM.waiting_for_deal_username)

    await message.answer(
        f"Для успешного прохождения верификации достаточно {MIN_REQUIRED} подтверждений успешной сделки, "
        f"отправить просьбу о подтверждении вы можете {MAX_REQUESTS} людям.\n\n"
        "Пришли юзернейм первой подтверждающей стороны. Пример: @somebody",
        protect_content=False,
    )


@router.message(UIDVerificationFSM.waiting_for_deal_username, F.chat.type == "private")
async def verify_uid_got_deal_username(message: types.Message, state: FSMContext, bot: Bot):
    uname = _parse_username(message.text or "")
    if not uname:
        await message.answer("Нужен юзернейм. Пример: @somebody", protect_content=False)
        return

    data = await state.get_data()
    deals: list[dict] = list(data.get("deals") or [])

    if len(deals) >= MAX_REQUESTS:
        await message.answer(f"Уже указано {MAX_REQUESTS} пользователей. Нажми «✅ Готово».", reply_markup=_kb_done(),
                             protect_content=False)
        return

    actor = message.from_user
    actor_uname = (actor.username or "").lower().lstrip("@")
    if actor_uname and uname == actor_uname:
        await message.answer("Нельзя указывать себя в подтверждениях.", protect_content=False)
        return

    if any(d["username"] == uname for d in deals):
        await message.answer("Этого пользователя ты уже указывал. Нужен другой.", protect_content=False)
        return

    info = await get_user_basic_info_by_username(uname)
    if not info:
        await message.answer(
            "К сожалению, этого пользователя нет в моей базе.\n"
            "Попросите его нажать /start в чате с ботом @RomanticClubBot, или введите другого подтверждающего",
            protect_content=False,
        )
        return

    if not bool(info.get("pm_opened")):
        await message.answer(
            "Этот пользователь ещё не открывал личные сообщения с ботом (не нажимал /start), "
            "поэтому бот не сможет отправить ему запрос подтверждения.\n\n"
            "Попроси его открыть @RomanticClubBot и нажать /start, затем пришли юзернейм снова.",
            protect_content=False,
        )
        return

    cp_id = int(info["user_id"])
    if cp_id == int(actor.id):
        await message.answer("Нельзя указывать себя в подтверждениях.", protect_content=False)
        return

    # попытка отсеять ЧС/недоступных
    try:
        await bot.get_chat(cp_id)
    except TelegramForbiddenError:
        await message.answer("Этот пользователь недоступен для бота (возможен ЧС). Выбери другого.",
                             protect_content=False)
        return
    except TelegramBadRequest:
        await message.answer("Не могу проверить этого пользователя. Выбери другого.", protect_content=False)
        return

    await state.update_data(current_cp={"user_id": cp_id, "username": uname})
    await state.set_state(UIDVerificationFSM.waiting_for_deal_screenshot)
    await message.answer("Теперь пришли скрин сделки (фото или документ).", protect_content=False)


@router.message(UIDVerificationFSM.waiting_for_deal_screenshot, F.chat.type == "private")
async def verify_uid_got_deal_screenshot(message: types.Message, state: FSMContext):
    packed = _pack_media(message)
    if not packed:
        await message.answer("Нужен скрин сделки (фото или документ).", protect_content=False)
        return

    data = await state.get_data()
    deals: list[dict] = list(data.get("deals") or [])
    cur = data.get("current_cp") or {}
    if not cur:
        await state.set_state(UIDVerificationFSM.waiting_for_deal_username)
        await message.answer("Пришли юзернейм подтверждающего. Пример: @somebody", protect_content=False)
        return

    deals.append({"user_id": int(cur["user_id"]), "username": str(cur["username"]), "deal_file_id": packed})
    await state.update_data(deals=deals, current_cp=None)
    await state.set_state(UIDVerificationFSM.waiting_for_deal_username)

    cnt = len(deals)
    if cnt >= MAX_REQUESTS:
        await message.answer(f"Готово: {cnt}/{MAX_REQUESTS}. Отправляю заявку…", protect_content=False)
        await _finalize(message.bot, message.from_user, state, chat_id=int(message.chat.id))
        return

    if cnt >= MIN_REQUIRED:
        await message.answer(
            f"Скрин принят ✅ ({cnt}/{MAX_REQUESTS}).\n"
            "Можешь добавить ещё подтверждающего или нажать «✅ Готово».",
            reply_markup=_kb_done(),
            protect_content=False,
        )
        return

    await message.answer(
        f"Скрин принят ✅ ({cnt}/{MAX_REQUESTS}).\n"
        f"Нужно минимум {MIN_REQUIRED}. Пришли следующий юзернейм подтверждающего.",
        protect_content=False,
    )


@router.callback_query(F.data == "uidv|done")
async def uidv_done(call: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if data.get("submitting"):
        await call.answer("Уже отправляю…", show_alert=False)
        return

    deals: list[dict] = list(data.get("deals") or [])
    if len(deals) < MIN_REQUIRED:
        await call.answer(f"Нужно минимум {MIN_REQUIRED}. Сейчас: {len(deals)}.", show_alert=True)
        return

    await state.update_data(submitting=True)

    try:
        if call.message:
            await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    try:
        await call.answer("Отправляю заявку…")
    except TelegramBadRequest:
        # бывает "query is too old" при лаге/потере сети – это не причина ронять флоу
        pass

    try:
        await _finalize(bot, call.from_user, state, chat_id=int(call.from_user.id))
    finally:
        if await state.get_state() is not None:
            await state.update_data(submitting=False)




# -------------------- revision (на доработку) --------------------

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


def _kb_fix_done(req_id: int) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Готово", callback_data=f"uidv_fix_done|{req_id}")
    kb.button(text="⬅️ Назад", callback_data=f"uidv_fix|{req_id}")
    kb.adjust(1)
    return kb.as_markup()


def _kb_fix_items(req_id: int, remaining: list[str]) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for flag in remaining:
        kb.button(
            text=_REV_FLAG_TITLES.get(flag, flag),
            callback_data=f"uidv_fix_item|{req_id}|{flag}",
        )
    if not remaining:
        kb.button(text="✅ Отправить на проверку", callback_data=f"uidv_fix_send|{req_id}")
    kb.button(text="⬅️ Назад", callback_data="uidv|start")
    kb.adjust(1)
    return kb.as_markup()


async def _get_revision_flags(req_id: int) -> list[str]:
    row = await fetchrow(
        "SELECT revision_flags FROM public.uid_verification_requests WHERE id=$1",
        int(req_id),
    )
    flags = list(row.get("revision_flags") or []) if row else []
    out = [str(x) for x in flags if str(x).strip()]

    # Убираем устаревшие пункты, чтобы они не висели в “доработке” навечно
    banned = {"uid_proof", "reg_date"}
    return [f for f in out if f not in banned]


@router.callback_query(F.data.startswith("uidv_fix|"))
async def uidv_fix_start(call: types.CallbackQuery, state: FSMContext) -> None:
    parts = (call.data or "").split("|")
    if len(parts) < 2:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    req_id = int(parts[1] or 0)
    req = await get_uid_verification_request(req_id)
    if not req:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    if int(req.get("user_id") or 0) != int(call.from_user.id):
        await call.answer("Это не твоя заявка.", show_alert=True)
        return

    status = (req.get("status") or "").strip().lower()
    if status != "revision":
        await call.answer("Эта заявка сейчас не на доработке.", show_alert=True)
        return

    uid_enc_value = (req.get("uid_enc") or "").strip()
    if not uid_enc_value:
        await call.answer("В заявке нет UID. Начни заново: /verify_uid", show_alert=True)
        return

    try:
        uid = uid_decrypt(uid_enc_value)
    except Exception:
        await call.answer("Не удалось прочитать UID из заявки. Начни заново: /verify_uid", show_alert=True)
        return

    code = gen_uid_verif_code()

    await state.clear()
    await state.update_data(uid=uid, code=code, revision_of_req_id=req_id)
    await state.set_state(UIDVerificationFSM.waiting_for_profile_with_code)

    await call.message.answer(
        "Ок, исправляем.\n\n"
        f"1) Вставь код в свой профиль: <code>{code}</code>\n"
        "2) Пришли скрин профиля (фото или документ), где видно UID и этот код.",
        parse_mode="HTML",
        protect_content=False,
    )
    await call.answer()


@router.callback_query(F.data.startswith("uidv_fix_item|"))
async def uidv_fix_choose_item(call: types.CallbackQuery, state: FSMContext) -> None:
    parts = (call.data or "").split("|")
    if len(parts) < 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    req_id = int(parts[1] or 0)
    flag = (parts[2] or "").strip()

    data = await state.get_data()
    if int(data.get("uidv_fix_req_id") or 0) != req_id:
        await call.answer("Сначала открой доработку заново.", show_alert=True)
        return

    if flag.endswith("_username"):
        await state.set_state(UIDVerificationFixFSM.waiting_username)
        await state.update_data(uidv_fix_current=flag)
        await call.answer()
        await call.message.answer(
            f"✏️ Пришли корректный @username для пункта:\n<b>{html.escape(_REV_FLAG_TITLES.get(flag, flag))}</b>\n\n"
            f"Пример: <code>@some_user</code>",
            protect_content=False,
        )
        return

    if flag == "other":
        await call.answer()
        await call.message.answer(
            "📝 Пришли текстом то, что попросил модератор исправить/добавить (если надо).\n"
            "Если модератор просил именно скрин/фото, выбери соответствующий пункт.",
            protect_content=False,
        )
        # считаем, что 'other' закрывается любым текстом, отдельного поля в БД нет
        await state.set_state(UIDVerificationFixFSM.waiting_username)
        await state.update_data(uidv_fix_current="other_text")
        return

    if flag == "extra":
        await state.set_state(UIDVerificationFixFSM.collecting_extra)
        await state.update_data(uidv_fix_current=flag, uidv_fix_extra=[])
        await call.answer()
        await call.message.answer(
            "➕ Пришли дополнительные пруфы (фото/файлы) одним или несколькими сообщениями.\n"
            "Когда закончишь, нажми «✅ Готово».",
            reply_markup=_kb_fix_done(req_id),
            protect_content=False,
        )
        return

    # media flags
    await state.set_state(UIDVerificationFixFSM.waiting_media)
    await state.update_data(uidv_fix_current=flag)
    await call.answer()
    await call.message.answer(
        f"📎 Пришли фото/файл для пункта:\n<b>{html.escape(_REV_FLAG_TITLES.get(flag, flag))}</b>",
        protect_content=False,
    )


@router.message(UIDVerificationFixFSM.waiting_media, F.chat.type == "private")
async def uidv_fix_media(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    req_id = int(data.get("uidv_fix_req_id") or 0)
    flag = str(data.get("uidv_fix_current") or "")

    packed = _pack_media(message)
    if not packed:
        await message.answer("Нужен файл/фото.")
        return

    ok = False
    if flag == "profile":
        ok = await set_uid_verification_request_profile_proof(req_id, packed)
    elif flag == "uid_proof":
        ok = await set_uid_verification_request_uid_proof(req_id, packed)
    elif flag == "reg_date":
        ok = await set_uid_verification_request_reg_date_proof(req_id, packed)
    elif flag.startswith("deal") and flag.endswith("_screen"):
        try:
            idx = int(flag.replace("deal", "").replace("_screen", ""))
        except Exception:
            idx = 0
        if idx:
            ok = await set_uid_verification_request_deal_media(req_id, idx, packed)

    if not ok:
        await message.answer("Не получилось сохранить. Попробуй ещё раз.")
        return

    remaining = list(data.get("uidv_fix_remaining") or [])
    if flag in remaining:
        remaining.remove(flag)

    await state.set_state(UIDVerificationFixFSM.choosing_item)
    await state.update_data(uidv_fix_remaining=remaining, uidv_fix_current=None)

    await message.answer(
        f"✅ Принято: <b>{html.escape(_REV_FLAG_TITLES.get(flag, flag))}</b>\n"
        f"Осталось пунктов: <b>{len(remaining)}</b>",
        reply_markup=_kb_fix_items(req_id, remaining),
        protect_content=False,
    )


@router.message(UIDVerificationFixFSM.waiting_username, F.chat.type == "private")
async def uidv_fix_username(message: types.Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    req_id = int(data.get("uidv_fix_req_id") or 0)
    flag = str(data.get("uidv_fix_current") or "")

    text = (message.text or "").strip()
    if not text:
        await message.answer("Нужен текст.")
        return

    if flag == "other_text":
        remaining = list(data.get("uidv_fix_remaining") or [])
        if "other" in remaining:
            remaining.remove("other")

        await state.set_state(UIDVerificationFixFSM.choosing_item)
        await state.update_data(uidv_fix_remaining=remaining, uidv_fix_current=None)

        await message.answer(
            "✅ Текст принят.",
            reply_markup=_kb_fix_items(req_id, remaining),
            protect_content=False,
        )
        return

    if not flag.endswith("_username"):
        await message.answer("Некорректный шаг.")
        return

    username = text.lstrip("@").strip().lower()
    if not username:
        await message.answer("Нужен @username.")
        return

    try:
        idx = int(flag.replace("deal", "").replace("_username", ""))
    except Exception:
        idx = 0
    if not idx:
        await message.answer("Некорректный пункт.")
        return

    req = await get_uid_verification_request(req_id)
    if not req:
        await message.answer("Заявка не найдена.")
        return

    old_usernames = list(req.get("counterparty_usernames") or [])
    old_username = (old_usernames[idx - 1] if idx - 1 < len(old_usernames) else "").strip().lower()

    ok = await set_uid_verification_request_deal_username(req_id, idx, username)
    if not ok:
        await message.answer("Не получилось сохранить ник. Попробуй ещё раз.")
        return

    if old_username and old_username != username:
        try:
            await execute(
                "DELETE FROM public.uid_verification_confirmations WHERE request_id=$1 AND counterparty_username=$2",
                int(req_id),
                old_username,
            )
        except Exception:
            pass

    try:
        await create_uid_verification_confirmation(int(req_id), username)
        await send_uid_verification_confirmation_request(bot, int(req_id), username)
    except Exception:
        pass

    remaining = list(data.get("uidv_fix_remaining") or [])
    if flag in remaining:
        remaining.remove(flag)

    await state.set_state(UIDVerificationFixFSM.choosing_item)
    await state.update_data(uidv_fix_remaining=remaining, uidv_fix_current=None)

    await message.answer(
        f"✅ Ник обновлён: <code>@{html.escape(username)}</code>\n"
        f"Осталось пунктов: <b>{len(remaining)}</b>",
        reply_markup=_kb_fix_items(req_id, remaining),
        protect_content=False,
    )


@router.message(UIDVerificationFixFSM.collecting_extra, F.chat.type == "private")
async def uidv_fix_extra_collect(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    req_id = int(data.get("uidv_fix_req_id") or 0)

    packed = _pack_media(message)
    if not packed:
        await message.answer("Нужен файл/фото.")
        return

    items = list(data.get("uidv_fix_extra") or [])
    items.append(packed)
    await state.update_data(uidv_fix_extra=items)

    await message.answer(
        f"✅ Принято. Сейчас доп. пруфов: <b>{len(items)}</b>\n"
        f"Можешь прислать ещё или нажать «✅ Готово».",
        reply_markup=_kb_fix_done(req_id),
        protect_content=False,
    )


@router.callback_query(F.data.startswith("uidv_fix_done|"))
async def uidv_fix_extra_done(call: types.CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != UIDVerificationFixFSM.collecting_extra.state:
        return

    parts = (call.data or "").split("|")
    req_id = int(parts[1] or 0)

    data = await state.get_data()
    if int(data.get("uidv_fix_req_id") or 0) != req_id:
        await call.answer("Открой доработку заново.", show_alert=True)
        return

    items = list(data.get("uidv_fix_extra") or [])
    if not items:
        await call.answer("Сначала пришли пруфы.", show_alert=True)
        return

    ok = await replace_uid_verification_request_extra_proofs(req_id, items)
    if not ok:
        await call.answer("Не удалось сохранить.", show_alert=True)
        return

    remaining = list(data.get("uidv_fix_remaining") or [])
    if "extra" in remaining:
        remaining.remove("extra")

    await state.set_state(UIDVerificationFixFSM.choosing_item)
    await state.update_data(uidv_fix_remaining=remaining, uidv_fix_current=None, uidv_fix_extra=[])

    await call.answer("Готово ✅")
    await call.message.answer(
        f"✅ Доп. пруфы сохранены.\nОсталось пунктов: <b>{len(remaining)}</b>",
        reply_markup=_kb_fix_items(req_id, remaining),
        protect_content=False,
    )


@router.callback_query(F.data.startswith("uidv_fix_send|"))
async def uidv_fix_send(call: types.CallbackQuery, state: FSMContext) -> None:
    parts = (call.data or "").split("|")
    if len(parts) < 2:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    req_id = int(parts[1] or 0)
    data = await state.get_data()
    if int(data.get("uidv_fix_req_id") or 0) != req_id:
        await call.answer("Открой доработку заново.", show_alert=True)
        return

    remaining = list(data.get("uidv_fix_remaining") or [])
    if remaining:
        await call.answer("Сначала закрой все пункты.", show_alert=True)
        return

    ok = await clear_uid_verification_request_revision(req_id)
    if not ok:
        await call.answer("Не удалось отправить на проверку.", show_alert=True)
        return

    await state.clear()

    await call.answer("Отправлено ✅")
    await call.message.answer(
        f"✅ Исправления отправлены.\nЗаявка <b>#{req_id}</b> снова на проверке.",
        protect_content=False,
    )

    try:
        await send_admin_log(f"🔁 Заявка UID-верификации #{req_id}: пользователь дослал исправления, снова <b>pending</b>.")
    except Exception:
        pass


# -------------------- confirmations --------------------

@router.callback_query(F.data.startswith("uidc|"))
async def uid_confirm_cb(call: types.CallbackQuery):
    data = (call.data or "").split("|")
    if len(data) < 3:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    conf_id = int(data[1])
    row = await fetchrow(
        "SELECT request_id FROM public.uid_verification_confirmations WHERE id=$1",
        conf_id,
    )
    request_id = int(row["request_id"]) if row and row.get("request_id") is not None else 0
    answer = (data[2] or "").strip().lower()

    if answer == "ok":
        status = "confirmed"
        ok_text = "✅ Подтверждено"
    elif answer == "no":
        status = "rejected"
        ok_text = "❌ Отклонено"
    else:
        await call.answer("Некорректный ответ.", show_alert=True)
        return

    ok = await set_uid_verification_confirmation_status(confirmation_id=conf_id, status=status)
    if not ok:
        await call.answer("Уже обработано или устарело.", show_alert=True)
        # на всякий случай уберём кнопки
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    try:
        await call.answer(ok_text)
    except TelegramBadRequest:
        pass

    if request_id:
        await _notify_request_owner(call.bot, request_id, call.from_user, status)

    # уничтожаем сообщение (как ты и хотел)
    try:
        if call.message:
            await call.bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        # если вдруг нельзя удалить, хотя бы уберём кнопки
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


# -------------------- background loop --------------------

async def uid_verification_watch_loop(bot: Bot) -> None:
    """
    Напоминалки заявителю:
      - осталось ~2 дня (через ~24ч)
      - осталось ~1 день (через ~48ч)
    Авто-удаление:
      - если <3 подтверждений за 72 часа
    """
    while True:
        try:
            # ~2 дня осталось (прошло 24ч)
            rows = await fetch(
                """
                SELECT r.id,
                       r.user_id,
                       (SELECT count(*)
                        FROM uid_verification_confirmations c
                        WHERE c.request_id = r.id
                          AND c.status = 'confirmed') AS confirmed_cnt
                FROM uid_verification_requests r
                WHERE r.status = 'pending'
                  AND r.created_at <= now() - interval '24 hours'
                  AND r.created_at > now() - interval '25 hours'
                """
            )
            for r in rows:
                if int(r["confirmed_cnt"] or 0) >= MIN_REQUIRED:
                    continue
                await bot.send_message(
                    int(r["user_id"]),
                    f"⏳ UID-верификация: осталось ~2 дня.\nПодтверждений: {int(r['confirmed_cnt'] or 0)}/{MIN_REQUIRED}.",
                    protect_content=False,
                )

            # ~1 день осталось (прошло 48ч)
            rows = await fetch(
                """
                SELECT r.id,
                       r.user_id,
                       (SELECT count(*)
                        FROM uid_verification_confirmations c
                        WHERE c.request_id = r.id
                          AND c.status = 'confirmed') AS confirmed_cnt
                FROM uid_verification_requests r
                WHERE r.status = 'pending'
                  AND r.created_at <= now() - interval '48 hours'
                  AND r.created_at > now() - interval '49 hours'
                """
            )
            for r in rows:
                if int(r["confirmed_cnt"] or 0) >= MIN_REQUIRED:
                    continue
                await bot.send_message(
                    int(r["user_id"]),
                    f"⏳ UID-верификация: осталось ~1 день.\nПодтверждений: {int(r['confirmed_cnt'] or 0)}/{MIN_REQUIRED}.",
                    protect_content=False,
                )

            # истекло 72ч и <3 подтверждений
            rows = await fetch(
                f"""
                SELECT r.id, r.user_id,
                       (SELECT count(*) FROM uid_verification_confirmations c
                        WHERE c.request_id=r.id AND c.status='confirmed') AS confirmed_cnt
                FROM uid_verification_requests r
                WHERE r.status='pending'
                  AND r.created_at <= now() - interval '{CONFIRM_TTL_HOURS} hours'
                """
            )
            for r in rows:
                if int(r["confirmed_cnt"] or 0) >= MIN_REQUIRED:
                    continue

                req_id = int(r["id"])
                owner_id = int(r["user_id"])

                await execute("DELETE FROM uid_verification_requests WHERE id=$1 AND status='pending'", req_id)

                await bot.send_message(
                    owner_id,
                    "⌛️ UID-верификация: не набрано 3 подтверждения за 72 часа.\n"
                    "Заявка удалена автоматически. Можешь подать заново: /verify_uid",
                    protect_content=False,
                )

                try:
                    await send_admin_log(
                        bot,
                        "🆔 UID-верификация: авто-удаление заявки\n"
                        f"ID: {req_id}\n"
                        f"От: id{owner_id}\n"
                        f"Подтверждений: {int(r['confirmed_cnt'] or 0)}/{MIN_REQUIRED}\n"
                        "Причина: таймаут 72ч",
                    )
                except Exception:
                    pass

        except Exception:
            await asyncio.sleep(10)

        await asyncio.sleep(900)
def _norm_username(x: str) -> str:
    return (x or "").strip().lstrip("@").lower()


async def create_uid_verification_confirmation(request_id: int, username: str) -> dict | None:
    """
    Создаёт (или достаёт существующую) запись подтверждения для контрагента.
    """
    uname = _norm_username(username)
    if not request_id or not uname:
        return None

    info = await get_user_basic_info_by_username(uname)
    if not info:
        return None

    conf = await add_uid_verification_confirmation(
        request_id=int(request_id),
        counterparty_user_id=int(info["user_id"]),
        counterparty_username=uname,
    )
    return conf


async def send_uid_verification_confirmation_request(bot: Bot, request_id: int, username: str) -> None:
    """
    Шлёт контрагенту запрос подтверждения (как при первичной подаче заявки),
    но используется при досыле/исправлении username.
    """
    uname = _norm_username(username)
    if not request_id or not uname:
        return

    req = await get_uid_verification_request(int(request_id))
    if not req:
        return

    info = await get_user_basic_info_by_username(uname)
    if not info:
        return

    cp_id = int(info["user_id"])

    # создаём/получаем confirmation
    conf = await add_uid_verification_confirmation(
        request_id=int(request_id),
        counterparty_user_id=cp_id,
        counterparty_username=uname,
    )
    conf_id = int(conf["id"])

    # пытаемся найти подходящий скрин сделки для этого username
    deal_file_id = None
    cps = [ _norm_username(x) for x in (req.get("counterparty_usernames") or []) ]
    deals = list(req.get("deal_file_ids") or [])
    for i, u in enumerate(cps):
        if u == uname:
            if i < len(deals) and (deals[i] or "").strip():
                deal_file_id = str(deals[i]).strip()
            break

    # кто подал заявку (для текста)
    owner_row = await fetchrow(
        """
        SELECT u.username, u.full_name, r.user_id
        FROM public.uid_verification_requests r
        LEFT JOIN public.users u ON u.user_id = r.user_id
        WHERE r.id = $1
        """,
        int(request_id),
    )
    owner_tag = None
    if owner_row:
        ou = (owner_row.get("username") or "").strip()
        ofn = (owner_row.get("full_name") or "").strip()
        if ou:
            owner_tag = f"@{ou}"
        elif ofn:
            owner_tag = ofn
        else:
            owner_tag = f"id{int(owner_row.get('user_id') or req.get('user_id') or 0)}"
    else:
        owner_tag = f"id{int(req.get('user_id') or 0)}"

    caption = (
        f"🆔 <b>Подтверди сделку для UID-верификации</b>\n\n"
        f"Заявка <code>#{int(request_id)}</code>\n"
        f"Если это реально ваша сделка с {html.escape(owner_tag)} — жми «✅ Подтвердить».\n"
        f"Если нет — «❌ Отклонить».\n\n"
        f"⏳ Срок подтверждения: {CONFIRM_TTL_HOURS} часов."
    )

    try:
        if deal_file_id:
            sent = await _send_media_any(
                bot,
                cp_id,
                deal_file_id,
                caption=caption,
                reply_markup=_kb_confirm(conf_id),
                parse_mode="HTML",
                protect_content=False,
            )
        else:
            sent = await bot.send_message(
                cp_id,
                caption,
                reply_markup=_kb_confirm(conf_id),
                parse_mode="HTML",
                protect_content=False,
            )
    except (TelegramForbiddenError, TelegramBadRequest):
        await set_uid_verification_confirmation_status(conf_id=conf_id, status="unreachable")
        return

    try:
        await set_uid_verification_confirmation_message(conf_id, int(sent.chat.id), int(sent.message_id))
    except Exception:
        pass

import html

@router.callback_query(F.data == "uidv|start")
async def uidv_start(call: types.CallbackQuery, state: FSMContext) -> None:
    row = await fetchrow(
        """
        SELECT id, status, revision_flags, revision_reason
        FROM uid_verification_requests
        WHERE user_id=$1
        ORDER BY id DESC
        LIMIT 1
        """,
        int(call.from_user.id),
    )
    if not row:
        await call.answer("Заявок нет. Начни заново: /verify_uid", show_alert=True)
        return

    req_id = int(row["id"])
    status = (row["status"] or "").strip().lower()
    rev_flags = row.get("revision_flags") or []
    rev_reason = (row.get("revision_reason") or "").strip()

    text = (
        f"📌 <b>Твоя заявка на верификацию</b>\n\n"
        f"Заявка: <b>#{req_id}</b>\n"
        f"Статус: <b>{html.escape(status)}</b>\n"
    )

    kb = InlineKeyboardBuilder()

    if status == "revision":
        lines = "\n".join([f"• {html.escape(str(x))}" for x in rev_flags]) if rev_flags else "• —"
        text += (
            "\n\n🔧 <b>Нужно исправить:</b>\n"
            f"{lines}\n\n"
            f"<b>Причина:</b>\n{html.escape(rev_reason or '—')}"
        )
        kb.button(text="🔧 Исправить заявку", callback_data=f"uidv_fix|{req_id}")

    kb.button(text="✅ Закрыть", callback_data="uidv|done")
    kb.adjust(1)

    try:
        await call.message.edit_text(text, reply_markup=kb.as_markup())
    except Exception:
        await call.message.answer(text, reply_markup=kb.as_markup())

    await call.answer()

@router.callback_query(F.data.startswith("uidv_fix|"))
async def uidv_fix_start(call: types.CallbackQuery, state: FSMContext) -> None:
    parts = (call.data or "").split("|")
    if len(parts) < 2:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    req_id = int(parts[1] or 0)
    req = await get_uid_verification_request(req_id)
    if not req:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    # защита: только владелец заявки
    if int(req.get("user_id") or 0) != int(call.from_user.id):
        await call.answer("Это не твоя заявка.", show_alert=True)
        return

    status = (req.get("status") or "").strip().lower()
    if status != "revision":
        await call.answer("Эта заявка сейчас не на доработке.", show_alert=True)
        return

    uid = (req.get("uid") or "").strip()
    if not uid:
        await call.answer("В заявке нет UID. Начни заново: /verify_uid", show_alert=True)
        return

    code = gen_uid_verif_code()

    await state.clear()
    await state.update_data(uid=uid, code=code, revision_of_req_id=req_id)
    await state.set_state(UIDVerificationFSM.waiting_for_profile_with_code)

    await call.message.answer(
        "Ок, исправляем.\n\n"
        f"1) Вставь код в свой профиль: <code>{code}</code>\n"
        "2) Пришли скрин профиля (фото или документ), где видно UID и этот код.",
        protect_content=False,
    )
    await call.answer()

@router.callback_query(F.data == "uidv|start")
async def uidv_show_my_request(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()

    rows = await fetch(
        """
        SELECT id, status, revision_flags, revision_reason, admin_comment, created_at
        FROM uid_verification_requests
        WHERE user_id=$1
        ORDER BY id DESC
        LIMIT 1
        """,
        int(call.from_user.id),
    )

    if not rows:
        await call.message.answer("У тебя нет заявки. Отправить новую: /verify_uid", protect_content=False)
        return

    req = dict(rows[0])
    rid = int(req["id"])
    st = (req.get("status") or "").strip().lower()

    text = (
        f"📌 <b>Твоя заявка на UID-верификацию</b>\n\n"
        f"Заявка: <b>#{rid}</b>\n"
        f"Статус: <b>{st}</b>\n"
    )

    kb = InlineKeyboardBuilder()

    if st == "revision":
        flags = req.get("revision_flags") or []
        reason = (req.get("revision_reason") or req.get("admin_comment") or "").strip()

        lines = "\n".join([f"• {html.escape(str(x))}" for x in flags]) if flags else "• —"
        text += f"\n<b>Нужно исправить:</b>\n{lines}\n\n<b>Причина:</b>\n{html.escape(reason) or '—'}\n"
        kb.button(text="🔧 Исправить заявку", callback_data=f"uidv_fix|{rid}")

    kb.button(text="➕ Новая заявка", callback_data="uidv_new")
    kb.adjust(1)

    await call.message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML", protect_content=False)

@router.callback_query(F.data == "uidv_new")
async def uidv_new(call: types.CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await call.message.answer("1) Пришли юид", protect_content=False)
    await state.set_state(UIDVerificationFSM.waiting_for_uid)

@router.callback_query(F.data == "uidv|start")
async def uidv_start_btn(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not call.message:
        return

    # если у пользователя есть заявка, покажем кратко; если нет — стартуем /verify_uid
    rows = await fetch(
        """
        SELECT id, uid, status, revision_reason, revision_flags
        FROM public.uid_verification_requests
        WHERE user_id=$1
        ORDER BY id DESC
        LIMIT 1
        """,
        call.from_user.id,
    )
    if not rows:
        await verify_uid_start(call.message, state)
        return

    r = rows[0]
    rid = int(r["id"])
    uid = (r.get("uid") or "—")
    st = (r.get("status") or "—")
    rev_reason = (r.get("revision_reason") or "").strip()
    rev_flags = r.get("revision_flags") or []

    txt = (
        "📌 <b>Твоя последняя заявка на верификацию</b>\n"
        f"• ID: <code>{rid}</code>\n"
        f"• UID: <code>{uid}</code>\n"
        f"• Статус: <code>{st}</code>\n"
    )
    if rev_flags or rev_reason:
        txt += "\n🛠 <b>Нужно исправить:</b>\n"
        if rev_flags:
            txt += "• " + "\n• ".join([str(x) for x in rev_flags]) + "\n"
        if rev_reason:
            txt += f"\n<b>Комментарий:</b> {rev_reason}\n"

    await call.message.answer(txt)

@router.callback_query(F.data.startswith("uidv_fix|"))
async def uidv_fix_btn(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not call.message:
        return

    # пока просто запускаем стандартный сценарий (чтобы кнопка не была “мертвой”)
    # и сохраняем request_id в state на будущее расширение
    raw = call.data or ""
    req_id = 0
    try:
        req_id = int(raw.split("|", 1)[1])
    except Exception:
        req_id = 0

    if req_id:
        await state.update_data(uidv_fix_request_id=req_id)

    await verify_uid_start(call.message, state)

def gen_uid_verif_code() -> str:
    return _gen_code()