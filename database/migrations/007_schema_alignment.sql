-- Align deployments created from the historical bootstrap with the confirmed
-- pgAdmin table inventory and current SQL contracts.
--
-- This migration is deliberately additive: it performs no data rewrite,
-- status/enum coercion, validation scan, DROP, or function replacement.

ALTER TABLE IF EXISTS public.auction_manual_results
    ADD COLUMN IF NOT EXISTS moderator_comment text;

-- The newest pgAdmin ERD export does not include functions/triggers.  Define a
-- missing function only when the earlier full snapshot proves its body.  Never
-- replace a production implementation that may have been customized.
DO $schema_alignment$
BEGIN
    IF to_regprocedure('public.prevent_currency_change_if_bids()') IS NULL THEN
        EXECUTE $function$
            CREATE FUNCTION public.prevent_currency_change_if_bids()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $body$
            BEGIN
                IF NEW.currency IS DISTINCT FROM OLD.currency
                   AND EXISTS (
                       SELECT 1
                       FROM public.bids AS b
                       WHERE b.auction_id = OLD.auction_id
                   )
                THEN
                    RAISE EXCEPTION 'Cannot change auction currency after bids exist';
                END IF;
                RETURN NEW;
            END
            $body$
        $function$;
    END IF;

    IF to_regprocedure('public.touch_updated_at()') IS NULL THEN
        EXECUTE $function$
            CREATE FUNCTION public.touch_updated_at()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $body$
            BEGIN
                NEW.updated_at := CURRENT_TIMESTAMP;
                RETURN NEW;
            END
            $body$
        $function$;
    END IF;

    IF to_regprocedure('public.touch_market_listing()') IS NULL THEN
        EXECUTE $function$
            CREATE FUNCTION public.touch_market_listing()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $body$
            BEGIN
                NEW.updated_at := now();
                RETURN NEW;
            END
            $body$
        $function$;
    END IF;

    IF to_regprocedure('public.uid_verif_sync_cols()') IS NULL THEN
        EXECUTE $function$
            CREATE FUNCTION public.uid_verif_sync_cols()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $body$
            BEGIN
                NEW.verification_code := COALESCE(
                    NEW.verification_code,
                    NEW.challenge_code
                );
                NEW.challenge_code := COALESCE(
                    NEW.challenge_code,
                    NEW.verification_code
                );
                NEW.profile_proof_file_id := COALESCE(
                    NEW.profile_proof_file_id,
                    NEW.profile_file_id
                );
                NEW.profile_file_id := COALESCE(
                    NEW.profile_file_id,
                    NEW.profile_proof_file_id
                );
                RETURN NEW;
            END
            $body$
        $function$;
    END IF;
END
$schema_alignment$;

DO $schema_alignment$
BEGIN
    IF to_regclass('public.auctions') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
           FROM pg_catalog.pg_trigger
           WHERE tgrelid = to_regclass('public.auctions')
             AND tgname = 'trg_no_currency_flip'
             AND NOT tgisinternal
       )
    THEN
        EXECUTE $trigger$
            CREATE TRIGGER trg_no_currency_flip
            BEFORE UPDATE OF currency ON public.auctions
            FOR EACH ROW
            EXECUTE FUNCTION public.prevent_currency_change_if_bids()
        $trigger$;
    END IF;

    IF to_regclass('public.user_appeals') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
           FROM pg_catalog.pg_trigger
           WHERE tgrelid = to_regclass('public.user_appeals')
             AND tgname = 'trg_user_appeals_touch'
             AND NOT tgisinternal
       )
    THEN
        EXECUTE $trigger$
            CREATE TRIGGER trg_user_appeals_touch
            BEFORE UPDATE ON public.user_appeals
            FOR EACH ROW
            EXECUTE FUNCTION public.touch_updated_at()
        $trigger$;
    END IF;

    IF to_regclass('public.market_listings') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
           FROM pg_catalog.pg_trigger
           WHERE tgrelid = to_regclass('public.market_listings')
             AND tgname = 'trg_market_listings_touch'
             AND NOT tgisinternal
       )
    THEN
        EXECUTE $trigger$
            CREATE TRIGGER trg_market_listings_touch
            BEFORE UPDATE ON public.market_listings
            FOR EACH ROW
            EXECUTE FUNCTION public.touch_market_listing()
        $trigger$;
    END IF;

    IF to_regclass('public.uid_verification_requests') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
           FROM pg_catalog.pg_trigger
           WHERE tgrelid = to_regclass('public.uid_verification_requests')
             AND tgname = 'trg_uid_verif_sync_cols'
             AND NOT tgisinternal
       )
    THEN
        EXECUTE $trigger$
            CREATE TRIGGER trg_uid_verif_sync_cols
            BEFORE INSERT OR UPDATE ON public.uid_verification_requests
            FOR EACH ROW
            EXECUTE FUNCTION public.uid_verif_sync_cols()
        $trigger$;
    END IF;
END
$schema_alignment$;

-- Intentionally absent: trg_auctions_fix_end_time and
-- trg_prevent_time_change.  Both conflict with migration 004 workflows.
