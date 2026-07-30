from __future__ import annotations

VALID_TARGET_KINDS = frozenset({
    "deck",
    "card",
    "auction",
    "rarity",
    "service",
    "spins",
    "default",
})
VALID_MEDIA_TYPES = frozenset({"photo", "video", "animation", "document"})
VALID_RARITIES = frozenset({"bronze", "silver", "gold", "diamond", "any"})

_TARGET_ALIASES = {
    "deck": "deck",
    "decks": "deck",
    "колода": "deck",
    "колоды": "deck",
    "card": "card",
    "cards": "card",
    "карта": "card",
    "карты": "card",
    "auction": "auction",
    "lot": "auction",
    "аукцион": "auction",
    "лот": "auction",
    "rarity": "rarity",
    "редкость": "rarity",
    "service": "service",
    "услуга": "service",
    "spins": "spins",
    "spin": "spins",
    "кручения": "spins",
    "default": "default",
    "по_умолчанию": "default",
}

_MEDIA_TYPE_ALIASES = {
    "photo": "photo",
    "image": "photo",
    "picture": "photo",
    "фото": "photo",
    "картинка": "photo",
    "video": "video",
    "видео": "video",
    "animation": "animation",
    "gif": "animation",
    "анимация": "animation",
    "document": "document",
    "file": "document",
    "документ": "document",
}

_RARITY_ALIASES = {
    "bronze": "bronze",
    "бронза": "bronze",
    "бронзовая": "bronze",
    "silver": "silver",
    "серебро": "silver",
    "серебряная": "silver",
    "gold": "gold",
    "золото": "gold",
    "золотая": "gold",
    "diamond": "diamond",
    "алмаз": "diamond",
    "алмазная": "diamond",
    "эпик": "diamond",
    "any": "any",
    "любая": "any",
}


def normalize_target_kind(value: str) -> str:
    kind = _TARGET_ALIASES.get((value or "").strip().lower(), "")
    if kind not in VALID_TARGET_KINDS:
        raise ValueError("unsupported_target_kind")
    return kind


def normalize_media_type(value: str | None) -> str:
    raw = (value or "photo").strip().lower()
    media_type = _MEDIA_TYPE_ALIASES.get(raw, raw)
    if media_type not in VALID_MEDIA_TYPES:
        raise ValueError("unsupported_media_type")
    return media_type


def normalize_target_key(kind: str, value: str | int) -> str:
    kind = normalize_target_kind(kind)
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("empty_target_key")

    if kind in {"deck", "card", "auction", "spins"}:
        try:
            number = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("target_key_must_be_integer") from exc
        if number <= 0:
            raise ValueError("target_key_must_be_positive")
        return str(number)

    if kind == "rarity":
        rarity = _RARITY_ALIASES.get(raw.lower(), raw.lower())
        if rarity not in VALID_RARITIES:
            raise ValueError("unsupported_rarity")
        return rarity

    return raw.lower()


def infer_media_type(file_id: str, explicit: str | None = None) -> str:
    if explicit:
        return normalize_media_type(explicit)

    value = (file_id or "").strip()
    if value.startswith("BAAC"):
        return "video"
    if value.startswith("AgAC"):
        return "photo"
    if value.startswith("CgAC"):
        return "document"
    return "photo"
