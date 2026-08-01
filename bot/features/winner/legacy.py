"""Legacy winner preview callbacks and print-win orchestration."""

from __future__ import annotations

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message

from bot.core.legacy_config import legacy_config
from bot.repositories import winner as winner_repository
from db.pool import get_db_pool

from .common import (
    CB_WIN_REFRESH,
    PENDING_EDIT,
    PENDING_WIN_MANUAL,
    WIN_DRAFTS,
    _admin_tag,
    _build_channel_link,
    _cb_last_int,
    _emoji_by_currency,
    _fmt_msk,
    _get_owners,
    _kb_winner_actions,
    _log_admin,
    _mention,
    _msk_now,
    _norm_username,
    _user_links_html,
    get_user,
    get_user_by_username,
)
from .notifications import _send_win_dm_to_targets
from .presentation import (
    _edit_print_win_menu,
    _resolve_user_ref,
    _send_print_win_menu,
    _upsert_manual_result,
)
from .resolution import _winner_preview_text


async def cmd_print_win(message: Message, bot: Bot):
    if message.from_user.id not in legacy_config.ADMINS:
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


async def cb_win_edit_amt(call: types.CallbackQuery):
    await call.answer()
    try:
        _, _, aid_s, wid_s = call.data.split(":")
        auction_id = int(aid_s)
        winner_id = int(wid_s)
    except Exception:
        await call.message.answer("❌ Неверные данные кнопки.")
        return
    PENDING_EDIT[call.from_user.id] = {
        "auction_id": auction_id,
        "field": "amount",
        "winner_id": winner_id,
    }
    await call.message.answer(
        f"✎ Введите новую сумму ставки для лота <code>{auction_id}</code> (число).",
        parse_mode="HTML",
    )


async def cb_win_edit_user(call: types.CallbackQuery):
    await call.answer()
    try:
        _, _, aid_s, _ = call.data.split(":")
        auction_id = int(aid_s)
    except Exception:
        await call.message.answer("❌ Неверные данные кнопки.")
        return

    PENDING_EDIT[call.from_user.id] = {"auction_id": auction_id, "field": "winner"}
    await call.message.answer(
        f"👤 Пришлите нового победителя для лота <code>{auction_id}</code> в формате @username или числовой id.",
        parse_mode="HTML",
    )


async def handle_pending_edit(message: types.Message, bot: Bot):
    ctx = PENDING_EDIT.pop(message.from_user.id, None)
    if not ctx:
        return
    auction_id = ctx["auction_id"]
    fld = ctx["field"]

    d = WIN_DRAFTS.get(auction_id, {})
    admin_user = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else f"id{message.from_user.id}"
    )

    if fld == "amount":
        txt = (message.text or "").strip().replace(" ", "").lower().replace("к", "k")
        if txt.endswith("k"):
            try:
                val = int(txt[:-1]) * 1000
            except:
                await message.answer("❌ Неверное число.")
                return
        else:
            if not txt.isdigit():
                await message.answer("❌ Неверное число.")
                return
            val = int(txt)

        # валидация по валюте
        cur = (
            await winner_repository.get_auction_currency(
                await get_db_pool(),
                auction_id,
            )
            or ""
        ).lower()
        if cur in {"алмазы", "diamond", "diamonds"} and val % 10 != 0:
            await message.answer("Для алмазов ставка должна быть кратной 10.")
            return
        if cur in {"чашки", "cups"} and val % 2 != 0:
            await message.answer("Для чашек ставка должна быть чётной.")
            return

        d["amount"] = val
        WIN_DRAFTS[auction_id] = d

        # актуальный победитель
        b = await winner_repository.get_top_bid(await get_db_pool(), auction_id)
        wid = int(d.get("winner_id") or (b["bidder_id"] if b else 0))
        preview = await _winner_preview_text(auction_id, val, wid)

        await message.answer("✔︎ Стоимость обновлена в черновике.", parse_mode="HTML")
        await message.answer(
            preview,
            parse_mode="HTML",
            reply_markup=_kb_winner_actions(auction_id, wid),
            disable_web_page_preview=True,
        )

        await _log_admin(
            bot,
            f"✎ Админ {admin_user} установил ставку <b>{val}</b> в черновике для лота <b>{auction_id}</b>.",
        )

    elif fld == "winner":
        raw = (message.text or "").strip()
        wid = None
        if raw.startswith("@"):
            user = await get_user_by_username(raw.lstrip("@"))
            if user:
                wid = int(user["user_id"])
        elif raw.isdigit():
            wid = int(raw)
        if not wid:
            await message.answer("❌ Пользователь не найден.")
            return

        d["winner_id"] = wid
        WIN_DRAFTS[auction_id] = d

        b = await winner_repository.get_top_bid(await get_db_pool(), auction_id)
        amt = int(d.get("amount") or (b["amount"] if b else 0))
        preview = await _winner_preview_text(auction_id, amt, wid)

        await message.answer("✔︎ Победитель обновлён в черновике.", parse_mode="HTML")
        await message.answer(
            preview,
            parse_mode="HTML",
            reply_markup=_kb_winner_actions(auction_id, wid),
            disable_web_page_preview=True,
        )

        await _log_admin(
            bot,
            f"👤 Админ {admin_user} сменил победителя на <code>{wid}</code> в черновике для лота <b>{auction_id}</b>.",
        )


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

    ok, fail, deliveries, used_amount = await _send_notifications(
        bot, auction_id, winner_id, override_amount=override_amount
    )

    now_str = _fmt_msk(_msk_now())
    cur_emoji = _emoji_by_currency(
        await winner_repository.get_auction_currency(
            await get_db_pool(),
            auction_id,
        )
    )

    lines = [
        f"📨 Рассылка по лоту <b>{auction_id}</b> завершена ({now_str} МСК).",
        f"Ставка: <b>{used_amount} {cur_emoji}</b>",
        f"Успешно: <b>{ok}</b>  Ошибок: <b>{fail}</b>",
        "",
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
    for chat_id in legacy_config.ADMIN_LOG_CHATS:
        try:
            await call.bot.send_message(
                chat_id, report_text, parse_mode="HTML", disable_web_page_preview=True
            )
        except Exception:
            pass


async def cb_winner_skip(call: types.CallbackQuery, bot: Bot):
    await call.answer("Рассылка отменена.")
    try:
        _, _, aid_s, wid_s = call.data.split(":")
        auction_id = int(aid_s)
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
            f"⛔ Рассылка по лоту <b>{auction_id}</b> отменена админом.", parse_mode="HTML"
        )
    except Exception:
        pass

    admin_user = (
        f"@{call.from_user.username}" if call.from_user.username else f"id{call.from_user.id}"
    )
    await _log_admin(
        bot,
        f"⛔ Админ {admin_user} отменил рассылку по лоту <b>{auction_id}</b> "
        f"(winner={winner_id}, amount={used_amount if used_amount is not None else '—'}).",
    )


