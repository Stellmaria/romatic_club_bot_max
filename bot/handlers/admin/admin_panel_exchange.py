"""Administrative media and manual exchange operations.

Handlers retain their relative order from the legacy ``admin_panel`` module.
"""

import html
from aiogram import (
    Bot,
    F,
    Router,
    types,
)
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)
from aiogram.filters import Command
from bot.services.exchanges import ExchangeService
from aiogram.fsm.context import FSMContext
from bot.domain.auctions import InvalidExchangeTransition
from aiogram.fsm.state import (
    State,
    StatesGroup,
)
from aiogram.exceptions import TelegramBadRequest
from zoneinfo import ZoneInfo
from html import escape as _h
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.admin_thanks import build_thanks_kb
from bot.handlers.auction.exchange.common import currency_to_emoji
from datetime import datetime
from db.cards import (
    get_card_by_id,
    get_deck_by_id,
    set_card_video_by_id,
)
from db.exchange import (
    get_exchange_batch_by_id,
    get_exchange_items_by_batch_id,
    mark_exchange_manual_sent,
    reset_exchange_manual,
    set_exchange_manual_link,
    set_exchange_manual_price,
    set_exchange_manual_winner,
)
from db.users import (
    get_user,
    get_user_by_username,
)
from db.admin import (
    is_admin,
    log_audit_action,
)
from bot.handlers.admin.helper.new.keyboards import menu_keyboard
from bot.services.admin_logging import send_admin_log
from bot.handlers.admin.logs_admin import short_media_id


from bot.telegram.callback_parser import split_callback_data
from bot.handlers.admin.presentation.exchange_queue import (
    EX1_APPROVE,
    EX1_DELETE,
    EX1_DEL_NO,
    EX1_DEL_YES,
    EX1_REJECT,
    ExchangeOneRejectFSM,
    build_exchange_one_delete_confirmation,
    build_exchange_one_keyboard,
    show_pending_exchange_one,
)
from bot.handlers.admin.presentation.media import extract_media_file_id
from bot.services.admin_thanks import admin_tag


def _short_media(v: object) -> str:
    # чтобы file_id не раздувал логи
    return short_media_id(v) if "short_media_id" in globals() else (str(v)[:12] + "…" if v else "—")


