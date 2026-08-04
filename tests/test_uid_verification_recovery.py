from __future__ import annotations

from bot.handlers.uid_verification_recovery import _revision_text, _valid_uid


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
