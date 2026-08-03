from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from bot.core.environment import load_project_environment, resolve_project_root
from bot.core.settings import BotSettings, DatabaseSettings
from bot.uid_crypto import (
    configure_uid_crypto,
    norm_uid,
    uid_decrypt,
    uid_encrypt,
    uid_hash,
    uid_last4,
)
from db.core import (
    close_db,
    get_db_pool,
)
from db.migrator import apply_migrations

UID_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)


@dataclass(slots=True)
class MigrationStats:
    user_uids: int = 0
    requests: int = 0
    bans: int = 0


def _plain_uid(value: object) -> str | None:
    candidate = norm_uid(str(value or ""))
    return candidate if UID_RE.fullmatch(candidate) else None


def _validated_encrypted_uid(
    *, digest: str, encrypted: str, row_label: str
) -> tuple[str, str, str]:
    try:
        plaintext = uid_decrypt(encrypted)
    except Exception as exc:
        raise RuntimeError(f"{row_label} contains an unreadable encrypted UID") from exc

    calculated = uid_hash(plaintext)
    if calculated != digest:
        raise RuntimeError(f"{row_label} contains mismatched uid_hash and uid_enc values")
    return calculated, encrypted, uid_last4(plaintext)


async def migrate_user_uids(conn) -> int:
    rows = await conn.fetch(
        "SELECT user_id, uid, uid_hash, uid_enc, uid_last4 FROM public.user_uids FOR UPDATE"
    )
    prepared: list[tuple[int, str, str, str]] = []
    owners_by_hash: dict[str, int] = {}

    for row in rows:
        user_id = int(row["user_id"])
        plaintext = _plain_uid(row.get("uid"))
        existing_hash = str(row.get("uid_hash") or "").strip()
        existing_enc = str(row.get("uid_enc") or "").strip()

        if plaintext:
            digest = uid_hash(plaintext)
            encrypted = uid_encrypt(plaintext)
            last4 = uid_last4(plaintext)
        elif existing_hash and existing_enc:
            digest, encrypted, last4 = _validated_encrypted_uid(
                digest=existing_hash,
                encrypted=existing_enc,
                row_label=f"user_uids user_id={user_id}",
            )
        else:
            raise RuntimeError(f"user_uids row for user_id={user_id} cannot be migrated safely")

        previous_owner = owners_by_hash.setdefault(digest, user_id)
        if previous_owner != user_id:
            raise RuntimeError(
                f"UID collision: user_id={previous_owner} and user_id={user_id} have the same UID hash"
            )
        prepared.append((user_id, digest, encrypted, last4))

    for user_id, digest, encrypted, last4 in prepared:
        await conn.execute(
            """
            UPDATE public.user_uids
            SET uid=$2,
                uid_hash=$2,
                uid_enc=$3,
                uid_last4=$4,
                updated_at=now()
            WHERE user_id=$1
            """,
            user_id,
            digest,
            encrypted,
            last4,
        )
    return len(prepared)


async def migrate_requests(conn) -> int:
    rows = await conn.fetch("""
        SELECT id, uid, uid_hash, uid_enc, uid_last4
        FROM public.uid_verification_requests
        FOR UPDATE
        """)
    changed = 0
    for row in rows:
        request_id = int(row["id"])
        plaintext = _plain_uid(row.get("uid"))
        existing_hash = str(row.get("uid_hash") or "").strip()
        existing_enc = str(row.get("uid_enc") or "").strip()

        if plaintext:
            digest = uid_hash(plaintext)
            encrypted = uid_encrypt(plaintext)
            last4 = uid_last4(plaintext)
        elif existing_hash and existing_enc:
            digest, encrypted, last4 = _validated_encrypted_uid(
                digest=existing_hash,
                encrypted=existing_enc,
                row_label=f"uid_verification_requests id={request_id}",
            )
        else:
            raise RuntimeError(
                f"uid_verification_requests id={request_id} cannot be migrated safely"
            )

        await conn.execute(
            """
            UPDATE public.uid_verification_requests
            SET uid=$2, uid_hash=$2, uid_enc=$3, uid_last4=$4
            WHERE id=$1
            """,
            request_id,
            digest,
            encrypted,
            last4,
        )
        changed += 1
    return changed


async def migrate_bans(conn) -> int:
    rows = await conn.fetch(
        "SELECT uid, uid_hash, uid_enc, uid_last4 FROM public.uid_bans FOR UPDATE"
    )
    prepared: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()

    for row in rows:
        legacy_key = str(row.get("uid") or "")
        plaintext = _plain_uid(legacy_key)
        existing_hash = str(row.get("uid_hash") or "").strip()
        existing_enc = str(row.get("uid_enc") or "").strip()

        if plaintext:
            digest = uid_hash(plaintext)
            encrypted = uid_encrypt(plaintext)
            last4 = uid_last4(plaintext)
        elif existing_hash and existing_enc:
            digest, encrypted, last4 = _validated_encrypted_uid(
                digest=existing_hash,
                encrypted=existing_enc,
                row_label=f"uid_bans row {legacy_key!r}",
            )
        else:
            raise RuntimeError(f"uid_bans row {legacy_key!r} cannot be migrated safely")

        if digest in seen and digest != legacy_key:
            raise RuntimeError(f"Duplicate UID ban detected for hash {digest}")
        seen.add(digest)
        prepared.append((legacy_key, digest, encrypted, last4))

    for legacy_key, digest, encrypted, last4 in prepared:
        await conn.execute(
            """
            UPDATE public.uid_bans
            SET uid=$2, uid_hash=$2, uid_enc=$3, uid_last4=$4
            WHERE uid=$1
            """,
            legacy_key,
            digest,
            encrypted,
            last4,
        )
    return len(prepared)


async def assert_plaintext_scrubbed(conn) -> None:
    checks = {
        "user_uids": "SELECT count(*) FROM public.user_uids WHERE uid ~* '^[0-9a-f]{24}$'",
        "uid_verification_requests": (
            "SELECT count(*) FROM public.uid_verification_requests WHERE uid ~* '^[0-9a-f]{24}$'"
        ),
        "uid_bans": "SELECT count(*) FROM public.uid_bans WHERE uid ~* '^[0-9a-f]{24}$'",
    }
    leftovers = {name: int(await conn.fetchval(sql) or 0) for name, sql in checks.items()}
    dirty = {name: count for name, count in leftovers.items() if count}
    if dirty:
        raise RuntimeError(f"Plaintext UID rows remain after migration: {dirty}")


async def main(database: DatabaseSettings) -> None:
    pool = await get_db_pool(database)
    try:
        await apply_migrations(pool)

        async with pool.acquire() as conn, conn.transaction():
            stats = MigrationStats(
                user_uids=await migrate_user_uids(conn),
                requests=await migrate_requests(conn),
                bans=await migrate_bans(conn),
            )
            await assert_plaintext_scrubbed(conn)

        print(f"user_uids migrated: {stats.user_uids}")
        print(f"uid_verification_requests migrated: {stats.requests}")
        print(f"uid_bans migrated: {stats.bans}")
        print("UID migration finished; plaintext UID values were scrubbed.")
    finally:
        await close_db()


def run() -> None:
    project_root = resolve_project_root()
    load_project_environment(project_root)
    database = DatabaseSettings.from_env(project_root=project_root)
    bot = BotSettings.from_env(project_root=project_root)
    configure_uid_crypto(
        bot.uid_hash_key,
        bot.uid_enc_key,
        (bot.uid_enc_key_previous,),
    )
    asyncio.run(main(database))


if __name__ == "__main__":
    run()