async def _log_exchange_batch_action(
    bot: Bot,
    *,
    action_type: str,
    admin_user: types.User,
    batch_id: int,
    status: str,
) -> None:
    batch = await get_exchange_batch_by_id(int(batch_id))

    # fallback, если заявка исчезла
    if not batch:
        title = {"approved": "одобрено", "rejected": "отклонено", "deleted": "удалено"}.get(
            status, status
        )
        log_text = (
            f"🛒 <b>Биржа: {title}</b>\n"
            f"🕒 {datetime.now(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M:%S')} (МСК)\n"
            f"Админ: <b>{admin_tag(admin_user)}</b>\n"
            f"Batch: <code>{int(batch_id)}</code>\n"
            f"⚠️ Заявка не найдена в БД\n"
            f"Действие: <code>{_h(action_type)}</code>"
        )
        await send_admin_log(bot, log_text)
        await log_audit_action(
            user_id=admin_user.id,
            action_type=action_type,
            auction_id=None,
            details=f"batch_id={batch_id} status={status} batch_not_found",
        )
        return

    # владелец
    owner_id = int(batch.get("user_id") or 0)
    owner = await get_user(owner_id)
    owner_un = (owner.get("username") if owner else None) or None
    owner_txt = _safe_user_mention(owner_id, owner_un)

    # колода
    deck_id = int(batch.get("deck_id") or 0)
    deck_name = ""
    try:
        d = await get_deck_by_id(deck_id)
        deck_name = (d.get("name") or "").strip() if d else ""
    except Exception:
        deck_name = ""

    deck_line = f"{deck_id} колода"
    if deck_name:
        deck_line = f"{deck_id} колода — {deck_name}"

    # режим по-русски
    mode = (batch.get("mode") or "card").strip()
    mode_ru = {
        "card": "Одна карта",
        "deck": "Колода целиком",
        "deck_split": "Колода по картам (сплит)",
    }.get(mode, mode)

    # цена/валюта
    cur = (batch.get("currency") or "алмазы").strip()
    cur_emoji = currency_to_emoji(cur) or "💎"
    price = int(batch.get("price") or 0)

    # пруф
    proof_id = (batch.get("proof_photo_id") or "").strip()
    has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"
    proof_line = "✅ Есть" if has_proof else "❌ Нет"

    # карты в заявке
    items = []
    try:
        items = await get_exchange_items_by_batch_id(int(batch_id))
    except Exception:
        items = []
    cards_lines = []
    if items:
        # коротко: первые 6, чтобы лог не превращался в роман
        for i, it in enumerate(items[:6], start=1):
            cn = (it.get("card_name") or "—").strip()
            hn = (it.get("hero_name") or "—").strip()
            cards_lines.append(f"{i}. {hn} — {cn}")
        if len(items) > 6:
            cards_lines.append(f"…и ещё {len(items) - 6}")

    cards_block = "\n".join(cards_lines) if cards_lines else "—"

    created_at = batch.get("created_at")
    try:
        if isinstance(created_at, datetime):
            created_msk = created_at.astimezone(ZoneInfo("Europe/Moscow")).strftime(
                "%d.%m.%Y %H:%M"
            )
        else:
            created_msk = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
    except Exception:
        created_msk = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")

    comment = (batch.get("comment") or "").strip()
    comment_line = _h(comment) if comment else "—"

    log_text = (
        f"🛒 <b>Биржа: {'одобрено' if status == 'approved' else 'отклонено'}</b>\n"
        f"🕒 {created_msk} (МСК)\n"
        f"Админ: <b>{admin_tag(admin_user)}</b> (id: {admin_user.id})\n"
        f"Batch: <code>{int(batch_id)}</code>\n"
        f"Пользователь: {owner_txt}\n\n"
        f"Колода: <b>{_h(deck_line)}</b>\n"
        f"Режим: <b>{_h(mode_ru)}</b>\n"
        f"Карт: <b>{len(items) if items else 0}</b>\n"
        f"Цена: <b>{price}</b> {cur_emoji}\n"
        f"Пруф: <b>{proof_line}</b>\n"
        f"Комментарий: <b>{comment_line}</b>\n\n"
        f"Состав:\n{_h(cards_block)}\n\n"
        f"Действие: <code>{_h(action_type)}</code>"
    )

    await send_admin_log(bot, log_text)
    await log_audit_action(
        user_id=admin_user.id,
        action_type=action_type,
        auction_id=None,
        details=(
            f"batch_id={batch_id} status={status} mode={mode} currency={cur} "
            f"price={price} owner={owner_id} deck_id={deck_id} has_proof={has_proof}"
        ),
    )


def _extract_video_from_message(msg: Message) -> tuple[str, str | None, str | None] | None:
    """
    Возвращает (file_id, unique_id, thumb_file_id) для video/animation/video-document.
    """
    if msg.video:
        thumb = msg.video.thumbnail.file_id if msg.video.thumbnail else None
        return (msg.video.file_id, msg.video.file_unique_id, thumb)

    if msg.animation:
        thumb = msg.animation.thumbnail.file_id if msg.animation.thumbnail else None
        return (msg.animation.file_id, msg.animation.file_unique_id, thumb)

    if msg.document and (msg.document.mime_type or "").startswith("video/"):
        return (msg.document.file_id, msg.document.file_unique_id, None)

    return None


PEX_PREFIX = "pex"  # callback: pex|<batch_id>|<action>


class PrintExFSM(StatesGroup):
    winner = State()
    price = State()
    link = State()


def _pex_cb(batch_id: int, action: str) -> str:
    return f"{PEX_PREFIX}|{int(batch_id)}|{action}"


def _safe_user_mention(
    user_id: int | None, username: str | None, *, title: str | None = None
) -> str:
    """
    Формирует упоминание для parse_mode=HTML:
    - если есть username -> возвращает @username (ровно один @)
    - иначе -> кликабельная ссылка по id
    """
    un = (username or "").strip()
    if un.startswith("@"):
        un = un[1:]

    if un:
        return f"@{html.escape(un)}"

    uid = int(user_id or 0)
    if uid > 0:
        label = html.escape(title) if title else f"id{uid}"
        return f'<a href="tg://user?id={uid}">{label}</a>'

    return "—"


