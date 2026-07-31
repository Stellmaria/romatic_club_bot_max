"""Persistence boundary for exchange cover-media lookup."""

from __future__ import annotations

import asyncpg


class ExchangeMediaRepository:
    """Resolve exchange media using an explicitly supplied application pool."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def cover_media(self, batch_id: int) -> tuple[str | None, str]:
        """Return the first card media, then the proof photo as fallback.

        Older databases may not have ``cards.media_type`` yet. Keep the read
        path compatible by retrying without that column and treating the media
        as a photo.
        """

        async with self._pool.acquire() as connection:
            try:
                row = await connection.fetchrow(
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
            except Exception:
                row = await connection.fetchrow(
                    """
                    SELECT c.image_id AS media_id
                    FROM public.exchange_items ei
                    JOIN public.cards c ON c.card_id = ei.card_id
                    WHERE ei.batch_id = $1
                    ORDER BY ei.item_id
                    LIMIT 1
                    """,
                    int(batch_id),
                )
                kind = "photo"
            else:
                kind = str(row["kind"] or "photo").strip().lower() if row else "photo"

            if row:
                media_id = str(row["media_id"] or "").strip()
                if kind not in {"photo", "video", "animation"}:
                    kind = "photo"
                if media_id:
                    return media_id, kind

            proof_id = await connection.fetchval(
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


__all__ = ["ExchangeMediaRepository"]
