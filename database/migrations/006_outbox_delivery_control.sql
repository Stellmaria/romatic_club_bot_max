-- Phase 6: classify delivery certainty and support safe administrative review.

ALTER TABLE public.telegram_outbox
    ADD COLUMN IF NOT EXISTS topic text NOT NULL DEFAULT 'legacy',
    ADD COLUMN IF NOT EXISTS delivery_state text NOT NULL DEFAULT 'not_attempted',
    ADD COLUMN IF NOT EXISTS reviewed_at timestamptz,
    ADD COLUMN IF NOT EXISTS reviewed_by bigint,
    ADD COLUMN IF NOT EXISTS review_note text;

ALTER TABLE public.telegram_outbox
    DROP CONSTRAINT IF EXISTS chk_telegram_outbox_method;

ALTER TABLE public.telegram_outbox
    ADD CONSTRAINT chk_telegram_outbox_method
    CHECK (method IN ('send_message', 'copy_message'));

UPDATE public.telegram_outbox
SET delivery_state = CASE
    WHEN status = 'sent' THEN 'confirmed_sent'
    WHEN status IN ('processing', 'failed') THEN 'unknown'
    ELSE 'not_attempted'
END
WHERE delivery_state = 'not_attempted';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.telegram_outbox'::regclass
          AND conname = 'chk_telegram_outbox_delivery_state'
    ) THEN
        ALTER TABLE public.telegram_outbox
            ADD CONSTRAINT chk_telegram_outbox_delivery_state
            CHECK (delivery_state IN (
                'not_attempted',
                'unknown',
                'confirmed_sent',
                'confirmed_not_sent'
            ));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_telegram_outbox_failed_review
    ON public.telegram_outbox (delivery_state, updated_at DESC, outbox_id DESC)
    WHERE status = 'failed';

CREATE INDEX IF NOT EXISTS ix_telegram_outbox_topic_created
    ON public.telegram_outbox (topic, created_at DESC);
