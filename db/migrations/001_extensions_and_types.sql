-- 001_extensions_and_types.sql
-- Расширения и прикладные ENUM-типы. Внутренние функции pg_trgm не дампятся вручную.

CREATE SCHEMA IF NOT EXISTS public;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typname = 'obtain_type'
    ) THEN
        CREATE TYPE public.obtain_type AS ENUM ('diamonds', 'tea', 'cups', 'treasures');
    END IF;
END
$migration$;

-- Поддерживаем и старое значение tea, и новое cups. Это позволяет обновить старую БД
-- без InvalidTextRepresentationError и не ломает старые обработчики.
ALTER TYPE public.obtain_type ADD VALUE IF NOT EXISTS 'diamonds';
ALTER TYPE public.obtain_type ADD VALUE IF NOT EXISTS 'tea';
ALTER TYPE public.obtain_type ADD VALUE IF NOT EXISTS 'cups';
ALTER TYPE public.obtain_type ADD VALUE IF NOT EXISTS 'treasures';

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typname = 'deck_type'
    ) THEN
        CREATE TYPE public.deck_type AS ENUM ('resource', 'roulette');
    END IF;
END
$migration$;

ALTER TYPE public.deck_type ADD VALUE IF NOT EXISTS 'resource';
ALTER TYPE public.deck_type ADD VALUE IF NOT EXISTS 'roulette';

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typname = 'market_currency'
    ) THEN
        CREATE TYPE public.market_currency AS ENUM ('cups', 'diamonds', 'treasures', 'cash');
    END IF;
END
$migration$;

ALTER TYPE public.market_currency ADD VALUE IF NOT EXISTS 'cups';
ALTER TYPE public.market_currency ADD VALUE IF NOT EXISTS 'diamonds';
ALTER TYPE public.market_currency ADD VALUE IF NOT EXISTS 'treasures';
ALTER TYPE public.market_currency ADD VALUE IF NOT EXISTS 'cash';

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typname = 'listing_status'
    ) THEN
        CREATE TYPE public.listing_status AS ENUM ('active', 'hidden', 'sold', 'archived', 'deleted');
    END IF;
END
$migration$;

ALTER TYPE public.listing_status ADD VALUE IF NOT EXISTS 'active';
ALTER TYPE public.listing_status ADD VALUE IF NOT EXISTS 'hidden';
ALTER TYPE public.listing_status ADD VALUE IF NOT EXISTS 'sold';
ALTER TYPE public.listing_status ADD VALUE IF NOT EXISTS 'archived';
ALTER TYPE public.listing_status ADD VALUE IF NOT EXISTS 'deleted';

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typname = 'offer_kind'
    ) THEN
        CREATE TYPE public.offer_kind AS ENUM (
            'cards', 'cups', 'diamonds', 'treasures', 'whole_deck', 'service'
        );
    END IF;
END
$migration$;

ALTER TYPE public.offer_kind ADD VALUE IF NOT EXISTS 'cards';
ALTER TYPE public.offer_kind ADD VALUE IF NOT EXISTS 'cups';
ALTER TYPE public.offer_kind ADD VALUE IF NOT EXISTS 'diamonds';
ALTER TYPE public.offer_kind ADD VALUE IF NOT EXISTS 'treasures';
ALTER TYPE public.offer_kind ADD VALUE IF NOT EXISTS 'whole_deck';
ALTER TYPE public.offer_kind ADD VALUE IF NOT EXISTS 'service';
