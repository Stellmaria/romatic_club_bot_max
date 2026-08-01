import asyncio
import html
import logging
import os
import random
import re
import shlex
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, date
from datetime import timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram.fsm.context import FSMContext
from dateutil import tz

from bot.handlers.admin.helper.new.wrapper import admin_only
from db.legacy import get_print_win_missed_for_day, get_exchange_batches_for_card, \
    upsert_exchange_print_stats, get_exchange_print_stats, reset_exchange_print_stats, \
    get_exchange_cards_for_batch, get_exchange_batch_by_id, \
    get_autobid_action_by_msg_id, _is_user_uid_verified, _users_uid_verification_counts
from bot.legacy_fsm import PrintExStates

MSK = tz.gettz("Europe/Moscow")
from aiogram import Bot
from aiogram import Router, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from dateutil import parser
from flask import Flask, request

from bot.domain.auctions import AuctionKind, Currency
from bot.handlers.admin.helper.admin_constants import WARN_TEXTS
from bot.handlers.admin.action_support.compat import send_admin_log
from bot.handlers.admin.helper.new.formatting import format_admin_action_log
from bot.services.auction_winners import AuctionWinnerService
from bot.services.auction_admin import AuctionAdminService
from bot.services.auction_comments import AuctionCommentService
from bot.services.card_subscriptions import CardSubscriptionsService
from bot.services.warnings import WarningService
from bot.core.legacy_config import DISCUSSION_CHAT_ID, ADMINS, BOT_TOKEN, LOG_CHAT_ID, AUCTION_CHANNEL_USERNAME, AUCTION_CHANNEL_ID, \
    ADMIN_LOG_CHATS
from db import db
from db.legacy import add_bid, update_lot_field, add_warning, get_auction_by_discussion_id, \
    get_warnings_count, ban_user, is_user_banned, get_current_auction, add_user, execute, fetchrow, get_lot_owners, \
    get_user, get_lots_by_owner, get_user_by_username, log_audit_action, update_auction_status, get_lot_by_id, fetch

try:
    from bot.core.legacy_config import WINNER_NOTIFY_DEADLINE_MINUTES
except Exception:
    WINNER_NOTIFY_DEADLINE_MINUTES = 5

logger = logging.getLogger("auction")

if not logger.hasHandlers():
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    logger.addHandler(handler)
router = Router()
_PG_DSN = os.getenv("DATABASE_URL")
user_warnings = defaultdict(list)
admin_pending_warns = {}

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
app = Flask(__name__)

USERBOT_BID_MODERATION = True

BID_VALIDATION_MODE = os.getenv("BID_VALIDATION_MODE", "userbot").lower()  # "bot" | "userbot"

TG_MAX = 3900

# Эти константы уже есть в config.py; не надо тихо перезатирать их тут.
AUCTION_SUPPORT_CONTACT = "@Dear_Davidik"
AUCTION_SUPPORT_CONTACT_2 = "@Dummo_loh"
AUCTION_PROBLEMS_CONTACT = "@Dear_Davidik"

CURRENCY_EMOJI = {
    "алмазы": "💎",
    "чашки": "☕️",
    "сокровища": "🪙",
    "treasures": "🪙",
    "diamonds": "💎",
    "tea": "☕️",
}

CB_REFRESH = "pw:r:"
CB_SEND_OWNER = "pw:o:"
CB_SEND_WINNER = "pw:w:"
CB_MANUAL = "pw:m:"
CB_THANKS = "pw:t:"  # pw:t:<auction_id>:<author_tag>

_MSK = ZoneInfo("Europe/Moscow")

async def _safe_pin_pm_message(bot: Bot, user_id: int, message_id: int) -> bool:
    try:
        await bot.pin_chat_message(
            chat_id=user_id,
            message_id=message_id,
            disable_notification=True,
        )
        return True
    except Exception:
        return False
