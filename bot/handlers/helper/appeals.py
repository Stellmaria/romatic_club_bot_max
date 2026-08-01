import html
import logging
from typing import List

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.handlers.admin.action_support.compat import send_admin_log
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.handlers.auction_comments import build_thanks_kb
from bot.handlers.auctions import admin_tag
from bot.handlers.helper.appeals_service import (
    create_appeal,
    get_appeal_by_id,
    get_first_pending,
    get_next_pending,
    set_reply,
    set_status,
)
from bot.legacy_fsm import AppealFSM
from bot.telegram.callback_parser import split_callback_data

router = Router(name="appeals")
logger = logging.getLogger(__name__)

TOPICS = ["Жалоба", "Вопрос", "Предложение"]

APPEAL_RULES = (
    "Напишите тему обращения (жалоба, вопрос, предложение).\n"
    "Подробно опишите проблему.\n\n"
    "Если есть юзернеймы участников, укажите их.\n"
    "Если есть скрины/пруфы, прикрепите их.\n\n"
    "Можно отправлять несколько медиа подряд.\n"
    "Админы рассмотрят обращение и дадут ответ."
)


# -------------------------
# Helpers / UI
# -------------------------


def _clean(s: str) -> str:
    return (s or "").strip()


def _status_human(status: str) -> str:
    s = (status or "").strip().lower()
    return {
        "pending": "pending",
        "resolved": "✅ решено",
        "unresolved": "❌ не решено",
        "answered": "✉️ дан ответ",
    }.get(s, html.escape(status or "pending"))


