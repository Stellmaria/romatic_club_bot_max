"""Shared navigation for a seller's marketplace listings.

The add and manage routers both use this UI, so it lives outside either router
module and cannot introduce an import-order dependency between them.
"""

from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, Message

from bot.handlers.admin.services.market_keyboards import (
    my_sales_filters_reply_kb,
    my_sales_nav_kb,
)
from bot.services.market import (
    market_listing_navigation_view,
    market_seller_listing_ids,
)


MY_SALES_STATE_KEY = "my_sales"
_MY = MY_SALES_STATE_KEY


async def _my_sales_set_filter_and_show(
    message: Message,
    state: FSMContext,
    tab: str,
) -> None:
    user_id = message.from_user.id
    statuses = ["active", "hidden", "sold", "archived"] if tab == "all" else [tab]
    ids = await market_seller_listing_ids(user_id, statuses)
    await state.update_data({_MY: {"ids": ids, "idx": 0, "tab": tab}})
    await message.answer("Фильтр применён.", reply_markup=my_sales_filters_reply_kb(tab))
    if not ids:
        await message.answer("Пусто.")
        return
    await _my_sales_render(message, state, edit=False)


async def _my_sales_enter(message: Message, state: FSMContext, tab: str) -> None:
    await message.answer(
        "Фильтр объявлений",
        reply_markup=my_sales_filters_reply_kb(tab),
        disable_notification=True,
    )

    statuses = ["active", "hidden", "sold", "archived"] if tab == "all" else [tab]
    ids = await market_seller_listing_ids(message.from_user.id, statuses)
    await state.update_data({_MY: {"ids": ids, "idx": 0, "tab": tab}})

    if not ids:
        await message.answer("Пусто.")
        return

    await _my_sales_render(message, state, edit=False)


async def _my_sales_render(
    target: Message | CallbackQuery,
    state: FSMContext,
    edit: bool,
) -> None:
    """Render the selected listing; ``edit`` remains for API compatibility."""
    del edit

    data = await state.get_data()
    sales = data.get(_MY) or {}
    ids: list[int] = list(sales.get("ids") or [])
    idx = int(sales.get("idx") or 0)
    listing_id = int(ids[idx])

    lot, card, tiers = await market_listing_navigation_view(listing_id)

    title = card.get("title") or (lot.get("description") or "—").splitlines()[0]
    rarity = str(card.get("rarity") or "—")
    deck_name = card.get("deck_name") or (
        f"Колода {card.get('deck_id')}" if card.get("deck_id") else "—"
    )

    yields = []
    if int(card.get("diamonds") or 0) > 0:
        yields.append(f"💎 {int(card['diamonds'])}")
    if int(card.get("cups") or 0) > 0:
        yields.append(f"☕ {int(card['cups'])}")
    if int(card.get("treasures") or 0) > 0:
        yields.append(f"🏴‍☠️ {int(card['treasures'])}")
    gives_line = " · ".join(yields) if yields else "—"

    price_lines = []
    for tier in tiers:
        if tier["pay_type"] == "cash":
            code = (tier.get("cash_code") or "").upper()
            price_lines.append(f"{code} {tier['price']:.2f}")
        elif tier["pay_type"] == "diamonds":
            price_lines.append(f"💎 {int(tier['price'])}")
        elif tier["pay_type"] == "cups":
            price_lines.append(f"☕ {int(tier['price'])}")
        elif tier["pay_type"] == "treasures":
            price_lines.append(f"🏴‍☠️ {int(tier['price'])}")

    count = int(lot.get("items_count") or 0)
    status = str(lot.get("status") or "unknown")
    proof = "есть" if lot.get("cover_file_id") else "отсутствует"

    caption = (
        f"<b>{title}</b> — <i>{rarity}</i>\n"
        f"{deck_name}\n\n"
        f"<b>Цены:</b>\n"
        + ("\n".join(f"• {price}" for price in price_lines) or "—")
        + "\n\n"
        f"Доступно: {count}\n"
        f"Даёт: {gives_line}\n"
        f"Фото подтверждения: {proof}\n\n"
        f"<i>Статус: {status}</i>"
    )

    keyboard = my_sales_nav_kb(idx, len(ids), status)
    cover = lot.get("cover_file_id")

    if isinstance(target, CallbackQuery):
        if cover:
            media = InputMediaPhoto(media=cover, caption=caption, parse_mode="HTML")
            await target.message.edit_media(media=media, reply_markup=keyboard)
        else:
            await target.message.edit_text(caption, reply_markup=keyboard, parse_mode="HTML")
    elif cover:
        await target.answer_photo(cover, caption=caption, reply_markup=keyboard, parse_mode="HTML")
    else:
        await target.answer(caption, reply_markup=keyboard, parse_mode="HTML")
