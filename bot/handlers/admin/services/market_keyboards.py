from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

from bot.handlers.admin.services.market_constants import CB_CANCEL, PAGE_CARDS, CB_SEL, CB_BACK, CB_KIND, CB_PAGE, \
    CB_PREFIX
from bot.handlers.admin.services.market_utils import fiat_flag


def my_listing_actions(listing_id: int, status: str | None = None) -> InlineKeyboardMarkup:
    lid = str(listing_id)

    is_active = (status or "").lower() == "active"
    if is_active:
        main_btn = InlineKeyboardButton(text="🙈 Скрыть", callback_data=f"{CB_PREFIX}:act:hide:{lid}")
    else:
        main_btn = InlineKeyboardButton(text="👁 Активировать", callback_data=f"{CB_PREFIX}:act:activate:{lid}")

    rows = [
        [main_btn, InlineKeyboardButton(text="✅ Продано", callback_data=f"{CB_PREFIX}:act:sold:{lid}")],
        [InlineKeyboardButton(text="🗄 Архив", callback_data=f"{CB_PREFIX}:act:archive:{lid}"),
         InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"{CB_PREFIX}:act:edit:{lid}")],
        [InlineKeyboardButton(text="🖼 Фото", callback_data=f"{CB_PREFIX}:proof:show:{lid}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{CB_PREFIX}:act:del:{lid}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def edit_listing_kb(listing_id: int) -> InlineKeyboardMarkup:
    lid = str(listing_id)
    mk = lambda action, text: InlineKeyboardButton(
        text=text, callback_data=f"{CB_PREFIX}:edit:{action}:{lid}"
    )
    rows = [
        [mk("qty", "🔢 Количество"), mk("photo", "🖼 Фото")],
        [mk("desc", "💬 Текст")],
        [mk("prices", "💲 Цены"), mk("clearprices", "🧹 Сбросить цены")],  # ← новое
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB_PREFIX}:edit:back:{lid}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def prices_menu_kb(listing_id: int) -> InlineKeyboardMarkup:
    lid = str(listing_id)
    mk = lambda cb, text: InlineKeyboardButton(text=text, callback_data=cb)
    rows = [
        [mk(f"{CB_PREFIX}:edit:price:cups:{lid}", "☕ Чашки"),
         mk(f"{CB_PREFIX}:edit:price:diamonds:{lid}", "💎 Алмазы")],
        [mk(f"{CB_PREFIX}:edit:price:treasures:{lid}", "🪙 Сокровища"),
         mk(f"{CB_PREFIX}:edit:price:tgstars:{lid}", "⭐ TG-звёзды")],  # ← новое
        [mk(f"{CB_PREFIX}:edit:pricecash:{lid}", "💵 Наличные…")],
        [mk(f"{CB_PREFIX}:edit:back:{lid}", "⬅️ Назад")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def prices_cash_menu_kb(listing_id: int) -> InlineKeyboardMarkup:
    lid = str(listing_id)
    CASH_CODES = ["BYN", "RUB", "UAH", "KZT", "USD", "EUR"]
    row = []
    rows = []
    for code in CASH_CODES:
        row.append(InlineKeyboardButton(
            text=code, callback_data=f"{CB_PREFIX}:edit:price:cash-{code}:{lid}"
        ))
        if len(row) == 3:
            rows.append(row);
            row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB_PREFIX}:edit:prices:{lid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sold_confirm_kb(listing_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, уменьшить на 1", callback_data=f"{CB_PREFIX}:sold_yes:{listing_id}")],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data=f"{CB_PREFIX}:sold_no")]
    ])


def _done_label(selected_count: int, limit: int | None) -> str:
    if isinstance(limit, int) and limit > 0:
        return f"✅ Готово ({selected_count}/{limit})"
    return "✅ Готово" if selected_count <= 0 else f"✅ Готово ({selected_count})"


def cash_multi_keyboard(selected: set[str], extras: list[str] | None = None) -> InlineKeyboardMarkup:
    extras = extras or []

    def tgl(label: str, code: str) -> InlineKeyboardButton:
        mark = "✅ " if code in selected else ""
        return InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"{CB_PREFIX}:cash_toggle:{code}")

    base_rows = [
        [tgl(f"{fiat_flag('BYN')} BYN", "BYN"), tgl(f"{fiat_flag('RUB')} RUB", "RUB")],
        [tgl(f"{fiat_flag('UAH')} UAH", "UAH"), tgl(f"{fiat_flag('KZT')} KZT", "KZT")],
        [tgl(f"{fiat_flag('USD')} USD", "USD")],
    ]

    extra_rows: list[list[InlineKeyboardButton]] = []
    if extras:
        row: list[InlineKeyboardButton] = []
        for code in extras:
            row.append(tgl(f"{fiat_flag(code)} {code}".strip(), code))
            if len(row) == 2:
                extra_rows.append(row)
                row = []
        if row:
            extra_rows.append(row)

    done_text = _done_label(len(selected), None)

    tail = [
        [InlineKeyboardButton(text="➕ Свой код", callback_data=f"{CB_PREFIX}:cash_add")],
        [InlineKeyboardButton(text=done_text, callback_data=f"{CB_PREFIX}:cash_done")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=base_rows + extra_rows + tail)


def kb_deck_mode() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📦 Вся колода одним лотом",
                callback_data=f"{CB_PREFIX}:deckmode:bulk"
            )
        ],
        [
            InlineKeyboardButton(
                text="🧩 По картам (каждая отдельно)",
                callback_data=f"{CB_PREFIX}:deckmode:split"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"{CB_PREFIX}:cancel"
            )
        ],
    ])


