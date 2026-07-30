"""Moderator diagnostics, proof, and exchange commands.

Handlers retain their relative order from the legacy ``moderation`` module.
"""

from bot.handlers.admin.moderation_shared import *  # noqa: F403
from bot.services.admin_diagnostics import AdminDiagnosticsQueries

router = Router(name=__name__)


@router.message(Command("lux_wait"), F.chat.type.in_({"private", "group", "supergroup"}))
async def cmd_lux_wait(message: types.Message):
    queries = await AdminDiagnosticsQueries.create()
    rows = await queries.delayed_luxury_lots()
    logging.getLogger("auction_bot").info("/lux_wait rows=%s", len(rows or []))
    if not rows:
        await message.answer("Нет назначенных лотов у Лакшери с ожиданием больше 3 дней.")
        return
    MSK = tz.gettz("Europe/Moscow")
    now = datetime.now(MSK)

    def _to_msk(dt):
        return (dt.replace(tzinfo=MSK) if dt.tzinfo is None else dt.astimezone(MSK))

    out = ["<b>Назначенные лоты у Лакшери (> 3 дней ожидания):</b>"]
    for r in rows[:60]:
        st = _to_msk(r["start_time"])
        diff = st - now
        days, hours = diff.days, diff.seconds // 3600
        owner = ("@" + r["username"]) if r.get("username") else (r.get("full_name") or str(r["user_id"]))
        title = r.get("card_name") or "-"
        if r.get("hero_name"):
            title += f" ({r['hero_name']})"
        out.append(
            f"🃏 <b>{_html.escape(title)}</b>\n"
            f"👑 Владелец: {_html.escape(owner)}\n"
            f"⏰ {st.strftime('%d.%m.%Y %H:%M')} МСК • через {days} д {hours} ч"
        )
    await message.answer("\n\n".join(out), parse_mode="HTML")


@router.message(Command("lux_wait_dbg"), F.chat.type.in_({"private", "group", "supergroup"}))
async def cmd_lux_wait_dbg(message: types.Message):
    queries = await AdminDiagnosticsQueries.create()
    meta, count = await queries.database_overview()
    await message.answer(
        "<b>DB-диагностика</b>\n"
        f"База: <code>{meta['db']}</code>\n"
        f"Роль: <code>{meta['usr']}</code>\n"
        f"Хост: <code>{meta['host']}:{meta['port']}</code>\n"
        f"server_timezone: <code>{meta['tz']}</code>\n\n"
        f"Совпадений по фильтру: <b>{count}</b>",
        parse_mode="HTML"
    )


@router.message(Command("multi_auctions"), F.chat.type.in_({"private", "group", "supergroup"}))
async def cmd_multi_auctions(message: types.Message):
    user_id = message.from_user.id
    try:
        admin = await is_admin(user_id)
    except Exception:
        admin = False
    if not admin:
        try:
            if await is_luxury_user(user_id):
                await message.answer("Команда только для обычных пользователей. Лакшери — мимо. 👋")
                return
        except Exception:
            pass
    now_utc = utc_now()
    queries = await AdminDiagnosticsQueries.create()
    rows = await queries.owners_with_multiple_future_lots(after=now_utc)

    if not rows:
        await message.answer("Сейчас нет владельцев с более чем одной будущей заявкой.")
        return
    by_owner = {}
    for r in rows:
        oid = r["user_id"]
        owner = by_owner.setdefault(oid, {
            "cnt": r["cnt"],
            "username": r["username"],
            "full_name": r["full_name"],
            "items": []
        })
        owner["items"].append(r)
    owners_sorted = sorted(
        by_owner.values(),
        key=lambda x: (-x["cnt"], min(i["start_time"] for i in x["items"]))
    )[:10]
    out = ["<b>Владельцы с > 1 будущей заявкой:</b>"]
    now_msk = datetime.now(MSK)
    for owner in owners_sorted:
        name = ("@" + owner["username"]) if owner.get("username") else (owner.get("full_name") or "безымянный")
        out.append(f"👤 <b>{_html.escape(name)}</b> • заявок: <b>{owner['cnt']}</b>")
        for r in sorted(owner["items"], key=lambda x: x["start_time"])[:5]:
            st = _to_msk(r["start_time"])
            diff = st - now_msk
            days, hours = diff.days, diff.seconds // 3600
            title = r["card_name"] or "-"
            if r.get("hero_name"):
                title += f" ({r['hero_name']})"
            out.append(
                f" • 🃏 <b>{_html.escape(title)}</b>\n"
                f"   ⏰ {st.strftime('%d.%m.%Y %H:%M')} МСК • через {days} д {hours} ч"
            )
        out.append("")
    await message.answer("\n".join(out).strip(), parse_mode="HTML")


