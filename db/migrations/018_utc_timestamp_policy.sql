-- Complete the UTC persistence contract for legacy timestamp columns.
--
-- Historical timestamp-without-time-zone values in this project were written
-- as Europe/Moscow wall-clock values. Converting with AT TIME ZONE preserves
-- the represented instant while making PostgreSQL/asyncpg round-trips aware.
-- This migration intentionally covers archived legacy application tables too;
-- no persisted instant in public is allowed to remain ambiguous afterwards.

DROP INDEX IF EXISTS public.idx_apb_post_day;

DO $$
DECLARE
    target record;
BEGIN
    FOR target IN
        SELECT table_name, column_name, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND data_type = 'timestamp without time zone'
        ORDER BY table_name, ordinal_position
    LOOP
        IF target.column_default IS NOT NULL THEN
            EXECUTE format(
                'ALTER TABLE public.%I ALTER COLUMN %I DROP DEFAULT',
                target.table_name,
                target.column_name
            );
        END IF;

        EXECUTE format(
            'ALTER TABLE public.%I ALTER COLUMN %I TYPE timestamptz '
            'USING %I AT TIME ZONE %L',
            target.table_name,
            target.column_name,
            target.column_name,
            'Europe/Moscow'
        );

        IF target.column_default IS NOT NULL THEN
            EXECUTE format(
                'ALTER TABLE public.%I ALTER COLUMN %I SET DEFAULT %s',
                target.table_name,
                target.column_name,
                target.column_default
            );
        END IF;
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_apb_post_day
    ON public.auction_posts_backfill (
        ((post_date_msk AT TIME ZONE 'Europe/Moscow')::date)
    );

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND data_type = 'timestamp without time zone'
    ) THEN
        RAISE EXCEPTION 'UTC timestamp policy migration left naive columns in public';
    END IF;
END $$;
