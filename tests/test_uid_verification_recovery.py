from __future__ import annotations

import re

from bot.handlers.uid_verification_recovery import (
    MAX_OTHER_RESPONSE_LENGTH,
    _new_revision_code,
    _normalize_other_response,
    _remaining_after_completion,
    _revision_text,
    _valid_uid,
)


def test_valid_uid_normalizes_legacy_plaintext() -> None:
    assert _valid_uid(" ABCDEF0123456789ABCDEF01 ") == "abcdef0123456789abcdef01"


def test_valid_uid_rejects_digest_and_malformed_values() -> None:
    assert _valid_uid("a" * 64) is None
    assert _valid_uid("not-a-uid") is None
    assert _valid_uid(None) is None


def test_revision_text_escapes_moderator_comment() -> None:
    text = _revision_text(266, ["profile"], "<b>unsafe</b>")

    assert "Доработка заявки #266" in text
    assert "Профиль + код" in text
    assert "&lt;b&gt;unsafe&lt;/b&gt;" in text
    assert "<b>unsafe</b>" not in text


def test_new_revision_code_matches_profile_challenge_contract() -> None:
    values = {_new_revision_code() for _ in range(20)}

    assert values
    assert all(re.fullmatch(r"MX-[0-9]{5}", value) for value in values)


def test_other_response_is_trimmed_and_bounded() -> None:
    assert _normalize_other_response("  исправлено  ") == "исправлено"
    assert _normalize_other_response("   ") is None
    assert _normalize_other_response("x" * (MAX_OTHER_RESPONSE_LENGTH + 1)) is None


def test_completed_revision_item_is_removed_without_reordering() -> None:
    remaining = ["profile", "other", "deal1_screen"]

    assert _remaining_after_completion(remaining, "other") == ["profile", "deal1_screen"]
    assert _remaining_after_completion(remaining, "missing") == remaining