@router.message(Command("proof"), F.chat.type == "private")
@admin_only
async def proof_cmd(message: types.Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer("Используй: /proof <auction_id>\nПример: /proof 4331")
        return
    auction_id = int(arg)
    lot = await get_lot_by_id(auction_id)
    if not lot:
        await message.answer(f"Лот с auction_id={auction_id} не найден.")
        return
    proof_photo_id = lot.get("proof_photo_id")
    if not proof_photo_id:
        await message.answer(MSG_PHOTO_NOT_FOUND)
        return
    kb = build_back_keyboard(auction_id)
    caption = (
        f"{MSG_PHOTO_CONFIRM}\n\n"
        f"🎴 Лот №{auction_id}: <b>{(lot.get('card_name') or '-')}</b>\n"
        f"🧾 proof_photo_id:\n<code>{proof_photo_id}</code>"
    )
    try:
        await message.answer_photo(
            proof_photo_id,
            caption=caption,
            reply_markup=kb,
            parse_mode="HTML",
        )
    except TelegramAPIError as e:
        await message.answer(f"Не смог отправить proof-фото (возможно, file_id протух): {e}")
        return
    try:
        admin_tag = message.from_user.username or message.from_user.full_name
        await send_admin_log(
            message.bot,
            f"📸 <b>Просмотр подтверждения</b>\n"
            f"👮 Админ: @{admin_tag}\n"
            f"🎴 Лот №{auction_id}: {lot.get('card_name')}\n"
            f"🧾 proof_photo_id: <code>{proof_photo_id}</code>"
        )
        await log_audit_action(
            user_id=message.from_user.id,
            action_type="show_proof",
            auction_id=auction_id,
            details=f"Запрошено proof-фото для лота {auction_id}",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("pending_menu|"))
@admin_only
async def pending_menu_router(call: types.CallbackQuery):
    kind = call.data.split("|", 1)[1]
    if kind == "exchange":
        await show_pending_exchange_requests(call.message)
    else:
        await show_pendinglots(call.message)
    await call.answer()


@router.callback_query(F.data.startswith("ex_show_proof|"))
@admin_only
async def ex_show_proof(call: types.CallbackQuery):
    batch_id = int(call.data.split("|")[1])
    batch = await get_exchange_batch(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
        return
    proof = batch.get("proof_photo_id")
    if not proof:
        await call.answer("Фото подтверждения не найдено.", show_alert=True)
        return
    await call.message.answer_photo(
        proof,
        caption=f"📸 Фото подтверждения для заявки <code>{batch_id}</code>",
        parse_mode="HTML"
    )
    await call.answer()


@router.callback_query(F.data.startswith("ex_approve|"))
@admin_only
async def ex_approve(call: types.CallbackQuery):
    batch_id = int(call.data.split("|")[1])
    batch = await get_exchange_batch(batch_id)
    if not batch:
        await call.answer("Заявка не найдена.", show_alert=True)
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
    try:
        await call.bot.send_message(
            int(batch["user_id"]),
            f"✅ Ваша заявка на биржу <code>{batch_id}</code> одобрена.",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await call.message.answer(f"✅ Заявка <code>{batch_id}</code> одобрена.", parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("ex_reject|"))
@admin_only
async def ex_reject_start(call: types.CallbackQuery, state: FSMContext):
    batch_id = int(call.data.split("|")[1])
    await state.update_data(ex_batch_id=batch_id)
    await call.message.answer(f"Напиши причину отклонения заявки биржи <code>{batch_id}</code>:", parse_mode="HTML")
    await state.set_state(ModActionFSM.waiting_for_reject_exchange_reason)
    await call.answer()


@router.message(Command("user_dbg"))
async def cmd_user_dbg(message: types.Message, bot):
    if not await is_admin(message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /user_dbg @username")
        return

    username = parts[1].strip().lstrip("@")
    u = await get_user_by_username(username)
    if not u:
        await message.answer(f"Не нашёл пользователя @{username} в базе.")
        return

    uid = int(u["user_id"])

    # 1) бан?
    banned = False
    try:
        banned = await is_user_banned(uid)
    except Exception:
        pass

    # 2) подписки/чаты
    def _member_ok(m) -> bool:
        st = getattr(m, "status", None)
        return st in {"member", "administrator", "creator"}

    in_channel = False
    in_discussion = False

    try:
        m1 = await bot.get_chat_member(AUCTION_CHANNEL_ID, uid)
        in_channel = _member_ok(m1)
    except Exception:
        in_channel = False

    try:
        m2 = await bot.get_chat_member(DISCUSSION_CHAT_ID, uid)
        in_discussion = _member_ok(m2)
    except Exception:
        in_discussion = False

    # 3) сводка причин
    reasons = []
    if banned:
        reasons.append("⛔️ В БАНЕ (addlot запрещён)")
    if not in_channel:
        reasons.append("📢 НЕ подписан на канал")
    if not in_discussion:
        reasons.append("💬 НЕ состоит в чате обсуждения")

    lux = "да" if u.get("is_luxury") else "нет"
    trusted = "да" if u.get("is_trusted") else "нет"

    text = (
            f"👤 Проверка пользователя: <b>@{u.get('username') or username}</b>\n"
            f"id: <code>{uid}</code>\n"
            f"лакшери: <b>{lux}</b>\n"
            f"trusted: <b>{trusted}</b>\n\n"
            + (
                "✅ Блокеров для /addlot не вижу." if not reasons else "⚠️ Причины, почему /addlot может не пускать:\n- " + "\n- ".join(
                    reasons))
    )
    await message.answer(text, parse_mode="HTML")


__all__ = [
    "router",
    "cmd_lux_wait",
    "cmd_lux_wait_dbg",
    "cmd_multi_auctions",
    "proof_cmd",
    "pending_menu_router",
    "ex_show_proof",
    "ex_approve",
    "ex_reject_start",
    "cmd_user_dbg",
]
