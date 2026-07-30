-- 009_auction_type_and_free_currencies.sql
-- Stores the currencies accepted by a free auction separately from the
-- compatibility scalar currency column. Existing rows keep their old currency.

SET search_path = public, pg_catalog;

ALTER TABLE public.auctions
    ADD COLUMN IF NOT EXISTS accepted_currencies text[];

UPDATE public.auctions
SET accepted_currencies = ARRAY[currency]::text[]
WHERE accepted_currencies IS NULL
   OR cardinality(accepted_currencies) = 0;

ALTER TABLE public.auctions
    ALTER COLUMN accepted_currencies SET DEFAULT ARRAY['чашки']::text[],
    ALTER COLUMN accepted_currencies SET NOT NULL;

ALTER TABLE public.auctions
    DROP CONSTRAINT IF EXISTS chk_auctions_accepted_currencies;

ALTER TABLE public.auctions
    ADD CONSTRAINT chk_auctions_accepted_currencies
    CHECK (
        cardinality(accepted_currencies) BETWEEN 1 AND 2
        AND accepted_currencies <@ ARRAY['алмазы', 'чашки', 'сокровища']::text[]
    );

CREATE OR REPLACE FUNCTION public.auctions_sync_accepted_currencies()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF lower(COALESCE(NEW.auction_kind, 'standard')) <> 'free' THEN
        NEW.accepted_currencies := ARRAY[NEW.currency]::text[];
    ELSIF NEW.accepted_currencies IS NULL
       OR cardinality(NEW.accepted_currencies) = 0 THEN
        NEW.accepted_currencies := ARRAY[NEW.currency]::text[];
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_auctions_sync_accepted_currencies ON public.auctions;
CREATE TRIGGER trg_auctions_sync_accepted_currencies
BEFORE INSERT OR UPDATE OF currency, accepted_currencies, auction_kind ON public.auctions
FOR EACH ROW
EXECUTE FUNCTION public.auctions_sync_accepted_currencies();

CREATE INDEX IF NOT EXISTS idx_auctions_kind_start_time
    ON public.auctions (auction_kind, start_time);
