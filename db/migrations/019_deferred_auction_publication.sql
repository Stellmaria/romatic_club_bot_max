-- Safe expand migration for deferred Telegram video publications.
-- Existing production violations are repaired by the explicit operator tool;
-- NOT VALID constraints still protect every new or updated row immediately.

SET search_path = public, pg_catalog;

ALTER TABLE public.auctions
    DROP CONSTRAINT IF EXISTS chk_auctions_status;

ALTER TABLE public.auctions
    ADD CONSTRAINT chk_auctions_status
    CHECK (
        status IN (
            'draft',
            'moderation',
            'pending',
            'approved',
            'scheduled',
            'publishing',
            'publication_deferred',
            'publication_failed',
            'active',
            'finalizing',
            'finalization_failed',
            'finished',
            'rejected',
            'cancelled',
            'closed'
        )
    );

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.auctions'::regclass
          AND conname = 'chk_auctions_message_id_positive'
    ) THEN
        ALTER TABLE public.auctions
            ADD CONSTRAINT chk_auctions_message_id_positive
            CHECK (message_id IS NULL OR message_id > 0)
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.auctions'::regclass
          AND conname = 'chk_auctions_unpublished_state_has_no_message'
    ) THEN
        ALTER TABLE public.auctions
            ADD CONSTRAINT chk_auctions_unpublished_state_has_no_message
            CHECK (
                status NOT IN (
                    'scheduled',
                    'publishing',
                    'publication_deferred'
                )
                OR message_id IS NULL
            )
            NOT VALID;
    END IF;
END $$;

ALTER TABLE public.telegram_outbox
    DROP CONSTRAINT IF EXISTS chk_telegram_outbox_method;

ALTER TABLE public.telegram_outbox
    ADD CONSTRAINT chk_telegram_outbox_method
    CHECK (
        method IN (
            'send_message',
            'copy_message',
            'refresh_auction_publication'
        )
    );

CREATE INDEX IF NOT EXISTS ix_auctions_publication_deferred
    ON public.auctions (publication_started_at, auction_id)
    WHERE status = 'publication_deferred' AND message_id IS NULL;
