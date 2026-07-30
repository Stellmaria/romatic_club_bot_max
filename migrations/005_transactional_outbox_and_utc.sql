-- Phase 5: durable Telegram delivery and timezone-safe auction timestamps.
--
-- Historical timestamp-without-time-zone values in auctions/bids were written
-- and displayed as Europe/Moscow wall-clock time.  Preserve their instants by
-- attaching that zone before converting them to timestamptz (stored as UTC).

DROP INDEX IF EXISTS public.idx_auctions_start_time_date;

DO $$
DECLARE
    fix_end_time_trigger_ddl text;
    fix_end_time_trigger_enabled text;
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'auctions'
          AND column_name = 'start_time'
          AND data_type = 'timestamp without time zone'
    ) THEN
        -- PostgreSQL does not allow changing a column type while an
        -- UPDATE OF trigger explicitly depends on that column.  Preserve the
        -- live definition instead of hard-coding it: installations may have
        -- an older equivalent definition or a non-default enabled state.
        SELECT pg_get_triggerdef(t.oid, true), t.tgenabled::text
        INTO fix_end_time_trigger_ddl, fix_end_time_trigger_enabled
        FROM pg_catalog.pg_trigger AS t
        WHERE t.tgrelid = 'public.auctions'::regclass
          AND t.tgname = 'trg_auctions_fix_end_time'
          AND NOT t.tgisinternal;

        IF fix_end_time_trigger_ddl IS NOT NULL THEN
            DROP TRIGGER trg_auctions_fix_end_time ON public.auctions;
        END IF;

        ALTER TABLE public.auctions
            ALTER COLUMN start_time TYPE timestamptz
            USING start_time AT TIME ZONE 'Europe/Moscow';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'auctions'
          AND column_name = 'end_time'
          AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE public.auctions
            ALTER COLUMN end_time TYPE timestamptz
            USING end_time AT TIME ZONE 'Europe/Moscow';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'auctions'
          AND column_name = 'created_at'
          AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE public.auctions
            ALTER COLUMN created_at DROP DEFAULT;
        ALTER TABLE public.auctions
            ALTER COLUMN created_at TYPE timestamptz
            USING created_at AT TIME ZONE 'Europe/Moscow';
        ALTER TABLE public.auctions
            ALTER COLUMN created_at SET DEFAULT now();
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'bids'
          AND column_name = 'placed_at'
          AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE public.bids
            ALTER COLUMN placed_at DROP DEFAULT;
        ALTER TABLE public.bids
            ALTER COLUMN placed_at TYPE timestamptz
            USING placed_at AT TIME ZONE 'Europe/Moscow';
        ALTER TABLE public.bids
            ALTER COLUMN placed_at SET DEFAULT now();
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'bids'
          AND column_name = 'created_at'
          AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE public.bids
            ALTER COLUMN created_at DROP DEFAULT;
        ALTER TABLE public.bids
            ALTER COLUMN created_at TYPE timestamptz
            USING created_at AT TIME ZONE 'Europe/Moscow';
        ALTER TABLE public.bids
            ALTER COLUMN created_at SET DEFAULT now();
    END IF;

    IF fix_end_time_trigger_ddl IS NOT NULL THEN
        EXECUTE fix_end_time_trigger_ddl;

        -- pg_get_triggerdef() does not include a trigger's enabled mode.
        -- Restore it as well so the migration has no behavioural side effect.
        CASE fix_end_time_trigger_enabled
            WHEN 'D' THEN
                ALTER TABLE public.auctions
                    DISABLE TRIGGER trg_auctions_fix_end_time;
            WHEN 'R' THEN
                ALTER TABLE public.auctions
                    ENABLE REPLICA TRIGGER trg_auctions_fix_end_time;
            WHEN 'A' THEN
                ALTER TABLE public.auctions
                    ENABLE ALWAYS TRIGGER trg_auctions_fix_end_time;
            ELSE
                NULL; -- 'O': the recreated trigger is enabled normally.
        END CASE;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_auctions_start_time_date
    ON public.auctions (((start_time AT TIME ZONE 'Europe/Moscow')::date));

CREATE TABLE IF NOT EXISTS public.telegram_outbox (
    outbox_id bigserial PRIMARY KEY,
    dedupe_key text NOT NULL UNIQUE,
    method text NOT NULL DEFAULT 'send_message',
    chat_id bigint NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    available_at timestamptz NOT NULL DEFAULT now(),
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 5,
    locked_at timestamptz,
    sent_at timestamptz,
    telegram_message_id bigint,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_telegram_outbox_method
        CHECK (method IN ('send_message')),
    CONSTRAINT chk_telegram_outbox_status
        CHECK (status IN ('pending', 'processing', 'sent', 'failed')),
    CONSTRAINT chk_telegram_outbox_payload
        CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT chk_telegram_outbox_attempts
        CHECK (attempts >= 0 AND max_attempts > 0)
);

CREATE INDEX IF NOT EXISTS ix_telegram_outbox_pending
    ON public.telegram_outbox (available_at, outbox_id)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS ix_telegram_outbox_processing
    ON public.telegram_outbox (locked_at, outbox_id)
    WHERE status = 'processing';