def market_decks_kb(decks: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for d in decks:
        did = int(d.get("deck_id") or d.get("id"))
        name = d.get("deck_name") or d.get("name") or f"Колода #{did}"
        rows.append([InlineKeyboardButton(text=f"{did}. {name}", callback_data=f"{CB_PREFIX}:deck:{did}:0")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def market_cards_kb(
        deck_id: int,
        cards: list[dict],
        selected: set[int],
        page: int,
        page_size: int = PAGE_CARDS,
        limit: int | None = None,  # None => без лимита
) -> InlineKeyboardMarkup:
    from math import ceil
    rows: list[list[InlineKeyboardButton]] = []

    pages = max(1, ceil(len(cards) / page_size))
    page = max(0, min(page, pages - 1))
    start = page * page_size
    chunk = cards[start:start + page_size]

    for c in chunk:
        cid = int(c["card_id"])
        hero = c.get("hero_name") or "-"
        rarity = c.get("rarity") or "?"
        num = c.get("num", "?")
        mark = "✅" if cid in selected else "▫️"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {num}. {hero} ({rarity})",
            callback_data=f"{CB_SEL}:{deck_id}:{cid}:{page}",
        )])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="«", callback_data=f"{CB_PAGE}:{deck_id}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=f"{CB_PREFIX}:nop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="»", callback_data=f"{CB_PAGE}:{deck_id}:{page + 1}"))
    if nav:
        rows.append(nav)

    done_text = _done_label(len(selected), limit)
    rows.append([
        InlineKeyboardButton(text="⬅️ К колодам", callback_data=CB_BACK),
        InlineKeyboardButton(text="🔁 Сбросить", callback_data=f"{CB_PREFIX}:reset:{deck_id}:{page}"),
        InlineKeyboardButton(text=done_text, callback_data=f"{CB_PREFIX}:done"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_proof_choice() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📎 Одно фото для всех", callback_data=f"{CB_PREFIX}:add:proof:single")],
        [InlineKeyboardButton(text="📸 Фото для каждой карты", callback_data=f"{CB_PREFIX}:add:proof:each")],
        [InlineKeyboardButton(text="⏭ Пропустить фото", callback_data=f"{CB_PREFIX}:add:proof:skip")],
    ])


def kb_proof_single_skip() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить фото", callback_data=f"{CB_PREFIX}:add:proof:skip")]
    ])


def kb_custom_qty_choice() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Указать количество", callback_data=f"{CB_PREFIX}:cur_custom_qty:ask"),
            InlineKeyboardButton(text="Без количества", callback_data=f"{CB_PREFIX}:cur_custom_qty:skip"),
        ]
    ])


def confirm_publish_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, опубликовать", callback_data=f"{CB_PREFIX}:confirm:yes")],
        [InlineKeyboardButton(text="✖ Отмена", callback_data=f"{CB_PREFIX}:confirm:no")],
    ])


def confirm_kb(action: str, lid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=f"{CB_PREFIX}:do:{action}:{lid}:yes"),
         InlineKeyboardButton(text="✖ Нет", callback_data=f"{CB_PREFIX}:do:{action}:{lid}:no")]
    ])


def market_kind_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🃏 Карты поштучно", callback_data=f"{CB_KIND}:cards")],
        [InlineKeyboardButton(text="📚 Целая колода", callback_data=f"{CB_KIND}:whole_deck")],
        [InlineKeyboardButton(text="💎 Алмазы", callback_data=f"{CB_KIND}:diamonds"),
         InlineKeyboardButton(text="☕ Чашки", callback_data=f"{CB_KIND}:cups")],
        [InlineKeyboardButton(text="🏴‍☠️ Сокровища", callback_data=f"{CB_KIND}:treasures")],
        [InlineKeyboardButton(text="🛠 Услуга", callback_data=f"{CB_KIND}:service")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL)],
    ])


