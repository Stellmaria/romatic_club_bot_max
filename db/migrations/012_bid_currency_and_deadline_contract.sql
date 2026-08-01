ALTER TABLE public.bids
    ADD COLUMN IF NOT EXISTS currency TEXT;

-- Legacy bids can violate the current cross-table bid rule after an auction's
-- price or currency changed. PostgreSQL rechecks every CHECK constraint when
-- this migration updates bids.currency, even though the constraint does not
-- reference that column. Preserve those historical rows while keeping the
-- same rule enforced for all future inserts and updates.
DO $migration$
DECLARE
    legacy_constraint_definition text;
BEGIN
    SELECT pg_get_constraintdef(constraint_row.oid, true)
      INTO legacy_constraint_definition
    FROM pg_catalog.pg_constraint AS constraint_row
    WHERE constraint_row.conrelid = 'public.bids'::regclass
      AND constraint_row.conname = 'chk_bids_step_and_min_by_currency'
      AND constraint_row.contype = 'c';

    IF legacy_constraint_definition IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE public.bids DROP CONSTRAINT %I',
            'chk_bids_step_and_min_by_currency'
        );
    END IF;

    UPDATE public.bids AS b
    SET currency = a.currency
    FROM public.auctions AS a
    WHERE a.auction_id = b.auction_id
      AND (b.currency IS NULL OR btrim(b.currency) = '');

    IF legacy_constraint_definition IS NOT NULL THEN
        legacy_constraint_definition := regexp_replace(
            legacy_constraint_definition,
            '\s+NOT VALID\s*$',
            '',
            'i'
        );
        EXECUTE format(
            'ALTER TABLE public.bids ADD CONSTRAINT %I %s NOT VALID',
            'chk_bids_step_and_min_by_currency',
            legacy_constraint_definition
        );
    END IF;
END
$migration$;

CREATE OR REPLACE FUNCTION public.fill_bid_currency()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.currency IS NULL OR btrim(NEW.currency) = '' THEN
        SELECT a.currency INTO NEW.currency
        FROM public.auctions AS a
        WHERE a.auction_id = NEW.auction_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_fill_bid_currency ON public.bids;
CREATE TRIGGER trg_fill_bid_currency
BEFORE INSERT OR UPDATE OF auction_id, currency ON public.bids
FOR EACH ROW EXECUTE FUNCTION public.fill_bid_currency();

ALTER TABLE public.bids
    ALTER COLUMN currency SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_bids_auction_currency_amount
    ON public.bids (auction_id, currency, amount, placed_at, bid_id);
