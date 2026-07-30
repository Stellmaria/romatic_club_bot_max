from __future__ import annotations

import html
import logging

from aiogram import Router, types
from aiogram.filters import Command

from bot.domain.auctions import (
    AuctionNotActive,
    AuctionNotFound,
    AutobidLimitTooLow,
    AutobidTargetNotFound,
)
from bot.handlers.admin.helper.new.wrapper import admin_only
from bot.services.auction_autobids import AuctionAutobidService
from config import AUTOBID_SET_PASSWORD

logger = logging.getLogger("auction_bot.autobid_commands")
router = Router(name="auction-autobid")


def _usage() -> str:
    suffix = " [password]" if AUTOBID_SET_PASSWORD else ""
    return (
        "Формат:\n"
        f"<code>/autobid_set &lt;lot_id&gt; &lt;@username&gt; &lt;max_amount&gt;{suffix}</code>"
    )


def _password_is_valid(parts: list[str]) -> bool:
    if not AUTOBID_SET_PASSWORD:
        return True
    return len(parts) >= 5 and parts[4] == AUTOBID_SET_PASSWORD


@router.message(Command("autobid_set"))
@admin_only
async def set_autobid(message: types.Message) -> None:
    parts = (message.text or "").split()
    if len(parts) < 4:
        await message.answer(_usage(), parse_mode="HTML")
        return
    if not _password_is_valid(parts):
        await message.answer("Неверный пароль автоставки.")
        return

    try:
        auction_id = int(parts[1])
        username = parts[2].lstrip("@").strip()
        max_amount = int(parts[3])
    except (TypeError, ValueError):
        await message.answer(_usage(), parse_mode="HTML")
        return

    service = await AuctionAutobidService.create()
    try:
        autobid = await service.configure(
            auction_id=auction_id,
            target_username=username,
            max_amount=max_amount,
            created_by=int(message.from_user.id),
        )
    except AuctionNotFound:
        await message.answer("Лот не найден.")
        return
    except AuctionNotActive:
        await message.answer("Для завершённого или закрытого лота автоставку включить нельзя.")
        return
    except AutobidTargetNotFound:
        await message.answer(
            f"Пользователь @{html.escape(username)} не найден в базе. "
            "Сначала он должен открыть бота через /start.",
            parse_mode="HTML",
        )
        return
    except AutobidLimitTooLow as exc:
        await message.answer(
            f"Максимум автоставки должен быть не меньше <b>{exc.minimum}</b>.",
            parse_mode="HTML",
        )
        return
    except Exception:
        logger.exception("Could not configure autobid for auction %s", auction_id)
        await message.answer("Не удалось сохранить автоставку из-за внутренней ошибки.")
        return

    await message.answer(
        "✅ <b>Автоставка включена</b>\n"
        f"Лот: <code>{autobid.auction_id}</code>\n"
        f"Пользователь: @{html.escape(autobid.target_username or username)}\n"
        f"Максимум: <b>{autobid.max_amount}</b>\n"
        f"Шаг: <b>{autobid.step}</b>",
        parse_mode="HTML",
    )


@router.message(Command("autobid_stop"))
@admin_only
async def stop_autobid(message: types.Message) -> None:
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Формат: <code>/autobid_stop &lt;lot_id&gt; &lt;@username&gt;</code>", parse_mode="HTML")
        return
    try:
        auction_id = int(parts[1])
    except ValueError:
        await message.answer("ID лота должен быть числом.")
        return
    username = parts[2].lstrip("@").strip()

    service = await AuctionAutobidService.create()
    try:
        disabled = await service.disable(auction_id=auction_id, target_username=username)
    except AutobidTargetNotFound:
        await message.answer("Пользователь не найден в базе.")
        return
    except Exception:
        logger.exception("Could not disable autobid for auction %s", auction_id)
        await message.answer("Не удалось выключить автоставку.")
        return

    await message.answer("✅ Автоставка выключена." if disabled else "Активная автоставка не найдена.")


@router.message(Command("autobid_list"))
@admin_only
async def list_autobids(message: types.Message) -> None:
    parts = (message.text or "").split()
    auction_id = None
    if len(parts) >= 2:
        try:
            auction_id = int(parts[1])
        except ValueError:
            await message.answer("Формат: <code>/autobid_list [lot_id]</code>", parse_mode="HTML")
            return

    service = await AuctionAutobidService.create()
    try:
        rows = await service.list_active(auction_id=auction_id)
    except Exception:
        logger.exception("Could not list autobids")
        await message.answer("Не удалось получить список автоставок.")
        return

    if not rows:
        await message.answer("Активных автоставок нет.")
        return

    lines = ["🤖 <b>Активные автоставки</b>"]
    for row in rows[:100]:
        username = html.escape(row.target_username or f"id{row.target_user_id}")
        lines.append(
            f"• лот <code>{row.auction_id}</code>: @{username}, "
            f"макс. <b>{row.max_amount}</b>, шаг {row.step}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")