async def _build_print_ex_view(batch_id: int) -> tuple[str, InlineKeyboardMarkup]:
    batch = await get_exchange_batch_by_id(int(batch_id))
    if not batch:
        return (
            f"⚠️ Заявка биржи не найдена: <code>{batch_id}</code>",
            InlineKeyboardMarkup(inline_keyboard=[]),
        )

    items = await get_exchange_items_by_batch_id(int(batch_id))

    owner = await get_user(int(batch["user_id"]))
    owner_username = (owner.get("username") if owner else None) or None
    owner_txt = _safe_user_mention(int(batch["user_id"]), owner_username)

    manual_winner_id = batch.get("manual_winner_id")
    manual_winner_username = (batch.get("manual_winner_username") or "").strip() or None

    winner_txt = "—"
    if manual_winner_id:
        winner_txt = _safe_user_mention(int(manual_winner_id), manual_winner_username)

    price = batch.get("manual_price")
    if price is None:
        price = batch.get("price")

    link = (batch.get("manual_link") or "").strip() or "—"
    sent = "✅ да" if batch.get("manual_sent_at") else "❌ нет"

    lines = [
        f"🛒 <b>PRINT_EX</b> • заявка <code>{batch_id}</code>",
        f"Статус: <b>{batch.get('status')}</b>",
        f"Владелец: {owner_txt}",
        f"Режим: <b>{batch.get('mode')}</b>",
        f"Цена: <b>{int(price or 0)}</b> {batch.get('currency')}",
        f"Комментарий: {(batch.get('comment') or '').strip() or '—'}",
        "",
        "📦 <b>Состав:</b>",
    ]

    if items:
        for it in items:
            nm = f"{(it.get('hero_name') or '').strip()} — {(it.get('card_name') or '').strip()}".strip(
                " —"
            )
            qty = int(it.get("qty") or 1)
            lines.append(f"• {nm} ×{qty}  (<code>card_id={it.get('card_id')}</code>)")
    else:
        lines.append("—")

    lines += [
        "",
        "🧾 <b>Ручной итог:</b>",
        f"Победитель: {winner_txt}",
        f"Ссылка: {link}",
        f"Отправлено: {sent}",
    ]

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📨 Отправить обоим", callback_data=_pex_cb(batch_id, "send_both")
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👤 Сменить победителя", callback_data=_pex_cb(batch_id, "set_winner")
                ),
                InlineKeyboardButton(
                    text="💰 Сменить цену", callback_data=_pex_cb(batch_id, "set_price")
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔗 Сменить ссылку", callback_data=_pex_cb(batch_id, "set_link")
                ),
                InlineKeyboardButton(text="♻️ Сброс", callback_data=_pex_cb(batch_id, "reset")),
            ],
            [
                InlineKeyboardButton(text="🧙 Мастер", callback_data=_pex_cb(batch_id, "wizard")),
                InlineKeyboardButton(
                    text="🔄 Обновить", callback_data=_pex_cb(batch_id, "refresh")
                ),
            ],
        ]
    )
    return ("\n".join(lines), kb)


