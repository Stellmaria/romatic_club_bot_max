from __future__ import annotations

import html
import logging

from telethon import events

from bot.core.legacy_config import legacy_config
from bot.domain.auctions import (
    AuctionEnded,
    AuctionKind,
    AuctionKindNotBiddable,
    AuctionNotActive,
    AuctionNotFound,
    BidAlreadyRecorded,
    BidFormatError,
    BidNotFound,
    BidOwnershipError,
    BidRevisionWindowExpired,
    BidStepError,
    BidTooHigh,
    BidTooLow,
    BidderBanned,
    BidderNotEligible,
    Currency,
    UnsupportedCurrency,
    normalize_currency_choices,
    parse_bid_offer,
    reverse_maximum_for_currency,
)
from bot.domain.auctions.rules import minimum_next_bid
from bot.services.auction_bids import AuctionBidService
from userbot.autobid_engine import maybe_place_autobid, pop_local_autobid_action
from userbot.runtime import (
    ACCEPTED_BIDS,
    BOT_DELETED as _BOT_DELETED,
    BOT_DELETED_TTL as _BOT_DELETED_TTL,
    require_client as _require_client,
)
from userbot.services import (
    _fetch_auction_by_root,
    _fetch_best_bid,
    _fetch_best_bid_units,
    _fetch_max_bid,
    _get_root_id,
    _is_auction_active,
    _is_chat_admin,
    _is_direct_reply_to_root,
    _mention,
    _mute_1m,
    _now_ts,
    _post_rules_under_lot,
    _prune_missing_bid_messages,
    _remove_last_warnings,
    _resolve_autobid_mapping,
    _send_reply_or_plain,
    _try_bind_root_message,
    _try_parse_bid_amount,
    _user_link,
)


logger = logging.getLogger("userbot")

OOPS_EDIT_WINDOW_SEC = 60

