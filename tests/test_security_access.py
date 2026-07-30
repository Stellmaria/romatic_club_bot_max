from bot.security.access import admin_secret_matches, is_owner_or_valid_secret


def test_blank_admin_secret_never_authorizes_blank_input() -> None:
    assert not admin_secret_matches("", configured_secret="")
    assert not admin_secret_matches("   ", configured_secret="   ")


def test_admin_secret_check_and_owner_bypass_are_explicit() -> None:
    assert admin_secret_matches("correct", configured_secret="correct")
    assert not admin_secret_matches("wrong", configured_secret="correct")
    assert is_owner_or_valid_secret(
        42,
        None,
        owner_ids={42},
        configured_secret="",
    )
    assert not is_owner_or_valid_secret(
        7,
        "",
        owner_ids={42},
        configured_secret="",
    )