async def _send_media_any(
        bot: Bot,
        chat_id: int,
        file_id: str,
        caption: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    """
    Пытается отправить file_id как фото.
    Если Telegram ругается, что это видео/анимация/документ — пробуем соответствующий метод.
    """
    file_id = (file_id or "").strip()
    if not file_id:
        raise ValueError("empty file_id")

    try:
        return await bot.send_photo(
            chat_id,
            photo=file_id,
            caption=caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as e:
        msg = str(e)
        if "Video as Photo" in msg or "type Video as Photo" in msg:
            return await bot.send_video(
                chat_id,
                video=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
                supports_streaming=True,
            )
        if "Animation as Photo" in msg or "type Animation as Photo" in msg:
            return await bot.send_animation(
                chat_id,
                animation=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        if "Document as Photo" in msg or "type Document as Photo" in msg:
            return await bot.send_document(
                chat_id,
                document=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        raise


async def _resolve_winner(text: str) -> tuple[int | None, str | None]:
    s = (text or "").strip()
    if not s:
        return None, None

    if s.isdigit():
        return int(s), None

    username = s[1:] if s.startswith("@") else s
    username = username.strip()
    if not username:
        return None, None

    u = await get_user_by_username(username)
    if u and u.get("user_id"):
        return int(u["user_id"]), username

    # пользователь не найден в БД (скорее всего не нажимал /start)
    return None, username


async def _resolve_lot_from_reply(message: Message, max_depth: int = 7) -> Optional[dict]:
    """Ищем лот по reply: либо это reply на пост лота, либо на ставку внутри треда."""
    cur = message.reply_to_message
    for _ in range(max_depth):
        if not cur:
            break
        lot = await get_auction_by_discussion_id(cur.message_id)
        if lot:
            return lot

        auction_id = await (await AuctionCommentService.create()).auction_id_for_bid_message(cur.message_id)
        if auction_id:
            lot = await get_lot_by_id(auction_id)
            if lot:
                return lot

        cur = cur.reply_to_message
    return None


async def answer_html_chunks(message, lines: list[str], max_len: int = TG_MAX) -> None:
    buf: list[str] = []
    size = 0

    for line in lines:
        line = line.rstrip()
        add = len(line) + (1 if buf else 0)  # + \n если не первая строка

        if size + add > max_len:
            await message.answer("\n".join(buf), parse_mode="HTML")
            buf = [line]
            size = len(line)
        else:
            if buf:
                size += 1
            buf.append(line)
            size += len(line)

    if buf:
        await message.answer("\n".join(buf), parse_mode="HTML")


@app.route('/notify_bid_deleted', methods=['POST'])
def notify_bid_deleted():
    data = request.json
    print(f"[FLASK] Получен POST: {data}")
    chat_id = data["chat_id"]
    reply_to_message_id = data["reply_to_message_id"]
    username = data.get("username")
    amount = data.get("amount")
    user_id = data.get("user_id")
    msg1 = (
        f"❗️ <b>Ставка удалена</b>\n"
        f"@{username}, ваша ставка удалена. (сумма: {amount})"
    )

    async def process_and_send():
        warnings = 1
        if user_id:
            warnings = await (await WarningService.create()).count_warnings(int(user_id))
        from bot.handlers.admin.helper.admin_constants import WARN_TEXTS
        import random
        msg2 = random.choice(WARN_TEXTS).format(username=username, warnings=warnings)
        m1 = await bot.send_message(
            chat_id=chat_id,
            text=msg1,
            reply_to_message_id=reply_to_message_id
        )
        await bot.send_message(
            chat_id=chat_id,
            text=msg2,
            reply_to_message_id=m1.message_id
        )
        print("[FLASK] Сообщения об удалении ставки и преды отправлены!")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        import threading
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
    asyncio.run_coroutine_threadsafe(process_and_send(), loop)
    return "ok"


def run_flask():
    app.run("127.0.0.1", 8002)


async def _legacy_show_lot_owners(message: types.Message):
    # Только для админов
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return
    # Получить номер лота
    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: /lot_owner <auction_id>")
        return
    lot_id = int(parts[1])
    # Получить владельцев лота
    owners = await get_lot_owners(lot_id)
    if not owners:
        await message.answer(f"Владельцы лота {lot_id} не найдены.")
        return
    lines = []
    for o in owners:
        user = await get_user(o["user_id"])
        if user:
            uname = user.get("username")
            uname_str = f"@{uname}" if uname else "-"
            lines.append(f"id: <code>{user['user_id']}</code> | username: {uname_str}")
        else:
            lines.append(f"id: <code>{o['user_id']}</code> | username: -")
    text = f"Владельцы лота <b>{lot_id}</b>:\n" + "\n".join(lines)
    await message.answer(text, parse_mode="HTML")


async def _legacy_activate_lot_cmd(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return

    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: /activate_lot <auction_id>")
        return

    auction_id = int(parts[1])
    lot = await get_lot_by_id(auction_id)
    if not lot:
        await message.answer("Лот не найден.")
        return

    await update_auction_status(auction_id, "scheduled")

    # Формируем лог и сообщение
    owners = await get_lot_owners(auction_id)
    owner_users = []
    for o in owners:
        user = await get_user(o["user_id"])
        if user:
            user = dict(user)
            user["is_luxury"] = user.get("is_luxury", False)
            owner_users.append(user)
    owners_text = ", ".join(
        "👑 @" + u["username"] if u.get("is_luxury") and u.get("username") else
        ("@" + u["username"] if u.get("username") else f"id:{u['user_id']}")
        for u in owner_users
    ) or "-"

    await message.answer(
        f"✅ Лот <b>{lot.get('card_name')}</b> (ID {auction_id}) теперь активен.",
        parse_mode="HTML"
    )

    log_text = format_admin_action_log(
        action="force_activate_lot",
        admin={"id": message.from_user.id, "username": message.from_user.username or message.from_user.full_name},
        lot=lot,
        owners_text=owners_text
    )
    await send_admin_log(message.bot, log_text)
    await log_audit_action(
        user_id=message.from_user.id,
        action_type="force_activate_lot",
        auction_id=auction_id,
        details="Лот активирован вручную"
    )


async def _legacy_show_user_lots(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return

    parts = message.text.strip().split()
    if len(parts) < 2:
        await message.answer("Формат: /user_lots <user_id или @username>")
        return

    who = parts[1]
    user = None
    # Получить user_id по username или сразу использовать id
    if who.isdigit():
        user = await get_user(int(who))
    else:
        uname = who.lstrip("@")
        user = await get_user_by_username(uname)
    if not user:
        await message.answer("Пользователь не найден.")
        return

    lots = await get_lots_by_owner(user["user_id"])
    if not lots:
        await message.answer("У пользователя нет лотов.")
        return

    text_lines = [
        f"Лоты пользователя <b>{user.get('username') or user['user_id']}</b>:"
    ]
    for lot in lots:
        line = (
            f"— <b>{lot.get('card_name', '-')}</b> "
            f"(ID: <code>{lot['auction_id']}</code>, "
            f"Дата: <code>{lot['start_time'].strftime('%d.%m %H:%M')}</code>, "
            f"Статус: <i>{lot.get('status', '-')}</i>)"
        )
        text_lines.append(line)
    await answer_html_chunks(message, text_lines)


async def _legacy_admin_delete_bid(message: types.Message):
    # доступ
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return

    # нужно reply на сообщение-ставку
    if not message.reply_to_message:
        await message.answer("Используй команду reply на сообщение-ставку.")
        return

    replied_id = message.reply_to_message.message_id

    # ВНИМАНИЕ: в таблице bids первичный ключ bid_id, а не id
    bid_row = await (await AuctionAdminService.create()).delete_bid_with_warning(
        discussion_message_id=replied_id,
    )
    if not bid_row:
        await message.answer("Это не ставка или ставка не найдена.")
        return

    # удаляем сообщение в чате, если можно
    try:
        await message.bot.delete_message(message.chat.id, replied_id)
    except Exception as e:
        print(f"Не удалось удалить сообщение: {e}")

    warns = int(bid_row["warnings_count"])
    banned = bool(bid_row["is_banned"])

    bidder = f"@{bid_row['username']}" if bid_row["username"] else f"id{bid_row['bidder_id']}"
    text = (
        f"❌ <b>Ставка удалена админом</b>\n"
        f"{bidder}, ваша ставка удалена как ошибочная."
        f"\nПредупреждений: {warns}/4"
        f"\n{'🚫 Пользователь забанен!' if banned else ''}"
    )
    await message.answer(text, reply_to_message_id=replied_id, parse_mode="HTML")


async def _legacy_admin_start_auction(message: types.Message):
    """
    Админ-команда для принудительного старта/продления аукциона.
    Работает через reply к сообщению лота или любой ставке.
    Ставит статус "active" и новый end_time на 30 минут вперёд.
    Уведомляет чат и владельца.
    """
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return
    if not message.reply_to_message:
        await message.answer("Используйте команду reply к сообщению лота или любой ставке.")
        return

    lot = await _resolve_lot_from_reply(message)
    if not lot:
        await message.answer("Не удалось найти аукцион по reply.")
        return
    new_end_time = datetime.now(_MSK) + timedelta(minutes=30)
    await update_auction_status(lot["auction_id"], "active")
    # В базе у нас обычно лежит naive timestamp "в МСК".
    await update_lot_field(lot["auction_id"], "end_time", new_end_time.replace(tzinfo=None))
    auction_msg_id = lot.get("discussion_message_id") or lot.get("message_id")
    chat_id = message.chat.id
    notify_text = (
        f"⏳ <b>Аукцион возобновлён администратором!</b>\n"
        f"Карта: <b>{lot['card_name']}</b>\n"
        f"Продлён до: <b>{new_end_time.strftime('%d.%m %H:%M')}</b>\n"
        f"Ставки принимаются снова!"
    )
    try:
        await message.bot.send_message(chat_id, notify_text, parse_mode="HTML", reply_to_message_id=auction_msg_id)
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.warning("[Макс старт] Не удалось отправить уведомление в чат: %r", e)
    for owner in (await get_lot_owners(lot["auction_id"])):
        try:
            await message.bot.send_message(
                owner["user_id"],
                f"⏳ Ваш аукцион <b>{lot['card_name']}</b> был принудительно запущен/продлён админом!\n"
                f"Новая дата окончания: <b>{new_end_time.strftime('%d.%m %H:%M')}</b>",
                parse_mode="HTML",
            )
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            logger.warning("[Макс старт] Не удалось отправить владельцу %s: %r", owner.get("user_id"), e)
    with contextlib.suppress(TelegramBadRequest, TelegramForbiddenError):
        await message.bot.send_animation(
            chat_id=chat_id,
            animation="https://media.stickerswiki.app/lovestory/590143.512.webp",
            reply_to_message_id=auction_msg_id,
        )
    await message.answer(
        f"✅ Аукцион <b>{lot['card_name']}</b> запущен заново до <b>{new_end_time.strftime('%d.%m %H:%M')}</b>.",
        parse_mode="HTML"
    )


async def _legacy_admin_stop_auction(message: types.Message):
    """
    Админ-команда для досрочного завершения аукциона (макс стоп).
    Работает через reply к сообщению лота или любой ставке.
    Ставит статус "finished" и фиксирует end_time.
    Уведомляет чат и владельца.
    """
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return
    if not message.reply_to_message:
        await message.answer("Используйте команду reply к сообщению лота или любой ставке.")
        return
    lot = await _resolve_lot_from_reply(message)
    if not lot:
        await message.answer("Не удалось найти аукцион по reply.")
        return
    stop_time = datetime.now(_MSK)
    await update_auction_status(lot["auction_id"], "finished")
    await update_lot_field(lot["auction_id"], "end_time", stop_time.replace(tzinfo=None))
    auction_msg_id = lot.get("discussion_message_id") or lot.get("message_id")
    chat_id = message.chat.id
    notify_text = (
        f"⏹ <b>Аукцион остановлен администратором!</b>\n"
        f"Карта: <b>{lot['card_name']}</b>\n"
        f"Завершён в: <b>{stop_time.strftime('%d.%m %H:%M')}</b>\n"
        f"Ставки больше не принимаются!"
    )
    try:
        await message.bot.send_message(chat_id, notify_text, parse_mode="HTML", reply_to_message_id=auction_msg_id)
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.warning("[Макс стоп] Не удалось отправить уведомление в чат: %r", e)

    for owner in (await get_lot_owners(lot["auction_id"])):
        try:
            await message.bot.send_message(
                owner["user_id"],
                f"⏹ Ваш аукцион <b>{lot['card_name']}</b> был досрочно остановлен админом!\n"
                f"Дата завершения: <b>{stop_time.strftime('%d.%m %H:%M')}</b>",
                parse_mode="HTML",
            )
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            logger.warning("[Макс стоп] Не удалось отправить владельцу %s: %r", owner.get("user_id"), e)

    with contextlib.suppress(TelegramBadRequest, TelegramForbiddenError):
        await message.bot.send_animation(
            chat_id=chat_id,
            animation="https://media.stickerswiki.app/lovestory/590143.512.webp",
            reply_to_message_id=auction_msg_id,
        )
    await message.answer(
        f"✅ Аукцион <b>{lot['card_name']}</b> досрочно завершён.",
        parse_mode="HTML"
    )


async def _legacy_admin_unmute(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return
    parts = message.text.strip().split()
    if len(parts) < 3:
        await message.answer("Формат: Макс размут @username")
        return
    username = parts[2].lstrip("@")
    from db.legacy import get_user_by_username
    user = await get_user_by_username(username)
    if not user:
        await message.answer(f"Пользователь @{username} не найден.")
        return
    user_id = user["user_id"]
    try:
        await message.bot.restrict_chat_member(
            message.chat.id,
            user_id,
            permissions=types.ChatPermissions(can_send_messages=True)
        )
        await message.answer(f"✅ Пользователь @{username} размучен.")
    except Exception as e:
        await message.answer(f"Ошибка при размуте: {e}")


MAX_LOVE_RESPONSES = [
    "Я тоже тебя люблю, только тихо — это аукцион!",
    "Ставь на карту, а не на чувства 😉",
    "Проверка на любовь к боту пройдена!",
    "Твоя любовь ценнее любых алмазов!",
    "Записал признание в историю ставок 😄",
    "Инициировано: взаимность подтверждена ✅",
    "Хорошо, буду млеть ровно 3 секунды... ок, прошло.",
    "Любовь засчитана. Бонус к удаче +1.",
    "Шшш... конкуренты услышат и начнут переплачивать.",
    "Сервер чувств перезагружен. Связь устойчивая.",
    "Твоя заявка на любовь принята, очередь без очереди.",
    "Сердечко поставлено. Дубликаты не создаются.",
    "Люблю тебя обратно. В квадрате.",
    "Храню это в кеше тепла. Без TTL.",
    "Поддерживаю высокий курс на взаимность.",
    "Эмоции зафиксированы. Ставки повышены.",
    "Нежность принята. Без комиссии.",
    "Если это лжеставка — бан по сердцу. Если нет — обниму.",
    "Ладно, признаю, ты чудо. Никому не говори.",
    "Судя по телеметрии, ты вообще самый милый человек тут.",
    "Люблю, но правила одни для всех: ставки — только цифрами.",
    "Оформим это как VIP-чувство. Престижный пакет.",
    "Поставлю это признание в избранные.",
    "Так и запишем: «любовь без торга».",
    "Пока ты это читал, я уже тоже полюбил ещё раз.",
]
CREATOR_ID = 7221553045
CREATOR_USERNAME = "aam_cheshire"
LOVE_PATTERNS = [
    "макс я тебя люблю",
    "макс я люблю тебя",
    "макс люблю тебя",
    "макс тебя люблю",
    "макс люблю макса",
    "макс тебя люблю макс",
    "макс люблю",
    "макс люблю вас",
    "макс обожаю тебя",
    "макс обожаю",
    "макс люблю сильно"
]

LOVE_REGEX = re.compile(
    r'макс[^a-zа-яё\d]*?(я)?[^a-zа-яё\d]*?(люб(?:ишь|лю))[^a-zа-яё\d]*?(тебя|вас|макс)?', re.IGNORECASE
)

CB_WIN_SEND = "win:send"
CB_WIN_SKIP = "win:skip"


def _emoji_by_currency(currency: str | None) -> str:
    c = (currency or "").strip().lower()
    if c in ("чашки", "чай", "tea", "cups"):
        return "🍵"
    if c in ("сокровища", "treasure", "treasures"):
        return "🪙"
    return "💎"


def _mention(user_id: int, username: str | None) -> str:
    return f"@{username}" if username else f'<a href="tg://user?id={user_id}">id{user_id}</a>'


def _norm_username(username: str | None) -> str | None:
    u = (username or "").strip()
    if not u:
        return None
    return u[1:] if u.startswith("@") else u


def _user_links_html(user_id: int, username: str | None) -> str:
    """
    Возвращает кликабельные ссылки:
      - https://t.me/<username> (если есть username)
      - tg://user?id=<id>
      - tg://openmessage?user_id=<id>
    """
    uid = int(user_id)
    uname = _norm_username(username)

    parts: list[str] = []
    if uname:
        safe = html.escape(uname)
        parts.append(f'<a href="https://t.me/{safe}">https://t.me/{safe}</a>')

    parts.append(f'<a href="tg://user?id={uid}">tg://user?id={uid}</a>')
    parts.append(f'<a href="tg://openmessage?user_id={uid}">tg://openmessage?user_id={uid}</a>')
    return " | ".join(parts)


def _build_channel_link(message_id: int | None) -> str | None:
    if not message_id:
        return None
    if AUCTION_CHANNEL_USERNAME:
        return f"https://t.me/{AUCTION_CHANNEL_USERNAME.lstrip('@')}/{message_id}"
    if AUCTION_CHANNEL_ID and str(AUCTION_CHANNEL_ID).startswith("-100"):
        return f"https://t.me/c/{str(AUCTION_CHANNEL_ID)[4:]}/{message_id}"
    return None


async def _get_owners(auction_id: int) -> list[dict]:
    return await (await AuctionWinnerService.create()).owners(auction_id)


def _kb_winner_action(auction_id: int, winner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Отправить уведомления", callback_data=f"{CB_WIN_SEND}:{auction_id}:{winner_id}")],
        [InlineKeyboardButton(text="⛔ Не отправлять", callback_data=f"{CB_WIN_SKIP}:{auction_id}:{winner_id}")],
    ])


@router.message(lambda m: m.text and LOVE_REGEX.search(m.text))
async def love_handler(message: types.Message):
    sender_id = message.from_user.id
    sender_username = (message.from_user.username or "").strip().lower()

    if sender_id == CREATOR_ID or sender_username == CREATOR_USERNAME.lower():
        reply = "И я тебя, мам, спасибо что создала меня! 🫶"
        await message.answer(reply)
        return

    try:
        is_owner = await (await AuctionCommentService.create()).is_active_lot_owner(
            user_id=sender_id,
            username=sender_username,
        )
    except Exception as e:
        print(f"[MAX_LOVE] Ошибка поиска владельцев: {e}")

    reply = "И я тебя, мамочка! 😘" if is_owner else random.choice(MAX_LOVE_RESPONSES)
    await message.answer(reply)


MAX_BID_MSGS = {}


@router.message(F.text.lower().startswith('макс мои преды'))
async def my_warns_handler(message: types.Message):
    warnings = await get_warnings_count(message.from_user.id)
    banned = await is_user_banned(message.from_user.id)
    text = (
        f"👤 @{message.from_user.username or 'user'}\n"
        f"Ваших предупреждений: <b>{warnings}/4</b>\n"
        f"Статус: {'<b>ЗАБАНЕН</b> 🚫' if banned else 'Активен ✅'}"
    )
    await message.answer(text, parse_mode="HTML", reply_to_message_id=message.message_id)


@router.message(F.text.lower().startswith('макс фас'))
async def admin_warn_step1(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("У вас нет прав на выдачу предупреждений.")
        return
    parts = message.text.strip().split()
    if len(parts) < 3:
        await message.answer("Формат: Макс фас @username\nСледующим сообщением укажите причину.")
        return
    target_username = parts[2].lstrip('@')
    admin_pending_warns[message.from_user.id] = target_username
    await message.answer(f"Теперь пришлите причину для @{target_username} отдельным сообщением.")


@router.message(lambda m: m.from_user.id in admin_pending_warns)
async def admin_warn_step2(message: types.Message):
    from db.legacy import get_user_id_by_username  # реализуй если нет
    target_username = admin_pending_warns.pop(message.from_user.id)
    user_id = await get_user_id_by_username(target_username)
    if not user_id:
        await message.answer(f"Пользователь @{target_username} не найден.")
        return
    reason = (message.text or "").strip()
    await add_warning(user_id, f"admin: {reason}")
    warnings = await get_warnings_count(user_id)
    banned = await is_user_banned(user_id)
    await message.answer(
        f"@{target_username} получил предупреждение от администратора.\n"
        f"Причина: {reason}\n"
        f"Всего предупреждений: {warnings}/4\n"
        f"Статус: {'ЗАБАНЕН' if banned else 'Активен'}"
    )
    if warnings >= 4 and not banned:
        await ban_user(user_id, reason="4 warnings (от администратора)")
        await message.answer(f"Пользователь @{target_username} ЗАБАНЕН за 4 предупреждения.")


@router.message(F.text.lower().startswith('макс преды'))
async def admin_check_warns(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Формат: Макс преды @username")
        return
    username = parts[2].lstrip("@")
    from db.legacy import get_user_id_by_username
    user_id = await get_user_id_by_username(username)
    if not user_id:
        await message.answer("Пользователь не найден.")
        return
    warns = await get_warnings_count(user_id)
    banned = await is_user_banned(user_id)
    await message.answer(
        f"@{username}: {warns}/4 предупреждений\nСтатус: {'ЗАБАНЕН' if banned else 'Активен'}"
    )


@router.message(F.text.lower().startswith('макс амнистия'))
async def admin_unban_and_reset(message: types.Message):
    """
    Полный рабан пользователя: снимает мут/бан и разрешает отправлять любые сообщения,
    включая картинки, документы, видео, стикеры и т.д.
    Формат: макс амнистия @username

    Только для админов.
    """
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return

    parts = message.text.strip().split()
    if len(parts) < 3:
        await message.answer("Формат: макс амнистия @username")
        return

    username = parts[2].lstrip("@")
    from db.legacy import get_user_by_username, get_user_id_by_username, unban_user, reset_warnings, is_user_banned
    user = await get_user_by_username(username)
    user_id = user["user_id"] if user else await get_user_id_by_username(username)
    if not user_id:
        await message.answer(f"Пользователь @{username} не найден.")
        return
    await unban_user(user_id)
    await reset_warnings(user_id)
    try:
        await message.bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=user_id,
            permissions=types.ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_invite_users=True,
                can_pin_messages=True,
                can_change_info=True
            )
        )
    except Exception as e:
        await message.answer(f"Ошибка снятия ограничений: {e}")
    banned = await is_user_banned(user_id)
    await message.answer(
        f"✅ Пользователь @{username} полностью разбанен, может отправлять любые сообщения!\n"
        f"Статус: {'<b>ЗАБАНЕН</b> 🚫' if banned else 'Активен ✅'}",
        parse_mode="HTML"
    )


@router.message(F.text.lower().startswith('макс рабан'))
async def admin_full_unrestrict(message: types.Message):
    """
    Снимает все ограничения Telegram с пользователя в чате (разбан, размут, разрешает фото, видео, документы и пр).
    Формат: макс рабан @username или reply к сообщению пользователя.
    Только для админов.
    """
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        username = message.reply_to_message.from_user.username or f"id{user_id}"
    else:
        parts = message.text.strip().split()
        if len(parts) < 3:
            await message.answer("Формат: макс рабан @username (или используйте reply)")
            return
        username = parts[2].lstrip("@")
        from db.legacy import get_user_by_username
        user = await get_user_by_username(username)
        if not user:
            await message.answer(
                f"Пользователь @{username} не найден в базе. Используйте рабан через reply, если он не писал боту.")
            return
        user_id = user["user_id"]
    try:
        await message.bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=user_id,
            permissions=types.ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_send_polls=True,
                can_add_web_page_previews=True,
                can_invite_users=True
            )
        )
        await message.answer(f"✅ Пользователь @{username} полностью разблокирован и может отправлять любые сообщения.")
    except Exception as e:
        await message.answer(f"Ошибка снятия ограничений: {e}")


@router.message(F.text.lower().startswith('макс обнулить'))
async def admin_reset_warns(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return
    parts = message.text.strip().split()
    if len(parts) < 3:
        await message.answer("Формат: Макс обнулить @username")
        return
    username = parts[2].lstrip("@")
    from db.legacy import get_user_by_username, reset_warnings, is_user_banned
    user = await get_user_by_username(username)
    if not user:
        await message.answer(f"Пользователь @{username} не найден.")
        return
    user_id = user["user_id"]
    await reset_warnings(user_id)
    banned = await is_user_banned(user_id)
    await message.answer(
        f"✅ Предупреждения @{username} обнулены. Статус: {'ЗАБАНЕН' if banned else 'Активен'}"
    )


@router.message(F.text.lower().startswith('макс все преды'))
async def admin_all_warns(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return
    rows = await (await WarningService.create()).list_users_with_warnings()
    if not rows:
        await message.answer("Нет пользователей с предупреждениями.")
        return
    lines = []
    for r in rows:
        user_str = f"@{r['username']}" if r['username'] else f"id{r['user_id']}"
        lines.append(f"{user_str} — {r['warnings_count']}/4")
    reply = "<b>Список всех предупреждений:</b>\n" + "\n".join(lines)
    await message.answer(reply, parse_mode="HTML")


@router.message(F.text.lower().startswith('макс удалённые ставки'))
async def show_deleted_bids(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return
    rows = await (await WarningService.create()).list_deleted_bid_warnings(limit=50)
    if not rows:
        await message.answer("Нет удалённых ставок за последнее время.")
        return
    text = "<b>Последние удаления ставок:</b>\n"
    for r in rows:
        user = f"@{r['username']}" if r['username'] else f"id{r['user_id']}"
        text += f"{user} — {r['issued_at'].strftime('%d.%m.%Y %H:%M:%S')}\n"
    await message.answer(text, parse_mode="HTML")


async def get_lot_by_msg_id(msg_id: int):
    return await get_auction_by_discussion_id(msg_id)


async def get_lot_by_discussion_msg_id(msg_id: int):
    return await get_auction_by_discussion_id(msg_id)


def _legacy_parse_bid(text: str) -> int | None:
    txt = (text or '').replace(' ', '').replace('к', 'k').lower()
    if txt.endswith('k'):
        try:
            return int(txt[:-1]) * 1000
        except ValueError:
            return None
    if txt.isdigit():
        return int(txt)
    return None


async def _legacy_admin_ban_user(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return

    parts = message.text.strip().split()
    if len(parts) < 3:
        await message.answer("Формат: Макс бан @username (или reply на сообщение пользователя)")
        return

    username = parts[2].lstrip("@")
    from db.legacy import get_user_by_username, ban_user
    user = await get_user_by_username(username)
    if not user:
        await message.answer(f"Пользователь @{username} не найден.")
        return

    user_id = user["user_id"]
    await ban_user(user_id, reason="бан через команду 'макс бан'")
    try:
        await message.bot.restrict_chat_member(
            message.chat.id,
            user_id,
            permissions=types.ChatPermissions(can_send_messages=False)
        )
    except Exception as e:
        await message.answer(f"Ошибка при бане пользователя: {e}")

    ban_text = f"@{username or 'нарушитель'}, Скажи честно, неужели ты ожидал другого исхода?"
    await message.answer(ban_text)


MAX_AUF_QUOTES = [
    "Что за лузер на ржавомобиле?",
    "Чего ты в голом мужике не видела?",
    "Мне кажется, смотря на тебя даже геи думают: «А может я зря?»",
    "Слушай, курица умерла, между прочим. Можно и уважить птаху... Пошла на банальное рагу или скончалась ради фрикасе.",
    "От нафталиновой напыщенности зубы порой сводит.",
    "Половина правды — целая ложь.",
    "Ты их порвёшь. Одного только оставь в живых. Пусть другим расскажет, чтобы боялись.",
    "Потому что я выбрал тебя. И всегда без колебаний буду выбирать тебя.",
    "Это князю волноваться надо, его убивать будут. Нам-то чего?",
    "Домой хочу. Жизни… тебя… отмыться от всего этого… и торт.",
    "Харизма открывает любые двери. — Харизма и скромность. — И чувство юмора.",
    "Я смотрю на небо через твои глаза. — А мне нет дела до неба, когда можно смотреть на тебя.",
    "Топаем отсюда, чокнутая. Поймают же.",
    "Да нормальная девчонка. Правильная. Симпатичная. ",
    "Это ты со мной сексом не занималась",
    "Ты еще и тупой? С первого раза не запомнил?",
    "Найдем твоих обидчиков ноги выдергаем, пусть поползают",
    "Ты вон хоть улыбаешься. А то на девушку из рекламы похоронного бюро похожа была",
    "Тебя вывести, или сам со мной выйдешь?"

]


@router.message(F.text.lower().startswith('макс ауф'))
async def max_auf_quote(message: types.Message):
    quote = random.choice(MAX_AUF_QUOTES)
    await message.answer(f"💬{quote}", parse_mode="HTML")


def get_warning_text(username, warnings, is_ban=False, is_mute=False):
    ban_text = f"@{username or 'нарушитель'}, Скажи честно, неужели ты ожидал другого исхода?"
    warn_texts = [
        f"@{username or 'нарушитель'}, Может попробуем соблюдать правила хотя бы иногда? (предов: {warnings}/4)",
        f"@{username or 'нарушитель'}, Вот оно, начало конца твоей репутации в чате. (предов: {warnings}/4)"
    ]
    mute_texts = [
        "Твоя очередь помолчать, послушаем тишину.",
        "Время подумать над своим поведением."
    ]
    if is_ban:
        return ban_text
    elif is_mute:
        return random.choice(mute_texts)
    else:
        return random.choice(warn_texts)


async def is_auction_finished(lot):
    now = datetime.now()
    end_time = lot.get('end_time')
    start_time = lot.get('start_time')
    if end_time and isinstance(end_time, str):
        from dateutil import parser
        try:
            end_time = parser.parse(end_time)
        except Exception as e:
            logger.warning(f"[is_auction_finished] Ошибка парсинга end_time '{end_time}': {e}")
            end_time = None
    if not end_time and start_time:
        if isinstance(start_time, str):
            from dateutil import parser
            try:
                start_time = parser.parse(start_time)
            except Exception as e:
                logger.warning(f"[is_auction_finished] Ошибка парсинга start_time '{start_time}': {e}")
                return True
        end_time = start_time + timedelta(minutes=30, seconds=59)
    elif end_time:
        end_time = end_time + timedelta(seconds=59)
    else:
        return True
    return now > end_time


async def is_auction_active(lot):
    now = datetime.now()
    if lot.get('status') == 'closed':
        return False
    end_time = lot.get('end_time')
    start_time = lot.get('start_time')
    if end_time and isinstance(end_time, str):
        try:
            end_time = parser.parse(end_time)
        except Exception as e:
            print(f"[AUCTION DEBUG] Не удалось распарсить end_time '{end_time}': {e}")
            end_time = None
    if not end_time and start_time:
        if isinstance(start_time, str):
            try:
                start_time = parser.parse(start_time)
            except Exception as e:
                print(f"[AUCTION DEBUG] Не удалось распарсить start_time '{start_time}': {e}")
                return False
        end_time = start_time + timedelta(minutes=30, seconds=59)
    elif end_time:
        end_time = end_time + timedelta(seconds=59)
    else:
        return False
    print(f"[AUCTION ACTIVE] now={now}, end_time(final)={end_time}, status={lot.get('status')}")
    return now <= end_time


@router.message(F.text.startswith('/bind_lot'))
async def bind_lot_to_discussion(message: types.Message):
    print(f"[FLOOD] Got: '{message.text}' by {message.from_user.id}")
    if not message.reply_to_message:
        await message.answer("Используй только как reply на пересланный пост!")
        return
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            await message.answer("Используй: /bind_lot <auction_id> (в reply на нужный пост)")
            return
        auction_id = int(parts[1])
        replied_id = message.reply_to_message.message_id
        await update_lot_field(auction_id, "discussion_message_id", replied_id)
        await message.answer(f"Привязано: auction_id={auction_id} discussion_message_id={replied_id}")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")


async def _legacy_filter_auction_bids(message: types.Message):
    if USERBOT_BID_MODERATION:
        return
    if BID_VALIDATION_MODE != "bot":
        return
    print(
        f"[AUCTION DEBUG] New msg: {message.text} | chat={message.chat.id} | reply_to={getattr(message.reply_to_message, 'message_id', None)} | user={message.from_user.id} @{message.from_user.username}"
    )
    if not message.reply_to_message:
        print("[AUCTION DEBUG] Не reply — игнор")
        return
    replied_id = message.reply_to_message.message_id
    lot = await get_lot_by_msg_id(replied_id)
    if not lot:
        print(f"[AUCTION DEBUG] Нет лота для replied_id={replied_id}")
        return
    try:
        await add_user(
            user_id=message.from_user.id,
            username=message.from_user.username or "",
            full_name=(message.from_user.first_name or "") + " " + (message.from_user.last_name or "")
        )
    except Exception as e:
        print(f"[ERROR] Ошибка регистрации пользователя: {e}")
    if await is_user_banned(message.from_user.id):
        print(f"[AUCTION DEBUG] User {message.from_user.id} забанен")
        await message.delete()
        await message.answer(
            get_warning_text(message.from_user.username, 4, is_ban=True),
            reply_to_message_id=message.message_id
        )
        return
    end_time = lot.get('end_time')
    if end_time:
        from dateutil import parser
        if isinstance(end_time, str):
            try:
                end_time = parser.parse(end_time)
            except Exception:
                end_time = None
    now = datetime.now()
    if end_time and now >= end_time.replace(second=0, microsecond=0) + timedelta(minutes=1):
        await message.answer(
            "⏰ Аукцион завершён, ставки больше не принимаются.",
            reply_to_message_id=message.message_id
        )
        return
    if not await is_auction_active(lot):
        print(f"[AUCTION DEBUG] Лот {lot['auction_id']} не активен (фильтр выключен)")
        return
    username = message.from_user.username or f"id{message.from_user.id}"
    bid_text = (message.text or "").strip()
    currency = lot.get('currency', 'алмазы')
    bid_amount = None
    cleaned_text = bid_text
    not_valid = False
    reason = ""
    if currency == 'алмазы':
        suffix = cleaned_text[-1:].lower()
        if suffix in ('k', 'к'):
            cleaned_text = cleaned_text[:-1]
        if cleaned_text.isdigit():
            bid_amount = int(cleaned_text)
        if bid_amount is None or bid_amount % 10 != 0:
            not_valid = True
            reason = (
                "Ставка должна быть числом, кратным 10. Можно добавить букву K/К на конце (пример: 100, 140К, 240k). "
                "Пример валидной ставки: 150, 320К, 8000k"
            )
    elif currency == 'чашки':
        if not bid_text.isdigit() or int(bid_text) % 2 != 0:
            not_valid = True
            reason = (
                "Ставка должна быть чётным числом без букв (пример: 2, 14, 100). "
                "Пример валидной ставки: 4, 12, 444"
            )
        else:
            bid_amount = int(bid_text)
    elif currency == 'сокровища':
        if not bid_text.isdigit() or int(bid_text) % 10 != 0:
            not_valid = True
            reason = "Ставка в сокровищах должна быть положительным числом, кратным 10."
        else:
            bid_amount = int(bid_text)
    else:
        not_valid = True
        reason = "Валюта аукциона не поддерживается."
    if not_valid:
        await message.answer(
            f"⏳ @{username}\nСтавка не засчитана: {reason}",
            reply_to_message_id=message.message_id
        )
        try:
            until_date = datetime.now() + timedelta(minutes=1)
            await message.bot.restrict_chat_member(
                message.chat.id,
                message.from_user.id,
                permissions=types.ChatPermissions(can_send_messages=False),
                until_date=until_date
            )
            print(f"[AUCTION DEBUG] Мут выдан user_id={message.from_user.id} до {until_date}")
        except Exception as e:
            print(f"[AUCTION DEBUG] Не удалось замутить пользователя: {e}")
        await asyncio.sleep(2)
        try:
            await message.delete()
        except Exception as e:
            print(f"[AUCTION DEBUG] Не удалось удалить невалидную ставку: {e}")
        return
    kind = AuctionKind.from_raw(lot.get("auction_kind"))
    if not kind.is_automatic_bidding:
        await message.answer(
            "ℹ️ В этом типе аукциона числовые ставки автоматически не принимаются.",
            reply_to_message_id=message.message_id,
        )
        return

    current_best = await get_best_bid_for_auction(lot['auction_id'])
    step = Currency.from_raw(currency).bid_step
    if kind.lowest_bid_wins:
        if current_best is not None and bid_amount > int(current_best) - step:
            await message.answer(
                f"⏳ В обратном аукционе ставка должна быть НИЖЕ текущей минимум на {step}. "
                f"Текущая лучшая: {current_best}; допустимо не больше {max(1, int(current_best) - step)}.",
                reply_to_message_id=message.message_id,
            )
            return
    elif current_best is not None and bid_amount <= current_best:
        await message.answer(
            f"⏳ Ваша ставка должна быть БОЛЬШЕ текущей максимальной: {current_best}",
            reply_to_message_id=message.message_id
        )
        return
    try:
        await add_bid(
            auction_id=lot['auction_id'],
            bidder_id=message.from_user.id,
            amount=bid_amount,
            discussion_message_id=message.message_id
        )
        print(
            f"[AUCTION DEBUG] Ставка {bid_amount} добавлена для auction_id={lot['auction_id']} от user={message.from_user.id}"
        )
    except Exception as e:
        print(f"[ERROR] Ошибка добавления ставки: {e}")
        await message.answer(
            "❌ Не удалось добавить вашу ставку. Напишите /start боту в личку и попробуйте снова.",
            reply_to_message_id=message.message_id
        )
    try:
        owners = lot.get("owners", [])
        if not isinstance(owners, list):
            owners = [owners]
        owners = [o for o in owners if o]
        owners_str = ', '.join(
            f'@{o}' if isinstance(o, str) and not o.startswith('@') else str(o)
            for o in owners
        )
        await message.bot.send_message(
            LOG_CHAT_ID,
            f"💬 Новая ставка:\n"
            f"Аукцион: {lot['auction_id']}\n"
            f"Пользователь: @{username} ({message.from_user.id})\n"
            f"Сумма: {bid_amount} {currency}\n"
            f"Владельцы: {owners_str}\n"
            f"msg_id: {message.message_id}",
        )
    except Exception as e:
        logger.warning(f"[LOG_CHAT] Не удалось отправить лог ставки: {e}")


async def _ranked_bid_rows(auction_id: int, *, limit: int | None = None):
    return await (await AuctionWinnerService.create()).ranked_bids(auction_id, limit=limit)


async def _best_bid_row(auction_id: int):
    rows = await _ranked_bid_rows(auction_id, limit=1)
    return rows[0] if rows else None


async def get_two_winners_for_multi_owner_auction(auction_id: int):
    bids = await _ranked_bid_rows(auction_id)
    if not bids:
        return []
    winners = []
    first_bidder = None
    for bid in bids:
        if not first_bidder:
            winners.append(bid)
            first_bidder = bid['bidder_id']
        elif bid['bidder_id'] != first_bidder:
            winners.append(bid)
            break
    return winners


async def get_best_bid_for_auction(auction_id: int) -> int | None:
    row = await _best_bid_row(auction_id)
    return int(row['amount']) if row and row.get('amount') is not None else None


async def _legacy_get_max_bid_for_auction(auction_id: int) -> int | None:
    """Compatibility alias. For reverse auctions this returns the minimum/best bid."""
    return await get_best_bid_for_auction(auction_id)


@router.message(F.text.lower().startswith('макс текущий аукцион'))
async def handle_current_auction(message: types.Message):
    lot = await get_current_auction()
    if not lot:
        await message.answer("Сейчас нет активных аукционов.")
        return
    await message.answer(
        f"Текущий аукцион:\n"
        f"Карта: {lot['card_name']}\n"
        f"Начало: {lot['start_time']}\n"
        f"Окончание: {lot['end_time']}\n"
        f"Статус: {lot['status']}"
    )


async def _legacy_edited_bid_handler(message: types.Message):
    if USERBOT_BID_MODERATION:
        return
    if BID_VALIDATION_MODE != "bot":
        return

    if message.from_user and message.from_user.is_bot:
        return
    if not message.reply_to_message:
        return
    replied_id = message.reply_to_message.message_id
    lot = await get_lot_by_msg_id(replied_id)
    if not lot:
        return
    if not await is_auction_active(lot):
        return
    username = message.from_user.username or f"id{message.from_user.id}"
    msg1 = await message.answer(
        "⛔ Редактирование ставок запрещено! Ставка удалена.",
        reply_to_message_id=message.message_id
    )
    await add_warning(message.from_user.id, "edit_bid")
    warnings = await get_warnings_count(message.from_user.id)
    import random
    warn = random.choice(WARN_TEXTS)
    await message.answer(
        warn.format(username=username, warnings=warnings),
        reply_to_message_id=msg1.message_id
    )
    await asyncio.sleep(1)
    try:
        await message.delete()
    except Exception as e:
        logger.warning(f"Ошибка: {e}")


def get_winner(bids):
    if not bids:
        return None
    sorted_bids = sorted(bids, key=lambda x: (-x['amount'], x['placed_at']))
    return sorted_bids[0]


WIN_REVIEW_THRESHOLD_DIAMONDS = 1000
WIN_REVIEW_THRESHOLD_CUPS = 100

# Кнопки для админ-логов (помимо CB_WIN_SEND/CB_WIN_SKIP)
CB_WIN_EDIT_AMT = "win:edit_amt"
CB_WIN_EDIT_USER = "win:edit_user"

# Черновики правок победителя/ставки по лоту (живут в памяти процесса)
WIN_DRAFTS: dict[int, dict] = {}  # {auction_id: {"amount": int|None, "winner_id": int|None}}
PENDING_EDIT: dict[int, dict] = {}


def _winner_threshold(currency: str | None) -> int:
    cur = (currency or "").lower()
    if cur in {"алмазы", "diamond", "diamonds"}:
        return WIN_REVIEW_THRESHOLD_DIAMONDS
    if cur in {"чашки", "cups"}:
        return WIN_REVIEW_THRESHOLD_CUPS
    return 0


async def _winner_preview_text(auction_id: int, amount: int, winner_id: int) -> str:
    a = await (await AuctionWinnerService.create()).auction(auction_id) or {}
    cur_emoji = _emoji_by_currency(a.get("currency"))
    link = _build_channel_link(a.get("message_id")) or "(ссылка недоступна)"
    lot_line = (a.get("hero_name") or "-") + (f" — {a.get('card_name')}" if a.get("card_name") else "")

    w = await get_user(winner_id) or {}
    wname = _mention(winner_id, w.get("username"))

    owners = await _get_owners(auction_id)
    owners_mentions = ", ".join(_mention(o["user_id"], o.get("username")) for o in owners) or "—"
    owner_user_ids = [int(o["user_id"]) for o in owners] if owners else []

    w_verified = await _is_user_uid_verified(int(winner_id))
    w_verif_line = "✅" if w_verified else "❌"

    s_total, s_verified, sellers_all_verified = await _users_uid_verification_counts(owner_user_ids)
    if s_total:
        s_verif_line = "✅" if sellers_all_verified else "❌"
    else:
        s_verif_line = "—"

    return (
        "Привет!\n\n"
        "Поздравляю!!!! 🥳\n\n"
        f"Аукцион {link} завершён!\n"
        f"Лот: {lot_line}\n\n"
        f"Стоимость карты: {amount} {cur_emoji}\n"
        f"Победитель: {wname} ({w_verif_line})\n"
        f"Продавец вериф.: {s_verif_line}\n"
        f"Владелец карты: {owners_mentions}"
    )

async def _autobid_win_note(
        *,
        auction_id: int,
        winner_bid: dict | object,
        wid: int,
        max_amt: int,
        wname: str,
) -> str:
    """
    Если победная ставка была сделана через автоставку (платно),
    вернём строку для добавления в итог.
    """
    # 1) пытаемся взять discussion_message_id прямо из winner_bid
    win_msg_id = None
    try:
        if isinstance(winner_bid, dict):
            win_msg_id = winner_bid.get("discussion_message_id")
        else:
            win_msg_id = getattr(winner_bid, "discussion_message_id", None)
    except Exception:
        win_msg_id = None

    # 2) если его нет — найдём по БД (последняя ставка победителя на эту сумму)
    if not win_msg_id:
        win_msg_id = await (await AuctionWinnerService.create()).bid_message_id(
            auction_id, wid, max_amt
        )

    if not win_msg_id:
        return ""

    mapped = await get_autobid_action_by_msg_id(int(win_msg_id))
    if not mapped:
        return ""

    return f"\n🤖 <i>Платная автоставка для {wname}</i>"


async def announce_winner(telegram_bot, auction, bids, send_admin_log=None):
    """
    Готовит сводку по победителю, постит финальный комментарий под лотом
    и шлёт в админ-лог карточку с дедлайном, превью и кнопками:
      — Отправить уведомления
      — Исправить ставку
      — Исправить победителя
      — Не отправлять
    """
    auction_id = int(auction["auction_id"])
    currency = (auction.get("currency") or "").lower()
    cur_emoji = _emoji_by_currency(currency)

    kind = AuctionKind.from_raw(auction.get("auction_kind"))
    lowest_wins = kind is AuctionKind.REVERSE

    winner_bid = None
    max_amt = None
    for b in (bids or []):
        try:
            amt = int(b["amount"] if isinstance(b, dict) else b.amount)
        except Exception:
            continue
        if max_amt is None or (amt < max_amt if lowest_wins else amt > max_amt):
            max_amt = amt
            winner_bid = b

    reply_to_id = auction.get('discussion_message_id') or auction.get('message_id')
    if not winner_bid:
        txt = "⏰ <b>Аукцион завершён!</b>\n❌ <i>Победителей нет, ставок не было.</i>"
        try:
            await telegram_bot.send_message(DISCUSSION_CHAT_ID, txt, parse_mode="HTML", reply_to_message_id=reply_to_id)
        except Exception:
            await telegram_bot.send_message(DISCUSSION_CHAT_ID, txt, parse_mode="HTML")
        for chat_id in ADMIN_LOG_CHATS:
            try:
                await telegram_bot.send_message(chat_id, f"🏁 Лот {auction_id}: ставок не было.", parse_mode="HTML")
            except Exception:
                pass
        return

    win_msg_id = None
    try:
        if isinstance(winner_bid, dict):
            win_msg_id = winner_bid.get("discussion_message_id")
        else:
            win_msg_id = getattr(winner_bid, "discussion_message_id", None)
    except Exception:
        win_msg_id = None

    winner_bidder_id = None
    if not win_msg_id:
        top = await (await AuctionWinnerService.create()).top_bid(
            auction_id, lowest_wins=lowest_wins
        )
        if top:
            winner_bidder_id = int(top["bidder_id"])
            try:
                max_amt = int(top["amount"])
            except Exception:
                pass
            win_msg_id = top.get("discussion_message_id")
    else:
        winner_bidder_id = int(
            winner_bid["bidder_id"] if isinstance(winner_bid, dict) else winner_bid.bidder_id
        )

    mapped = None
    try:
        if win_msg_id:
            mapped = await get_autobid_action_by_msg_id(int(win_msg_id))
    except Exception:
        mapped = None

    if mapped and mapped.get("target_user_id"):
        wid = int(mapped["target_user_id"])
    else:
        wid = int(winner_bidder_id)

    wuser = await get_user(wid) or {}
    wname = _mention(wid, wuser.get("username"))

    owners = await _get_owners(auction_id)
    owners_mentions = ", ".join(_mention(o["user_id"], o.get("username")) for o in owners) or "—"
    owner_user_ids = [int(o["user_id"]) for o in owners] if owners else []

    # верификация
    winner_verified = await _is_user_uid_verified(wid)
    winner_verif_tag = "✅" if winner_verified else "❌"

    s_total, s_verified, sellers_all_verified = await _users_uid_verification_counts(owner_user_ids)
    if s_total:
        seller_verif_line = (
            "🔒 Лот от верифицированного продавца ✅"
            if sellers_all_verified
            else "🔒 Лот от НЕВЕРИФИЦИРОВАННОГО продавца ❌"
        )
    else:
        seller_verif_line = "🔒 Верификация продавца: —"

    deck_tag = ""
    try:
        row = await (await AuctionWinnerService.create()).deck_for_auction(auction_id)
        if row:
            if row.get("deck_id"):
                deck_tag = f" Колода №{row['deck_id']}"
            elif row.get("deck_name"):
                deck_tag = f" Колода «{row['deck_name']}»"
    except Exception:
        pass

    try:
        dmsg_id = await _get_discussion_msg_id(auction_id)
        auto_note = await _autobid_win_note(
            auction_id=auction_id,
            winner_bid=winner_bid,
            wid=wid,
            max_amt=max_amt,
            wname=wname,
        )

        end_text = (
            "🏁 <b>Аукцион завершён</b>\n"
            f"Победитель: {wname} ({winner_verif_tag})\n"
            f"Ставка: <b>{max_amt} {cur_emoji}</b>"
            f"{auto_note}\n"
            f"{seller_verif_line}"
        )

        if dmsg_id:
            await telegram_bot.send_message(DISCUSSION_CHAT_ID, end_text, parse_mode="HTML", reply_to_message_id=dmsg_id)
        else:
            await telegram_bot.send_message(DISCUSSION_CHAT_ID, end_text, parse_mode="HTML")
    except Exception:
        pass

    link = _build_channel_link(auction.get("message_id"))
    now_msk = _msk_now()
    deadline_msk = now_msk + timedelta(minutes=int(WINNER_NOTIFY_DEADLINE_MINUTES or 10))
    rel_minutes = int((deadline_msk - now_msk).total_seconds() // 60)

    lot_title = f"{(auction.get('hero_name') or '-')}" + (f" — {auction.get('card_name')}" if auction.get("card_name") else "")
    preview_dm = await _winner_preview_text(auction_id, max_amt, wid)

    threshold = 0 if lowest_wins else _winner_threshold(currency)
    need_review = threshold and int(max_amt or 0) >= threshold
    review_line = f"\n⚠️ <b>Сумма ≥ порога проверки ({threshold} {cur_emoji}). Рекомендуется сверка ставок.</b>\n" if need_review else ""

    admin_text = (
        "🏁 <b>Аукцион завершён</b>\n\n"
        f"Лот: <b>{lot_title}</b>{deck_tag}\n"
        f"Стоимость: <b>{max_amt} {cur_emoji}</b>\n"
        f"Победитель: {wname} ({winner_verif_tag})\n"
        f"Владелец(ы): {owners_mentions}\n"
        f"{seller_verif_line}\n"
        f"{'Ссылка: ' + link if link else ''}\n\n"
        "🧾 <b>Отправка уведомлений</b>\n"
        f"• Рекомендуем отправить <b>до {_fmt_msk(deadline_msk)} МСК</b> "
        f"(через ~{rel_minutes} мин)."
        f"{review_line}\n"
        "<i>Превью ЛС (как получит победитель/владельцы):</i>\n"
        "<code>" + preview_dm.replace("<", "‹").replace(">", "›") + "</code>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Отправить уведомления", callback_data=f"{CB_WIN_SEND}:{auction_id}:{wid}")],
        [
            InlineKeyboardButton(text="✎ Исправить ставку", callback_data=f"{CB_WIN_EDIT_AMT}:{auction_id}:{wid}"),
            InlineKeyboardButton(text="👤 Исправить победителя", callback_data=f"{CB_WIN_EDIT_USER}:{auction_id}:{wid}")
        ],
        [InlineKeyboardButton(text="⛔ Не отправлять", callback_data=f"{CB_WIN_SKIP}:{auction_id}:{wid}")]
    ])

    for chat_id in ADMIN_LOG_CHATS:
        try:
            await telegram_bot.send_message(chat_id, admin_text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        except Exception:
            pass

CB_WIN_SEND_OWNER = "win:send_owner"
CB_WIN_SEND_WINNER = "win:send_winner"
CB_WIN_REFRESH = "win:refresh"
CB_WIN_MANUAL = "win:manual"
CB_WIN_SEND_BOTH = "win:send_both"
CB_WIN_EDIT_MANUAL_WINNER = "win:edit_manual_winner"
CB_WIN_EDIT_MANUAL_OWNER = "win:edit_manual_owner"
CB_WIN_EDIT_MANUAL_AMOUNT = "win:edit_manual_amount"
CB_WIN_CLEAR_MANUAL = "win:clear_manual"

PENDING_WIN_FIELD_EDIT: dict[int, dict] = {}

_WIN_TABLES_READY = False

PENDING_WIN_MANUAL: dict[int, dict] = {}


@dataclass
class AuctionWinContext:
    auction_id: int
    hero_name: str
    card_name: str
    currency: str
    channel_message_id: Optional[int]

    owner_user_id: Optional[int]
    owner_mention: str

    winner_user_id: Optional[int]
    winner_mention: str

    final_price: Optional[int]
    sent_total: int
    sent_owner: int
    sent_winner: int


def _admin_tag(user: types.User) -> str:
    return f"@{user.username}" if user.username else f"id{user.id}"


async def _ensure_print_win_tables() -> None:
    """Таблицы для (1) счётчика рассылок по лоту и (2) ручного результата."""
    global _WIN_TABLES_READY
    if _WIN_TABLES_READY:
        return

    await (await AuctionWinnerService.create()).ensure_print_win_schema()

    _WIN_TABLES_READY = True


async def _win_mailing_counts(auction_id: int) -> tuple[int, int, int]:
    await _ensure_print_win_tables()
    return await (await AuctionWinnerService.create()).mailing_counts(auction_id)


async def _add_win_mailing(auction_id: int, target: str, admin: types.User) -> None:
    await _ensure_print_win_tables()
    await (await AuctionWinnerService.create()).add_mailing(auction_id, target, admin)


async def _get_manual_result(auction_id: int) -> dict | None:
    await _ensure_print_win_tables()
    return await (await AuctionWinnerService.create()).manual_result(auction_id)


async def _upsert_manual_result(
        auction_id: int,
        *,
        winner_user_id: int | None,
        winner_username: str | None,
        owner_user_id: int | None,
        owner_username: str | None,
        amount: int | None,
        updated_by: int,
        moderator_comment: str | None = None,
) -> None:
    await _ensure_print_win_tables()
    await (await AuctionWinnerService.create()).upsert_manual_result(
        auction_id,
        winner_user_id=winner_user_id,
        winner_username=winner_username,
        owner_user_id=owner_user_id,
        owner_username=owner_username,
        amount=amount,
        updated_by=updated_by,
        moderator_comment=moderator_comment,
    )


async def _resolve_user_ref(raw: str) -> tuple[int | None, str | None]:
    """
    Возвращает (user_id, username_without_at) по:
      - @username
      - числовому id
    Если @username не найден в users — вернём (None, username) чтобы хотя бы текстом показать.
    """
    s = (raw or "").strip()
    if not s:
        return None, None
    if s.startswith("@"):
        uname = s.lstrip("@")
        u = await get_user_by_username(uname)
        if u and u.get("user_id"):
            return int(u["user_id"]), uname
        return None, uname
    if s.isdigit():
        return int(s), None
    return None, None


def _mention_soft(user_id: int | None, username: str | None) -> str:
    """
    - если есть username -> @username
    - если только id -> кликабельный id + короткая tg-ссылка
    """
    if username:
        return f"@{username}"
    if user_id:
        uid = int(user_id)
        return (
            f'<a href="tg://user?id={uid}">id{uid}</a>'
            f' (<a href="tg://openmessage?user_id={uid}">tg</a>)'
        )
    return "—"

async def _build_verification_warning_block(
        *,
        winner_user_id: int | None,
        owner_user_ids: list[int] | None,
) -> str:
    owner_ids = [int(x) for x in (owner_user_ids or []) if x]

    seller_unverified = False
    winner_unverified = False

    if owner_ids:
        s_total, s_verified, sellers_all_verified = await _users_uid_verification_counts(owner_ids)
        seller_unverified = bool(s_total and not sellers_all_verified)

    if winner_user_id:
        winner_unverified = not await _is_user_uid_verified(int(winner_user_id))

    if not (seller_unverified or winner_unverified):
        return ""

    need = []
    if seller_unverified:
        need.append("продавцу")
    if winner_unverified:
        need.append("победителю")

    need_text = " и ".join(need) if need else "участникам сделки"

    return (
        "\n⚠️ <b>Для вашей безопасности рекомендуем "
        f"{need_text} пройти верификацию в Максе до завершения сделки.</b>\n"
        "Проверить человека можно так:\n"
        "• <code>/who @username</code>\n"
        "• <code>/who 123456789</code>\n"
        "• или reply на сообщение человека с командой <code>/who</code>\n"
    )
async def _compose_user_win_text(
        *,
        auction_id: int,
        link: str,
        lot_line: str,
        amount: int | None,
        cur_emoji: str,
        winner_user_id: int | None,
        winner_username: str | None,
        owner_mentions: str,
        moderator_tag: str,
        owner_user_ids: list[int] | None = None,
        moderator_comment: str | None = None,
) -> str:
    has_winner = bool(winner_user_id or winner_username)
    wname = _mention_soft(winner_user_id, winner_username)

    extra_links = ""
    if winner_user_id and not winner_username:
        extra_links = f"Профиль: {_user_links_html(int(winner_user_id), None)}\n"

    # верификация продавца(ов)
    s_total, s_verified, sellers_all_verified = await _users_uid_verification_counts(owner_user_ids or [])
    if s_total:
        seller_verif_line = (
            "🔒 Лот от верифицированного продавца ✅\n"
            if sellers_all_verified
            else "🔒 Лот от НЕВЕРИФИЦИРОВАННОГО продавца ❌\n"
        )
    else:
        seller_verif_line = "🔒 Верификация продавца: —\n"

    # верификация победителя
    if winner_user_id:
        w_verified = await _is_user_uid_verified(int(winner_user_id))
        winner_verif_line = (
            "🛡️ Победитель верифицирован: ✅\n"
            if w_verified
            else "🛡️ Победитель НЕ верифицирован: ❌\n"
        )
    else:
        winner_verif_line = "🛡️ Верификация победителя: —\n"

    verification_warning_block = await _build_verification_warning_block(
        winner_user_id=winner_user_id,
        owner_user_ids=owner_user_ids or [],
    )

    # комментарий модератора
    c = (moderator_comment or "").strip()
    comment_block = ""
    if c:
        comment_block = "\n💬 <b>Комментарий модератора:</b>\n<i>" + html.escape(c) + "</i>\n"

    if not has_winner:
        return (
            "Привет! 👋\n\n"
            f"🏁 Аукцион завершён: {link}\n\n"
            f"🃏 Лот: {lot_line}\n\n"
            f"{seller_verif_line}\n"
            "😿 Ставок не было, так что карта осталась у тебя. 🫶\n"
            "Это не провал. Просто сегодня чат не поймал волну (или цена/валюта/время были не в тему).\n\n"
            f"👑 Владелец карты: {owner_mentions}\n\n"
            f"🛡️ Модератор аукциона: {moderator_tag}\n"
            f"❓ Вопросы об аукционе: {AUCTION_SUPPORT_CONTACT} {AUCTION_SUPPORT_CONTACT_2}\n"
            f"🧯 Проблемы по ауку: {AUCTION_PROBLEMS_CONTACT} {AUCTION_SUPPORT_CONTACT_2}"
            f"{comment_block}\n"
            "🔁 Если хочешь, попробуй ещё раз: иногда решает другое время, другая валюта или чуть мягче старт.\n"
            "⚡️ Или закинь лот в другой формат: быстрый, свободный или чёрный аукцион.\n"
            "🏪 Ну и биржа тоже вариант, если хочется закрыть вопрос побыстрее."
        )

    amt = amount if amount is not None else 0
    return (
        "Поздравляю!!!! 🥳\n\n"
        f"Аукцион {link} завершён!\n"
        f"Лот: {lot_line}\n\n"
        f"Стоимость карты: {amt} {cur_emoji}\n"
        f"{winner_verif_line}"
        f"{seller_verif_line}"
        f"Победитель: {wname}\n"
        f"{extra_links}"
        f"Владелец карты: {owner_mentions}\n"
        f"{verification_warning_block}\n"
        f"Модератор аукциона: {moderator_tag}\n"
        f"Вопросы об аукционе сюда: {AUCTION_SUPPORT_CONTACT} {AUCTION_SUPPORT_CONTACT_2}\n"
        f"По проблемам по ауку сюда: {AUCTION_PROBLEMS_CONTACT} {AUCTION_SUPPORT_CONTACT_2}"
        f"{comment_block}\n"
        "Если хочешь, можешь сказать спасибо модератору ниже ❤️\n"
    )


async def _thanks_kb(auction_id: int, moderator_tag: str) -> InlineKeyboardMarkup:
    total, users = await get_admin_thanks_totals(moderator_tag)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"🙏 Спасибо: {total} | 👥 {users}",
            callback_data=f"{CB_WIN_THANKS}:{auction_id}:{moderator_tag}",
        )
    ]])


def _print_win_menu_kb(auction_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Отправить обоим", callback_data=f"{CB_WIN_SEND_BOTH}:{auction_id}")],
        [InlineKeyboardButton(text="👑 Отправить только владельцу", callback_data=f"{CB_WIN_SEND_OWNER}:{auction_id}")],
        [InlineKeyboardButton(text="🏆 Отправить только победителю", callback_data=f"{CB_WIN_SEND_WINNER}:{auction_id}")],

        [InlineKeyboardButton(text="💬 Комментарий от модератора", callback_data=f"win:edit_manual_comment:{auction_id}")],

        [InlineKeyboardButton(text="🏆 Сменить победителя", callback_data=f"{CB_WIN_EDIT_MANUAL_WINNER}:{auction_id}")],
        [InlineKeyboardButton(text="👑 Сменить владельца", callback_data=f"{CB_WIN_EDIT_MANUAL_OWNER}:{auction_id}")],
        [InlineKeyboardButton(text="💰 Сменить цену", callback_data=f"{CB_WIN_EDIT_MANUAL_AMOUNT}:{auction_id}")],
        [InlineKeyboardButton(text="🧹 Сбросить ручной итог", callback_data=f"{CB_WIN_CLEAR_MANUAL}:{auction_id}")],

        [InlineKeyboardButton(text="✍️ Мастер ручного итога (побед/влад/цена)", callback_data=f"{CB_WIN_MANUAL}:{auction_id}")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"{CB_WIN_REFRESH}:{auction_id}")],
    ])


async def _build_print_win_context(auction_id: int) -> dict:
    a = await (await AuctionWinnerService.create()).auction(auction_id)
    if not a:
        return {"ok": False, "err": "Лот не найден."}

    a = dict(a)
    photo = a.get("image_id")
    cur_emoji = _emoji_by_currency(a.get("currency"))
    link = _build_channel_link(a.get("message_id")) or "(ссылка недоступна)"
    lot_line = (a.get("hero_name") or "-") + (f" — {a.get('card_name')}" if a.get("card_name") else "")

    manual = await _get_manual_result(auction_id)
    moderator_comment = (manual or {}).get("moderator_comment")

    b = await _best_bid_row(auction_id)

    winner_user_id = int(b["bidder_id"]) if b and b.get("bidder_id") else None
    amount = int(b["amount"]) if b and b.get("amount") is not None else None
    winner_username = None

    owners = await _get_owners(auction_id)
    owner_mentions = ", ".join(_mention(o["user_id"], o.get("username")) for o in owners) if owners else "—"
    owner_user_ids = [int(o["user_id"]) for o in owners] if owners else []

    if manual:
        if manual.get("winner_user_id") is not None or manual.get("winner_username") is not None:
            winner_user_id = int(manual["winner_user_id"]) if manual.get("winner_user_id") else None
            winner_username = manual.get("winner_username")
        if manual.get("amount") is not None:
            amount = int(manual["amount"])
        if manual.get("owner_user_id") is not None or manual.get("owner_username") is not None:
            ouid = int(manual["owner_user_id"]) if manual.get("owner_user_id") else None
            oun = manual.get("owner_username")
            owner_mentions = _mention_soft(ouid, oun)
            owner_user_ids = [ouid] if ouid else []

    # попробуем дорезолвить id по username (если руками вводили только @username)
    if winner_username and not winner_user_id:
        u = await get_user_by_username(winner_username) or {}
        if u.get("user_id"):
            winner_user_id = int(u["user_id"])

    if not owner_user_ids:
        # если ручной владелец задан только username, пробуем найти id
        if manual and manual.get("owner_username") and not manual.get("owner_user_id"):
            uo = await get_user_by_username(str(manual["owner_username"])) or {}
            if uo.get("user_id"):
                owner_user_ids = [int(uo["user_id"])]

    if winner_user_id and not winner_username:
        u = await get_user(winner_user_id) or {}
        if u.get("username"):
            winner_username = u["username"]

    total, owner_cnt, winner_cnt = await _win_mailing_counts(auction_id)

    return {
        "ok": True,
        "auction_id": auction_id,
        "link": link,
        "lot_line": lot_line,
        "cur_emoji": cur_emoji,
        "amount": amount,
        "winner_user_id": winner_user_id,
        "winner_username": winner_username,
        "owner_mentions": owner_mentions,
        "owner_user_ids": owner_user_ids,
        "moderator_comment": moderator_comment,
        "sent_total": total,
        "sent_owner": owner_cnt,
        "sent_winner": winner_cnt,
        "has_manual": bool(manual),
        "photo": photo,
    }
async def _post_taken_comment_and_pin_after_print_win(bot: Bot, *, auction_id: int) -> tuple[bool, str | None]:
    ctx = await _build_print_win_context(auction_id)
    if not ctx.get("ok"):
        return False, str(ctx.get("err") or "ctx_error")

    # не плодим дубли: закрепляем только после первой успешной рассылки /print_win
    if int(ctx.get("sent_total") or 0) > 1:
        return False, "already_posted"

    winner_user_id = ctx.get("winner_user_id")
    winner_username = ctx.get("winner_username")
    amount = ctx.get("amount")

    if not winner_user_id and not winner_username:
        return False, "no_winner"

    dmsg_id = await _get_discussion_msg_id(int(auction_id))
    if not dmsg_id:
        return False, "no_discussion_message_id"

    winner_line = _mention_soft(winner_user_id, winner_username)

    verification_hint = await _build_verification_warning_block(
        winner_user_id=winner_user_id,
        owner_user_ids=list(ctx.get("owner_user_ids") or []),
    )

    text = (
        "📌 <b>Карту забрали</b>\n"
        f"🏆 Победитель: {winner_line}\n"
        f"💰 Ставка: <b>{int(amount or 0)} {ctx['cur_emoji']}</b>\n"
        "🔎 Проверить участника в Максе: <code>/who @username</code> или <code>/who 123456789</code>"
        f"{verification_hint}"
    )

    try:
        msg = await bot.send_message(
            DISCUSSION_CHAT_ID,
            text,
            parse_mode="HTML",
            reply_to_message_id=int(dmsg_id),
            disable_web_page_preview=True,
        )
    except Exception as e:
        return False, f"send_failed: {e}"

    try:
        await bot.pin_chat_message(
            chat_id=DISCUSSION_CHAT_ID,
            message_id=msg.message_id,
            disable_notification=True,
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        # сообщение отправили, но закрепить не смогли
        return True, "sent_but_not_pinned"

    return True, None
async def _compose_print_win_menu_text(ctx: dict, moderator_tag: str) -> str:
    warns = []
    if ctx.get("winner_user_id") is None and not ctx.get("winner_username"):
        warns.append("⚠️ Победитель не определён (нет ставок и нет ручного результата).")
    if ctx.get("amount") is None:
        warns.append("⚠️ Цена не определена (нет ставок и нет ручного результата).")
    if (ctx.get("owner_mentions") or "—") == "—":
        warns.append("⚠️ Владелец не определён (нет auction_owners и нет ручного результата).")

    warn_block = ("\n" + "\n".join(warns) + "\n") if warns else ""

    preview = await _compose_user_win_text(
        auction_id=int(ctx["auction_id"]),
        link=str(ctx["link"]),
        lot_line=str(ctx["lot_line"]),
        amount=ctx.get("amount"),
        cur_emoji=str(ctx["cur_emoji"]),
        winner_user_id=ctx.get("winner_user_id"),
        winner_username=ctx.get("winner_username"),
        owner_mentions=str(ctx.get("owner_mentions") or "—"),
        moderator_tag=moderator_tag,
        owner_user_ids=list(ctx.get("owner_user_ids") or []),
        moderator_comment=ctx.get("moderator_comment"),
    )

    manual_line = "📝 Есть ручной результат.\n" if ctx.get("has_manual") else ""

    text = (
        f"📨 <b>/print_win</b> для лота <b>{ctx['auction_id']}</b>\n"
        f"Отправлено рассылок: <b>{ctx['sent_total']}</b> | 👑 владельцу: <b>{ctx['sent_owner']}</b> | 🏆 победителю: <b>{ctx['sent_winner']}</b>\n"
        f"{manual_line}"
        f"{warn_block}\n"
        f"<b>Превью сообщения (ЛС):</b>\n\n"
        f"{preview}"
    )

    if len(text) > TG_MAX:
        text = text[:TG_MAX - 50] + "\n\n…<i>превью обрезано из-за лимита Telegram</i>"
    return text

async def _send_print_win_menu(message: types.Message, auction_id: int) -> None:
    ctx = await _build_print_win_context(auction_id)
    if not ctx.get("ok"):
        await message.answer(f"❌ {ctx.get('err')}")
        return

    moderator_tag = _admin_tag(message.from_user)
    text = await _compose_print_win_menu_text(ctx, moderator_tag)
    await message.answer(text, parse_mode="HTML", reply_markup=_print_win_menu_kb(auction_id),
                         disable_web_page_preview=True)


async def _edit_print_win_menu(call: types.CallbackQuery, auction_id: int) -> None:
    ctx = await _build_print_win_context(auction_id)
    if not ctx.get("ok"):
        try:
            await call.message.edit_text(f"❌ {ctx.get('err')}", parse_mode="HTML")
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return
            raise
        return

    moderator_tag = _admin_tag(call.from_user)
    text = await _compose_print_win_menu_text(ctx, moderator_tag)

    try:
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=_print_win_menu_kb(auction_id),
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise


async def _refresh_print_win_menu_by_ids(
        bot: Bot,
        *,
        chat_id: int,
        message_id: int,
        auction_id: int,
        admin_user: types.User,
) -> None:
    ctx = await _build_print_win_context(auction_id)
    if not ctx.get("ok"):
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"❌ {ctx.get('err')}",
            parse_mode="HTML",
        )
        return

    moderator_tag = _admin_tag(admin_user)
    text = await _compose_print_win_menu_text(ctx, moderator_tag)

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=_print_win_menu_kb(auction_id),
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise


def _parse_amount_text(raw: str) -> int | None:
    txt = (raw or "").strip().replace(" ", "").lower().replace("к", "k")
    if not txt:
        return None
    if txt.endswith("k"):
        base = txt[:-1]
        if not base.isdigit():
            return None
        return int(base) * 1000
    if not txt.isdigit():
        return None
    return int(txt)


@router.callback_query(F.data.startswith(f"{CB_WIN_EDIT_MANUAL_WINNER}:"))
async def cb_print_win_edit_manual_winner(call: types.CallbackQuery):
    await call.answer()
    auction_id = _cb_last_int(call.data)

    PENDING_WIN_FIELD_EDIT[call.from_user.id] = {
        "auction_id": auction_id,
        "field": "winner",
        "menu_chat_id": call.message.chat.id,
        "menu_message_id": call.message.message_id,
    }

    await call.message.answer(
        "🏆 <b>Сменить победителя</b>\n\n"
        "Пришли <code>@username</code> или числовой <code>id</code>.\n"
        "Если победителя нет (ставок не было) — пришли <code>-</code>.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith(f"{CB_WIN_EDIT_MANUAL_OWNER}:"))
async def cb_print_win_edit_manual_owner(call: types.CallbackQuery):
    await call.answer()
    auction_id = _cb_last_int(call.data)

    PENDING_WIN_FIELD_EDIT[call.from_user.id] = {
        "auction_id": auction_id,
        "field": "owner",
        "menu_chat_id": call.message.chat.id,
        "menu_message_id": call.message.message_id,
    }

    await call.message.answer(
        "👑 <b>Сменить владельца</b>\n\n"
        "Пришли <code>@username</code> или числовой <code>id</code>.\n"
        "Чтобы сбросить ручного владельца и брать из <code>auction_owners</code> — пришли <code>-</code>.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith(f"{CB_WIN_EDIT_MANUAL_AMOUNT}:"))
async def cb_print_win_edit_manual_amount(call: types.CallbackQuery):
    await call.answer()
    auction_id = _cb_last_int(call.data)

    PENDING_WIN_FIELD_EDIT[call.from_user.id] = {
        "auction_id": auction_id,
        "field": "amount",
        "menu_chat_id": call.message.chat.id,
        "menu_message_id": call.message.message_id,
    }

    await call.message.answer(
        "💰 <b>Сменить цену</b>\n\n"
        "Пришли число (можно <code>6700</code> или <code>6k</code>).\n"
        "Чтобы сбросить ручную цену и брать из ставок — пришли <code>-</code>.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith(f"{CB_WIN_CLEAR_MANUAL}:"))
async def cb_print_win_clear_manual(call: types.CallbackQuery, bot: Bot):
    await call.answer()
    auction_id = _cb_last_int(call.data)

    await _ensure_print_win_tables()
    await (await AuctionWinnerService.create()).clear_manual_result(auction_id)

    await call.answer("🧹 Ручной итог сброшен.")
    await _refresh_print_win_menu_by_ids(
        bot,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        auction_id=auction_id,
        admin_user=call.from_user,
    )


@router.message(lambda m: m.from_user and m.from_user.id in PENDING_WIN_FIELD_EDIT)
async def msg_print_win_edit_single_field(message: types.Message, bot: Bot):
    st = PENDING_WIN_FIELD_EDIT.pop(message.from_user.id, None)
    if not st:
        return

    auction_id = int(st["auction_id"])
    field = st["field"]
    raw = (message.text or "").strip()

    prev = await _get_manual_result(auction_id) or {}
    winner_user_id = prev.get("winner_user_id")
    winner_username = prev.get("winner_username")
    owner_user_id = prev.get("owner_user_id")
    owner_username = prev.get("owner_username")
    amount = prev.get("amount")
    moderator_comment_prev = prev.get("moderator_comment")

    moderator_comment_new: str | None = None  # None = не трогаем (COALESCE сохранит старое)

    if field == "winner":
        if raw == "-":
            winner_user_id, winner_username = None, None
        else:
            uid, uname = await _resolve_user_ref(raw)
            if uid is None and uname is None:
                await message.answer("❌ Не понял победителя. Дай @username или числовой id (или '-')", parse_mode="HTML")
                return
            winner_user_id, winner_username = uid, uname

    elif field == "owner":
        if raw == "-":
            owner_user_id, owner_username = None, None
        else:
            uid, uname = await _resolve_user_ref(raw)
            if uid is None and uname is None:
                await message.answer("❌ Не понял владельца. Дай @username или числовой id (или '-')", parse_mode="HTML")
                return
            owner_user_id, owner_username = uid, uname

    elif field == "amount":
        if raw == "-":
            amount = None
        else:
            val = _parse_amount_text(raw)
            if val is None:
                await message.answer("❌ Цена должна быть числом (пример: 6700 или 6k) или '-'.", parse_mode="HTML")
                return

            cur = (await _auction_currency(auction_id)).lower()
            if cur in {"алмазы", "diamond", "diamonds"} and val % 10 != 0:
                await message.answer("Для 💎 ставка/цена должна быть кратной 10.", parse_mode="HTML")
                return
            if cur in {"чашки", "cups"} and val % 2 != 0:
                await message.answer("Для 🍵 ставка/цена должна быть чётной.", parse_mode="HTML")
                return

            amount = val

    elif field == "comment":
        if raw == "-":
            moderator_comment_new = ""  # очистить
        else:
            txt = raw.strip()
            if len(txt) > 900:
                await message.answer("❌ Слишком длинно. Комментарий должен быть до 900 символов.", parse_mode="HTML")
                return
            moderator_comment_new = txt

    await _upsert_manual_result(
        auction_id,
        winner_user_id=int(winner_user_id) if winner_user_id else None,
        winner_username=winner_username,
        owner_user_id=int(owner_user_id) if owner_user_id else None,
        owner_username=owner_username,
        amount=int(amount) if amount is not None else None,
        updated_by=int(message.from_user.id),
        moderator_comment=moderator_comment_new,  # None => не затираем старый
    )

    await message.answer("✅ Обновлено.", parse_mode="HTML")

    await _refresh_print_win_menu_by_ids(
        bot,
        chat_id=int(st["menu_chat_id"]),
        message_id=int(st["menu_message_id"]),
        auction_id=auction_id,
        admin_user=message.from_user,
    )

    await _log_admin(
        bot,
        f"✎ Админ {_admin_tag(message.from_user)} обновил поле <b>{field}</b> для лота <b>{auction_id}</b>.",
    )
async def _send_win_dm_to_targets(
        bot: Bot,
        *,
        auction_id: int,
        target: str,  # 'owner' | 'winner' | 'both'
        admin_user: types.User,
) -> tuple[int, int, list[dict], int | None]:
    """
    Отправляет ЛС победителю и/или владельцу.
    После успешной отправки закрепляет именно это сообщение в ЛС.
    Возвращает (ok, fail, deliveries, used_amount).
    """
    ctx = await _build_print_win_context(auction_id)
    if not ctx.get("ok"):
        return 0, 1, [{
            "role": "error",
            "user_id": 0,
            "username": None,
            "ok": False,
            "err": ctx.get("err"),
            "pinned": False,
        }], None

    moderator_tag = _admin_tag(admin_user)

    text = await _compose_user_win_text(
        auction_id=auction_id,
        link=str(ctx["link"]),
        lot_line=str(ctx["lot_line"]),
        amount=ctx.get("amount"),
        cur_emoji=str(ctx["cur_emoji"]),
        winner_user_id=ctx.get("winner_user_id"),
        winner_username=ctx.get("winner_username"),
        owner_mentions=str(ctx.get("owner_mentions") or "—"),
        moderator_tag=moderator_tag,
        owner_user_ids=list(ctx.get("owner_user_ids") or []),
        moderator_comment=ctx.get("moderator_comment"),
    )
    kb = await _thanks_kb(auction_id, moderator_tag)

    ok = 0
    fail = 0
    deliveries: list[dict] = []

    async def _try_send(uid: int, role: str, username: str | None):
        nonlocal ok, fail, deliveries
        try:
            photo = ctx.get("photo")
            sent_msg = None

            if photo and len(text) <= 900:
                sent_msg = await _send_media_any(
                    bot,
                    uid,
                    str(photo),
                    text,
                    reply_markup=kb,
                )
            else:
                sent_msg = await bot.send_message(
                    uid,
                    text,
                    parse_mode="HTML",
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )

            pinned = False
            if sent_msg and getattr(sent_msg, "message_id", None):
                pinned = await _safe_pin_pm_message(bot, uid, sent_msg.message_id)

            try:
                await _add_win_mailing(auction_id, role, admin_user)
            except Exception as e:
                logger.warning(
                    "[print_win] не удалось записать mailing auction_id=%s role=%s uid=%s: %r",
                    auction_id, role, uid, e
                )

            ok += 1
            deliveries.append({
                "role": role,
                "user_id": uid,
                "username": username,
                "ok": True,
                "err": None,
                "pinned": pinned,
            })
        except Exception as e:
            fail += 1
            deliveries.append({
                "role": role,
                "user_id": uid,
                "username": username,
                "ok": False,
                "err": str(e),
                "pinned": False,
            })

    if target in {"winner", "both"}:
        wid = ctx.get("winner_user_id")
        if wid:
            u = await get_user(int(wid)) or {}
            await _try_send(int(wid), "winner", u.get("username"))
        else:
            fail += 1
            deliveries.append({
                "role": "winner",
                "user_id": 0,
                "username": None,
                "ok": False,
                "err": "winner not set",
                "pinned": False,
            })

    if target in {"owner", "both"}:
        for oid in (ctx.get("owner_user_ids") or []):
            if not oid:
                continue
            u = await get_user(int(oid)) or {}
            await _try_send(int(oid), "owner", u.get("username"))

    return ok, fail, deliveries, ctx.get("amount")
@router.message(Command("print_win_missed"))
@admin_only
async def cmd_print_win_missed(message: types.Message) -> None:
    args = (message.text or "").split(maxsplit=1)

    # дата по умолчанию: сегодня (по МСК)
    msk = ZoneInfo("Europe/Moscow")
    today_msk = datetime.now(msk).date()

    if len(args) == 1:
        target_date = today_msk
    else:
        raw = args[1].strip()
        parsed: date | None = None

        for fmt in ("%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m"):
            try:
                d = datetime.strptime(raw, fmt).date()
                if fmt == "%d.%m":
                    d = d.replace(year=today_msk.year)
                parsed = d
                break
            except ValueError:
                continue

        if not parsed:
            await message.answer("❌ Неверный формат даты. Примеры: 20.01.2026 или 20.01")
            return

        target_date = parsed

    rows = await get_print_win_missed_for_day(target_date)

    if not rows:
        await message.answer(f"✅ За {target_date.strftime('%d.%m.%Y')} пропусков /print_win не найдено.")
        return

    lines = [
        f"⚠️ За {target_date.strftime('%d.%m.%Y')} НЕ было рассылок /print_win (только лоты из расписания):",
        ""
    ]

    for r in rows:
        auction_id = int(r["auction_id"])
        st = r.get("start_time")
        t = st.strftime("%H:%M") if isinstance(st, datetime) else "??:??"

        bids_count = int(r.get("bids_count") or 0)
        no_bids_mark = " 😿 без ставок" if bids_count == 0 else ""

        hero = (r.get("hero_name") or "").strip()
        card = (r.get("card_name") or "").strip()
        lot_name = f" — {hero} • {card}" if (hero or card) else ""

        lines.append(f"{t} — {auction_id}{no_bids_mark}{lot_name}")

    # чтобы не упереться в лимит 4096
    text = "\n".join(lines)
    if len(text) <= 3800:
        await message.answer(text)
        return

    # режем по строкам
    chunk: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > 3800:
            await message.answer("\n".join(chunk))
            chunk = []
            size = 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        await message.answer("\n".join(chunk))


@router.message(Command("ex_owners"))
async def cmd_ex_owners(message: Message) -> None:
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Формат: /ex_owners <card_id>")
        return

    try:
        card_id = int(parts[1].strip())
    except Exception:
        await message.answer("card_id должен быть числом.")
        return

    batches = await get_exchange_batches_for_card(card_id, status="approved")
    if not batches:
        await message.answer(f"🛒 По карте id={card_id} нет одобренных заявок биржи.")
        return

    lines = [f"🛒 <b>Владельцы по карте</b> <code>{card_id}</code> (биржа):", ""]
    for r in batches:
        uname = f"@{r['username']}" if r.get("username") else f"id{r['user_id']}"
        lines.append(f"• 🆔 batch <code>{r['batch_id']}</code> — {uname} × <b>{r['qty']}</b>")

    await message.answer("\n".join(lines), parse_mode="HTML")


import re
import contextlib
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, CallbackQuery, Message
from aiogram.exceptions import TelegramForbiddenError


def _mention_html(user_id: int, username: str | None) -> str:
    if username:
        return f"@{username}"
    return f'<a href="tg://user?id={user_id}">id{user_id}</a>'


def _kb_print_ex(batch_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📨 Отправить обоим", callback_data=f"pex|send_both|{batch_id}")
    kb.button(text="👑 Отправить владельцу", callback_data=f"pex|send_owner|{batch_id}")
    kb.button(text="🏆 Отправить победителю", callback_data=f"pex|send_winner|{batch_id}")
    kb.button(text="🏆 Сменить победителя", callback_data=f"pex|set_winner|{batch_id}")
    kb.button(text="💰 Сменить цену", callback_data=f"pex|set_price|{batch_id}")
    kb.button(text="♻️ Сброс", callback_data=f"pex|reset|{batch_id}")
    kb.button(text="🧩 Мастер", callback_data=f"pex|master|{batch_id}")
    kb.button(text="🔄 Обновить", callback_data=f"pex|refresh|{batch_id}")
    kb.adjust(1, 2, 2, 2, 1)
    return kb.as_markup()


async def _render_print_ex_text(batch: dict, cards: list[dict], st: dict | None) -> str:
    owner_id = int(batch["user_id"])
    owner_username = (batch.get("username") or "").strip() or None
    owner = _mention_html(owner_id, owner_username)

    winner_id = int(st["manual_winner_id"]) if st and st.get("manual_winner_id") else None
    winner_name = (st.get("manual_winner_name") or "").strip() if st else ""
    winner_ref = (
        _mention_html(winner_id, winner_name) if winner_id else (f"@{winner_name}" if winner_name else "—")
    )

    price = st.get("manual_price") if st else None
    if price is None:
        price = batch.get("price")
    link = (st.get("manual_link") or "").strip() if st else ""
    if not link:
        link = "—"

    cards_lines = []
    for c in cards:
        title = f"{c.get('hero_name') or ''} — {c.get('card_name')}".strip(" —")
        cards_lines.append(f"• {title} (id={c['card_id']}) × {c['qty']}")

    cards_block = "\n".join(cards_lines) if cards_lines else "—"

    return (
        f"🛒 <b>PRINT EX</b>\n"
        f"🆔 batch_id: <code>{batch['batch_id']}</code>\n"
        f"Статус: <b>{batch.get('status', '?')}</b>\n\n"
        f"👑 Владелец: {owner}\n"
        f"🏆 Победитель: {winner_ref}\n"
        f"💰 Цена: <b>{price}</b> {batch.get('currency', '')}\n"
        f"🔗 Ссылка: {link}\n\n"
        f"<b>Состав:</b>\n{cards_block}"
    )


@router.message(Command("print_ex"))
async def cmd_print_ex(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Формат: /print_ex <batch_id>")
        return

    try:
        batch_id = int(parts[1].strip())
    except Exception:
        await message.answer("batch_id должен быть числом.")
        return

    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await message.answer(f"Не нашёл заявку биржи batch_id={batch_id}")
        return

    cards = await get_exchange_cards_for_batch(batch_id)
    st = await get_exchange_print_stats(batch_id)

    text = await _render_print_ex_text(batch, cards, st)
    await message.answer(text, parse_mode="HTML", reply_markup=_kb_print_ex(batch_id))


@router.callback_query(F.data.startswith("pex|"))
async def cb_print_ex(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if call.from_user.id not in ADMINS:
        await call.answer("Нет доступа.", show_alert=True)
        return

    _, action, bid_s = (call.data or "").split("|", 2)
    batch_id = int(bid_s)

    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return

    async def _refresh() -> None:
        cards = await get_exchange_cards_for_batch(batch_id)
        st = await get_exchange_print_stats(batch_id)
        text = await _render_print_ex_text(batch, cards, st)
        try:
            await call.message.edit_text(text, parse_mode="HTML", reply_markup=_kb_print_ex(batch_id))
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                pass
            else:
                raise

    if action == "refresh":
        await _refresh()
        await call.answer("Обновлено.")
        return

    if action == "reset":
        await reset_exchange_print_stats(batch_id, updated_by=call.from_user.id)
        await _refresh()
        await call.answer("Сброшено.")
        return

    if action in {"set_winner", "set_price", "master"}:
        await state.set_state(PrintExStates.waiting_manual)
        await state.update_data(ex_batch_id=batch_id, ex_action=action, ex_msg_chat=call.message.chat.id,
                                ex_msg_id=call.message.message_id)

        if action == "set_winner":
            await call.message.answer("Введи победителя: <code>@username</code> или <code>user_id</code>",
                                      parse_mode="HTML")
        elif action == "set_price":
            await call.message.answer("Введи новую цену числом (без валюты).", parse_mode="HTML")
        else:
            await call.message.answer(
                "🧩 <b>Мастер ручного итога</b>\n"
                "Отправь 2–3 строки:\n"
                "1) победитель: <code>@username</code> или <code>user_id</code>\n"
                "2) ссылка на биржу (t.me/...)\n"
                "3) цена (необязательно)\n",
                parse_mode="HTML",
            )
        await call.answer()
        return

    # SEND
    st = await get_exchange_print_stats(batch_id)
    owner_id = int(batch["user_id"])
    owner_username = (batch.get("username") or "").strip() or None

    winner_id = int(st["manual_winner_id"]) if st and st.get("manual_winner_id") else None
    winner_name = (st.get("manual_winner_name") or "").strip() if st else ""
    price = st.get("manual_price") if st else None
    if price is None:
        price = batch.get("price")
    link = (st.get("manual_link") or "").strip() if st else ""

    if action in {"send_winner", "send_both"} and not (winner_id or winner_name):
        await call.answer("Сначала укажи победителя (🏆).", show_alert=True)
        return

    text_owner = (
        f"🛒 <b>Биржа: сделка</b>\n"
        f"🆔 batch_id: <code>{batch_id}</code>\n"
        f"🏆 Победитель: {winner_name or (f'id{winner_id}' if winner_id else '—')}\n"
        f"💰 Цена: <b>{price}</b> {batch.get('currency', '')}\n"
        f"🔗 Ссылка: {link or '—'}\n"
    )
    text_winner = (
        f"🛒 <b>Биржа: ты победитель</b>\n"
        f"🆔 batch_id: <code>{batch_id}</code>\n"
        f"👑 Владелец: {_mention_html(owner_id, owner_username)}\n"
        f"💰 Цена: <b>{price}</b> {batch.get('currency', '')}\n"
        f"🔗 Ссылка: {link or '—'}\n"
    )

    async def _send(uid: int, txt: str) -> bool:
        try:
            await bot.send_message(uid, txt, parse_mode="HTML")
            return True
        except (TelegramForbiddenError, TelegramBadRequest):
            return False

    ok1 = ok2 = True
    if action in {"send_owner", "send_both"}:
        ok1 = await _send(owner_id, text_owner)
    if action in {"send_winner", "send_both"}:
        if winner_id:
            ok2 = await _send(int(winner_id), text_winner)

    await call.answer(f"Отправлено: владелец={'✅' if ok1 else '❌'} победитель={'✅' if ok2 else '❌'}")


@router.message(PrintExStates.waiting_manual)
async def ex_manual_input(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in ADMINS:
        await state.clear()
        return

    data = await state.get_data()
    batch_id = int(data["ex_batch_id"])
    action = data["ex_action"]

    text = (message.text or "").strip()

    def _parse_winner(s: str) -> tuple[int | None, str | None]:
        s = s.strip()
        if not s:
            return None, None
        if s.startswith("@"):
            return None, s.lstrip("@")
        if s.isdigit():
            return int(s), None
        return None, s  # как есть

    if action == "set_winner":
        wid, wname = _parse_winner(text)
        await upsert_exchange_print_stats(batch_id, winner_id=wid, winner_name=wname, updated_by=message.from_user.id)

    elif action == "set_price":
        try:
            p = int(re.sub(r"[^\d]", "", text) or "0")
        except Exception:
            p = 0
        await upsert_exchange_print_stats(batch_id, price=p, updated_by=message.from_user.id)

    else:  # master
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        winner_line = lines[0] if len(lines) >= 1 else ""
        link_line = lines[1] if len(lines) >= 2 else ""
        price_line = lines[2] if len(lines) >= 3 else ""

        wid, wname = _parse_winner(winner_line)
        p = None
        if price_line:
            try:
                p = int(re.sub(r"[^\d]", "", price_line) or "0")
            except Exception:
                p = None

        await upsert_exchange_print_stats(
            batch_id,
            winner_id=wid,
            winner_name=wname,
            link=link_line,
            price=p,
            updated_by=message.from_user.id,
        )

    await state.clear()
    await message.answer("✅ Сохранено. Теперь жми 🔄 Обновить в /print_ex.")


@router.message(F.text.startswith("/print_win"))
async def cmd_print_win(message: Message, bot: Bot):
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return

    parts = (message.text or "").strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: /print_win <auction_id>")
        return

    auction_id = int(parts[1])

    # новое меню /print_win (работает даже если ставок нет)
    await _send_print_win_menu(message, auction_id)

    admin_user = _admin_tag(message.from_user)
    await _log_admin(bot, f"🔎 Админ {admin_user} открыл /print_win для лота <b>{auction_id}</b>.")


@router.callback_query(F.data.startswith(f"{CB_WIN_EDIT_AMT}:"))
async def cb_win_edit_amt(call: types.CallbackQuery):
    await call.answer()
    try:
        _, _, aid_s, wid_s = call.data.split(":")
        auction_id = int(aid_s);
        winner_id = int(wid_s)
    except Exception:
        await call.message.answer("❌ Неверные данные кнопки.");
        return
    PENDING_EDIT[call.from_user.id] = {"auction_id": auction_id, "field": "amount", "winner_id": winner_id}
    await call.message.answer(f"✎ Введите новую сумму ставки для лота <code>{auction_id}</code> (число).",
                              parse_mode="HTML")


@router.callback_query(F.data.startswith(f"{CB_WIN_EDIT_USER}:"))
async def cb_win_edit_user(call: types.CallbackQuery):
    await call.answer()
    try:
        _, _, aid_s, _ = call.data.split(":")
        auction_id = int(aid_s)
    except Exception:
        await call.message.answer("❌ Неверные данные кнопки.");
        return

    PENDING_EDIT[call.from_user.id] = {"auction_id": auction_id, "field": "winner"}
    await call.message.answer(
        f"👤 Пришлите нового победителя для лота <code>{auction_id}</code> в формате @username или числовой id.",
        parse_mode="HTML"
    )


@router.message(lambda m: m.from_user.id in PENDING_EDIT)
async def handle_pending_edit(message: types.Message, bot: Bot):
    ctx = PENDING_EDIT.pop(message.from_user.id, None)
    if not ctx:
        return
    auction_id = ctx["auction_id"];
    fld = ctx["field"]

    d = WIN_DRAFTS.get(auction_id, {})
    admin_user = f"@{message.from_user.username}" if message.from_user.username else f"id{message.from_user.id}"

    if fld == "amount":
        txt = (message.text or "").strip().replace(" ", "").lower().replace("к", "k")
        if txt.endswith("k"):
            try:
                val = int(txt[:-1]) * 1000
            except:
                await message.answer("❌ Неверное число.");
                return
        else:
            if not txt.isdigit():
                await message.answer("❌ Неверное число.");
                return
            val = int(txt)

        # валидация по валюте
        cur = (await _auction_currency(auction_id)).lower()
        if cur in {"алмазы", "diamond", "diamonds"} and val % 10 != 0:
            await message.answer("Для алмазов ставка должна быть кратной 10.");
            return
        if cur in {"чашки", "cups"} and val % 2 != 0:
            await message.answer("Для чашек ставка должна быть чётной.");
            return

        d["amount"] = val
        WIN_DRAFTS[auction_id] = d

        # актуальный победитель
        b = await _best_bid_row(auction_id)
        wid = int(d.get("winner_id") or (b["bidder_id"] if b else 0))
        preview = await _winner_preview_text(auction_id, val, wid)

        await message.answer("✔︎ Стоимость обновлена в черновике.", parse_mode="HTML")
        await message.answer(preview, parse_mode="HTML",
                             reply_markup=_kb_winner_actions(auction_id, wid),
                             disable_web_page_preview=True)

        await _log_admin(bot,
                         f"✎ Админ {admin_user} установил ставку <b>{val}</b> в черновике для лота <b>{auction_id}</b>.")

    elif fld == "winner":
        raw = (message.text or "").strip()
        wid = None
        if raw.startswith("@"):
            user = await get_user_by_username(raw.lstrip("@"))
            if user: wid = int(user["user_id"])
        elif raw.isdigit():
            wid = int(raw)
        if not wid:
            await message.answer("❌ Пользователь не найден.");
            return

        d["winner_id"] = wid
        WIN_DRAFTS[auction_id] = d

        b = await _best_bid_row(auction_id)
        amt = int(d.get("amount") or (b["amount"] if b else 0))
        preview = await _winner_preview_text(auction_id, amt, wid)

        await message.answer("✔︎ Победитель обновлён в черновике.", parse_mode="HTML")
        await message.answer(preview, parse_mode="HTML",
                             reply_markup=_kb_winner_actions(auction_id, wid),
                             disable_web_page_preview=True)

        await _log_admin(bot,
                         f"👤 Админ {admin_user} сменил победителя на <code>{wid}</code> в черновике для лота <b>{auction_id}</b>.")


RULES_COMMENT = (
    "<b>📌 Правила ставок</b>\n"
    "• Ставки только цифрами, только в комментариях к этому посту.\n"
    "• Валюта как в шапке лота:\n"
    "  – 💎/🪙: кратно 10\n"
    "  – 🍵: только чётные\n"
    "• Редактирование ставки запрещено. Новая ставка = новый комментарий.\n"
    "• Флуд и оффтоп удаляются. 3 предупреждения = мут.\n"
    "• Оплата ставки — в течение месяца (если не указано иное).\n"
    "• Отказ от ставки — бан.\n"
    "• Спам и сторонние ссылки запрещены.\n\n"
    "<b>🛡️ Бот автоматически:</b>\n"
    "• ❌ удаляет невалидные ставки и выдаёт мут на 1 минуту\n"
    "• ⚠️ выдаёт предупреждение за удаление или редактирование своей ставки\n"
    "• 🧹 удаляет сообщения, не относящиеся к ставкам (флуд)\n\n"
    "За нарушение — предупреждение. 4 преда = <b>бан</b> 🚫\n"
    "Подробнее: https://teletype.in/@velassya/karty_kr_pravila"
)


async def _get_discussion_msg_id(auction_id: int) -> int | None:
    return await (await AuctionWinnerService.create()).discussion_message_id(auction_id)


async def _auction_currency(auction_id: int) -> str:
    return await (await AuctionWinnerService.create()).auction_currency(auction_id) or ""


async def _post_rules_under_lot(bot: Bot, auction_id: int, retries: int = 5, delay: float = 1.5) -> None:
    """
    Пытается найти discussion_message_id и отправить «Правила» реплаем.
    Делаем несколько попыток, пока Telethon-юзербот не привяжет обсуждение.
    """
    if USERBOT_BID_MODERATION:
        return

    dmsg_id = await _get_discussion_msg_id(auction_id)
    for _ in range(retries):
        if dmsg_id:
            break
        await asyncio.sleep(delay)
        dmsg_id = await _get_discussion_msg_id(auction_id)

    if not dmsg_id:
        # не нашли — логируем и сдаёмся, без истерик
        for chat_id in ADMIN_LOG_CHATS:
            try:
                await bot.send_message(chat_id,
                                       f"⚠️ Не удалось разместить правила под лотом <code>{auction_id}</code>: нет discussion_message_id.",
                                       parse_mode="HTML")
            except Exception:
                pass
        return

    try:
        await bot.send_message(DISCUSSION_CHAT_ID, RULES_COMMENT, parse_mode="HTML", reply_to_message_id=dmsg_id)
        # опционально: короткий лог
        for chat_id in ADMIN_LOG_CHATS:
            try:
                await bot.send_message(chat_id, f"📌 Правила размещены под лотом <code>{auction_id}</code>.",
                                       parse_mode="HTML")
            except Exception:
                pass
    except Exception as e:
        for chat_id in ADMIN_LOG_CHATS:
            try:
                await bot.send_message(chat_id,
                                       f"⚠️ Ошибка при размещении правил по лоту <code>{auction_id}</code>: {re.escape(str(e))}",
                                       parse_mode="HTML")
            except Exception:
                pass


@router.callback_query(F.data.startswith(f"{CB_WIN_SEND}:"))
async def cb_winner_send(call: types.CallbackQuery, bot: Bot):
    await call.answer()
    try:
        _, _, aid_s, wid_s = call.data.split(":")
        auction_id = int(aid_s)
        winner_id = int(wid_s)
    except Exception:
        await call.message.edit_text("❌ Неверные данные кнопки.", parse_mode="HTML")
        return

    # Учтём черновики правок
    draft = WIN_DRAFTS.get(auction_id, {})
    override_winner = int(draft["winner_id"]) if draft.get("winner_id") else None
    override_amount = int(draft["amount"]) if draft.get("amount") else None
    if override_winner:
        winner_id = override_winner

    ok, fail, deliveries, used_amount = await _send_notifications(bot, auction_id, winner_id,
                                                                  override_amount=override_amount)

    now_str = _fmt_msk(_msk_now())
    cur_emoji = _emoji_by_currency(await _auction_currency(auction_id))

    lines = [
        f"📨 Рассылка по лоту <b>{auction_id}</b> завершена ({now_str} МСК).",
        f"Ставка: <b>{used_amount} {cur_emoji}</b>",
        f"Успешно: <b>{ok}</b>  Ошибок: <b>{fail}</b>",
        ""
    ]
    for d in deliveries:
        tag = "🏆" if d["role"] == "winner" else "👑"
        uname = ("@" + d["username"]) if d["username"] else f"id{d['user_id']}"
        if d["ok"]:
            lines.append(f"{tag} {uname} — OK")
        else:
            lines.append(f"{tag} {uname} — FAIL: {d['err'][:120]}")
    report_text = "\n".join(lines)

    try:
        await call.message.edit_text(report_text, parse_mode="HTML")
    except Exception:
        pass
    for chat_id in ADMIN_LOG_CHATS:
        try:
            await call.bot.send_message(chat_id, report_text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            pass


@router.callback_query(F.data.startswith(f"{CB_WIN_SKIP}:"))
async def cb_winner_skip(call: types.CallbackQuery, bot: Bot):
    await call.answer("Рассылка отменена.")
    try:
        _, _, aid_s, wid_s = call.data.split(":")
        auction_id = int(aid_s);
        winner_id = int(wid_s)
    except Exception:
        await call.message.edit_text("❌ Неверные данные кнопки.", parse_mode="HTML")
        return

    draft = WIN_DRAFTS.get(auction_id, {})
    used_amount = draft.get("amount")
    winner_id = int(draft.get("winner_id") or winner_id)

    # правим текст кнопочного сообщения
    try:
        await call.message.edit_text(
            f"⛔ Рассылка по лоту <b>{auction_id}</b> отменена админом.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    admin_user = f"@{call.from_user.username}" if call.from_user.username else f"id{call.from_user.id}"
    await _log_admin(bot, f"⛔ Админ {admin_user} отменил рассылку по лоту <b>{auction_id}</b> "
                          f"(winner={winner_id}, amount={used_amount if used_amount is not None else '—'}).")


async def _send_notifications(bot: Bot, auction_id: int, winner_id: int, *, override_amount: int | None = None) -> \
        tuple[int, int, list[dict], int]:
    a = await (await AuctionWinnerService.create()).auction(auction_id) or {}

    cur_emoji = _emoji_by_currency(a.get("currency"))
    link = _build_channel_link(a.get("message_id")) or "(ссылка недоступна)"
    lot_line = (a.get("hero_name") or "-") + (f" — {a.get('card_name')}" if a.get("card_name") else "")

    has_winner = int(winner_id or 0) > 0

    w = {}
    wname = "—"
    winner_links_line = ""

    if has_winner:
        w = await get_user(int(winner_id)) or {}
        wname = _mention(int(winner_id), w.get("username"))

        # Если username нет — добавляем “3 ссылки” (на деле 2 tg:// + (t.me если вдруг есть))
        if _norm_username(w.get("username")) is None:
            winner_links_line = f"\nСсылки победителя: {_user_links_html(int(winner_id), w.get('username'))}"

    owners = await _get_owners(auction_id)
    owners_mentions = ", ".join(_mention(o["user_id"], o.get("username")) for o in owners) or "—"

    if override_amount is not None:
        amount = int(override_amount)
    else:
        b = await _best_bid_row(auction_id)
        amount = int(b["amount"]) if b and b.get("amount") is not None else 0

    text_common = (
        "Поздравляю!!!! 🥳\n\n"
        f"Аукцион {link} завершён!\n"
        f"Лот: {lot_line}\n\n"
        f"Стоимость карты: {amount} {cur_emoji}\n"
        f"Победитель: {wname}{winner_links_line}\n"
        f"Владелец карты: {owners_mentions}"
    )

    ok = 0
    fail = 0
    deliveries: list[dict] = []

    # победителю (только если он есть)
    if has_winner:
        try:
            await bot.send_message(int(winner_id), text_common, parse_mode="HTML", disable_web_page_preview=True)
            ok += 1
            deliveries.append(
                {"role": "winner", "user_id": int(winner_id), "username": w.get("username"), "ok": True, "err": ""})
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            fail += 1
            deliveries.append({"role": "winner", "user_id": int(winner_id), "username": w.get("username"), "ok": False,
                               "err": str(e)})
        except Exception as e:
            fail += 1
            deliveries.append({"role": "winner", "user_id": int(winner_id), "username": w.get("username"), "ok": False,
                               "err": repr(e)})
    else:
        deliveries.append({"role": "winner", "user_id": 0, "username": None, "ok": False, "err": "no_winner"})

    text_for_owners = text_common

    if not has_winner:
        text_for_owners = (
            "Привет!\n\n"
            f"Аукцион {link} завершён!\n"
            f"Лот: {lot_line}\n\n"
            "Ставок не было, поэтому карта не нашла нового владельца. 🫶\n"
            "Ничего страшного: такое бывает, просто не попали в настроение чата.\n\n"
            f"Владелец карты: {owners_mentions}\n\n"
            "Хочешь, выставь её снова (часто решает другая цена/валюта/время) или закинь в биржу."
        )

    # владельцам
    for o in owners:
        uid = int(o["user_id"]);
        uname = o.get("username")
        await bot.send_message(uid, text_for_owners, parse_mode="HTML", disable_web_page_preview=True)
        try:
            await bot.send_message(uid, text_common, parse_mode="HTML", disable_web_page_preview=True)
            ok += 1;
            deliveries.append({"role": "owner", "user_id": uid, "username": uname, "ok": True, "err": ""})
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            fail += 1;
            deliveries.append({"role": "owner", "user_id": uid, "username": uname, "ok": False, "err": str(e)})
        except Exception as e:
            fail += 1;
            deliveries.append({"role": "owner", "user_id": uid, "username": uname, "ok": False, "err": repr(e)})

    return ok, fail, deliveries, amount


async def notify_card_subscribers(telegram_bot, card_id: int, auction: dict):
    subs = await (await CardSubscriptionsService.from_runtime()).subscriber_ids(card_id)
    if not subs:
        return

    # готовим текст
    start_dt = auction.get("start_time")
    when_str = start_dt.strftime("%d.%m в %H:%M") if start_dt else "скоро"
    hero = auction.get("hero_name") or "-"
    name = auction.get("card_name") or "Без названия"

    caption = (
        f"🔔 <b>Отслеживаемая карта!</b>\n"
        f"Карта <b>{name}</b> ({hero}) выйдет на аукцион {when_str}!"
    )

    photo = auction.get("image_id")  # можно и без фото, если нет
    for uid in subs:
        try:
            if photo:
                await _send_media_any(telegram_bot, int(uid), str(photo), caption)
            else:
                await telegram_bot.send_message(uid, caption, parse_mode="HTML")
        except Exception as e:
            print(f"[notify_card_subscribers] Не удалось отправить {uid}: {e}")


async def _is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """
    Разрешаем команду только админам в супергруппе/группе.
    В личке — разрешаем (зачастую суперюзер).
    """
    try:
        if chat_id > 0:
            return True  # private chat, пусть суперюзер запускает из ЛС
        member = await bot.get_chat_member(chat_id, user_id)
        status = getattr(member, "status", None)
        return status in {"administrator", "creator"}
    except Exception:
        return False


def _parse_args(arg_str: str) -> tuple[bool, Optional[int]]:
    """
    Парсим "--dry" и "--user <id>".
    Поддерживаем короткие формы: "-n" и "-u 123".
    Также допускаем просто число без ключа как user_id.
    """
    if not arg_str:
        return False, None

    args = shlex.split(arg_str)
    dry = False
    user_id: Optional[int] = None

    i = 0
    while i < len(args):
        a = args[i].lower()
        if a in {"--dry", "-n"}:
            dry = True
            i += 1
            continue
        if a in {"--user", "-u"} and i + 1 < len(args):
            try:
                user_id = int(args[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        # просто число без ключа
        try:
            user_id = int(args[i])
            i += 1
            continue
        except ValueError:
            i += 1

    return dry, user_id


def _format_result(rows: list[dict]) -> str:
    if not rows:
        return "Нечего чистить. Ноль записей попадает под критерий."
    lines = [f"Почистили предупреждения у {len(rows)} пользователей:"]
    for r in rows:
        uid = int(r.get("user_id", 0))
        cnt = int(r.get("removed", 0))
        lines.append(f"• user_id {uid}: удалено {cnt}")
    return "\n".join(lines)


async def _legacy_cmd_prune_warns(message: Message, command: CommandObject, bot: Bot) -> None:
    """
    /prune_warns [--dry|-n] [--user|-u <id>|<id>]
    Примеры:
      /prune_warns --dry
      /prune_warns --user 123456
      /prune_warns -n 123456
      /prune_warns 123456
    """
    if not await _is_chat_admin(bot, message.chat.id, message.from_user.id):
        await message.reply("Команда только для админов. Не обижайся, это забота о порядке.")
        return

    dry, target_user_id = _parse_args((command.args or "").strip())
    try:
        rows = await prune_old_warns(target_user_id=target_user_id, dry=dry)
        prefix = "DRY-RUN: " if dry else ""
        await message.reply(prefix + _format_result(rows))
    except Exception as e:
        await message.reply(f"Не вышло. База опять грустит: {e!r}")


def _msk_now() -> datetime:
    # Python 3.9+ всегда имеет zoneinfo, а у тебя вообще 3.13.
    return datetime.now(ZoneInfo("Europe/Moscow"))


def _fmt_msk(dt: datetime) -> str:
    if ZoneInfo and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Moscow"))
    return dt.strftime("%d.%m %H:%M")


def _kb_winner_actions(aid: int, wid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Отправить уведомления", callback_data=f"{CB_WIN_SEND}:{aid}:{wid}")],
        [
            InlineKeyboardButton(text="✎ Исправить стоимость", callback_data=f"{CB_WIN_EDIT_AMT}:{aid}:{wid}"),
            InlineKeyboardButton(text="👤 Исправить победителя", callback_data=f"{CB_WIN_EDIT_USER}:{aid}:{wid}"),
        ],
        [InlineKeyboardButton(text="⛔ Не отправлять", callback_data=f"{CB_WIN_SKIP}:{aid}:{wid}")],
    ])


async def _log_admin(bot: Bot, text: str) -> None:
    for chat_id in _iter_admin_log_chats():
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            pass


def _iter_admin_log_chats() -> list[int]:
    out = []
    try:
        for x in ADMIN_LOG_CHATS:
            if isinstance(x, int):
                out.append(x)
    except Exception:
        pass
    try:
        if isinstance(LOG_CHAT_ID, int):
            out.append(LOG_CHAT_ID)
    except Exception:
        pass
    # уникализируем
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c);
            uniq.append(c)
    return uniq


def _iter_admin_log_chats() -> list[int]:
    out = []
    try:
        for x in ADMIN_LOG_CHATS:
            if isinstance(x, int):
                out.append(x)
    except Exception:
        pass
    try:
        if isinstance(LOG_CHAT_ID, int):
            out.append(LOG_CHAT_ID)
    except Exception:
        pass
    # уникализируем
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c);
            uniq.append(c)
    return uniq


async def _log_admin(bot: Bot, text: str) -> None:
    for chat_id in _iter_admin_log_chats():
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            pass


async def _resolve_user_id(arg: str | None) -> int | None:
    """
    Понимает @username или числовой id. Возвращает user_id или None.
    Использует вашу таблицу users, если есть.
    """
    return await (await WarningService.create()).resolve_user_id(arg)


PRUNE_WARN_AGE_DAYS = 30
MAX_WARN_BEFORE_BAN = 4


async def prune_old_warns(*, target_user_id: int | None = None, dry: bool = False) -> list[dict]:
    """
    Удаляет (или считает при dry) предупреждения старше PRUNE_WARN_AGE_DAYS
    у пользователей, у которых текущее число предупреждений < MAX_WARN_BEFORE_BAN.
    Возвращает [{"user_id": int, "removed": int}, ...]
    """
    return await (await WarningService.create()).prune_old(
        maximum_warning_count=MAX_WARN_BEFORE_BAN,
        age_days=PRUNE_WARN_AGE_DAYS,
        target_user_id=target_user_id,
        dry_run=dry,
    )


async def _legacy_cmd_prune_warns_compat(message: Message, bot: Bot, command: CommandObject):
    """
    /prune_warns                — глобальная чистка старше 30 дней у всех с <4 предами
    /prune_warns --dry          — показать, что удалится, без удаления
    /prune_warns @user          — чистка конкретного пользователя
    /prune_warns @user --dry    — dry-run для пользователя
    (можно реплаем на сообщение пользователя без аргументов)
    """
    if message.from_user.id not in ADMINS:
        await message.answer("Нет доступа.")
        return

    args = (command.args or "").strip().split()
    dry = any(a.lower() in {"--dry", "--test", "dry"} for a in args)

    # попытка понять таргет
    target_id = None
    # 1) если был реплай на пользователя
    if not target_id and message.reply_to_message and message.reply_to_message.from_user:
        target_id = int(message.reply_to_message.from_user.id)
    # 2) если в аргументах @username или id
    if not target_id:
        for a in args:
            maybe = await _resolve_user_id(a)
            if maybe:
                target_id = maybe
                break

    # Выполняем
    stats = await prune_old_warns(target_user_id=target_id, dry=dry)

    if not stats:
        who = f"для пользователя <code>{target_id}</code>" if target_id else "по всем пользователям"
        await message.answer(
            f"✅ Нечего {'удалять' if not dry else 'чистить'} {who}: подходящих предупреждений нет.",
            parse_mode="HTML"
        )
        return

    total = sum(s["removed"] for s in stats)
    lines = []
    if target_id:
        # Отчёт по одному
        remain = await (await WarningService.create()).count_warnings(target_id)
        lines.append(
            f"{'🧪' if dry else '🧹'} Пользователь <code>{target_id}</code>: "
            f"{'будет удалено' if dry else 'удалено'} <b>{total}</b> пред(ов); "
            f"осталось: <b>{remain}</b>."
        )
    else:
        # Сводка + топ
        lines.append(
            f"{'🧪' if dry else '🧹'} {'План очищения' if dry else 'Очищено'}: всего <b>{total}</b> пред(ов) у <b>{len(stats)}</b> пользовател(ей).")
        top = stats[:15]
        lines.append("")
        lines.append("Топ по удалённым:")
        for s in top:
            lines.append(f" • id{s['user_id']}: {s['removed']}")
        if len(stats) > len(top):
            lines.append(f" … и ещё {len(stats) - len(top)} пользователей.")

    text = "\n".join(lines)
    await message.answer(text, parse_mode="HTML")

    # логи админам
    action = "DRY-RUN" if dry else "DELETE"
    target_note = f" user={target_id}" if target_id else " all-users"
    await _log_admin(
        bot,
        f"🧯 <b>{action}</b> prune_warns:{target_note} "
        f"— удалено/запланировано: <b>{total}</b> пред(ов)."
    )


@router.callback_query(F.data.startswith(f"{CB_WIN_REFRESH}:"))
async def cb_print_win_refresh(call: types.CallbackQuery):
    try:
        auction_id = _cb_last_int(call.data)
    except Exception:
        await call.answer("❌ Неверные данные", show_alert=True)
        return

    await call.answer()
    await _edit_print_win_menu(call, auction_id)


@router.callback_query(F.data.startswith(f"{CB_WIN_SEND_OWNER}:"))
async def cb_print_win_send_owner(call: types.CallbackQuery, bot: Bot):
    await call.answer()
    try:
        auction_id = _cb_last_int(call.data)
    except Exception:
        await call.answer("❌ Неверные данные", show_alert=True)
        return

    ok, fail, deliveries, used_amount = await _send_win_dm_to_targets(
        bot,
        auction_id=auction_id,
        target="owner",
        admin_user=call.from_user,
    )

    cur_emoji = _emoji_by_currency(await _auction_currency(auction_id))

    lines = [
        f"👑 Рассылка владельцу по лоту <b>{auction_id}</b> завершена.",
        f"Ставка: <b>{(used_amount or 0)} {cur_emoji}</b>",
        f"Успешно: <b>{ok}</b>  Ошибок: <b>{fail}</b>",
        "",
    ]

    for d in deliveries:
        tag = "👑" if d["role"] == "owner" else "⚠️"
        uname = ("@" + d["username"]) if d.get("username") else (f"id{d['user_id']}" if d.get("user_id") else "—")
        pin_mark = " 📌" if d.get("pinned") else ""
        lines.append(f"{tag} {uname} — {'OK' if d['ok'] else ('FAIL: ' + (d['err'] or '')[:120])}{pin_mark}")

    report_text = "\n".join(lines)
    await call.message.answer(report_text, parse_mode="HTML", disable_web_page_preview=True)
    await _edit_print_win_menu(call, auction_id)
@router.callback_query(F.data.startswith(f"{CB_WIN_SEND_WINNER}:"))
async def cb_print_win_send_winner(call: types.CallbackQuery, bot: Bot):
    await call.answer()
    try:
        auction_id = _cb_last_int(call.data)
    except Exception:
        await call.answer("❌ Неверные данные", show_alert=True)
        return

    ok, fail, deliveries, used_amount = await _send_win_dm_to_targets(
        bot,
        auction_id=auction_id,
        target="winner",
        admin_user=call.from_user,
    )

    cur_emoji = _emoji_by_currency(await _auction_currency(auction_id))

    lines = [
        f"🏆 Рассылка победителю по лоту <b>{auction_id}</b> завершена.",
        f"Ставка: <b>{(used_amount or 0)} {cur_emoji}</b>",
        f"Успешно: <b>{ok}</b>  Ошибок: <b>{fail}</b>",
        "",
    ]

    for d in deliveries:
        tag = "🏆" if d["role"] == "winner" else "⚠️"
        uname = ("@" + d["username"]) if d.get("username") else (f"id{d['user_id']}" if d.get("user_id") else "—")
        pin_mark = " 📌" if d.get("pinned") else ""
        lines.append(f"{tag} {uname} — {'OK' if d['ok'] else ('FAIL: ' + (d['err'] or '')[:120])}{pin_mark}")

    report_text = "\n".join(lines)
    await call.message.answer(report_text, parse_mode="HTML", disable_web_page_preview=True)
    await _edit_print_win_menu(call, auction_id)

@router.callback_query(F.data.startswith(f"{CB_WIN_SEND_BOTH}:"))
async def cb_print_win_send_both(call: types.CallbackQuery, bot: Bot):
    await call.answer()
    try:
        auction_id = int(call.data.rsplit(":", 1)[1])
    except Exception:
        await call.answer("❌ Неверные данные", show_alert=True)
        return

    ok, fail, deliveries, used_amount = await _send_win_dm_to_targets(
        bot,
        auction_id=auction_id,
        target="both",
        admin_user=call.from_user,
    )

    cur_emoji = _emoji_by_currency(await _auction_currency(auction_id))

    lines = [
        f"📨 Рассылка ОБОИМ по лоту <b>{auction_id}</b> завершена.",
        f"Ставка: <b>{(used_amount or 0)} {cur_emoji}</b>",
        f"Успешно: <b>{ok}</b>  Ошибок: <b>{fail}</b>",
        "",
    ]

    for d in deliveries:
        tag = "🏆" if d["role"] == "winner" else ("👑" if d["role"] == "owner" else "⚠️")
        uname = ("@" + d["username"]) if d.get("username") else (f"id{d['user_id']}" if d.get("user_id") else "—")
        pin_mark = " 📌" if d.get("pinned") else ""
        lines.append(f"{tag} {uname} — {'OK' if d['ok'] else ('FAIL: ' + (d['err'] or '')[:120])}{pin_mark}")

    await call.message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
    await _edit_print_win_menu(call, auction_id)


@router.callback_query(F.data.startswith(f"{CB_WIN_MANUAL}:"))
async def cb_print_win_manual(call: types.CallbackQuery):
    await call.answer()
    try:
        auction_id = _cb_last_int(call.data)
    except Exception:
        await call.answer("❌ Неверные данные", show_alert=True)
        return

    await call.answer()

    PENDING_WIN_MANUAL[call.from_user.id] = {
        "auction_id": auction_id,
        "step": "winner",
        "menu_chat_id": call.message.chat.id,
        "menu_message_id": call.message.message_id,
        "winner_user_id": None,
        "winner_username": None,
        "owner_user_id": None,
        "owner_username": None,
        "amount": None,
    }

    await call.message.answer(
        "✍️ <b>Ручной итог</b>\n\n"
        "1) Пришли победителя: <code>@username</code> или <code>id</code>\n"
        "   (если победителя нет — напиши <code>-</code>)",
        parse_mode="HTML",
    )


@router.message(lambda m: m.from_user and m.from_user.id in PENDING_WIN_MANUAL)
async def msg_print_win_manual(message: types.Message, bot: Bot):
    st = PENDING_WIN_MANUAL.get(message.from_user.id)
    if not st:
        return

    auction_id = int(st["auction_id"])
    step = st["step"]
    raw = (message.text or "").strip()

    if step == "winner":
        if raw == "-":
            st["winner_user_id"], st["winner_username"] = None, None
        else:
            uid, uname = await _resolve_user_ref(raw)
            st["winner_user_id"], st["winner_username"] = uid, uname
        st["step"] = "owner"
        await message.answer(
            "2) Пришли владельца карты: <code>@username</code> или <code>id</code>\n"
            "   (если оставить как в auction_owners — напиши <code>-</code>)",
            parse_mode="HTML",
        )
        return

    if step == "owner":
        if raw == "-":
            st["owner_user_id"], st["owner_username"] = None, None
        else:
            uid, uname = await _resolve_user_ref(raw)
            st["owner_user_id"], st["owner_username"] = uid, uname
        st["step"] = "amount"
        await message.answer(
            "3) Пришли цену (число). Пример: <code>640</code>\n"
            "   (если оставить как по ставкам — напиши <code>-</code>)",
            parse_mode="HTML",
        )
        return

    if step == "amount":
        if raw == "-":
            st["amount"] = None
        else:
            txt = raw.replace(" ", "")
            if not txt.isdigit():
                await message.answer("❌ Цена должна быть числом (или <code>-</code>).", parse_mode="HTML")
                return
            st["amount"] = int(txt)

        await _upsert_manual_result(
            auction_id,
            winner_user_id=st.get("winner_user_id"),
            winner_username=st.get("winner_username"),
            owner_user_id=st.get("owner_user_id"),
            owner_username=st.get("owner_username"),
            amount=st.get("amount"),
            updated_by=int(message.from_user.id),
        )

        # обновим меню, если можем
        try:
            menu_chat_id = int(st["menu_chat_id"])
            menu_message_id = int(st["menu_message_id"])
            fake_call = types.CallbackQuery(
                id="0",
                from_user=message.from_user,
                chat_instance="0",
                message=message.bot._wrap_message(message.chat, message.message_id, message),  # запасной путь
                data=f"{CB_WIN_REFRESH}:{auction_id}",
            )
        except Exception:
            fake_call = None

        # просто отправим новое меню (надёжнее, чем пляски вокруг edit через fake_call)
        await _send_print_win_menu(message, auction_id)

        admin_user = _admin_tag(message.from_user)
        await _log_admin(bot, f"✍️ Админ {admin_user} задал ручной итог для лота <b>{auction_id}</b>.")

        PENDING_WIN_MANUAL.pop(message.from_user.id, None)
        return


def _cb_last_int(data: str) -> int:
    # Берём число после последнего двоеточия: win:send_owner:4676 -> 4676
    return int(data.rsplit(":", 1)[1])


from aiogram.exceptions import TelegramBadRequest


async def safe_edit_text(message, text: str, **kwargs):
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise


CB_WIN_THANKS = "win:thanks"
_ADMIN_THANKS_READY = False


def _norm_author(author: str) -> str:
    a = (author or "").strip()
    if a.startswith("@"):
        a = a[1:]
    return a.strip().lower()


async def _ensure_admin_thanks_tables() -> None:
    """
    Таблицы для "админских спасибо".

    Как ты просил:
    - спасибо считается по НАЖАТИЯМ (один юзер может нажимать сколько угодно раз);
    - users_total — число УНИКАЛЬНЫХ юзеров, которые хоть раз нажали.
    """
    global _ADMIN_THANKS_READY
    if _ADMIN_THANKS_READY:
        return

    await (await AuctionWinnerService.create()).ensure_admin_thanks_schema()

    _ADMIN_THANKS_READY = True


async def _inc_admin_thanks(author: str, user_id: int) -> tuple[int, int]:
    """
    +1 к "спасибо" модератору.
    - thanks_total увеличивается всегда
    - users_total увеличивается только если это первый клик этого user_id по данному author
    """
    await _ensure_admin_thanks_tables()

    k = _norm_author(author)
    if not k:
        return 0, 0

    return await (await AuctionWinnerService.create()).increment_admin_thanks(k, int(user_id))


async def get_admin_thanks_totals(author: str) -> tuple[int, int]:
    await _ensure_admin_thanks_tables()
    k = _norm_author(author)
    if not k:
        return 0, 0

    return await (await AuctionWinnerService.create()).admin_thanks_totals(k)


async def build_thanks_kb(any_id: int, moderator_tag: str) -> InlineKeyboardMarkup:
    # оставлено для совместимости, чтобы не чинить импорты в других файлах
    return await _thanks_kb(int(any_id), moderator_tag)


@router.callback_query(F.data.startswith("win:thanks:"))
async def cb_win_thanks(call: types.CallbackQuery) -> None:
    parts = (call.data or "").split(":")
    if len(parts) < 4:
        try:
            await call.answer("Кривые данные.", show_alert=True)
        except Exception:
            pass
        return

    try:
        any_id = int(parts[2])
    except ValueError:
        any_id = 0

    author = ":".join(parts[3:]).strip()

    # быстро отвечаем, чтобы не ловить "query is too old"
    try:
        await call.answer("Спасибо учтено ✅")
    except Exception:
        pass

    await _inc_admin_thanks(author, int(call.from_user.id))

    try:
        if call.message:
            await call.message.edit_reply_markup(reply_markup=await build_thanks_kb(any_id, author))
    except Exception:
        pass
@router.callback_query(F.data.startswith("win:edit_manual_comment:"))
async def cb_print_win_edit_manual_comment(call: types.CallbackQuery):
    await call.answer()
    auction_id = _cb_last_int(call.data)

    PENDING_WIN_FIELD_EDIT[call.from_user.id] = {
        "auction_id": auction_id,
        "field": "comment",
        "menu_chat_id": call.message.chat.id,
        "menu_message_id": call.message.message_id,
    }

    await call.message.answer(
        "💬 <b>Комментарий от модератора</b>\n\n"
        "Пришли текст (он будет добавлен в рассылку победителю/владельцу).\n"
        "Чтобы очистить комментарий — пришли <code>-</code>.",
        parse_mode="HTML",
    )
