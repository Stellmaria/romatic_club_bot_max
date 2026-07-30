-- 004_schema_cleanup.sql
-- Безопасная очистка дубликатов, появившихся в старых ручных дампах.
SET search_path = public, pg_catalog;

ALTER TABLE IF EXISTS public.auctions
    DROP CONSTRAINT IF EXISTS auctions_end_eq_start_plus_31_window;

ALTER TABLE IF EXISTS public.bids
    DROP CONSTRAINT IF EXISTS ck_bids_positive;

ALTER TABLE IF EXISTS public.presets
    DROP CONSTRAINT IF EXISTS presets_key_key;

DROP INDEX IF EXISTS public.uq_market_listing_items;

-- В exchange_batches.batch_id используется bigint, поэтому зависимый ключ тоже должен быть bigint.
ALTER TABLE IF EXISTS public.exchange_print_stats
    ALTER COLUMN batch_id TYPE bigint USING batch_id::bigint;
