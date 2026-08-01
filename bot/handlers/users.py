import logging
import re
from datetime import date, timezone, datetime, timedelta

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.core.time import to_moscow, utc_now
from bot.handlers.admin.helper.new.formatting import format_admin_action_log
from bot.handlers.admin.helper.user_helpers import get_pretty_owners_for_log
from bot.handlers.admin.logs_admin import send_admin_log
from bot.handlers.auctions import addlot_start
from bot.handlers.card_subscribe import start_subscribe_card
from bot.handlers.constants import USER_MESSAGES
from bot.handlers.helper.helpers_users import (
    check_luxury,
    format_today_lots_fancy,
    format_user_lot_card,
    register_user,
    user_edit_lot_keyboard,
)
from bot.keyboards.keyboards import back_to_menu_keyboard, currency_choice_keyboard
from bot.services.auction_workflows import AuctionOwnerService
from db.legacy import (
    add_delete_request,
    get_auctions_by_date,
    get_lot_by_id,
    get_settings,
    is_subscribed,
    log_admin_action,
    set_settings,
    set_subscription,
    sync_trusted_status,
    update_lot_field, get_lots_by_owner_view, set_owner_lot_folder, auto_finish_old_lots_for_owner,
    get_user_verified_uid, get_user_basic_info_by_username, get_whois_admin_payload, get_user_id_by_uid_any,
    get_uid_profile_binding,
    mark_user_private_chat_closed,
    mark_user_private_chat_opened,
)
from bot.legacy_fsm import UserDeleteLotFSM, UserEditLotFSM, PublicWhoFSM
from bot.telegram.callback_parser import split_callback_data

router = Router()
logger = logging.getLogger(__name__)
UID_HEX_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)

async def _resolve_who_target_from_text_or_message(message: types.Message, raw: str | None = None) -> int | None:
    target_user_id = _extract_user_id_from_message(message)
    if target_user_id:
        return int(target_user_id)

    arg = (raw or "").strip()
    if not arg:
        return None

    a = arg.strip()

    if UID_HEX_RE.fullmatch(a):
        return await get_user_id_by_uid_any(a)

    if a.lower().startswith("id") and a[2:].isdigit():
        return int(a[2:])

    if a.isdigit():
        return int(a)

    u = a.lstrip("@").strip()
    if USERNAME_RE.fullmatch(u):
        info = await get_user_basic_info_by_username(username=u)
        if info:
            return int(info["user_id"])

    return None

@router.message(Command("start"), F.chat.type == "private")
async def start_cmd(message: types.Message, state: FSMContext, bot: Bot, command: CommandObject) -> None:
    # помечаем, что ЛС открыт
    try:
        await mark_user_private_chat_opened(message.from_user.id)
    except Exception:
        pass

    arg = (command.args or "").strip().lower()

    # спец-входы по диплинку
    if arg == "addlot":
        await addlot_start(message, state, bot)
        return

    if arg == "subs":
        # запуск мастера подписки из диплинка
        await start_subscribe_card(message, state)
        return

    # обычный старт с регистрацией
    try:
        await sync_trusted_status(message.from_user.id, message.from_user.username)
        is_lux, full_name = await register_user(message.from_user, bot)

        status_line = (
            "🌟 Ты в Лакшери-чате! Статус присвоен!\n" if is_lux
            else "✅ Ты успешно зарегистрирован в аукционном боте!\n"
        )

        await message.answer(
            USER_MESSAGES["welcome"].format(
                full_name=full_name,
                luxury_line=status_line,
                commands_info=USER_MESSAGES["commands_info"],
            ),
            parse_mode="HTML",
            reply_markup=back_to_menu_keyboard(),
        )

    except TelegramForbiddenError:
        # Юзер заблокировал бота. Писать ему больше нельзя. Просто выходим.
        logger.info("User %s blocked the bot: /start reply skipped", message.from_user.id)

        # (опционально) пометить в БД, что ЛС закрыто, чтобы потом не пытаться слать рассылки
        try:
            await mark_user_private_chat_closed(message.from_user.id)
        except Exception:
            pass
        return

    except Exception:
        logger.exception("Ошибка /start")
        # даже тут не делай второй раз “answer” без защиты
        try:
            await message.answer("Произошла ошибка регистрации. Попробуйте позже.")
        except TelegramForbiddenError:
            pass


@router.message(Command("luxury_check"), F.chat.type == "private")
async def luxury_check_cmd(message: types.Message, bot: Bot):
    try:
        is_lux = await check_luxury(message.from_user.id, bot)
        msg = USER_MESSAGES["luxury_success"] if is_lux else USER_MESSAGES["luxury_fail"]
        await message.answer(msg)
    except Exception as e:
        logger.error(f"/luxury_check error: {e}")
        await message.answer("Не удалось проверить статус. Попробуйте позже.")


