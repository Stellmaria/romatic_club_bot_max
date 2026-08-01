ALTER TABLE public.bids
    ADD COLUMN IF NOT EXISTS currency TEXT;

UPDATE public.bids AS b
SET currency = a.currency
FROM public.auctions AS a
WHERE a.auction_id = b.auction_id
  AND (b.currency IS NULL OR btrim(b.currency) = '');

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
