from dotenv import load_dotenv
from pathlib import Path
import asyncio

# грузим .env проекта
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from db.db import fetch, execute, get_db_pool
from bot.uid_crypto import uid_hash, uid_encrypt, uid_last4


async def migrate_user_uids() -> int:
    rows = await fetch("""
        SELECT user_id, uid
        FROM public.user_uids
        WHERE uid IS NOT NULL
          AND trim(uid) <> ''
          AND (uid_hash IS NULL OR uid_enc IS NULL OR uid_last4 IS NULL)
    """)
    count = 0

    for row in rows:
        uid = str(row["uid"]).strip()
        await execute(
            """
            UPDATE public.user_uids
            SET uid_hash = $2,
                uid_enc = $3,
                uid_last4 = $4
            WHERE user_id = $1
            """,
            int(row["user_id"]),
            uid_hash(uid),
            uid_encrypt(uid),
            uid_last4(uid),
        )
        count += 1

    return count


async def migrate_uid_verification_requests() -> int:
    rows = await fetch("""
        SELECT id, uid
        FROM public.uid_verification_requests
        WHERE uid IS NOT NULL
          AND trim(uid) <> ''
          AND (uid_hash IS NULL OR uid_enc IS NULL OR uid_last4 IS NULL)
    """)
    count = 0

    for row in rows:
        uid = str(row["uid"]).strip()
        await execute(
            """
            UPDATE public.uid_verification_requests
            SET uid_hash = $2,
                uid_enc = $3,
                uid_last4 = $4
            WHERE id = $1
            """,
            int(row["id"]),
            uid_hash(uid),
            uid_encrypt(uid),
            uid_last4(uid),
        )
        count += 1

    return count


async def migrate_uid_bans() -> int:
    rows = await fetch("""
        SELECT uid
        FROM public.uid_bans
        WHERE uid IS NOT NULL
          AND trim(uid) <> ''
          AND (uid_hash IS NULL OR uid_enc IS NULL OR uid_last4 IS NULL)
    """)
    count = 0

    for row in rows:
        uid = str(row["uid"]).strip()
        await execute(
            """
            UPDATE public.uid_bans
            SET uid_hash = $2,
                uid_enc = $3,
                uid_last4 = $4
            WHERE uid = $1
            """,
            uid,
            uid_hash(uid),
            uid_encrypt(uid),
            uid_last4(uid),
        )
        count += 1

    return count


async def main():
    await get_db_pool()

    a = await migrate_user_uids()
    b = await migrate_uid_verification_requests()
    c = await migrate_uid_bans()

    print(f"user_uids updated: {a}")
    print(f"uid_verification_requests updated: {b}")
    print(f"uid_bans updated: {c}")
    print("UID migration finished.")


if __name__ == "__main__":
    asyncio.run(main())