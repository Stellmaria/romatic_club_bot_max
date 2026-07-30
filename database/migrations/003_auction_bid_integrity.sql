-- Phase 3: one Telegram message may create at most one bid.
-- Existing duplicates are archived before cleanup so the migration is reversible.

CREATE TABLE IF NOT EXISTS public.bid_duplicate_archive (
    archive_id bigserial PRIMARY KEY,
    original_bid_id bigint NOT NULL,
    bid_payload jsonb NOT NULL,
    archived_at timestamptz NOT NULL DEFAULT now(),
    reason text NOT NULL DEFAULT 'duplicate discussion_message_id'
);

WITH ranked AS (
    SELECT b.*,
           ROW_NUMBER() OVER (
               PARTITION BY b.discussion_message_id
               ORDER BY b.bid_id ASC
           ) AS duplicate_rank
    FROM public.bids b
    WHERE b.discussion_message_id IS NOT NULL
), archived AS (
    INSERT INTO public.bid_duplicate_archive (original_bid_id, bid_payload)
    SELECT bid_id, to_jsonb(ranked) - 'duplicate_rank'
    FROM ranked
    WHERE duplicate_rank > 1
    ON CONFLICT DO NOTHING
    RETURNING original_bid_id
)
DELETE FROM public.bids b
USING ranked r
WHERE b.bid_id = r.bid_id
  AND r.duplicate_rank > 1;

CREATE UNIQUE INDEX IF NOT EXISTS ux_bids_discussion_message_id
    ON public.bids (discussion_message_id)
    WHERE discussion_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_bids_auction_winner_order
    ON public.bids (auction_id, amount DESC, placed_at ASC, bid_id ASC);

CREATE INDEX IF NOT EXISTS ix_auctions_discussion_active
    ON public.auctions (discussion_message_id, status)
    WHERE discussion_message_id IS NOT NULL;
