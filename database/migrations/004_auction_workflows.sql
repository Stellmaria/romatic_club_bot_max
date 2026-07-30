-- Phase 4: safe publication leases, canonical workflow statuses and
-- transactional exchange batches.

ALTER TABLE public.auctions
    ADD COLUMN IF NOT EXISTS publication_started_at timestamptz,
    ADD COLUMN IF NOT EXISTS publication_finished_at timestamptz,
    ADD COLUMN IF NOT EXISTS publication_next_attempt_at timestamptz,
    ADD COLUMN IF NOT EXISTS publication_attempts integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS publication_error text;

ALTER TABLE public.auctions
    DROP CONSTRAINT IF EXISTS chk_auctions_status;

-- Legacy schemas fixed every lot to exactly 31 minutes. That conflicts with
-- moderator restart/extension commands and with explicit rescheduling.
ALTER TABLE public.auctions
    DROP CONSTRAINT IF EXISTS auctions_end_eq_start_plus_31_window,
    DROP CONSTRAINT IF EXISTS auctions_end_eq_start_plus_31,
    DROP CONSTRAINT IF EXISTS chk_auctions_time_order;

DO $$
DECLARE
    unsupported_statuses text;
BEGIN
    SELECT string_agg(format('%s (%s row(s))', status, row_count), ', ' ORDER BY status)
    INTO unsupported_statuses
    FROM (
        SELECT status::text AS status, count(*) AS row_count
        FROM public.auctions
        WHERE status IS NOT NULL
          AND status::text <> ALL (ARRAY[
              'draft', 'moderation', 'pending', 'approved', 'scheduled',
              'publishing', 'publication_failed', 'active', 'finalizing',
              'finalization_failed', 'finished', 'rejected', 'cancelled', 'closed'
          ]::text[])
        GROUP BY status::text
    ) unknown;

    IF unsupported_statuses IS NOT NULL THEN
        RAISE EXCEPTION
            'Cannot install Phase 4 auction workflow; unsupported statuses: %',
            unsupported_statuses;
    END IF;
END $$;

ALTER TABLE public.auctions
    ADD CONSTRAINT chk_auctions_status
    CHECK (status::text = ANY (ARRAY[
        'draft', 'moderation', 'pending', 'approved', 'scheduled',
        'publishing', 'publication_failed', 'active', 'finalizing',
        'finalization_failed', 'finished', 'rejected', 'cancelled', 'closed'
    ]::text[]));

ALTER TABLE public.auctions
    ADD CONSTRAINT chk_auctions_time_order
    CHECK (
        status::text <> ALL (ARRAY[
            'scheduled', 'publishing', 'publication_failed', 'active',
            'finalizing', 'finalization_failed', 'finished'
        ]::text[])
        OR end_time > start_time
    );

CREATE INDEX IF NOT EXISTS ix_auctions_publication_queue
    ON public.auctions (status, publication_next_attempt_at, start_time, auction_id)
    WHERE message_id IS NULL
      AND status IN ('scheduled', 'publishing', 'publication_failed');

CREATE INDEX IF NOT EXISTS ix_auctions_schedule_conflicts
    ON public.auctions (start_time, end_time, auction_id)
    WHERE status IN ('scheduled', 'publishing', 'active');

CREATE INDEX IF NOT EXISTS ix_bids_auction_lowest_winner_order
    ON public.bids (auction_id, amount ASC, placed_at ASC, bid_id ASC);

ALTER TABLE public.exchange_batches
    ADD COLUMN IF NOT EXISTS moderator_id bigint,
    ADD COLUMN IF NOT EXISTS moderator_username text,
    ADD COLUMN IF NOT EXISTS moderator_comment text,
    ADD COLUMN IF NOT EXISTS moderated_at timestamptz,
    ADD COLUMN IF NOT EXISTS moderated_by bigint,
    ADD COLUMN IF NOT EXISTS moderated_username text,
    ADD COLUMN IF NOT EXISTS moderated_comment text,
    ADD COLUMN IF NOT EXISTS posted_chat_id bigint,
    ADD COLUMN IF NOT EXISTS posted_message_id bigint,
    ADD COLUMN IF NOT EXISTS posted_at timestamptz,
    ADD COLUMN IF NOT EXISTS publication_started_at timestamptz,
    ADD COLUMN IF NOT EXISTS publication_finished_at timestamptz,
    ADD COLUMN IF NOT EXISTS publication_error text,
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

ALTER TABLE public.exchange_batches
    DROP CONSTRAINT IF EXISTS chk_exchange_batches_status;

DO $$
DECLARE
    unsupported_statuses text;
BEGIN
    SELECT string_agg(format('%s (%s row(s))', status, row_count), ', ' ORDER BY status)
    INTO unsupported_statuses
    FROM (
        SELECT status::text AS status, count(*) AS row_count
        FROM public.exchange_batches
        WHERE status IS NOT NULL
          AND status::text <> ALL (ARRAY[
              'pending', 'approved', 'rejected', 'publishing',
              'publication_failed', 'published', 'deleted'
          ]::text[])
        GROUP BY status::text
    ) unknown;

    IF unsupported_statuses IS NOT NULL THEN
        RAISE EXCEPTION
            'Cannot install Phase 4 exchange workflow; unsupported statuses: %',
            unsupported_statuses;
    END IF;
END $$;

ALTER TABLE public.exchange_batches
    ADD CONSTRAINT chk_exchange_batches_status
    CHECK (status = ANY (ARRAY[
        'pending', 'approved', 'rejected', 'publishing',
        'publication_failed', 'published', 'deleted'
    ]::text[]));

CREATE UNIQUE INDEX IF NOT EXISTS ux_exchange_batches_posted_message
    ON public.exchange_batches (posted_chat_id, posted_message_id)
    WHERE posted_chat_id IS NOT NULL AND posted_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_exchange_batches_moderation_queue
    ON public.exchange_batches (status, created_at, batch_id)
    WHERE deleted_at IS NULL;

-- Multiple copies of the same card are meaningful for an exchange batch.
-- The old partial unique index made the UI's copies mode fail at runtime.
DROP INDEX IF EXISTS public.ux_exchange_items_batch_card;
CREATE INDEX IF NOT EXISTS ix_exchange_items_batch_card
    ON public.exchange_items (batch_id, card_id)
    WHERE card_id IS NOT NULL;
