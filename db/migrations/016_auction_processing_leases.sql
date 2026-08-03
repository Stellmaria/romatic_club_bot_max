-- Canonical continuation: durable publication/finalization leases.
--
-- Repository code claims work with FOR UPDATE SKIP LOCKED and records each
-- processing attempt. These columns existed in init_db.sql but were absent
-- from the packaged migration chain, leaving upgraded installations behind.

ALTER TABLE public.auctions
    ADD COLUMN IF NOT EXISTS finalization_started_at timestamptz,
    ADD COLUMN IF NOT EXISTS finalization_finished_at timestamptz,
    ADD COLUMN IF NOT EXISTS finalization_attempts integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS finalization_error text,
    ADD COLUMN IF NOT EXISTS publication_started_at timestamptz,
    ADD COLUMN IF NOT EXISTS publication_finished_at timestamptz,
    ADD COLUMN IF NOT EXISTS publication_next_attempt_at timestamptz,
    ADD COLUMN IF NOT EXISTS publication_attempts integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS publication_error text;

ALTER TABLE public.auctions
    DROP CONSTRAINT IF EXISTS chk_auctions_processing_attempts;

ALTER TABLE public.auctions
    ADD CONSTRAINT chk_auctions_processing_attempts
    CHECK (finalization_attempts >= 0 AND publication_attempts >= 0);

CREATE INDEX IF NOT EXISTS ix_auctions_publication_due
    ON public.auctions (start_time, auction_id)
    WHERE status = 'scheduled' AND message_id IS NULL;

CREATE INDEX IF NOT EXISTS ix_auctions_publication_lease
    ON public.auctions (publication_started_at, auction_id)
    WHERE status = 'publishing' AND message_id IS NULL;

CREATE INDEX IF NOT EXISTS ix_auctions_finalization_due
    ON public.auctions (end_time, auction_id)
    WHERE status IN ('scheduled', 'active');

CREATE INDEX IF NOT EXISTS ix_auctions_finalization_lease
    ON public.auctions (finalization_started_at, auction_id)
    WHERE status = 'finalizing';
