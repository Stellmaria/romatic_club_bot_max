-- Reserve auction completion for a single worker and keep failure diagnostics.

ALTER TABLE IF EXISTS public.auctions
    ADD COLUMN IF NOT EXISTS finalization_started_at timestamptz,
    ADD COLUMN IF NOT EXISTS finalization_finished_at timestamptz,
    ADD COLUMN IF NOT EXISTS finalization_attempts integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS finalization_error text;

-- Preserve every legacy state before adding the two finalization states.
-- `publishing` is an operational claim used by auction_publisher_loop and may
-- legitimately be present while this migration is applied.
ALTER TABLE IF EXISTS public.auctions
    DROP CONSTRAINT IF EXISTS chk_auctions_status;

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
              'pending'::text,
              'scheduled'::text,
              'publishing'::text,
              'active'::text,
              'finalizing'::text,
              'finalization_failed'::text,
              'finished'::text,
              'rejected'::text
          ])
        GROUP BY status::text
    ) AS invalid;

    IF unsupported_statuses IS NOT NULL THEN
        RAISE EXCEPTION
            'Cannot install chk_auctions_status; unsupported auction statuses: %',
            unsupported_statuses
            USING HINT = 'Normalize the listed rows before rerunning migration 002.';
    END IF;
END
$$;

ALTER TABLE IF EXISTS public.auctions
    ADD CONSTRAINT chk_auctions_status
    CHECK (status::text = ANY (ARRAY[
        'pending'::text,
        'scheduled'::text,
        'publishing'::text,
        'active'::text,
        'finalizing'::text,
        'finalization_failed'::text,
        'finished'::text,
        'rejected'::text
    ]));

CREATE INDEX IF NOT EXISTS ix_auctions_due_finalization
    ON public.auctions(status, end_time)
    WHERE status IN ('active', 'finalizing', 'finalization_failed');
