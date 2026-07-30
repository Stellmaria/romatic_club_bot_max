from aiogram.fsm.state import StatesGroup, State


class MarketAddFSM(StatesGroup):
    PRICE_BULK = State()
    CHOOSE_KIND = State()
    CHOOSE_DECK = State()
    PICK_CARDS = State()
    CURRENCY = State()
    CASH_CODE = State()
    PRICE = State()
    COVER = State()
    TIERS = State()
    DECK_MODE = State()
    DESCRIPTION = State()
    CONFIRM = State()
    QUANTITY = State()
    CUSTOM_VARIANT = State()
    CUSTOM_FIAT = State()
    PROOF_CHOICE = State()
    PROOF_EACH = State()
    PHOTO = State()
    CUSTOM_VARIANT_QTY = State()
    CUSTOM_VARIANT_QTY_INPUT = State()
    D_CURRENCY = State()
    D_TIER = State()


class MarketEditFSM(StatesGroup):
    QTY = State()
    PHOTO = State()
    DESC = State()
    PRICE = State()


class MarketSearchFSM(StatesGroup):
    ASK_QUERY = State()
    ASK_FILTERS = State()
