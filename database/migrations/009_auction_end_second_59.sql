-- Restore the established auction closing convention: a standard slot stays
-- open through second 59 of its displayed ending minute.  Example:
-- 22:00:00 -> 22:30:59.  Migration 008 remains unchanged because it may
-- already be recorded with its checksum in schema_migrations.

CREATE OR REPLACE FUNCTION public.auctions_fix_end_time()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    NEW.end_time :=
        date_trunc('minute', NEW.start_time + INTERVAL '30 minutes')
        + INTERVAL '59 seconds';
    RETURN NEW;
END
$body$;

-- Restore upcoming standard-duration rows that migration 008 may have shortened
-- to an exact 30 minutes.  Deliberately extended auctions are left untouched.
UPDATE public.auctions
SET end_time =
    date_trunc('minute', start_time + INTERVAL '30 minutes')
    + INTERVAL '59 seconds'
WHERE status IN ('scheduled', 'publication_failed')
  AND start_time > now()
  AND end_time >= start_time + INTERVAL '29 minutes'
  AND end_time <= start_time + INTERVAL '31 minutes 5 seconds'
  AND end_time IS DISTINCT FROM (
      date_trunc('minute', start_time + INTERVAL '30 minutes')
      + INTERVAL '59 seconds'
  );