def listing_public_kb(seller_id: int, listing_id: int, owner_view: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✉️ Написать продавцу", callback_data=f"{CB_PREFIX}:pm:{seller_id}")],
        [InlineKeyboardButton(text="📷 Подтверждение", callback_data=f"{CB_PREFIX}:proof:{listing_id}")]
    ]
    rows.append([InlineKeyboardButton(text="🟢 Актуально", callback_data=f"{CB_PREFIX}:toggle:{listing_id}"),
                 InlineKeyboardButton(text="🔼 Апнуть", callback_data=f"{CB_PREFIX}:bump:{listing_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def currency_multi_keyboard(selected: set[str], extras_count: int = 0) -> InlineKeyboardMarkup:
    def btn(txt, cur):
        mark = "✅ " if cur in selected else ""
        return InlineKeyboardButton(text=f"{mark}{txt}", callback_data=f"{CB_PREFIX}:cur_toggle:{cur}")

    def done_label(sel_cnt: int, extras_cnt: int) -> str:
        if sel_cnt == 0 and extras_cnt == 0:
            return "✅ Готово"
        if extras_cnt > 0:
            return f"✅ Готово ({sel_cnt}+{extras_cnt})" if sel_cnt > 0 else f"✅ Готово (+{extras_cnt})"
        return f"✅ Готово ({sel_cnt})"

    rows = [
        [btn("☕ Чашки", "cups"), btn("💎 Алмазы", "diamonds")],
        [btn("🏴‍☠️ Сокровища", "treasures"), btn("⭐ TG-звёзды", "tgstars")],
        [btn("💵 Деньги", "cash")],
        [InlineKeyboardButton(text="➕ Свой вариант", callback_data=f"{CB_PREFIX}:cur_custom")],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_BACK),
            InlineKeyboardButton(text=done_label(len(selected), extras_count), callback_data=f"{CB_PREFIX}:cur_done"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def proof_skip_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(
            text="⏭ Пропустить фото",
            callback_data=f"{CB_PREFIX}:proof:skip"
        )]]
    )


def market_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛒 Продать"), KeyboardButton(text="🔍 Поиск")],
            [KeyboardButton(text="📦 Мои объявления")],
        ],
        resize_keyboard=True
    )


def market_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🃏 Карты", callback_data="mkt:go:sell_cards"),
            InlineKeyboardButton(text="📚 Колоды", callback_data="mkt:go:sell_deck"),
            InlineKeyboardButton(text="💎 Валюта", callback_data="mkt:go:sell_currency"),
        ],
        [
            InlineKeyboardButton(text="🔍 Поиск", callback_data="mkt:go:find"),
            InlineKeyboardButton(text="📦 Мои объявления", callback_data="mkt:go:my_sales"),
            InlineKeyboardButton(text="ℹ️ Помощь", callback_data="mkt:go:help"),
        ],
    ])


from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton


def my_sales_filters_reply_kb(selected: str = "active") -> ReplyKeyboardMarkup:
    def btn(c, k):
        dot = "▪️ " if selected == k else "▫️ "
        return KeyboardButton(text=f"{dot}{c}")

    return ReplyKeyboardMarkup(
        keyboard=[
            [btn("Активные", "active"), btn("Скрытые", "hidden"), btn("Проданные", "sold")],
            [btn("Архив", "archived"), btn("Все", "all")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Фильтр объявлений…",
    )


def my_sales_nav_kb(idx: int, total: int, status: str) -> InlineKeyboardMarkup:
    center = InlineKeyboardButton(text=f"{idx + 1}/{total}", callback_data="noop")
    row_nav = [
        InlineKeyboardButton(text="⟨ Пред", callback_data="my:nav:prev"),
        center,
        InlineKeyboardButton(text="След ⟩", callback_data="my:nav:next"),
    ]
    # действия над лотом
    row_act1 = [
        InlineKeyboardButton(text="✏️ Изм.", callback_data="my:act:edit"),
        InlineKeyboardButton(text="🖼 Фото", callback_data="my:act:proof"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data="my:act:delete"),
    ]
    # статусные
    row_act2 = [
        InlineKeyboardButton(text=("👁‍🗨 Показать" if status == "hidden" else "👁 Скрыть"),
                             callback_data="my:act:toggle_hidden"),
        InlineKeyboardButton(text=("🗄 В архив" if status != "archived" else "📤 Из архива"),
                             callback_data="my:act:toggle_archive"),
        InlineKeyboardButton(text=("✅ Продано" if status != "sold" else "↩ Актив"), callback_data="my:act:toggle_sold"),
    ]
    row_close = [InlineKeyboardButton(text="❌ Закрыть", callback_data="my:nav:close")]
    return InlineKeyboardMarkup(inline_keyboard=[row_nav, row_act1, row_act2, row_close])