async def _send_notifications(
    bot: Bot, auction_id: int, winner_id: int, *, override_amount: int | None = None
) -> tuple[int, int, list[dict], int]:
    a = (
        await winner_repository.get_auction_summary(
            await get_db_pool(),
            auction_id,
        )
        or {}
    )

    cur_emoji = _emoji_by_currency(a.get("currency"))
    link = _build_channel_link(a.get("message_id")) or "(ссылка недоступна)"
    lot_line = (a.get("hero_name") or "-") + (
        f" — {a.get('card_name')}" if a.get("card_name") else ""
    )

    has_winner = int(winner_id or 0) > 0

    w = {}
    wname = "—"
    winner_links_line = ""

    if has_winner:
        w = await get_user(int(winner_id)) or {}
        wname = _mention(int(winner_id), w.get("username"))

        # Если username нет — добавляем “3 ссылки” (на деле 2 tg:// + (t.me если вдруг есть))
        if _norm_username(w.get("username")) is None:
            winner_links_line = (
                f"\nСсылки победителя: {_user_links_html(int(winner_id), w.get('username'))}"
            )

    owners = await _get_owners(auction_id)
    owners_mentions = ", ".join(_mention(o["user_id"], o.get("username")) for o in owners) or "—"

    if override_amount is not None:
        amount = int(override_amount)
    else:
        b = await winner_repository.get_top_bid(
            await get_db_pool(),
            auction_id,
            latest_on_tie=True,
        )
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
            await bot.send_message(
                int(winner_id), text_common, parse_mode="HTML", disable_web_page_preview=True
            )
            ok += 1
            deliveries.append(
                {
                    "role": "winner",
                    "user_id": int(winner_id),
                    "username": w.get("username"),
                    "ok": True,
                    "err": "",
                }
            )
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            fail += 1
            deliveries.append(
                {
                    "role": "winner",
                    "user_id": int(winner_id),
                    "username": w.get("username"),
                    "ok": False,
                    "err": str(e),
                }
            )
        except Exception as e:
            fail += 1
            deliveries.append(
                {
                    "role": "winner",
                    "user_id": int(winner_id),
                    "username": w.get("username"),
                    "ok": False,
                    "err": repr(e),
                }
            )
    else:
        deliveries.append(
            {"role": "winner", "user_id": 0, "username": None, "ok": False, "err": "no_winner"}
        )

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
        uid = int(o["user_id"])
        uname = o.get("username")
        try:
            await bot.send_message(
                uid,
                text_for_owners,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            ok += 1
            deliveries.append(
                {"role": "owner", "user_id": uid, "username": uname, "ok": True, "err": ""}
            )
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            fail += 1
            deliveries.append(
                {"role": "owner", "user_id": uid, "username": uname, "ok": False, "err": str(e)}
            )
        except Exception as e:
            fail += 1
            deliveries.append(
                {"role": "owner", "user_id": uid, "username": uname, "ok": False, "err": repr(e)}
            )

    return ok, fail, deliveries, amount


async def cb_print_win_refresh(call: types.CallbackQuery):
    try:
        auction_id = _cb_last_int(call.data)
    except Exception:
        await call.answer("❌ Неверные данные", show_alert=True)
        return

    await call.answer()
    await _edit_print_win_menu(call, auction_id)


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

    cur_emoji = _emoji_by_currency(
        await winner_repository.get_auction_currency(
            await get_db_pool(),
            auction_id,
        )
    )

    lines = [
        f"👑 Рассылка владельцу по лоту <b>{auction_id}</b> завершена.",
        f"Ставка: <b>{(used_amount or 0)} {cur_emoji}</b>",
        f"Успешно: <b>{ok}</b>  Ошибок: <b>{fail}</b>",
        "",
    ]

    for d in deliveries:
        tag = "👑" if d["role"] == "owner" else "⚠️"
        uname = (
            ("@" + d["username"])
            if d.get("username")
            else (f"id{d['user_id']}" if d.get("user_id") else "—")
        )
        pin_mark = " 📌" if d.get("pinned") else ""
        lines.append(
            f"{tag} {uname} — {'OK' if d['ok'] else ('FAIL: ' + (d['err'] or '')[:120])}{pin_mark}"
        )

    report_text = "\n".join(lines)
    await call.message.answer(report_text, parse_mode="HTML", disable_web_page_preview=True)
    await _edit_print_win_menu(call, auction_id)


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

    cur_emoji = _emoji_by_currency(
        await winner_repository.get_auction_currency(
            await get_db_pool(),
            auction_id,
        )
    )

    lines = [
        f"🏆 Рассылка победителю по лоту <b>{auction_id}</b> завершена.",
        f"Ставка: <b>{(used_amount or 0)} {cur_emoji}</b>",
        f"Успешно: <b>{ok}</b>  Ошибок: <b>{fail}</b>",
        "",
    ]

    for d in deliveries:
        tag = "🏆" if d["role"] == "winner" else "⚠️"
        uname = (
            ("@" + d["username"])
            if d.get("username")
            else (f"id{d['user_id']}" if d.get("user_id") else "—")
        )
        pin_mark = " 📌" if d.get("pinned") else ""
        lines.append(
            f"{tag} {uname} — {'OK' if d['ok'] else ('FAIL: ' + (d['err'] or '')[:120])}{pin_mark}"
        )

    report_text = "\n".join(lines)
    await call.message.answer(report_text, parse_mode="HTML", disable_web_page_preview=True)
    await _edit_print_win_menu(call, auction_id)


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

    cur_emoji = _emoji_by_currency(
        await winner_repository.get_auction_currency(
            await get_db_pool(),
            auction_id,
        )
    )

    lines = [
        f"📨 Рассылка ОБОИМ по лоту <b>{auction_id}</b> завершена.",
        f"Ставка: <b>{(used_amount or 0)} {cur_emoji}</b>",
        f"Успешно: <b>{ok}</b>  Ошибок: <b>{fail}</b>",
        "",
    ]

    for d in deliveries:
        tag = "🏆" if d["role"] == "winner" else ("👑" if d["role"] == "owner" else "⚠️")
        uname = (
            ("@" + d["username"])
            if d.get("username")
            else (f"id{d['user_id']}" if d.get("user_id") else "—")
        )
        pin_mark = " 📌" if d.get("pinned") else ""
        lines.append(
            f"{tag} {uname} — {'OK' if d['ok'] else ('FAIL: ' + (d['err'] or '')[:120])}{pin_mark}"
        )

    await call.message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
    await _edit_print_win_menu(call, auction_id)


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
                await message.answer(
                    "❌ Цена должна быть числом (или <code>-</code>).", parse_mode="HTML"
                )
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
                message=message.bot._wrap_message(
                    message.chat, message.message_id, message
                ),  # запасной путь
                data=f"{CB_WIN_REFRESH}:{auction_id}",
            )
        except Exception:
            fake_call = None

        # просто отправим новое меню (надёжнее, чем пляски вокруг edit через fake_call)
        await _send_print_win_menu(message, auction_id)

        admin_user = _admin_tag(message.from_user)
        await _log_admin(
            bot, f"✍️ Админ {admin_user} задал ручной итог для лота <b>{auction_id}</b>."
        )

        PENDING_WIN_MANUAL.pop(message.from_user.id, None)
        return
