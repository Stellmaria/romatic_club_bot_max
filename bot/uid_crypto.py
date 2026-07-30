import os
import hmac
import hashlib
from pathlib import Path

from dotenv import load_dotenv
from cryptography.fernet import Fernet


# Подгружаем .env рядом с корнем проекта
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

_uid_hash_key = os.getenv("UID_HASH_KEY")
_uid_enc_key = os.getenv("UID_ENC_KEY")

if not _uid_hash_key:
    raise RuntimeError("UID_HASH_KEY is missing in environment/.env")
if not _uid_enc_key:
    raise RuntimeError("UID_ENC_KEY is missing in environment/.env")

UID_HASH_KEY = _uid_hash_key.encode("utf-8")
UID_ENC_KEY = _uid_enc_key.encode("utf-8")

_fernet = Fernet(UID_ENC_KEY)


def norm_uid(uid: str | None) -> str:
    return (uid or "").strip().lower().replace(" ", "")


def uid_hash(uid: str) -> str:
    value = norm_uid(uid).encode("utf-8")
    return hmac.new(UID_HASH_KEY, value, hashlib.sha256).hexdigest()


def uid_encrypt(uid: str) -> str:
    value = norm_uid(uid).encode("utf-8")
    return _fernet.encrypt(value).decode("utf-8")


def uid_decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode("utf-8")).decode("utf-8")


def uid_last4(uid: str) -> str:
    s = norm_uid(uid)
    return s[-4:] if len(s) >= 4 else s


def mask_uid(uid: str) -> str:
    s = norm_uid(uid)
    if len(s) <= 8:
        return s
    return f"{s[:4]}…{s[-4:]}"


def mask_uid_by_last4(last4: str | None) -> str:
    s = (last4 or "").strip()
    return f"••••••••••••••••••••{s}" if s else "—"