@router.message(Command("subscribe"), F.chat.type == "private")
async def subscribe_cmd(message: types.Message):
    try:
        await set_subscription(message.from_user.id, True)
        await log_admin_action(message.from_user.id, "subscribe", None, "Пользователь подписался на уведомления")
        await message.answer(USER_MESSAGES["subscribed"])
    except Exception as e:
        logger.error(f"/subscribe error: {e}")
        await message.answer("Не удалось оформить подписку. Попробуйте позже.")


@router.message(Command("unsubscribe"), F.chat.type == "private")
async def unsubscribe_cmd(message: types.Message):
    try:
        await set_subscription(message.from_user.id, False)
        await log_admin_action(message.from_user.id, "unsubscribe", None, "Пользователь отписался от уведомлений")
        await message.answer(USER_MESSAGES["unsubscribed"])
    except Exception as e:
        logger.error(f"/unsubscribe error: {e}")
        await message.answer("Не удалось отменить подписку. Попробуйте позже.")


@router.message(Command("status"), F.chat.type == "private")
async def status_cmd(message: types.Message):
    try:
        sub = await is_subscribed(message.from_user.id)
        msg = USER_MESSAGES["status_subscribed"] if sub else USER_MESSAGES["status_not_subscribed"]
        await message.answer(msg)
    except Exception as e:
        logger.error(f"/status error: {e}")
        await message.answer("Не удалось получить статус подписки.")


@router.message(Command("profile"), F.chat.type == "private")
async def user_profile(message: types.Message):
    sub = await is_subscribed(message.from_user.id)
    status = "Подписан ✅" if sub else "Не подписан"
    await message.answer(
        f"<b>Профиль</b>\n"
        f"👤 {message.from_user.full_name}\n"
        f"ID: <code>{message.from_user.id}</code>\n"
        f"Статус уведомлений: {status}",
        parse_mode="HTML",
    )


USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{5,32}$")


def _moscow_today() -> date:
    """Return the calendar day used by the auction schedule."""

    return to_moscow(utc_now()).date()


def _parse_day_arg(
    raw: str | None,
    *,
    today: date | None = None,
) -> date | None:
    """Parse the date syntax accepted by ``/day`` in Moscow time.

    Supported values: empty argument, today/tomorrow/day-after-tomorrow,
    ``YYYY-MM-DD`` and ``DD.MM[.YYYY]`` (also ``/`` or ``-`` separators).
    """

    today = today or _moscow_today()
    value = (raw or "").strip().lower()
    if not value or value in {"сегодня", "today"}:
        return today
    if value in {"завтра", "tomorrow"}:
        return today + timedelta(days=1)
    if value in {"послезавтра", "dayaftertomorrow", "day-after-tomorrow"}:
        return today + timedelta(days=2)

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        pass

    match = re.fullmatch(
        r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?",
        value,
    )
    if not match:
        return None

    day = int(match.group(1))
    month = int(match.group(2))
    year_raw = match.group(3)

    if year_raw is None:
        try:
            candidate = date(today.year, month, day)
        except ValueError:
            return None
        if candidate < today:
            try:
                candidate = date(today.year + 1, month, day)
            except ValueError:
                return None
        return candidate

    year = int(year_raw)
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _fmt_dt(dt) -> str:
    if not dt:
        return "—"
    try:
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


def _replace_day_announce_header(text: str, target_day: date) -> str:
    lines = text.splitlines()
    if not lines:
        return f"🛜АНОНС НА {target_day.strftime('%d.%m.%Y')}🛜"
    lines[0] = f"🛜АНОНС НА {target_day.strftime('%d.%m.%Y')}🛜"
    return "\n".join(lines)


def _days_ago(dt) -> str:
    if not dt:
        return "—"
    try:
        now = datetime.now(timezone.utc)
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        d = (now - dt).days
        return f"{d} дн."
    except Exception:
        return "—"