async def _safe_edit(message: Message, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        raise


HOWMAX_TEXT = (
    "Регистрируетесь в боте <b>@RomanticClubBot</b>, нажимаете кнопку <b>Старт</b> и ждёте результат.\n"
    "Вам придут данные владельца/покупателя.\n"
    "Если возникнет ошибка, с вами свяжется админ.\n"
    "Обычно срок ожидания <b>одни сутки</b>."
)


def _pick_media_file(message: types.Message):
    """
    Возвращает (kind, file) где file имеет .file_id и .file_unique_id
    Поддержка: photo, video, animation, document, audio, voice, sticker
    """
    # Фото: берём самое большое
    if message.photo:
        return "photo", message.photo[-1]

    if message.video:
        return "video", message.video

    if message.animation:
        return "animation", message.animation

    # Часто mp4 присылают "как файл"
    if message.document:
        mt = (message.document.mime_type or "").lower()
        if mt.startswith("video/"):
            return "document(video)", message.document
        return "document", message.document

    if message.audio:
        return "audio", message.audio

    if message.voice:
        return "voice", message.voice

    if message.sticker:
        return "sticker", message.sticker

    return None, None


router = Router(name=__name__)


@router.message(
    (F.text.startswith("/card_video") | F.caption.startswith("/card_video")),
    F.chat.type == "private",
)
@admin_only
async def cmd_card_video(message: Message):
    raw = (message.text or message.caption or "").strip()
    parts = raw.split()

    if len(parts) < 2:
        await message.answer(
            "Формат: <code>/card_video CARD_ID</code>\n"
            "Команду пиши в подписи к видео или реплаем на видео.",
            parse_mode="HTML",
        )
        return

    try:
        card_id = int(parts[1])
    except Exception:
        await message.answer("CARD_ID должен быть числом.", parse_mode="HTML")
        return

    card = await get_card_by_id(card_id)
    if not card:
        await message.answer(f"Карта <code>{card_id}</code> не найдена.", parse_mode="HTML")
        return

    src = message
    media = _extract_video_from_message(src)

    if not media and message.reply_to_message:
        src = message.reply_to_message
        media = _extract_video_from_message(src)

    if not media:
        await message.answer(
            "Пришли видео с подписью <code>/card_video CARD_ID</code>\n"
            "или ответь командой на сообщение с видео.",
            parse_mode="HTML",
        )
        return

    file_id, unique_id, thumb_id = media

    res = await set_card_video_by_id(
        card_id=card_id,
        video_file_id=file_id,
        unique_id=unique_id,
        thumb_file_id=thumb_id,
    )

    if not res.get("ok"):
        await message.answer(f"Не вышло: <code>{res.get('reason')}</code>", parse_mode="HTML")
        return

    note = ""
    if not res.get("has_media_type"):
        note = "\n\n⚠️ В БД нет <code>cards.media_type</code>. Я записал <code>image_id</code>, но публикация видео не заработает, пока не добавишь колонку и логику отправки видео."

    await message.answer(
        "✅ Видео привязано.\n\n"
        f"🃏 Карта: <code>{res['card_id']}</code>\n"
        f"👤 Герой: <b>{res['hero_name'] or '-'}</b>\n"
        f"🪪 Название: <b>{res['card_name'] or '-'}</b>\n\n"
        f"Обновлено:\n"
        f"• cards: <b>{res['card_updated']}</b>\n"
        f"• auctions (строго): <b>{res['auctions_updated_strict']}</b>\n"
        f"• auctions (fallback): <b>{res['auctions_updated_fallback']}</b>"
        f"{note}",
        parse_mode="HTML",
    )
    # ✅ логи + аудит
    try:
        log_text = (
            "🎞️ <b>Видео на карте установлено</b>\n"
            f"Админ: <b>{admin_tag(message.from_user)}</b>\n"
            f"Карта: <code>{res['card_id']}</code> • "
            f"<b>{_h(res.get('hero_name') or '-')}</b> — <b>{_h(res.get('card_name') or '-')}</b>\n"
            f"file_id: <code>{_h(_short_media(file_id))}</code>\n"
            f"cards: <b>{res.get('card_updated')}</b>\n"
            f"auctions(strict): <b>{res.get('auctions_updated_strict')}</b>\n"
            f"auctions(fallback): <b>{res.get('auctions_updated_fallback')}</b>"
        )
        await send_admin_log(message.bot, log_text)

        await log_audit_action(
            user_id=message.from_user.id,
            action_type="set_card_video",
            auction_id=None,
            details=(
                f"card_id={res.get('card_id')} file_id={file_id} "
                f"strict={res.get('auctions_updated_strict')} fallback={res.get('auctions_updated_fallback')}"
            ),
        )
    except Exception:
        # логи не должны ломать команду
        pass


@router.message(Command("fileid"), F.chat.type == "private")
@admin_only
async def cmd_fileid(message: types.Message):
    src = message.reply_to_message or message
    fid = extract_media_file_id(src)
    if not fid:
        await message.answer(
            "Нет медиа в сообщении (пришли/перешли видео или ответь /fileid на видео)."
        )
        return
    await message.answer(f"file_id:\n<code>{fid}</code>", parse_mode="HTML")


@router.message(F.text.regexp(r"^/print_ex\s+\d+$"))
@admin_only
async def cmd_print_ex(message: Message):
    batch_id = int(message.text.split()[1])

    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await message.answer("Не нашёл заявку биржи с таким batch_id.")
        return

    text_view, kb = await _build_print_ex_view(batch_id)
    await message.answer(text_view, parse_mode="HTML", reply_markup=kb)

    # ✅ логи + аудит
    try:
        await send_admin_log(
            message.bot,
            (
                "🧾 <b>Биржа: открыт /print_ex</b>\n"
                f"🕒 {datetime.now(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M:%S')} (МСК)\n"
                f"Админ: <b>{admin_tag(message.from_user)}</b> (id: {message.from_user.id})\n"
                f"Batch: <code>{batch_id}</code>\n"
                "Действие: <code>exchange_print_ex_open</code>"
            ),
        )
    except Exception:
        pass

    try:
        await log_audit_action(
            user_id=message.from_user.id,
            action_type="exchange_print_ex_open",
            auction_id=None,
            details=f"batch_id={batch_id}",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith(f"{PEX_PREFIX}|"))
@admin_only
async def cb_print_ex(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    _, bid_s, action = split_callback_data(call.data or "", "|", 2)
    batch_id = int(bid_s)
    admin_id = int(call.from_user.id)

    if action == "refresh":
        text, kb = await _build_print_ex_view(batch_id)
        await _safe_edit(call.message, text, kb)
        await call.answer()
        return

    if action == "reset":
        await reset_exchange_manual(batch_id, admin_id)
        text, kb = await _build_print_ex_view(batch_id)
        await _safe_edit(call.message, text, kb)
        await call.answer("Сброшено")
        return

    if action in {"set_winner", "set_price", "set_link", "wizard"}:
        await state.update_data(
            pex_batch_id=batch_id,
            pex_chat_id=call.message.chat.id,
            pex_msg_id=call.message.message_id,
        )

        if action in {"set_winner", "wizard"}:
            await state.set_state(PrintExFSM.winner)
            await call.message.answer(
                "Пришли <b>победителя</b>: <code>user_id</code> или <code>@username</code> (можно форвард)."
            )
            await call.answer()
            return

        if action == "set_price":
            await state.set_state(PrintExFSM.price)
            await call.message.answer("Пришли <b>цену</b> числом (например <code>500</code>).")
            await call.answer()
            return

        if action == "set_link":
            await state.set_state(PrintExFSM.link)
            await call.message.answer("Пришли <b>ссылку</b> на биржу (или текст).")
            await call.answer()
            return

    if action == "send_both":
        batch = await get_exchange_batch_by_id(batch_id)
        if not batch:
            await call.answer("Не найдено", show_alert=True)
            return

        # 1) ЛОЧИМ сразу
        locked = await mark_exchange_manual_sent(batch_id)
        if not locked:
            await call.answer("Уже разослано (кто-то успел раньше).", show_alert=True)
            return

        owner_id = int(batch["user_id"])
        owner = await get_user(owner_id)
        owner_username = (owner.get("username") if owner else None) or None

        winner_id = int(batch.get("manual_winner_id") or 0) or None
        w_un = (batch.get("manual_winner_username") or "").strip() or None
        if not winner_id and not w_un:
            await call.answer("Сначала выставь победителя", show_alert=True)
            return

        price = batch.get("manual_price")
        if price is None:
            price = batch.get("price")
        price = int(price or 0)

        currency = (batch.get("manual_currency") or batch.get("currency") or "diamonds").strip()
        cur_emoji = currency_to_emoji(currency)
        price_line = f"<b>{price}</b> {cur_emoji}" if price else f"— {cur_emoji}"

        link = (batch.get("manual_link") or "").strip()
        link_line = html.escape(link) if link else "—"

        moderator_tag = admin_tag(call.from_user)
        thanks_kb = await build_thanks_kb(int(batch_id), moderator_tag)

        owner_ref = _safe_user_mention(owner_id, owner_username, title="владелец")
        winner_ref = _safe_user_mention(winner_id, w_un, title="покупатель")

        text_owner = (
            f"✅ <b>Биржа</b> • лот <code>{batch_id}</code> продан\n\n"
            f"Покупатель: {winner_ref}\n"
            f"Цена: {price_line}\n"
            f"Ссылка: {link_line}\n\n"
            f"Модератор: {moderator_tag}"
        )

        text_winner = (
            f"🎉 <b>Биржа</b> • ты выбран победителем по лоту <code>{batch_id}</code>\n\n"
            f"Владелец: {owner_ref}\n"
            f"Цена: {price_line}\n"
            f"Ссылка: {link_line}\n\n"
            f"Модератор: {moderator_tag}"
        )

        ok_owner = True
        try:
            await bot.send_message(
                owner_id,
                text_owner,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=thanks_kb,
            )
        except Exception:
            ok_owner = False

        ok_winner = True
        if winner_id:
            try:
                await bot.send_message(
                    int(winner_id),
                    text_winner,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=thanks_kb,
                )
            except Exception:
                ok_winner = False
        else:
            ok_winner = False

        if ok_owner and ok_winner:
            await call.answer("Отправлено владельцу и покупателю.")
        elif ok_owner and not ok_winner:
            await call.answer(
                "Владельцу ушло. Покупателю не дошло (скорее всего не писал боту /start). Лот закрыт.",
                show_alert=True,
            )
        else:
            await call.answer(
                "Не удалось отправить владельцу. Лот закрыт, проверь вручную.", show_alert=True
            )

        text, kb = await _build_print_ex_view(batch_id)
        await _safe_edit(call.message, text, kb)
        return


@router.message(Command("howmax"))
async def howmax_cmd(message: types.Message) -> None:
    # чтобы не спамили все подряд в чатах: в группах/каналах только админы из вашей таблицы admins
    if message.chat.type != "private":
        if not await is_admin(message.from_user.id):
            return

    await message.answer(HOWMAX_TEXT, parse_mode="HTML")


@router.message(PrintExFSM.winner)
@admin_only
async def pex_set_winner(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    batch_id = int(data["pex_batch_id"])
    admin_id = int(message.from_user.id)

    winner_id: int | None = None
    winner_un: str | None = None

    if message.forward_from and message.forward_from.id:
        winner_id = int(message.forward_from.id)
        winner_un = (message.forward_from.username or "").strip() or None
    else:
        t = (message.text or "").strip()
        if t.startswith("@"):
            u = await get_user_by_username(t[1:])
            if u:
                winner_id = int(u["user_id"])
                winner_un = (u.get("username") or "").strip() or None
        elif t.isdigit():
            winner_id = int(t)

    if not winner_id:
        await message.answer(
            "⚠️ Не понял победителя. Пришли <code>user_id</code>, <code>@username</code> или форвард."
        )
        return

    await set_exchange_manual_winner(batch_id, winner_id, winner_un, admin_id)

    # если это wizard — сразу попросим цену
    await state.set_state(PrintExFSM.price)
    await message.answer("Ок. Теперь пришли <b>цену</b> числом (например <code>500</code>).")


@router.message(PrintExFSM.price)
@admin_only
async def pex_set_price(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    batch_id = int(data["pex_batch_id"])
    admin_id = int(message.from_user.id)

    t = (message.text or "").strip()
    if not t.isdigit():
        await message.answer("⚠️ Цена должна быть числом.")
        return

    await set_exchange_manual_price(batch_id, int(t), admin_id)

    await state.set_state(PrintExFSM.link)
    await message.answer("Ок. Теперь пришли <b>ссылку</b> (или напиши <code>пропустить</code>).")


@router.message(PrintExFSM.link)
@admin_only
async def pex_set_link(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    batch_id = int(data["pex_batch_id"])
    admin_id = int(message.from_user.id)

    t = (message.text or "").strip()
    link = None if t.lower() in {"пропустить", "skip", "-"} else t

    await set_exchange_manual_link(batch_id, link, admin_id)

    # обновим меню
    text, kb = await _build_print_ex_view(batch_id)
    chat_id = int(data["pex_chat_id"])
    msg_id = int(data["pex_msg_id"])
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise

    await state.clear()
    await message.answer("✅ Ручной итог сохранён. Жми «Отправить обоим» в меню /print_ex.")


@router.message(F.text.in_({"/id", "/fid"}))
async def cmd_id(message: types.Message):
    # Команда должна быть ответом на сообщение с медиа
    target = message.reply_to_message
    if not target:
        await message.answer("Реплаем на сообщение с медиа и жми /id.")
        return

    kind, f = _pick_media_file(target)
    if not f:
        await message.answer("В реплае нет медиа (или оно слишком экзотическое даже для Telegram).")
        return

    # Иногда хочется знать mime_type и размер
    mime = getattr(f, "mime_type", None)
    size = getattr(f, "file_size", None)
    name = getattr(f, "file_name", None)

    lines = [
        f"🎞 Тип: <b>{kind}</b>",
        f"🧩 file_id:\n<code>{f.file_id}</code>",
        f"🧷 file_unique_id:\n<code>{f.file_unique_id}</code>",
    ]
    if mime:
        lines.append(f"🧬 mime: <code>{mime}</code>")
    if name:
        lines.append(f"📎 name: <code>{name}</code>")
    if size is not None:
        lines.append(f"📦 size: <code>{size}</code>")

    await message.answer("\n\n".join(lines), parse_mode="HTML")


@router.callback_query(F.data == "exmod:back")
@admin_only
async def ex_back_to_moderation(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.message.answer(
        "Выберите действие модерации:",
        reply_markup=menu_keyboard(
            ["🤝 Доверить пользователя", "❌ Снять доверие"],
            ["➕ Добавить админа", "➖ Удалить админа"],
            ["📝 Заявки на модерацию", "🗂️ Заявки на удаление"],
            ["💰 Экономика", "🆘 Обращения"],
            ["📅 Расписание", "🛒 Биржа"],
            ["📝 Редактировать расписание"],
            ["⬅️ Назад"],
        ),
    )
    await call.answer()


@router.callback_query(F.data.startswith(f"{EX1_APPROVE}|"))
@admin_only
async def ex1_approve(call: CallbackQuery):
    batch_id = int(split_callback_data(call.data or "", "|", 1)[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка уже не найдена.", show_alert=True)
        return

    try:
        service = await ExchangeService.create()
        batch = await service.approve(
            batch_id,
            moderator_id=call.from_user.id,
            moderator_username=call.from_user.username or call.from_user.full_name,
        )
    except InvalidExchangeTransition as exc:
        await call.answer(f"Заявка уже обработана: {exc.current}.", show_alert=True)
        return

    # лог (если хочешь, можно оставить)
    try:
        await _log_exchange_batch_action(
            call.bot,
            action_type="exchange_approve",
            admin_user=call.from_user,
            batch_id=batch_id,
            status="approved",
        )
    except Exception:
        pass

    # уведомление юзеру (коротко)
    try:
        user_id = int(batch.get("user_id") or 0)
        if user_id:
            thanks_kb = await build_thanks_kb(int(batch_id), admin_tag(call.from_user))
            await call.bot.send_message(
                user_id,
                f"✅ Ваша заявка на биржу <code>{batch_id}</code> одобрена.",
                parse_mode="HTML",
                reply_markup=thanks_kb,
            )
    except Exception:
        pass

    await call.answer("Одобрено ✅")

    try:
        await call.message.delete()
    except Exception:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    await show_pending_exchange_one(call.message)


@router.callback_query(F.data.startswith(f"{EX1_DELETE}|"))
@admin_only
async def ex1_delete_ask(call: CallbackQuery):
    batch_id = int(split_callback_data(call.data or "", "|", 1)[1])
    await call.answer()
    try:
        await call.message.edit_reply_markup(
            reply_markup=build_exchange_one_delete_confirmation(batch_id)
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith(f"{EX1_DEL_NO}|"))
@admin_only
async def ex1_delete_no(call: CallbackQuery):
    batch_id = int(split_callback_data(call.data or "", "|", 1)[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка уже не найдена.", show_alert=True)
        return

    proof_id = (batch.get("proof_photo_id") or "").strip()
    has_proof = bool(proof_id) and proof_id.upper() != "NO_PROOF"

    await call.answer("Ок, не удаляем")
    try:
        await call.message.edit_reply_markup(
            reply_markup=build_exchange_one_keyboard(batch_id, has_proof=has_proof)
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith(f"{EX1_DEL_YES}|"))
@admin_only
async def ex1_delete_yes(call: CallbackQuery):
    batch_id = int(split_callback_data(call.data or "", "|", 1)[1])
    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await call.answer("Заявка уже не найдена.", show_alert=True)
        return

    # если уже публиковали пост биржи, попробуем снести
    posted_chat_id = batch.get("posted_chat_id")
    posted_message_id = batch.get("posted_message_id")
    if posted_chat_id and posted_message_id:
        try:
            await call.bot.delete_message(int(posted_chat_id), int(posted_message_id))
        except Exception:
            pass

    service = await ExchangeService.create()
    await service.delete(
        batch_id,
        moderator_id=call.from_user.id,
        moderator_username=call.from_user.username or call.from_user.full_name,
        comment="deleted",
    )

    # лог
    try:
        await _log_exchange_batch_action(
            call.bot,
            action_type="exchange_delete",
            admin_user=call.from_user,
            batch_id=batch_id,
            status="deleted",
        )
    except Exception:
        pass

    # уведомим юзера
    try:
        user_id = int(batch.get("user_id") or 0)
        if user_id:
            await call.bot.send_message(
                user_id,
                f"🗑 Ваша заявка на биржу <code>{batch_id}</code> удалена модератором.",
                parse_mode="HTML",
            )
    except Exception:
        pass

    await call.answer("Удалено 🗑")

    try:
        await call.message.delete()
    except Exception:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    await show_pending_exchange_one(call.message)


@router.callback_query(F.data.startswith(f"{EX1_REJECT}|"))
@admin_only
async def ex1_reject_start(call: CallbackQuery, state: FSMContext):
    batch_id = int(split_callback_data(call.data or "", "|", 1)[1])
    await state.update_data(
        ex1_reject_batch_id=batch_id,
        ex1_origin_chat_id=call.message.chat.id,
        ex1_origin_msg_id=call.message.message_id,
    )
    await state.set_state(ExchangeOneRejectFSM.waiting_for_reason)
    await call.answer()
    await call.message.answer("Напиши причину отклонения заявки на биржу:")


@router.message(ExchangeOneRejectFSM.waiting_for_reason, F.chat.type == "private")
@admin_only
async def ex1_reject_reason(message: types.Message, state: FSMContext):
    data = await state.get_data()
    batch_id = int(data.get("ex1_reject_batch_id") or 0)
    reason = (message.text or "").strip()

    if not batch_id or not reason:
        await message.answer("Нужна причина текстом.")
        return

    batch = await get_exchange_batch_by_id(batch_id)
    if not batch:
        await message.answer("Заявка не найдена или уже обработана.")
        await state.clear()
        return

    try:
        service = await ExchangeService.create()
        batch = await service.reject(
            batch_id,
            moderator_id=message.from_user.id,
            moderator_username=message.from_user.username or message.from_user.full_name,
            comment=reason,
        )
    except InvalidExchangeTransition as exc:
        await message.answer(f"Заявка уже обработана: {exc.current}.")
        await state.clear()
        return

    # лог
    try:
        await _log_exchange_batch_action(
            message.bot,
            action_type="exchange_reject",
            admin_user=message.from_user,
            batch_id=batch_id,
            status="rejected",
        )
    except Exception:
        pass

    # уведомление юзеру
    try:
        user_id = int(batch.get("user_id") or 0)
        if user_id:
            thanks_kb = await build_thanks_kb(int(batch_id), admin_tag(message.from_user))
            await message.bot.send_message(
                user_id,
                f"❌ Ваша заявка на биржу <code>{batch_id}</code> отклонена.\n"
                f"Причина: <i>{html.escape(reason)}</i>",
                parse_mode="HTML",
                reply_markup=thanks_kb,
            )
    except Exception:
        pass

    # удаляем старое сообщение с заявкой (если сможем)
    try:
        chat_id = int(data.get("ex1_origin_chat_id"))
        msg_id = int(data.get("ex1_origin_msg_id"))
        await message.bot.delete_message(chat_id, msg_id)
    except Exception:
        pass

    await state.clear()
    await message.answer(f"Отклонено ❌ (Batch {batch_id})")

    # показываем следующую
    await show_pending_exchange_one(message)


__all__ = [
    "router",
    "cmd_card_video",
    "cmd_fileid",
    "cmd_print_ex",
    "cb_print_ex",
    "howmax_cmd",
    "pex_set_winner",
    "pex_set_price",
    "pex_set_link",
    "cmd_id",
    "ex_back_to_moderation",
    "ex1_approve",
    "ex1_delete_ask",
    "ex1_delete_no",
    "ex1_delete_yes",
    "ex1_reject_start",
    "ex1_reject_reason",
]
