from __future__ import annotations

from aiogram import F
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton as Btn, CallbackQuery

from bot.auction_notify import CB_PREFIX
from .market_fsm import MarketAddFSM

CB_PREFIX = "mkt"
router = Router(name="market_diamonds")


async def start_diamonds_currency_flow(message: Message, state: FSMContext) -> None:
    await state.update_data(
        d_curs=[],
        tiers_payload=[],
        d_qty=30,
        d_price=10
    )

    await state.set_state(MarketAddFSM.D_CURRENCY)
    await message.answer(
        "Выбери валюту(ы) выплат за алмазы:",
        reply_markup=kb_d_currency(set())
    )


def kb_d_currency(selected: set[str]) -> InlineKeyboardMarkup:
    rows = [
        [Btn(("✅ " if "RUB" in selected else "") + "🇷🇺 RUB", callback_data=f"{CB_PREFIX}:dcur:RUB")],
        [Btn(("✅ " if "BYN" in selected else "") + "🇧🇾 BYN", callback_data=f"{CB_PREFIX}:dcur:BYN")],
        [Btn(("✅ " if "UAH" in selected else "") + "🇺🇦 UAH", callback_data=f"{CB_PREFIX}:dcur:UAH")],
        [Btn(("✅ " if "KZT" in selected else "") + "🇰🇿 KZT", callback_data=f"{CB_PREFIX}:dcur:KZT")],
        [Btn(("✅ " if "USD" in selected else "") + "🇺🇸 USD", callback_data=f"{CB_PREFIX}:dcur:USD")],
        [Btn(("✅ " if "EUR" in selected else "") + "🇪🇺 EUR", callback_data=f"{CB_PREFIX}:dcur:EUR")],
        [Btn("✅ Готово", callback_data=f"{CB_PREFIX}:dcur:done")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:dcur:"))
async def cb_d_currency(call: CallbackQuery, state: FSMContext):
    _, _, code = call.data.split(":")
    data = await state.get_data()
    chosen = set(data.get("d_curs") or [])
    if code == "done":
        if not chosen:
            await call.answer("Выбери хотя бы одну валюту.", show_alert=True)
            return
        await state.update_data(d_qty=30, d_price=10)  # дефолт
        await state.set_state(MarketAddFSM.D_TIER)
        await call.message.edit_text(
            _render_d_tier_preview(state_data=await state.get_data()),
            reply_markup=kb_d_tier_builder(30, 10, next(iter(chosen)))
        )
        return

    if code in chosen:
        chosen.remove(code)
    else:
        chosen.add(code)
    await state.update_data(d_curs=list(chosen))
    await call.message.edit_reply_markup(reply_markup=kb_d_currency(chosen))


def kb_d_tier_builder(qty: int, price: int, currency: str) -> InlineKeyboardMarkup:
    rows = [
        [Btn("🔺 +10", callback_data=f"{CB_PREFIX}:dt:qi:10"),
         Btn("🔺 +50", callback_data=f"{CB_PREFIX}:dt:qi:50"),
         Btn("🔺 +100", callback_data=f"{CB_PREFIX}:dt:qi:100")],
        [Btn("🔻 -10", callback_data=f"{CB_PREFIX}:dt:qd:10"),
         Btn("🔻 -50", callback_data=f"{CB_PREFIX}:dt:qd:50"),
         Btn("🔻 -100", callback_data=f"{CB_PREFIX}:dt:qd:100")],
        [Btn("💲 +10", callback_data=f"{CB_PREFIX}:dt:pi:10"),
         Btn("💲 +50", callback_data=f"{CB_PREFIX}:dt:pi:50"),
         Btn("💲 +100", callback_data=f"{CB_PREFIX}:dt:pi:100")],
        [Btn("💲 -10", callback_data=f"{CB_PREFIX}:dt:pd:10"),
         Btn("💲 -50", callback_data=f"{CB_PREFIX}:dt:pd:50"),
         Btn("💲 -100", callback_data=f"{CB_PREFIX}:dt:pd:100")],
        [Btn("➕ Добавить строку", callback_data=f"{CB_PREFIX}:dt:add")],
        [Btn("⬅️ Назад", callback_data=f"{CB_PREFIX}:dt:back"),
         Btn("✅ Готово", callback_data=f"{CB_PREFIX}:dt:done")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _render_d_tier_preview(state_data: dict) -> str:
    tiers = list(state_data.get("tiers_payload") or [])
    qty = int(state_data.get("d_qty") or 30)
    price = int(state_data.get("d_price") or 10)
    curs = list(state_data.get("d_curs") or [])
    head = "Собери прайс для алмазов. Правила: минимум 30, кратно 10."
    cur_line = f"\nТекущая строка: <b>{qty}</b> алм. → <b>{price}</b> {', '.join(curs) or '?'}"
    if not tiers:
        return head + cur_line
    lines = []
    for t in tiers[:10]:
        lines.append(f"• {t['qty']} алм. → {t['price']} {t['pay_type']}")
    more = f"\n…и ещё {len(tiers) - 10}" if len(tiers) > 10 else ""
    return head + "\nДобавлено:\n" + "\n".join(lines) + more + cur_line


@router.callback_query(MarketAddFSM.D_TIER, F.data.startswith(f"{CB_PREFIX}:dt:"))
async def cb_d_tier(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(":")
    action = parts[2]
    step = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0

    data = await state.get_data()
    qty = int(data.get("d_qty") or 30)
    price = int(data.get("d_price") or 10)
    curs = list(data.get("d_curs") or [])
    if not curs:
        await call.answer("Сначала выбери валюту.", show_alert=True)
        return
    pay = curs[0]

    if action == "qi":
        qty += step
    elif action == "qd":
        qty = max(0, qty - step)
    elif action == "pi":
        price += step
    elif action == "pd":
        price = max(0, price - step)
    elif action == "add":
        if qty < 30 or qty % 10 != 0:
            await call.answer("Кол-во должно быть ≥30 и кратно 10.", show_alert=True)
            return
        if price <= 0:
            await call.answer("Цена должна быть > 0.", show_alert=True)
            return
        tiers: list[dict] = list(data.get("tiers_payload") or [])
        tiers.append({
            "label": f"{qty} алм.",
            "qty": qty,
            "pay_type": pay,
            "cash_code": pay,
            "price": float(price),
            "sort_order": qty
        })
        qty = qty + 10
        await state.update_data(tiers_payload=tiers, d_qty=qty, d_price=price)
        await call.message.edit_text(
            _render_d_tier_preview(await state.get_data()),
            reply_markup=kb_d_tier_builder(qty, price, pay)
        )
        return
    elif action == "back":
        await state.set_state(MarketAddFSM.D_CURRENCY)
        await call.message.edit_text("Выбери валюту(ы) выплат за алмазы:",
                                     reply_markup=kb_d_currency(set(curs)))
        return
    elif action == "done":
        tiers = list(data.get("tiers_payload") or [])
        if not tiers:
            await call.answer("Добавь хотя бы одну строку.", show_alert=True)
            return
        await state.set_state(MarketAddFSM.DESCRIPTION)
        await call.message.edit_text("Добавь описание/условия или напиши «-».")
        return

    await state.update_data(d_qty=qty, d_price=price)
    await call.message.edit_text(
        _render_d_tier_preview(await state.get_data()),
        reply_markup=kb_d_tier_builder(qty, price, pay)
    )
