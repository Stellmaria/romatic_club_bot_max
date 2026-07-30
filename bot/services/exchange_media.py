from __future__ import annotations

from db.core import get_db_pool


async def get_exchange_cover_media(batch_id: int) -> tuple[str | None, str]:
    """Return the first card media, then the proof photo as fallback."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT c.image_id AS media_id,
                   COALESCE(NULLIF(c.media_type, ''), 'photo') AS kind
            FROM public.exchange_items ei
            JOIN public.cards c ON c.card_id = ei.card_id
            WHERE ei.batch_id = $1
            ORDER BY ei.item_id
            LIMIT 1
            """,
            int(batch_id),
        )
        if row:
            media_id = str(row["media_id"] or "").strip()
            kind = str(row["kind"] or "photo").strip().lower()
            if kind not in {"photo", "video", "animation"}:
                kind = "photo"
            if media_id:
                return media_id, kind

        proof_id = await conn.fetchval(
            """
            SELECT proof_photo_id
            FROM public.exchange_batches
            WHERE batch_id = $1
            """,
            int(batch_id),
        )

    proof = str(proof_id or "").strip()
    if proof and proof.upper() != "NO_PROOF":
        return proof, "photo"
    return None, "photo"