@router.message(Command("day"), F.chat.type == "private")
async def day_lots(message: types.Message, command: CommandObject):
    target_day = _parse_day_arg(command.args)

    if target_day is None:
        await message.answer(
            "❌ Неверный формат даты.\n\n"
            "Примеры:\n"
            "/day\n"
            "/day 23.03\n"
            "/day 23.03.2026\n"
            "/day 2026-03-23\n"
            "/day сегодня\n"
            "/day завтра\n"
            "/day послезавтра",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    try:
        lots = await get_auctions_by_date(target_day)

        if not lots:
            await message.answer(
                f"🛜 На {target_day.strftime('%d.%m.%Y')} аукционов не найдено.",
                reply_markup=back_to_menu_keyboard(),
            )
            return

        msg = await format_today_lots_fancy(target_day, lots)
        msg = _replace_day_announce_header(msg, target_day)

        await message.answer(
            msg,
            parse_mode="HTML",
            reply_markup=back_to_menu_keyboard(),
            disable_web_page_preview=True,
        )

    except Exception as e:
        logger.exception("/day error for %s: %s", target_day, e)
        await message.answer("Не удалось получить анонс на выбранный день.")


def _extract_user_id_from_message(msg: types.Message) -> int | None:
    # reply
    if msg.reply_to_message and msg.reply_to_message.from_user:
        return int(msg.reply_to_message.from_user.id)

    # старое forward_from
    if msg.forward_from:
        return int(msg.forward_from.id)

    # новое forward_origin
    origin = getattr(msg, "forward_origin", None)
    if origin:
        sender = getattr(origin, "sender_user", None)
        if sender:
            return int(sender.id)

    return None

@router.message(Command("who", "who_u"), F.chat.type == "private")
async def cmd_who_public(message: types.Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()

    # 1) reply/forward
    target_user_id = _extract_user_id_from_message(message)

    # 2) если передан UID hex — отдельный безопасный режим
    if not target_user_id and arg and UID_HEX_RE.fullmatch(arg):
        data = await get_uid_profile_binding(arg.lower())
        is_banned = bool(data.get("is_banned")) if data else False

        await message.answer(
            "<b>WHO</b>\n"
            f"UID: <code>{arg[:4]}…{arg[-4:]}</code>\n"
            f"Есть в ЧС: <b>{'✅ да' if is_banned else '❌ нет'}</b>",
            parse_mode="HTML",
        )
        return

    # 3) обычный поиск по id / username
    if not target_user_id and arg:
        a = arg.strip()
        if a.lower().startswith("id") and a[2:].isdigit():
            target_user_id = int(a[2:])
        elif a.isdigit():
            target_user_id = int(a)
        else:
            u = a.lstrip("@").strip()
            if USERNAME_RE.fullmatch(u):
                info = await get_user_basic_info_by_username(username=u)
                if info:
                    target_user_id = int(info["user_id"])

    # 4) без аргумента -> про себя
    if not target_user_id:
        target_user_id = int(message.from_user.id)

    payload = await get_whois_admin_payload(user_id=int(target_user_id))
    if not payload:
        await message.answer("Не найден в базе. Возможно, не нажимал /start или бот в ЧС.")
        return

    u = payload["user"]
    lots_posted = int(payload.get("lots_posted") or 0)

    uname = (u.get("username") or "").strip()
    username_line = f"@{uname}" if uname else "—"

    created_at = u.get("created_at")
    reg_line = f"{_fmt_dt(created_at)} ({_days_ago(created_at)})"

    conf_done = u.get("uid_verif_confirmed_count", 0) or 0
    conf_rej = u.get("uid_verif_rejected_count", 0) or 0
    last_conf = u.get("uid_verif_last_confirmed_at")
    last_rej = u.get("uid_verif_last_rejected_at")

    uid_record = payload.get("uid_record") or {}
    uid_is_verified = str(uid_record.get("status") or "").lower() == "verified"
    uid_verif_line = "✅" if uid_is_verified else "—"

    in_blacklist = bool(payload.get("in_blacklist"))
    black_line = "✅ да" if in_blacklist else "❌ нет"

    await message.answer(
        "<b>WHO</b>\n"
        f"ID: <code>{u['user_id']}</code>\n"
        f"Username: <b>{username_line}</b>\n"
        f"Имя: {u.get('full_name') or '—'}\n\n"
        f"В Максе с: <code>{reg_line}</code>\n"
        f"Выставлял лотов (auction_owners): <b>{lots_posted}</b>\n"
        f"Подтверждал чужие сделки: ✅<b>{int(conf_done)}</b> / ❌<b>{int(conf_rej)}</b>\n"
        f"Последнее ✅: <code>{_fmt_dt(last_conf)}</code> • Последнее ❌: <code>{_fmt_dt(last_rej)}</code>\n"
        f"UID-верификация: <b>{uid_verif_line}</b>\n"
        f"Есть в ЧС: <b>{black_line}</b>",
        parse_mode="HTML",
    )
@router.message(PublicWhoFSM.waiting_for_who_target, F.chat.type == "private")
async def who_public_waiting_target(message: types.Message, state: FSMContext) -> None:
    txt = (message.text or "").strip()
    if txt.lower() in ("/cancel", "cancel", "отмена"):
        await state.clear()
        await message.answer("Ок, отменено.")
        return

    target_user_id = _extract_user_id_from_message(message)

    if not target_user_id and txt:
        arg = txt.strip()
        if arg.lower().startswith("id") and arg[2:].isdigit():
            target_user_id = int(arg[2:])
        elif arg.isdigit():
            target_user_id = int(arg)
        else:
            u = arg.lstrip("@").strip()
            if USERNAME_RE.fullmatch(u):
                info = await get_user_basic_info_by_username(username=u)
                if info:
                    target_user_id = int(info["user_id"])

    if not target_user_id:
        await message.answer(
            "Не смог определить пользователя.\n"
            "Нужен reply/forward или @username / user_id.\n"
            "Отмена: /cancel"
        )
        return

    await state.clear()

    payload = await get_whois_admin_payload(user_id=int(target_user_id))
    if not payload:
        await message.answer("Не найден в базе. Возможно, не нажимал /start или бот в ЧС.")
        return

    u = payload["user"]
    lots_posted = int(payload.get("lots_posted") or 0)

    uname = (u.get("username") or "").strip()
    username_line = f"@{uname}" if uname else "—"

    created_at = u.get("created_at")
    reg_line = f"{_fmt_dt(created_at)} ({_days_ago(created_at)})"

    conf_done = u.get("uid_verif_confirmed_count", 0) or 0
    conf_rej = u.get("uid_verif_rejected_count", 0) or 0
    last_conf = u.get("uid_verif_last_confirmed_at")
    last_rej = u.get("uid_verif_last_rejected_at")

    uid_record = payload.get("uid_record") or {}
    uid_is_verified = str(uid_record.get("status") or "").lower() == "verified"
    uid_verif_line = "✅" if uid_is_verified else "—"

    await message.answer(
        "<b>WHOIS</b>\n"
        f"ID: <code>{u['user_id']}</code>\n"
        f"Username: <b>{username_line}</b>\n"
        f"Имя: {u.get('full_name') or '—'}\n\n"
        f"В Максе с: <code>{reg_line}</code>\n"
        f"Выставлял лотов (auction_owners): <b>{lots_posted}</b>\n"
        f"Подтверждал чужие сделки: ✅<b>{int(conf_done)}</b> / ❌<b>{int(conf_rej)}</b>\n"
        f"Последнее ✅: <code>{_fmt_dt(last_conf)}</code> • Последнее ❌: <code>{_fmt_dt(last_rej)}</code>\n"
        f"UID-верификация: <b>{uid_verif_line}</b>",
        parse_mode="HTML",
    )

def build_lot_keyboard(lot: dict, role: str = "user") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    auction_id = int(lot.get("auction_id"))
    status = (lot.get("status") or "").strip().lower()
    folder = (lot.get("owner_folder") or lot.get("folder") or "default").strip().lower()

    if role == "user":
        # завершённые: вместо "редактировать" даём раскладку по папкам
        if status in {"finished", "completed"}:
            if folder == "default":
                kb.row(
                    InlineKeyboardButton(
                        text="🗄 Архивировать",
                        callback_data=f"my_lots:folder|{auction_id}|archived",
                    ),
                    InlineKeyboardButton(
                        text="💸 Выплачивается",
                        callback_data=f"my_lots:folder|{auction_id}|payable",
                    ),
                )
            else:
                kb.row(
                    InlineKeyboardButton(
                        text="↩️ Вернуть в список",
                        callback_data=f"my_lots:folder|{auction_id}|default",
                    )
                )
            return kb.as_markup()

        # актуальные (как раньше)
        kb.row(InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"useredit|{auction_id}"))
        kb.row(InlineKeyboardButton(text="🗑 Запросить удаление", callback_data=f"delete_lot|{auction_id}"))
        return kb.as_markup()

    return kb.as_markup()


@router.callback_query(F.data.startswith("my_lots:folder|"))
async def cb_my_lots_set_folder(call: types.CallbackQuery):
    try:
        _, auction_id_str, folder = split_callback_data(call.data, "|", 2)
        auction_id = int(auction_id_str)
    except Exception:
        await call.answer("Некорректная кнопка", show_alert=True)
        return

    user_id = call.from_user.id
    await set_owner_lot_folder(user_id=user_id, auction_id=auction_id, folder=folder)

    # перерисуем клавиатуру
    lot = await get_lot_by_id(auction_id)
    if lot:
        lot["owner_folder"] = folder
        kb = build_lot_keyboard(lot, role="user")
        try:
            await call.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass

    if folder == "archived":
        await call.answer("Перенесено в Архив")
    elif folder == "payable":
        await call.answer("Перенесено в Выплачивается")
    else:
        await call.answer("Возвращено в общий список")


async def _send_lot_media_any(
        message: types.Message,
        file_id: str,
        *,
        caption: str,
        reply_markup=None,
) -> bool:
    file_id = (file_id or "").strip()
    if not file_id:
        return False

    # 1) пробуем как фото
    try:
        await message.answer_photo(
            file_id,
            caption=caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        return True
    except TelegramBadRequest as e:
        # если это реально видео/гиф, Telegram ругнётся именно так
        if "Video as Photo" not in str(e):
            # другая ошибка: не маскируем
            raise
    except Exception:
        pass

    # 2) пробуем как видео
    try:
        await message.answer_video(
            file_id,
            caption=caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
            supports_streaming=True,
        )
        return True
    except Exception:
        pass

    # 3) на всякий случай пробуем как анимацию (gif)
    try:
        await message.answer_animation(
            file_id,
            caption=caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        return True
    except Exception:
        return False


async def _send_lots_list(message: types.Message, lots: list[dict], title: str):
    if not lots:
        await message.answer(f"{title}: пусто.")
        return

    await message.answer(f"<b>{title}</b>", parse_mode="HTML")

    for lot in lots:
        text = format_user_lot_card(lot)
        kb = build_lot_keyboard(lot, role="user")

        image_id = (lot.get("image_id") or "").strip()
        if image_id and image_id != "DEFAULT_PHOTO_ID":
            try:
                sent = await _send_lot_media_any(
                    message,
                    image_id,
                    caption=text,
                    reply_markup=kb,
                )
                if not sent:
                    await message.answer(text, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                logger.warning(f"Ошибка при отправке медиа {image_id}: {e}")
                await message.answer(text, parse_mode="HTML", reply_markup=kb)
        else:
            await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(Command("my_lots_actuals"), F.chat.type == "private")
async def my_lots_actuals_cmd(message: types.Message):
    lots = await get_lots_by_owner_view(
        message.from_user.id,
        folder=None,
        statuses=["pending", "scheduled", "active", "approved"],
    )
    await _send_lots_list(message, lots, "📌 Актуальные лоты")


@router.message(Command("my_lots_completed"), F.chat.type == "private")
async def my_lots_completed_cmd(message: types.Message):
    lots = await get_lots_by_owner_view(
        message.from_user.id,
        folder="default",
        statuses=["finished"],
    )
    await _send_lots_list(message, lots, "✅ Завершённые (разложи по папкам)")


@router.message(Command("my_lots_payable"), F.chat.type == "private")
async def my_lots_payable_cmd(message: types.Message):
    lots = await get_lots_by_owner_view(
        message.from_user.id,
        folder="payable",
        statuses=["finished"],
    )
    await _send_lots_list(message, lots, "💸 Выплачиваются")


@router.message(Command("my_lots_archival"), F.chat.type == "private")
async def my_lots_archival_cmd(message: types.Message):
    lots = await get_lots_by_owner_view(
        message.from_user.id,
        folder="archived",
        statuses=["finished"],
    )
    await _send_lots_list(message, lots, "🗄 Архив")


@router.message(Command("my_lots"), F.chat.type == "private")
async def my_lots_cmd(message: types.Message):
    # старьё > 2 месяцев в finished
    await auto_finish_old_lots_for_owner(message.from_user.id)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📌 Актуальные", callback_data="my_lots:view|actuals")],
            [InlineKeyboardButton(text="✅ Завершённые", callback_data="my_lots:view|completed")],
            [InlineKeyboardButton(text="💸 Выплачиваются", callback_data="my_lots:view|payable")],
            [InlineKeyboardButton(text="🗄 Архив", callback_data="my_lots:view|archival")],
        ]
    )
    await message.answer("Выбери раздел лотов:", reply_markup=kb)


@router.callback_query(F.data.startswith("my_lots:view|"))
async def cb_my_lots_view(call: types.CallbackQuery):
    view = split_callback_data(call.data, "|", 1)[1]
    uid = call.from_user.id

    if view == "actuals":
        lots = await get_lots_by_owner_view(uid, folder=None, statuses=["pending", "scheduled", "active", "approved"])
        title = "📌 Актуальные лоты"
    elif view == "completed":
        lots = await get_lots_by_owner_view(uid, folder="default", statuses=["finished"])
        title = "✅ Завершённые (разложи по папкам)"
    elif view == "payable":
        lots = await get_lots_by_owner_view(uid, folder="payable", statuses=["finished"])
        title = "💸 Выплачиваются"
    else:
        lots = await get_lots_by_owner_view(uid, folder="archived", statuses=["finished"])
        title = "🗄 Архив"

    await call.answer()
    await _send_lots_list(call.message, lots, title)


@router.callback_query(F.data.startswith("delete_lot|"))
async def user_delete_lot(call: types.CallbackQuery, state: FSMContext):
    lot_id = int(split_callback_data(call.data, "|")[1])
    owner_service = await AuctionOwnerService.create()
    try:
        lot = await owner_service.get_owned(lot_id, owner_id=call.from_user.id)
    except LookupError:
        lot = None
    if not lot:
        await call.answer("Лот не найден или недоступен.", show_alert=True)
        return

    if lot["status"] == "pending":
        await owner_service.cancel(lot_id, owner_id=call.from_user.id)
        await call.message.answer("Лот удалён.")
        owners_text = await get_pretty_owners_for_log(lot_id)
        log_text = format_admin_action_log(
            action="delete_lot",
            admin=None,
            lot=lot,
            owners_text=owners_text,
        )
        await send_admin_log(call.bot, log_text)

    elif lot["status"] in ["scheduled", "active"]:
        await state.update_data(lot_id=lot_id)
        await call.message.answer("Пожалуйста, напишите причину удаления (отправьте сообщением):")
        await state.set_state(UserDeleteLotFSM.waiting_for_delete_reason)

    await call.answer()


@router.message(UserDeleteLotFSM.waiting_for_delete_reason, ~F.text.lower().in_(["отмена", "cancel"]))
async def process_delete_reason(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lot_id = data.get("lot_id")
    reason = (message.text or "").strip()

    await add_delete_request(message.from_user.id, lot_id, reason)
    await message.answer("Ваша заявка на удаление отправлена модераторам!")

    lot = await get_lot_by_id(lot_id)
    owners_text = await get_pretty_owners_for_log(lot_id)
    log_text = format_admin_action_log(
        action="request_delete_lot",
        admin=None,
        lot=lot,
        owners_text=owners_text,
        reason=reason,
    )
    await send_admin_log(message.bot, log_text)
    await state.clear()


@router.message(UserDeleteLotFSM.waiting_for_delete_reason, F.text.lower().in_(["отмена", "cancel"]))
async def cancel_delete_reason(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Запрос на удаление отменён.", reply_markup=back_to_menu_keyboard())


@router.callback_query(F.data.startswith("useredit|"))
async def user_edit_lot_start(call: types.CallbackQuery, state: FSMContext):
    try:
        lot_id = int(split_callback_data(call.data, "|")[1])
        lot = await get_lot_by_id(lot_id)

        if not lot:
            await call.message.answer("Лот не найден.")
            await call.answer()
            return

        if lot["status"] != "pending":
            await call.message.answer("Редактировать можно только лоты на модерации.")
            await call.answer()
            return

        await state.update_data(lot_id=lot_id)
        await call.message.answer("Что хотите изменить?", reply_markup=user_edit_lot_keyboard(lot_id))
        await state.set_state(UserEditLotFSM.choosing_field)
        await call.answer()

    except Exception as e:
        logger.error(f"Ошибка запуска редактирования лота: {e}")
        await call.message.answer("Не удалось начать редактирование лота.")
        await call.answer()


@router.callback_query(UserEditLotFSM.choosing_field, F.data.startswith("user_edit_price"))
async def user_edit_price(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Введите новую стартовую цену (целое число):")
    await state.set_state(UserEditLotFSM.waiting_for_price)
    await call.answer()


@router.message(UserEditLotFSM.waiting_for_price, F.text.regexp(r"^\d+$"))
async def process_edit_price(message: types.Message, state: FSMContext):
    try:
        data = await state.get_data()
        lot_id = data["lot_id"]
        value = int(message.text)

        await update_lot_field(lot_id, "start_price", value)
        await message.answer("Стартовая цена обновлена.")

        lot = await get_lot_by_id(lot_id)
        owners_text = await get_pretty_owners_for_log(lot_id)
        log_text = format_admin_action_log(
            action="edit_lot",
            admin=None,
            lot=lot,
            owners_text=owners_text,
        )
        await send_admin_log(message.bot, log_text)
        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка обновления цены: {e}")
        await message.answer("Не удалось обновить цену. Попробуйте ещё раз.")
        await state.clear()


@router.callback_query(UserEditLotFSM.choosing_field, F.data.startswith("user_edit_currency"))
async def user_edit_currency(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Выберите валюту:", reply_markup=currency_choice_keyboard())
    await state.set_state(UserEditLotFSM.waiting_for_currency)
    await call.answer()


@router.callback_query(UserEditLotFSM.waiting_for_currency, F.data.startswith("user_edit_currency|"))
async def process_currency_choice(call: types.CallbackQuery, state: FSMContext):
    value = split_callback_data(call.data, "|", 1)[1]
    await state.update_data(new_currency=value)
    await call.message.answer(
        f"Валюта выбрана: <b>{value}</b>\nТеперь введите новую стартовую цену (целое число):",
        parse_mode="HTML",
    )
    await state.set_state(UserEditLotFSM.waiting_for_currency_price)
    await call.answer()


@router.message(UserEditLotFSM.waiting_for_currency_price, F.text.regexp(r"^\d+$"))
async def process_currency_and_price(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lot_id = data["lot_id"]
    currency = data["new_currency"]
    price = int(message.text)

    await update_lot_field(lot_id, "currency", currency)
    await update_lot_field(lot_id, "start_price", price)

    await message.answer(
        f"Валюта обновлена на <b>{currency}</b>, цена изменена на <b>{price}</b>.",
        parse_mode="HTML",
    )

    lot = await get_lot_by_id(lot_id)
    owners_text = await get_pretty_owners_for_log(lot_id)
    log_text = format_admin_action_log(
        action="edit_lot",
        admin=None,
        lot=lot,
        owners_text=owners_text,
    )
    await send_admin_log(message.bot, log_text)
    await state.clear()


@router.message(UserEditLotFSM.waiting_for_currency_price, ~F.text.lower().in_(["отмена", "cancel"]))
async def process_invalid_currency_price(message: types.Message, state: FSMContext):
    await message.answer("Введите корректную новую цену (целое число).")


@router.callback_query(UserEditLotFSM.choosing_field, F.data.startswith("user_edit_comment"))
async def user_edit_comment(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Введите новый комментарий:")
    await state.set_state(UserEditLotFSM.waiting_for_comment)
    await call.answer()


@router.message(UserEditLotFSM.waiting_for_comment, ~F.text.lower().in_(["отмена", "cancel"]))
async def process_edit_comment(message: types.Message, state: FSMContext):
    try:
        data = await state.get_data()
        lot_id = data["lot_id"]
        value = message.text.strip()

        await update_lot_field(lot_id, "comment", value)
        await message.answer("Комментарий обновлён.")

        lot = await get_lot_by_id(lot_id)
        owners_text = await get_pretty_owners_for_log(lot_id)
        log_text = format_admin_action_log(
            action="edit_lot",
            admin=None,
            lot=lot,
            owners_text=owners_text,
        )
        await send_admin_log(message.bot, log_text)
        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка обновления комментария: {e}")
        await message.answer("Не удалось обновить комментарий. Попробуйте ещё раз.")
        await state.clear()


@router.callback_query(UserEditLotFSM.choosing_field, F.data == "user_edit_cancel")
@router.callback_query(UserEditLotFSM.waiting_for_price, F.data == "user_edit_cancel")
@router.callback_query(UserEditLotFSM.waiting_for_currency, F.data == "user_edit_cancel")
@router.callback_query(UserEditLotFSM.waiting_for_comment, F.data == "user_edit_cancel")
@router.callback_query(UserEditLotFSM.waiting_for_currency_price, F.data == "user_edit_cancel")
async def user_edit_cancel(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Редактирование отменено.", reply_markup=back_to_menu_keyboard())
    await state.clear()
    await call.answer()


@router.message(
    UserEditLotFSM.waiting_for_price,
    UserEditLotFSM.waiting_for_currency,
    UserEditLotFSM.waiting_for_comment,
    UserEditLotFSM.waiting_for_currency_price,
    F.text.lower().in_(["отмена", "cancel"]),
)
async def user_edit_cancel_text(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=back_to_menu_keyboard())


@router.message(Command("today"), F.chat.type == "private")
async def today_lots(message: types.Message):
    today = date.today()
    try:
        lots = await get_auctions_by_date(today)
        if not lots:
            await message.answer(USER_MESSAGES["no_lots_today"], reply_markup=back_to_menu_keyboard())
            return

        msg = await format_today_lots_fancy(today, lots)
        await message.answer(msg, parse_mode="HTML", reply_markup=back_to_menu_keyboard())

    except Exception as e:
        logger.error("/today error: %s", e)
        await message.answer("Не удалось получить лоты на сегодня.")


@router.message(F.text == "🏠 Меню", F.chat.type == "private")
async def process_menu(message: types.Message):
    await message.answer(
        USER_MESSAGES["welcome"].format(
            full_name=message.from_user.full_name,
            luxury_line="",
            commands_info=USER_MESSAGES["commands_info"],
        ),
        parse_mode="HTML",
        reply_markup=back_to_menu_keyboard(),
    )


NOTIFY_DEFAULTS = {
    "notify_auction_start": True,
    "notify_bid_reminder": True,
    "notify_auction_end": True,
    "notify_daily_today": False,
}

# Соответствия "кнопка → (поле, msg_on, msg_off)"
NOTIFY_MAP = {
    "notify_toggle_start": ("notify_auction_start", "notify_start_on", "notify_start_off"),
    "notify_toggle_remind": ("notify_bid_reminder", "notify_remind_on", "notify_remind_off"),
    "notify_toggle_end": ("notify_auction_end", "notify_end_on", "notify_end_off"),
    "notify_toggle_today": ("notify_daily_today", "notify_today_on", "notify_today_off"),
}


def _as_bool(v, default=False):
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, int):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"1", "true", "t", "yes", "y", "да", "on"}:
            return True
        if s in {"0", "false", "f", "no", "n", "нет", "off"}:
            return False
    return default


def build_notifications_kb(settings: dict) -> InlineKeyboardMarkup:
    s = lambda k: _as_bool(settings.get(k, NOTIFY_DEFAULTS[k]))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔔 О начале аукциона {'✅' if s('notify_auction_start') else '❌'}",
                    callback_data="notify_toggle_start",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"⏰ За минуту до конца {'✅' if s('notify_bid_reminder') else '❌'}",
                    callback_data="notify_toggle_remind",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🏁 О завершении {'✅' if s('notify_auction_end') else '❌'}",
                    callback_data="notify_toggle_end",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"📅 Анонс дня в 00:00 {'✅' if s('notify_daily_today') else '❌'}",
                    callback_data="notify_toggle_today",
                )
            ],
        ]
    )


@router.message(Command("notifications"), F.chat.type == "private")
async def notifications_settings(message: types.Message):
    user_id = message.from_user.id
    settings = await get_settings(user_id) or {}
    await message.answer(
        USER_MESSAGES["notifications_header"],
        reply_markup=build_notifications_kb(settings),
    )


@router.callback_query(lambda call: call.data.startswith("notify_toggle_") or call.data == "toggle_notify_daily_today")
async def toggle_notification_pref(call: types.CallbackQuery):
    """
    Поддерживаем оба варианта callback'ов:
      - notify_toggle_*  (меню /notifications)
      - toggle_notify_*  (меню /settings из другого модуля; нам важен daily_today)
    """
    user_id = call.from_user.id
    data = call.data

    # Нормализуем к нашей карте
    if data == "toggle_notify_daily_today":
        data = "notify_toggle_today"

    if data not in NOTIFY_MAP:
        await call.answer("Ошибка!", show_alert=True)
        return

    field, msg_on, msg_off = NOTIFY_MAP[data]
    settings = (await get_settings(user_id)) or {}

    # правильный дефолт для каждого поля
    current = _as_bool(settings.get(field, NOTIFY_DEFAULTS[field]))
    new_value = not current
    await set_settings(user_id, **{field: new_value})

    # показываем верное сообщение
    popup_text = USER_MESSAGES.get(msg_on if new_value else msg_off) or "Настройка обновлена"
    await call.answer(popup_text, show_alert=True)

    # перерисовываем клавиатуру, если кнопка нажата из /notifications
    try:
        if call.message:
            fresh = (await get_settings(user_id)) or {}
            await call.message.edit_reply_markup(reply_markup=build_notifications_kb(fresh))
    except Exception:
        # не страшно, если сообщение уже не редактируется
        pass


@router.message(F.text.in_(["/help", "help"]), F.chat.type == "private")
async def user_help(message: types.Message):
    await message.answer(
        f"{USER_MESSAGES['commands_info']}\n{USER_MESSAGES['help_text']}\n{USER_MESSAGES['help_footer']}",
        parse_mode="HTML",
    )


@router.message(Command("hide_menu"), F.chat.type == "private")
async def hide_menu(message: types.Message):
    await message.answer(
        "Меню скрыто.",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@router.message(Command("profile"), F.chat.type == "private")
async def user_profile(message: types.Message):
    sub = await is_subscribed(message.from_user.id)
    status = "Подписан ✅" if sub else "Не подписан"

    uid = None
    try:
        uid = await get_user_verified_uid(message.from_user.id)
    except Exception:
        uid = None

    ver_line = "❌ НЕТ ВЕРИФИКАЦИИ"
    uid_line = ""
    if uid:
        ver_line = "✅ UID верифицирован"
        # показываем безопасно
        u = str(uid)
        uid_line = f"\nUID: <code>{u[:3]}***{u[-3:]}</code>"

    await message.answer(
        f"<b>Профиль</b>\n"
        f"👤 {message.from_user.full_name}\n"
        f"ID: <code>{message.from_user.id}</code>\n"
        f"Статус уведомлений: {status}\n"
        f"Верификация: {ver_line}"
        f"{uid_line}",
        parse_mode="HTML",
    )