def kb_for_appeal(aid: int, *, has_media: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    kb.row(
        InlineKeyboardButton(text="✅ Решено", callback_data=f"appeal:resolve:{aid}"),
        InlineKeyboardButton(text="❌ Не решено", callback_data=f"appeal:unresolve:{aid}"),
        width=2,
    )
    kb.row(
        InlineKeyboardButton(text="✉️ Ответить", callback_data=f"appeal:reply:{aid}"),
        width=1,
    )
    if has_media:
        kb.row(
            InlineKeyboardButton(text="📎 Посмотреть пруфы", callback_data=f"appeal:media:{aid}"),
            width=1,
        )
    kb.row(
        InlineKeyboardButton(text="➡️ Далее", callback_data=f"appeal:next:{aid}"),
        width=1,
    )
    return kb.as_markup()


async def _kb_done(aid: int, *, has_media: bool, moderator_tag_str: str) -> InlineKeyboardMarkup:
    """
    Клавиатура после обработки: спасибо + (пруфы) + далее
    """
    thanks_kb = await build_thanks_kb(aid, moderator_tag_str)
    rows = list(thanks_kb.inline_keyboard)

    if has_media:
        rows.append([InlineKeyboardButton(text="📎 Посмотреть пруфы", callback_data=f"appeal:media:{aid}")])
    rows.append([InlineKeyboardButton(text="➡️ Далее", callback_data=f"appeal:next:{aid}")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_appeal(a: dict) -> str:
    moderator_username = (a.get("moderator_username") or "").strip()
    moderator_id = a.get("moderator_id")
    mod_tag = "—"
    if moderator_username:
        mod_tag = f"@{html.escape(moderator_username.lstrip('@'))}"
    elif moderator_id:
        mod_tag = f"<code>{int(moderator_id)}</code>"

    comment = (a.get("moderator_comment") or "").strip()
    comment_block = f"\n\n<b>Комментарий/ответ:</b>\n{html.escape(comment)}" if comment else ""

    return (
        f"<b>🆘 Заявка #{int(a['id'])}</b>\n"
        f"Статус: <b>{_status_human(a.get('status') or 'pending')}</b>\n"
        f"Тема: <b>{html.escape(a.get('topic') or '—')}</b>\n\n"
        f"Описание:\n{html.escape(a.get('description') or '—')}\n\n"
        f"Участники: {html.escape(a.get('participants') or '—')}\n"
        f"Автор: @{html.escape(a.get('username') or '—')} "
        f"(id: <code>{int(a.get('user_id') or 0)}</code>)\n"
        f"Дата: <code>{a.get('created_at')}</code>\n"
        f"Вложений: {len(a.get('media_message_ids') or [])}\n"
        f"👨‍🔧 Обработал: {mod_tag}"
        f"{comment_block}"
    )


async def _safe_edit_text(
        message: types.Message,
        text: str,
        *,
        reply_markup=None,
        parse_mode: str | None = "HTML",
        disable_web_page_preview: bool = True,
) -> None:
    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise


async def log_appeal_decision(bot, appeal: dict, new_status: str, moderator: types.User) -> None:
    """
    Лог решения по обращению в админ-чаты.
    new_status: resolved | unresolved | answered
    """
    status_h = {
        "resolved": "РЕШЕНО ✅",
        "unresolved": "НЕ РЕШЕНО ❌",
        "answered": "ДАН ОТВЕТ ✉️",
    }.get(new_status, html.escape(new_status))

    desc = appeal.get("description") or "—"
    usernames = appeal.get("username") or "—"
    participants = appeal.get("participants") or "—"

    text = (
        "<b>🧾 Обработка обращения</b>\n"
        f"ID: <b>#{appeal['id']}</b>\n"
        f"Статус: <b>{status_h}</b>\n\n"
        f"Модератор: @{html.escape(moderator.username or '—')} "
        f"(id: <code>{moderator.id}</code>)\n"
        f"Автор: @{html.escape(usernames)} (id: <code>{appeal['user_id']}</code>)\n"
        f"Тема: {html.escape(appeal.get('topic') or '—')}\n"
        f"Участники: {html.escape(participants)}\n"
        f"Создано: <code>{appeal.get('created_at')}</code>\n"
        f"Вложений: {len(appeal.get('media_message_ids') or [])}\n\n"
        f"Описание:\n{html.escape(desc[:600])}{'…' if len(desc) > 600 else ''}"
    )
    await send_admin_log(bot, text)


# -------------------------
# Admin: просмотр и обработка обращений
# -------------------------


@router.message(F.text == "🆘 Обращения", F.chat.type == "private")
@router.message(F.text.in_({"/appeals", "/обращения"}), F.chat.type == "private")
@admin_only
async def show_first_pending(message: types.Message) -> None:
    appeal = await get_first_pending()
    if not appeal:
        await message.answer("Пока нет обращений.", reply_markup=ReplyKeyboardRemove())
        return

    aid = int(appeal["id"])
    has_media = bool(appeal.get("media_message_ids") or [])
    await message.answer(
        format_appeal(appeal),
        reply_markup=kb_for_appeal(aid, has_media=has_media),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("appeal:"), F.message.chat.type == "private")
@admin_only
async def appeals_cb(call: CallbackQuery, state: FSMContext) -> None:
    parts = split_callback_data(call.data or "", ":")
    if len(parts) < 2:
        await call.answer("Некорректная кнопка.", show_alert=True)
        return

    action = (parts[1] or "").strip().lower()
    arg = (parts[2] or "").strip() if len(parts) >= 3 else ""

    # NEXT
    if action == "next":
        after_id = int(arg) if arg.isdigit() else 0
        nxt = await (get_next_pending(after_id) if after_id else get_first_pending())
        if not nxt:
            await call.answer("Заявок больше нет.")
            await _safe_edit_text(call.message, "Пока нет обращений.", reply_markup=None)
            return

        aid = int(nxt["id"])
        has_media = bool(nxt.get("media_message_ids") or [])
        await _safe_edit_text(
            call.message,
            format_appeal(nxt),
            reply_markup=kb_for_appeal(aid, has_media=has_media),
        )
        await call.answer()
        return

    # дальше нужен id
    if not arg.isdigit():
        await call.answer("Нет ID.", show_alert=True)
        return
    aid = int(arg)

    appeal = await get_appeal_by_id(aid)
    if not appeal:
        await call.answer("Обращение не найдено.", show_alert=True)
        return

    has_media = bool(appeal.get("media_message_ids") or [])
    moderator = call.from_user
    moderator_tag_str = admin_tag(moderator)

    # MEDIA
    if action == "media":
        mids = appeal.get("media_message_ids") or []
        if not mids:
            await call.answer("Пруфов нет.", show_alert=False)
            return

        origin = int(appeal.get("origin_chat_id") or 0)
        if not origin:
            await call.answer("Нет источника медиа.", show_alert=True)
            return

        await call.answer("Показываю пруфы…", show_alert=False)
        await call.message.answer("📎 Пруфы:")

        for mid in mids:
            try:
                await call.message.bot.copy_message(
                    chat_id=call.message.chat.id,
                    from_chat_id=origin,
                    message_id=int(mid),
                )
            except Exception as e:
                logger.warning("copy_message failed mid=%s origin=%s: %s", mid, origin, e)
        return

    # REPLY -> ждём текст
    if action == "reply":
        await state.set_state(AppealFSM.waiting_for_admin_reply)
        await state.update_data(
            appeal_id=aid,
            admin_chat_id=call.message.chat.id,
            admin_message_id=call.message.message_id,
        )
        await call.message.answer(
            f"✉️ Напишите одним сообщением ответ пользователю по обращению <b>#{aid}</b>.\n"
            "Он уйдёт автору в ЛС и закроет обращение.",
            parse_mode="HTML",
        )
        await call.answer()
        return

    # RESOLVE / UNRESOLVE
    if action in {"resolve", "unresolve"}:
        new_status = "resolved" if action == "resolve" else "unresolved"

        await set_status(
            appeal_id=aid,
            status=new_status,
            moderator_id=moderator.id,
            moderator_username=moderator.username or "",
            comment=None,
        )

        # лог решения
        try:
            await log_appeal_decision(call.message.bot, appeal, new_status, moderator)
        except Exception:
            pass

        # сообщение пользователю + спасибо модератору
        user_id = int(appeal["user_id"])
        mod_tag = f"@{moderator.username}" if moderator.username else f"<code>{moderator.id}</code>"
        user_text = (
            "🆘 <b>Ваше обращение рассмотрено</b>\n\n"
            f"Обращение: <b>#{aid}</b>\n"
            f"Статус: <b>{'✅ Решено' if new_status == 'resolved' else '❌ Не решено'}</b>\n"
            f"👨‍🔧 Обработал: {mod_tag}"
        )
        try:
            await call.message.bot.send_message(
                user_id,
                user_text,
                parse_mode="HTML",
                reply_markup=await build_thanks_kb(aid, moderator_tag_str),
            )
        except Exception:
            pass

        # обновим админское сообщение: кто обработал + спасибо
        appeal = {
            **appeal,
            "status": new_status,
            "moderator_id": moderator.id,
            "moderator_username": moderator.username or "",
        }
        await _safe_edit_text(
            call.message,
            format_appeal(appeal),
            reply_markup=await _kb_done(aid, has_media=has_media, moderator_tag_str=moderator_tag_str),
        )
        await call.answer("Готово.")
        return

    await call.answer("Неизвестное действие.", show_alert=True)


@router.message(StateFilter(AppealFSM.waiting_for_admin_reply), F.chat.type == "private")
@admin_only
async def appeal_admin_reply_message(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    aid = int(data.get("appeal_id") or 0)
    if not aid:
        await message.answer("Не понял, к какому обращению ответ. Открой обращение заново.")
        await state.clear()
        return

    appeal = await get_appeal_by_id(aid)
    if not appeal:
        await message.answer("Обращение не найдено.")
        await state.clear()
        return

    reply_text = _clean(message.text or message.html_text or "")
    if not reply_text:
        await message.answer("Пустой ответ не отправляю.")
        return

    moderator = message.from_user
    moderator_tag_str = admin_tag(moderator)
    mod_tag = f"@{moderator.username}" if moderator.username else f"<code>{moderator.id}</code>"

    out = (
        "🆘 <b>Ответ администрации КД</b>\n\n"
        f"По обращению <b>#{aid}</b>\n"
        f"👨‍🔧 Обработал: {mod_tag}\n\n"
        f"{reply_text}"
    )

    user_id = int(appeal["user_id"])

    try:
        await message.bot.send_message(
            user_id,
            out,
            parse_mode="HTML",
            reply_markup=await build_thanks_kb(aid, moderator_tag_str),
        )
    except Exception:
        await message.answer("Не смог отправить пользователю (закрыт ЛС/блок/ошибка Telegram).")
        await state.clear()
        return

    # сохраняем ответ + закрываем обращение
    await set_reply(aid, moderator.id, moderator.username, reply_text)
    await set_status(
        appeal_id=aid,
        status="answered",
        moderator_id=moderator.id,
        moderator_username=moderator.username or "",
        comment=reply_text,
    )

    # лог (по желанию, но полезно)
    try:
        await log_appeal_decision(message.bot, appeal, "answered", moderator)
    except Exception:
        pass

    # обновим исходное админское сообщение (если оно было)
    admin_chat_id = data.get("admin_chat_id")
    admin_message_id = data.get("admin_message_id")
    if admin_chat_id and admin_message_id:
        try:
            updated = {
                **appeal,
                "status": "answered",
                "moderator_id": moderator.id,
                "moderator_username": moderator.username or "",
                "moderator_comment": reply_text,
            }
            has_media = bool(updated.get("media_message_ids") or [])
            await message.bot.edit_message_text(
                chat_id=int(admin_chat_id),
                message_id=int(admin_message_id),
                text=format_appeal(updated),
                parse_mode="HTML",
                reply_markup=await _kb_done(aid, has_media=has_media, moderator_tag_str=moderator_tag_str),
                disable_web_page_preview=True,
            )
        except Exception:
            pass

    await message.answer("✅ Ответ отправлен, обращение закрыто.")
    await state.clear()


# -------------------------
# User: создание обращения
# -------------------------


def _kb_topics() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t)] for t in TOPICS] + [[KeyboardButton(text="Отмена")]],
        resize_keyboard=True,
        selective=True,
    )


