from bot.security.access import admin_secret_matches, is_owner_or_valid_secret


def test_retired_admin_secret_never_authorizes_input() -> None:
    assert not admin_secret_matches("", configured_secret="")
    assert not admin_secret_matches("correct", configured_secret="correct")
    assert not admin_secret_matches("wrong", configured_secret="correct")


def test_owner_compatibility_check_uses_only_telegram_id() -> None:
    assert is_owner_or_valid_secret(
        42,
        None,
        owner_ids={42},
        configured_secret="",
    )
    assert is_owner_or_valid_secret(
        42,
        "former-secret",
        owner_ids={42},
        configured_secret="former-secret",
    )
    assert not is_owner_or_valid_secret(
        7,
        "former-secret",
        owner_ids={42},
        configured_secret="former-secret",
    )
