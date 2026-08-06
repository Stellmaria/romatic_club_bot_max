BEGIN;

-- Python has supported AuctionKind.PREORDER for a while, but production still
-- rejected the value at the database boundary. Replace every historic
-- auction_kind check with the canonical list, including preorder.
DO $$
DECLARE
    constraint_name text;
BEGIN
    FOR constraint_name IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'public.auctions'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%auction_kind%'
    LOOP
        EXECUTE format(
            'ALTER TABLE public.auctions DROP CONSTRAINT %I',
            constraint_name
        );
    END LOOP;
END
$$;

ALTER TABLE public.auctions
    ADD CONSTRAINT auctions_auction_kind_chk
    CHECK (
        auction_kind::text = ANY (
            ARRAY[
                'standard',
                'preorder',
                'reverse',
                'fast',
                'free',
                'black',
                'exchange'
            ]::text[]
        )
    );

CREATE TABLE IF NOT EXISTS public.auction_preorders
(
    auction_id integer PRIMARY KEY
        REFERENCES public.auctions (auction_id) ON DELETE CASCADE,
    deck_id integer NOT NULL
        REFERENCES public.decks (id) ON DELETE RESTRICT,
    mode text NOT NULL,
    request_key text NOT NULL UNIQUE,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT chk_auction_preorders_mode
        CHECK (mode = ANY (ARRAY['items', 'whole_deck']::text[])),
    CONSTRAINT chk_auction_preorders_request_key
        CHECK (char_length(request_key) BETWEEN 16 AND 128)
);

CREATE INDEX IF NOT EXISTS idx_auction_preorders_deck_id
    ON public.auction_preorders (deck_id);

CREATE TABLE IF NOT EXISTS public.auction_preorder_items
(
    auction_id integer NOT NULL
        REFERENCES public.auction_preorders (auction_id) ON DELETE CASCADE,
    rarity text NOT NULL,
    quantity integer NOT NULL,
    CONSTRAINT auction_preorder_items_pkey PRIMARY KEY (auction_id, rarity),
    CONSTRAINT chk_auction_preorder_items_rarity
        CHECK (rarity = ANY (ARRAY['bronze', 'silver', 'gold', 'epic']::text[])),
    CONSTRAINT chk_auction_preorder_items_quantity
        CHECK (quantity BETWEEN 1 AND 99)
);

CREATE OR REPLACE FUNCTION public.enforce_preorder_item_mode()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    preorder_mode text;
BEGIN
    SELECT mode
    INTO preorder_mode
    FROM public.auction_preorders
    WHERE auction_id = NEW.auction_id;

    IF preorder_mode IS DISTINCT FROM 'items' THEN
        RAISE EXCEPTION 'preorder items require mode=items';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_enforce_preorder_item_mode
    ON public.auction_preorder_items;
CREATE TRIGGER trg_enforce_preorder_item_mode
    BEFORE INSERT OR UPDATE ON public.auction_preorder_items
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_preorder_item_mode();

CREATE OR REPLACE FUNCTION public.enforce_preorder_whole_deck_empty()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.mode = 'whole_deck'
       AND EXISTS (
           SELECT 1
           FROM public.auction_preorder_items
           WHERE auction_id = NEW.auction_id
       ) THEN
        RAISE EXCEPTION 'whole_deck preorder cannot contain separate items';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_enforce_preorder_whole_deck_empty
    ON public.auction_preorders;
CREATE TRIGGER trg_enforce_preorder_whole_deck_empty
    BEFORE UPDATE OF mode ON public.auction_preorders
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_preorder_whole_deck_empty();

COMMIT;