def _kb_media() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Готово"), KeyboardButton(text="Без медиа")],
            [KeyboardButton(text="Отмена")],
        ],
        resize_keyboard=True,
        selective=True,
    )


@router.message(F.chat.type == "private", F.text.in_({"/support", "/contact"}))
async def appeal_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "🆘 <b>Обращение к админам</b>\n\n"
        f"{html.escape(APPEAL_RULES)}\n\n"
        "Выберите тему обращения:",
        reply_markup=_kb_topics(),
        parse_mode="HTML",
    )
    await state.set_state(AppealFSM.waiting_for_topic)


@router.message(StateFilter(AppealFSM.waiting_for_topic), F.text)
async def appeal_topic(message: types.Message, state: FSMContext) -> None:
    text = _clean(message.text)
    if text.lower() == "отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=ReplyKeyboardRemove())
        return

    topic = text.capitalize()
    if topic not in TOPICS:
        await message.answer("Выберите тему кнопкой снизу.", reply_markup=_kb_topics())
        return

    await state.update_data(topic=topic, media_ids=[])
    await message.answer(
        "Опишите проблему максимально подробно. Можно ссылками, временем, суммами, скринами.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Отмена")]],
            resize_keyboard=True,
            selective=True,
        ),
    )
    await state.set_state(AppealFSM.waiting_for_description)