async def on_new_message(event: events.NewMessage.Event):
    msg = event.message

    # -------------------------
    # 0) Корневой пост лота -> bind + правила (только если реально есть лот в БД)
    # -------------------------
    auction_id = await _try_bind_root_message(msg)
    if auction_id:
        await _post_rules_under_lot(int(msg.id))
        return

    # -------------------------
    # 1) Автоставки: проверяем маппинг по msg.id ДО любых фильтров msg.out
    # -------------------------
    mapped = await _resolve_autobid_mapping(int(msg.id))
    is_autobid_msg = bool(mapped)

    # -------------------------
    # 2) Базовые фильтры
    # -------------------------
    sender_id = getattr(msg, "sender_id", None)
    if not sender_id:
        return

    # Наши исходящие обычно игнорим,
    # НО для автоставки ждём гонку между событием, локальным кэшем и записью autobid_actions.
    if getattr(msg, "out", False) and not is_autobid_msg:
        mapped = await _resolve_autobid_mapping(int(msg.id), wait_for_race=True, attempts=8, delay=0.15)
        is_autobid_msg = bool(mapped)

    # Если это всё ещё не автоставка, но текст = чистое число, считаем это обычной исходящей ставкой юзербота.
    if getattr(msg, "out", False) and not is_autobid_msg:
        text_probe = (getattr(msg, "message", None) or "").strip()
        if _try_parse_bid_amount(text_probe) is None:
            return

    if getattr(msg, "sender_chat", None) is not None:
        return

    # Если это автоставка, фактический "участник" = target_user_id.
    bidder_id = int(mapped["target_user_id"]) if mapped else int(sender_id)
    actor_id = int(bidder_id) if is_autobid_msg else int(sender_id)
    actor_username = (mapped.get("target_username") if mapped else None) or None

    # is_admin нужен для модерации (удаления/мутов). Для автоставок модерацию выключаем.
    is_admin = True if is_autobid_msg else await _is_chat_admin(int(event.chat_id), int(sender_id))

    text_raw = (msg.message or "").strip()
    text_low = text_raw.lower()

    # -------------------------
    # 3) Админские команды (только для реальных людей, не для автоставок)
    # -------------------------
    if not is_autobid_msg:
        if text_low.startswith("/unwarn"):
            if not is_admin:
                return
            parts = text_raw.split()
            if len(parts) < 2:
                return
            uid = int(parts[1])
            n = int(parts[2]) if len(parts) >= 3 else 1
            left = await _remove_last_warnings(uid, n)
            await _send_reply_or_plain(
                f"✅ Преды сняты: {_user_link(uid)}. Теперь предов: <b>{left}</b>.",
                reply_to=_get_root_id(msg) or msg.id,
            )
            return

        if text_low.startswith("/recalc_lot"):
            if not is_admin:
                return
            parts = text_raw.split()
            if len(parts) < 2:
                return
            aid = int(parts[1])
            removed = await _prune_missing_bid_messages(aid)
            max_bid = await _fetch_max_bid(aid)
            await _send_reply_or_plain(
                f"♻️ Пересчёт лота <b>{aid}</b>: удалено “призрачных” ставок: <b>{removed}</b>.\n"
                f"Текущая максималка в БД: <b>{max_bid or 0}</b>.",
                reply_to=_get_root_id(msg) or msg.id,
            )
            return

    # -------------------------
    # 4) /oops ... (только для реальных людей, автоставки сюда не пускаем)
    # -------------------------
    if (not is_autobid_msg) and msg.reply_to_msg_id and (
            text_low.startswith("/oops")
            or text_low.startswith("oops")
            or text_low.startswith("опс")
            or text_low.startswith("упс")
            or text_low.startswith("макс отмена")
            or text_low.startswith("макс отменить")
    ):
        replacement = None
        for prefix in ("макс отменить", "макс отмена", "/oops", "oops", "опс", "упс"):
            if text_low.startswith(prefix):
                replacement = text_raw[len(prefix):].strip() or None
                break

        service = await AuctionBidService.create()
        try:
            revision = await service.revise_bid(
                bid_message_id=int(msg.reply_to_msg_id),
                actor_user_id=int(sender_id),
                new_bid_text=replacement,
                revision_window_seconds=OOPS_EDIT_WINDOW_SEC,
            )
        except BidNotFound:
            await _send_reply_or_plain(
                "⚠️ Не нашёл принятую ставку в сообщении, на которое дан ответ.",
                reply_to=_get_root_id(msg) or msg.reply_to_msg_id,
            )
            return
        except BidOwnershipError:
            await _send_reply_or_plain(
                "⛔ Исправлять или отменять можно только собственную ставку.",
                reply_to=_get_root_id(msg) or msg.reply_to_msg_id,
            )
            return
        except BidRevisionWindowExpired as exc:
            await _send_reply_or_plain(
                f"⏰ Исправление доступно только первые <b>{exc.seconds}</b> секунд после ставки.",
                reply_to=_get_root_id(msg) or msg.reply_to_msg_id,
            )
            return
        except (AuctionEnded, AuctionNotActive):
            await _send_reply_or_plain(
                "⏰ После завершения аукциона менять ставку нельзя.",
                reply_to=_get_root_id(msg) or msg.reply_to_msg_id,
            )
            return
        except BidTooLow as exc:
            await _send_reply_or_plain(
                f"⚠️ Исправленная ставка слишком мала. Минимум сейчас: <b>{exc.minimum}</b>.",
                reply_to=_get_root_id(msg) or msg.reply_to_msg_id,
            )
            return
        except BidTooHigh as exc:
            await _send_reply_or_plain(
                "⚠️ Для обратного аукциона исправленная ставка должна быть ниже. "
                f"Максимум сейчас: <b>{exc.maximum}</b>.",
                reply_to=_get_root_id(msg) or msg.reply_to_msg_id,
            )
            return
        except BidStepError as exc:
            await _send_reply_or_plain(
                f"⚠️ Неверный шаг. Нужен шаг <b>{exc.step}</b> от старта <b>{exc.start_price}</b>.",
                reply_to=_get_root_id(msg) or msg.reply_to_msg_id,
            )
            return
        except BidFormatError as exc:
            await _send_reply_or_plain(
                f"⚠️ {html.escape(exc.user_message)}",
                reply_to=_get_root_id(msg) or msg.reply_to_msg_id,
            )
            return
        except Exception:
            logger.exception("Failed to revise bid message_id=%s", msg.reply_to_msg_id)
            await _send_reply_or_plain(
                "❌ Не удалось изменить ставку. Администраторы уже получили запись об ошибке.",
                reply_to=_get_root_id(msg) or msg.reply_to_msg_id,
            )
            return

        key = (int(event.chat_id), int(msg.reply_to_msg_id))
        if revision.cancelled:
            ACCEPTED_BIDS.pop(key, None)
            _BOT_DELETED[int(msg.reply_to_msg_id)] = _now_ts() + _BOT_DELETED_TTL
            try:
                await _require_client().delete_messages(
                    legacy_config.DISCUSSION_CHAT_ID,
                    [int(msg.reply_to_msg_id)],
                )
            except Exception:
                logger.exception("Could not delete cancelled bid message %s", msg.reply_to_msg_id)
            await _send_reply_or_plain(
                f"✅ {_mention(None, sender_id)}, ставка <b>{revision.previous_amount}</b> отменена.",
                reply_to=int(revision.auction.discussion_message_id or _get_root_id(msg) or msg.reply_to_msg_id),
            )
        else:
            cached = ACCEPTED_BIDS.get(key)
            if cached is not None:
                cached["amount"] = int(revision.bid.amount)
                cached["currency"] = revision.bid.currency.value
            await _send_reply_or_plain(
                f"✅ {_mention(None, sender_id)}, ставка исправлена: "
                f"<s>{revision.previous_amount}</s> → <b>{revision.bid.amount}</b> "
                f"{revision.bid.currency.emoji}.",
                reply_to=int(revision.auction.discussion_message_id or _get_root_id(msg) or msg.reply_to_msg_id),
            )
        return

    # -------------------------
    # 5) Обычные сообщения: ставки/флуд
    # -------------------------
    root_id = _get_root_id(msg)
    if not root_id:
        return

    auction = await _fetch_auction_by_root(int(root_id))
    if not auction:
        return
    if not await _is_auction_active(auction):
        return

    try:
        auction_kind = AuctionKind.from_raw(auction.get("auction_kind"))
    except ValueError:
        logger.error(
            "Unsupported auction kind %r for auction_id=%s",
            auction.get("auction_kind"),
            auction.get("auction_id"),
        )
        return
    if not auction_kind.is_automatic_bidding:
        # Свободный аукцион принимает произвольные комментарии и разбирается
        # модератором вручную; userbot не должен удалять их как флуд.
        return

    thread_root_id = int(auction.get("discussion_message_id") or root_id)

    # флуд: не прямой ответ на пост лота
    # (для автоставок тоже проверим, но у тебя send_message делается reply_to root, так что ок)
    if not _is_direct_reply_to_root(msg, thread_root_id):
        if is_admin:
            return  # админам можно, просто не считаем как ставку

        # автоставки сюда не должны попадать, но на всякий случай не модерируем mapped
        if is_autobid_msg:
            return

        try:
            await _require_client().delete_messages(legacy_config.DISCUSSION_CHAT_ID, [msg.id])
            _BOT_DELETED[msg.id] = _now_ts() + _BOT_DELETED_TTL
        except Exception:  # noqa: BLE001
            pass

        await _send_reply_or_plain(
            f"❌ {_mention(None, sender_id)}, сообщение удалено.\n"
            f"В комментариях лота разрешены только <b>ставки</b> и только <b>ответом на пост лота</b>.",
            reply_to=thread_root_id,
        )
        await _mute_1m(int(legacy_config.DISCUSSION_CHAT_ID), int(sender_id))
        return

    # Единые правила валюты и ставок используются и bot, и userbot.
    accepted_currencies = normalize_currency_choices(
        auction.get("accepted_currencies"), fallback=auction.get("currency")
    )
    try:
        offer = parse_bid_offer(
            text_raw,
            accepted_currencies=accepted_currencies,
            fallback=Currency.from_raw(auction.get("currency")),
        )
        currency = offer.currency
    except BidFormatError:
        offer = None
        currency = Currency.from_raw(auction.get("currency"))
    except UnsupportedCurrency:
        logger.error(
            "Unsupported currency %r for auction_id=%s",
            auction.get("currency"),
            auction.get("auction_id"),
        )
        return

    step = currency.bid_step
    emoji = currency.emoji
    start_price = int(auction.get("start_price") or 0)
    amount = (
        int(mapped["amount"])
        if is_autobid_msg
        else (offer.amount if offer is not None else _try_parse_bid_amount(text_raw))
    )

    if auction_kind.lowest_bid_wins:
        best_bid_units = await _fetch_best_bid_units(int(auction["auction_id"]))
        reverse_maximum = reverse_maximum_for_currency(
            currency=currency,
            start_price=start_price,
            base_currency=Currency.from_raw(auction.get("currency")),
            current_best_units=best_bid_units,
        )
        min_required = (
            int(reverse_maximum)
            if reverse_maximum is not None
            else max(step, int(amount or step))
        )
        bid_limit_label = "Максимум"
    else:
        best_bid = await _fetch_best_bid(
            int(auction["auction_id"]),
            lowest_wins=False,
        )
        min_required = minimum_next_bid(
            start_price=start_price,
            current_max=best_bid,
            step=step,
        )
        bid_limit_label = "Минимум"
    if amount is None:
        if is_admin:
            return
        try:
            await _require_client().delete_messages(legacy_config.DISCUSSION_CHAT_ID, [msg.id])
            _BOT_DELETED[msg.id] = _now_ts() + _BOT_DELETED_TTL
        except Exception:
            pass
        await _send_reply_or_plain(
            f"❌ {_mention(None, sender_id)}, это не ставка.\n"
            f"Пиши числом или с K/К (например <code>10к</code>).\n"
            f"{bid_limit_label} сейчас: <b>{min_required}</b> {emoji} "
            f"(валюта: <b>{currency.value}</b>)",
            reply_to=thread_root_id,
        )
        await _mute_1m(int(legacy_config.DISCUSSION_CHAT_ID), int(sender_id))
        return

    sender_obj = await event.get_sender()
    sender_username = getattr(sender_obj, "username", None)
    sender_name = " ".join(
        str(part).strip()
        for part in (getattr(sender_obj, "first_name", None), getattr(sender_obj, "last_name", None))
        if part
    ).strip() or None

    service = await AuctionBidService.create()
    try:
        placement = await service.place_for_auction(
            auction_id=int(auction["auction_id"]),
            bid_message_id=int(msg.id),
            bidder_id=int(bidder_id),
            bid_text=text_raw,
            explicit_amount=int(amount) if is_autobid_msg else None,
            username=actor_username if is_autobid_msg else sender_username,
            full_name=None if is_autobid_msg else sender_name,
            check_ban=not is_autobid_msg,
        )
    except BidAlreadyRecorded:
        logger.info("Duplicate Telegram delivery ignored for bid message %s", msg.id)
        return
    except BidderBanned:
        if not is_autobid_msg:
            try:
                await _require_client().delete_messages(legacy_config.DISCUSSION_CHAT_ID, [msg.id])
                _BOT_DELETED[msg.id] = _now_ts() + _BOT_DELETED_TTL
            except Exception:
                pass
            await _send_reply_or_plain(
                f"🚫 {_mention(sender_username, sender_id)}, ставки недоступны из-за блокировки.",
                reply_to=thread_root_id,
            )
        return
    except BidderNotEligible:
        if not is_autobid_msg:
            try:
                await _require_client().delete_messages(legacy_config.DISCUSSION_CHAT_ID, [msg.id])
                _BOT_DELETED[msg.id] = _now_ts() + _BOT_DELETED_TTL
            except Exception:
                pass
            await _send_reply_or_plain(
                "👑 В чёрном аукционе ставки доступны только пользователям Лакшери.",
                reply_to=thread_root_id,
            )
        return
    except AuctionKindNotBiddable:
        # Свободный аукцион допускает произвольные предложения, поэтому
        # userbot не удаляет и не интерпретирует такие комментарии.
        return
    except BidTooLow as exc:
        await _send_reply_or_plain(
            f"⚠️ {_mention(actor_username, actor_id)}, ставка не принята.\n"
            f"Минимум сейчас: <b>{exc.minimum}</b> {emoji} "
            f"(валюта: <b>{currency.value}</b>)",
            reply_to=thread_root_id,
        )
        return
    except BidTooHigh as exc:
        await _send_reply_or_plain(
            f"⚠️ {_mention(actor_username, actor_id)}, ставка не принята.\n"
            "Для обратного аукциона предложение должно быть ниже. "
            f"Максимум сейчас: <b>{exc.maximum}</b> {emoji}.",
            reply_to=thread_root_id,
        )
        return
    except BidStepError as exc:
        await _send_reply_or_plain(
            f"⚠️ {_mention(actor_username, actor_id)}, ставка не засчитана: "
            f"шаг <b>{exc.step}</b> от старта <b>{exc.start_price}</b>.\n"
            f"{bid_limit_label} сейчас: <b>{min_required}</b> {emoji}",
            reply_to=thread_root_id,
        )
        if not is_admin and not is_autobid_msg:
            try:
                await _require_client().delete_messages(legacy_config.DISCUSSION_CHAT_ID, [msg.id])
                _BOT_DELETED[msg.id] = _now_ts() + _BOT_DELETED_TTL
            except Exception:
                pass
            await _mute_1m(int(legacy_config.DISCUSSION_CHAT_ID), int(sender_id))
        return
    except BidFormatError as exc:
        if is_admin:
            return
        try:
            await _require_client().delete_messages(legacy_config.DISCUSSION_CHAT_ID, [msg.id])
            _BOT_DELETED[msg.id] = _now_ts() + _BOT_DELETED_TTL
        except Exception:
            pass
        await _send_reply_or_plain(
            f"❌ {_mention(None, sender_id)}, {html.escape(exc.user_message)}",
            reply_to=thread_root_id,
        )
        await _mute_1m(int(legacy_config.DISCUSSION_CHAT_ID), int(sender_id))
        return
    except (AuctionNotFound, AuctionEnded, AuctionNotActive):
        return
    except Exception:
        logger.exception("Failed to record bid auction_id=%s msg_id=%s", auction.get("auction_id"), msg.id)
        await _send_reply_or_plain(
            f"⚠️ {_mention(actor_username, actor_id)}, ставка не записана из-за внутренней ошибки.",
            reply_to=thread_root_id,
        )
        return

    if is_autobid_msg:
        pop_local_autobid_action(int(msg.id))
    ACCEPTED_BIDS[(int(event.chat_id), int(msg.id))] = {
        "root_id": int(thread_root_id),
        "amount": int(placement.bid.amount),
        "user_id": int(bidder_id),
        "text": text_raw,
        "auction_id": int(placement.auction.auction_id),
        "currency": placement.bid.currency.value,
    }

    # -------------------------
    # 6) Триггер автоставки: только если это была НЕ автоставка
    # -------------------------
    if not is_autobid_msg:
        try:
            await maybe_place_autobid(
                _require_client(),
                discussion_chat_id=int(legacy_config.DISCUSSION_CHAT_ID),
                auction_id=int(auction["auction_id"]),
            )
        except Exception:
            logger.exception("Autobid engine failed for auction_id=%s", auction.get("auction_id"))


__all__ = ["OOPS_EDIT_WINDOW_SEC", "on_new_message"]
