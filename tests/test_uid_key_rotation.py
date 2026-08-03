from __future__ import annotations

from cryptography.fernet import Fernet

from bot.uid_crypto import (
    configure_uid_crypto,
    reset_uid_crypto_for_testing,
    uid_decrypt,
    uid_encrypt,
    uid_rotate_encryption,
)

OLD_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
NEW_KEY = "MTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTE="


def test_uid_encryption_rotation_preserves_existing_ciphertext() -> None:
    legacy_token = Fernet(OLD_KEY.encode("ascii")).encrypt(b"0123456789abcdef01234567").decode()
    configure_uid_crypto("test-only-hmac-key", NEW_KEY, (OLD_KEY,))
    try:
        assert uid_decrypt(legacy_token) == "0123456789abcdef01234567"

        rotated = uid_rotate_encryption(legacy_token)
        assert uid_decrypt(rotated) == "0123456789abcdef01234567"
        assert (
            Fernet(NEW_KEY.encode("ascii")).decrypt(rotated.encode()) == b"0123456789abcdef01234567"
        )

        fresh = uid_encrypt("0123456789ABCDEF01234567")
        assert (
            Fernet(NEW_KEY.encode("ascii")).decrypt(fresh.encode()) == b"0123456789abcdef01234567"
        )
    finally:
        reset_uid_crypto_for_testing()


def test_uid_encryption_keyring_rejects_duplicate_keys() -> None:
    try:
        configure_uid_crypto("test-only-hmac-key", NEW_KEY, (NEW_KEY,))
    except ValueError as error:
        assert "duplicates" in str(error)
    else:
        raise AssertionError("duplicate UID keys must be rejected")
    finally:
        reset_uid_crypto_for_testing()


def test_settings_accept_distinct_previous_encryption_key(tmp_path) -> None:
    from bot.core.settings import BotProcessSettings

    value = BotProcessSettings.from_env(
        {
            "BOT_TOKEN": "123:token",
            "DATABASE_URL": "postgresql://localhost/test",
            "AUCTION_CHANNEL_ID": "-100123",
            "DISCUSSION_CHAT_ID": "-100456",
            "UID_HASH_KEY": "test-only-hmac-key",
            "UID_ENC_KEY": NEW_KEY,
            "UID_ENC_KEY_PREVIOUS": OLD_KEY,
        },
        project_root=tmp_path,
    )
    assert value.bot.uid_enc_key_previous == OLD_KEY


def test_settings_reject_duplicate_previous_encryption_key(tmp_path) -> None:
    from bot.core.settings import BotProcessSettings, ConfigurationError

    try:
        BotProcessSettings.from_env(
            {
                "BOT_TOKEN": "123:token",
                "DATABASE_URL": "postgresql://localhost/test",
                "AUCTION_CHANNEL_ID": "-100123",
                "DISCUSSION_CHAT_ID": "-100456",
                "UID_HASH_KEY": "test-only-hmac-key",
                "UID_ENC_KEY": NEW_KEY,
                "UID_ENC_KEY_PREVIOUS": NEW_KEY,
            },
            project_root=tmp_path,
        )
    except ConfigurationError as error:
        assert "UID_ENC_KEY_PREVIOUS" in str(error)
    else:
        raise AssertionError("duplicate previous UID key must be rejected")