@router.message(StateFilter(AppealFSM.waiting_for_description))
async def appeal_description(message: types.Message, state: FSMContext) -> None:
    text = _clean(message.text or "")
    if text.lower() == "отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=ReplyKeyboardRemove())
        return
    if len(text) < 10:
        await message.answer("Слишком коротко. Добавьте деталей (даты, ссылки, суммы).")
        return

    await state.update_data(description=text)
    await message.answer(
        "Если есть юзернеймы участников, перечислите через запятую (например: @user1, @user2). "
        "Если нет — напишите «нет».",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Отмена")]],
            resize_keyboard=True,
            selective=True,
        ),
    )
    await state.set_state(AppealFSM.waiting_for_usernames)


@router.message(StateFilter(AppealFSM.waiting_for_usernames))
async def appeal_usernames(message: types.Message, state: FSMContext) -> None:
    text = _clean(message.text or "")
    if text.lower() == "отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=ReplyKeyboardRemove())
        return

    usernames = ", ".join(
        sorted(
            {
                u.strip().lstrip("@")
                for u in text.replace(";", ",").split(",")
                if u.strip() and u.strip().lower() != "нет"
            }
        )
    )
    participants = ("@" + usernames.replace(", ", ", @")) if usernames else "—"
    await state.update_data(participants=participants)

    await message.answer(
        "Прикрепите скрины/пруфы (можно несколько сообщений подряд).\n"
        "Когда закончите — нажмите «Готово» или «Без медиа».",
        reply_markup=_kb_media(),
    )
    await state.set_state(AppealFSM.waiting_for_media)


