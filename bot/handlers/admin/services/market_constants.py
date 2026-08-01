import re
from datetime import timedelta

ALLOWED_PAY = {"cash", "diamonds", "cups", "treasures"}

STAR_PSEUDO = "tgstars"
STAR_DB_CODE = "TGS"  # храним как CASH с кодом TGS
_RU_WORD = re.compile(r"^[А-Яа-яЁё\-]+$")
_EXTRAS_NUM_RE = re.compile(r"^\s*(\d+)\s*[xх*]?\s*(.+)$", re.IGNORECASE)

MAX_LISTINGS_ORDINARY = 5
MAX_LISTING_CARDS_ORDINARY = 5
PAGE_CARDS = 15
BUMP_COOLDOWN = timedelta(hours=6)
_EXTRAS_HEAD_RE = re.compile(r"^\s*(\d+)\s*[xх*]?\s*(.+)$", re.IGNORECASE)
_EXTRAS_TAIL_RE = re.compile(r"^\s*(.+?)\s*[×xх*]\s*(\d+)\s*$", re.IGNORECASE)

CB_PREFIX = "mkt"
CB_KIND = f"{CB_PREFIX}:kind"
CB_SEL = f"{CB_PREFIX}:sel"
CB_PAGE = f"{CB_PREFIX}:page"
CB_TOGGLE = f"{CB_PREFIX}:toggle"
CB_BUMP = f"{CB_PREFIX}:bump"
CB_CANCEL = f"{CB_PREFIX}:cancel"
CB_BACK = f"{CB_PREFIX}:back_decks"

TIER_RE = re.compile(
    r"^\s*(?:(?P<label>[^\d\s][^|]*)|(?P<qty>\d+\+?))\s+"
    r"(?P<price>\d+(?:[.,]\d{1,2})?)\s*"
    r"(?P<pay>(?:cups?|чашки|diamonds?|💎|treasures?)|[A-Z]{3})?\s*$",
    re.IGNORECASE,
)
MAP_PAY = {
    "cup": "cups", "cups": "cups", "чашки": "cups",
    "diamond": "diamonds", "diamonds": "diamonds", "💎": "diamonds",
    "treasure": "treasures", "treasures": "treasures",
}

ALLOWED_PAY = {"cash", "diamonds", "cups", "treasures"}

STAR_PSEUDO = "tgstars"  # псевдо-имя в интерфейсе
STAR_DB_CODE = "TGS"  # храним как cash с кодом TGS

CB_PREFIX = "mkt"
CB_KIND = f"{CB_PREFIX}:kind"
CB_SEL = f"{CB_PREFIX}:sel"
CB_PAGE = f"{CB_PREFIX}:page"
CB_BACK = f"{CB_PREFIX}:back_decks"
CB_CANCEL = f"{CB_PREFIX}:cancel"

# Регэксп для многострочных прайсов
TIER_RE = re.compile(
    r"^\s*(?:(?P<label>[^\d\s][^|]*)|(?P<qty>\d+\+?))\s+"
    r"(?P<price>\d+(?:[.,]\d{1,2})?)\s*"
    r"(?P<pay>(?:cups?|чашки|diamonds?|💎|treasures?)|[A-Z]{3})?\s*$",
    re.IGNORECASE,
)

# Public compatibility aliases. Cross-feature imports must use these names.
EXTRAS_HEAD_RE = _EXTRAS_HEAD_RE
EXTRAS_TAIL_RE = _EXTRAS_TAIL_RE
RU_WORD = _RU_WORD
