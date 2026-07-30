-- Обратный аукцион не использует start_price как фиксированный потолок.
-- Первая ставка задаёт исходную точку, последующие должны понижаться на шаг.

CREATE OR REPLACE FUNCTION public.is_valid_bid(
    p_auction_id integer,
    p_amount integer
) RETURNS boolean
STABLE
LANGUAGE plpgsql
AS $$
DECLARE
    cur text;
    kind text;
    start_p integer;
    step integer;
    min_cur integer;
    anchor integer;
    best_b integer;
BEGIN
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RETURN false;
    END IF;

    SELECT lower(trim(a.currency)),
           lower(trim(COALESCE(a.auction_kind, 'standard'))),
           COALESCE(a.start_price, 0)
      INTO cur, kind, start_p
      FROM public.auctions a
     WHERE a.auction_id = p_auction_id;

    IF NOT FOUND OR cur IS NULL THEN
        RETURN false;
    END IF;

    IF cur IN ('💎','алмаз','алмазы','diamond','diamonds') THEN cur := 'алмазы'; END IF;
    IF cur IN ('🍵','чай','чашки','cups') THEN cur := 'чашки'; END IF;
    IF cur IN ('🪙','сокровища','treasure','treasures') THEN cur := 'сокровища'; END IF;

    IF cur = 'алмазы' THEN
        step := 10; min_cur := 30;
    ELSIF cur = 'чашки' THEN
        step := 2; min_cur := 2;
    ELSIF cur = 'сокровища' THEN
        step := 10; min_cur := 0;
    ELSE
        RETURN false;
    END IF;

    IF kind = 'reverse' THEN
        IF p_amount < step OR (step > 1 AND p_amount % step <> 0) THEN
            RETURN false;
        END IF;

        SELECT min(b.amount)
          INTO best_b
          FROM public.bids b
         WHERE b.auction_id = p_auction_id;

        IF best_b IS NULL THEN
            RETURN true;
        END IF;

        RETURN p_amount <= best_b - step;
    END IF;

    anchor := GREATEST(start_p, min_cur);
    IF p_amount < anchor THEN
        RETURN false;
    END IF;
    IF step > 1 AND ((p_amount - anchor) % step) <> 0 THEN
        RETURN false;
    END IF;

    SELECT max(b.amount)
      INTO best_b
      FROM public.bids b
     WHERE b.auction_id = p_auction_id;

    IF best_b IS NULL THEN
        RETURN true;
    END IF;

    RETURN p_amount >= best_b + step;
END;
$$;
