-- Align persisted auction durations and database triggers with the moderation
-- slot policy. Different lots may share a 30-minute publication slot; only the
-- same card for at least one of the same owners is prohibited by the workflow.

DO $slot_policy$
BEGIN
    IF to_regclass('public.auctions') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS trg_auctions_fix_end_time ON public.auctions;
    END IF;
END
$slot_policy$;

-- Historical handlers and triggers stored a nominal half-hour slot as either
-- 30:59 or 31:00. Normalize only future, not-yet-active rows so adjacent slots
-- no longer overlap while preserving deliberately extended active auctions.
UPDATE public.auctions
SET end_time = start_time + INTERVAL '30 minutes'
WHERE status IN ('scheduled', 'publication_failed')
  AND start_time > now()
  AND end_time > start_time + INTERVAL '30 minutes'
  AND end_time <= start_time + INTERVAL '31 minutes 5 seconds';