@router.message(
    StateFilter(AppealFSM.waiting_for_media),
    F.photo | F.document | F.video | F.animation | F.audio | F.voice,
)
async def appeal_collect_media(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    media_ids: List[int] = data.get("media_ids", [])
    if len(media_ids) >= 20:
        await message.answer("Вложений уже много. Нажмите «Готово».", reply_markup=_kb_media())
        return

    media_ids.append(message.message_id)
    await state.update_data(media_ids=media_ids)
    await message.answer(f"Добавлено вложений: {len(media_ids)}. Когда закончите — «Готово».", reply_markup=_kb_media())


@router.message(StateFilter(AppealFSM.waiting_for_media), F.text.lower().in_({"готово", "без медиа"}))
async def appeal_submit(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()

    topic = data.get("topic") or "—"
    desc = data.get("description") or "—"
    participants = data.get("participants") or "—"
    media_ids: List[int] = data.get("media_ids", [])

    appeal_id = await create_appeal(
        user_id=message.from_user.id,
        username=message.from_user.username,
        topic=topic,
        description=desc,
        participants=participants,
        media_message_ids=media_ids,
        origin_chat_id=message.chat.id,
    )

    text = (
        "<b>🆘 Новое обращение</b>\n"
        f"ID: <b>#{appeal_id}</b>\n"
        f"Тема: <b>{html.escape(topic)}</b>\n\n"
        f"Описание:\n{html.escape(desc)}\n\n"
        f"Участники: {html.escape(participants)}\n"
        f"От: @{html.escape(message.from_user.username or '—')} "
        f"(id: <code>{message.from_user.id}</code>)\n"
        f"Вложений: {len(media_ids)}"
    )

    try:
        await send_admin_log(message.bot, text)

        # докидываем медиа в админ-логи (копиями)
        if media_ids:
            from bot.core.legacy_config import legacy_config  # локальный импорт чтобы не ловить циклы

            for chat_id in legacy_config.ADMIN_LOG_CHATS:
                await message.bot.send_message(chat_id, "📎 Вложения к обращению:")
                for mid in media_ids:
                    try:
                        await message.bot.copy_message(
                            chat_id=chat_id,
                            from_chat_id=message.chat.id,
                            message_id=int(mid),
                        )
                    except Exception as e:
                        logger.warning("copy_message failed mid=%s -> chat=%s: %s", mid, chat_id, e)

        await message.answer("✅ Отправлено. Админы получили ваше обращение.", reply_markup=ReplyKeyboardRemove())
    except Exception as e:
        logger.error("appeal_submit error: %s", e)
        await message.answer("⚠️ Не получилось отправить обращение. Попробуйте позже.",
                             reply_markup=ReplyKeyboardRemove())
    finally:
        await state.clear()


@router.message(StateFilter(AppealFSM.waiting_for_media), F.text)
async def appeal_media_text_controls(message: types.Message, state: FSMContext) -> None:
    text = _clean(message.text or "")
    if text.lower() == "отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=ReplyKeyboardRemove())
        return

    await message.answer("Прикрепите медиа или нажмите «Готово».", reply_markup=_kb_media())
