-- Reverse/free auctions: tea, diamonds or both; free auctions may also use custom combo terms.
SET search_path = public, pg_catalog;

ALTER TABLE public.auctions
    ADD COLUMN IF NOT EXISTS custom_offer_terms text;

CREATE OR REPLACE FUNCTION public.auctions_sync_accepted_currencies()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF lower(COALESCE(NEW.auction_kind, 'standard')) NOT IN ('free', 'reverse') THEN
        NEW.accepted_currencies := ARRAY[NEW.currency]::text[];
        NEW.custom_offer_terms := NULL;
    ELSIF NEW.accepted_currencies IS NULL
       OR cardinality(NEW.accepted_currencies) = 0 THEN
        NEW.accepted_currencies := ARRAY[NEW.currency]::text[];
    END IF;

    IF lower(COALESCE(NEW.auction_kind, 'standard')) <> 'free' THEN
        NEW.custom_offer_terms := NULL;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_auctions_sync_accepted_currencies ON public.auctions;
CREATE TRIGGER trg_auctions_sync_accepted_currencies
BEFORE INSERT OR UPDATE OF currency, accepted_currencies, custom_offer_terms, auction_kind
ON public.auctions
FOR EACH ROW
EXECUTE FUNCTION public.auctions_sync_accepted_currencies();

CREATE INDEX IF NOT EXISTS idx_auctions_kind_currency
    ON public.auctions (auction_kind, currency);
