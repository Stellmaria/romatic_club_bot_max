-- Deferred Telegram video publication and positive message-id invariants.
SET search_path = public, pg_catalog;

ALTER TABLE public.auctions
    DROP CONSTRAINT IF EXISTS chk_auctions_status;

ALTER TABLE public.auctions
    ADD CONSTRAINT chk_auctions_status
    CHECK (status IN (
        'draft','moderation','pending','approved','scheduled','publishing',
        'publication_deferred','publication_failed','active','finalizing',
        'finalization_failed','finished','rejected','cancelled','closed'
    ));

ALTER TABLE public.auctions
    ADD CONSTRAINT chk_auctions_message_id_positive
    CHECK (message_id IS NULL OR message_id > 0) NOT VALID;

-- Known invalid sentinel. Exact production IDs are repaired by the operator tool
-- after Telegram metadata has been verified; the migration never invents IDs.
UPDATE public.auctions
SET message_id = NULL,
    status = CASE WHEN status = 'finished' THEN 'publication_failed' ELSE status END,
    publication_error = COALESCE(publication_error, 'invalid Telegram message_id requires repair')
WHERE message_id <= 0;

ALTER TABLE public.auctions
    VALIDATE CONSTRAINT chk_auctions_message_id_positive;

ALTER TABLE public.auctions
    ADD CONSTRAINT chk_auctions_unpublished_state_has_no_message
    CHECK (
        status NOT IN ('scheduled','publishing','publication_deferred')
        OR message_id IS NULL
    ) NOT VALID;

ALTER TABLE public.auctions
    VALIDATE CONSTRAINT chk_auctions_unpublished_state_has_no_message;

CREATE INDEX IF NOT EXISTS ix_auctions_publication_deferred
    ON public.auctions (publication_started_at, auction_id)
    WHERE status = 'publication_deferred' AND message_id IS NULL;
