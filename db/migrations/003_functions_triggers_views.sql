-- 003_functions_triggers_views.sql
-- Только функции приложения. Системные функции и операторы pg_trgm создаёт CREATE EXTENSION.
SET search_path = public, pg_catalog;

CREATE OR REPLACE FUNCTION public.trg_set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION public.prevent_currency_change_if_bids()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.currency IS DISTINCT FROM OLD.currency
       AND EXISTS (
           SELECT 1 FROM public.bids b WHERE b.auction_id = OLD.auction_id
       )
    THEN
        RAISE EXCEPTION 'Нельзя менять валюту после появления ставок';
    END IF;
    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION public.prevent_time_change_if_bids()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF (
        NEW.start_time IS DISTINCT FROM OLD.start_time
        OR NEW.end_time IS DISTINCT FROM OLD.end_time
    )
       AND EXISTS (
           SELECT 1 FROM public.bids b WHERE b.auction_id = OLD.auction_id
       )
    THEN
        RAISE EXCEPTION 'Нельзя менять время аукциона после появления ставок';
    END IF;
    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION public.list_missing_ids(
    _schema text,
    _table text,
    _pk text,
    _limit integer DEFAULT 1000
)
RETURNS TABLE(missing_id bigint)
LANGUAGE plpgsql
AS $function$
BEGIN
    RETURN QUERY EXECUTE format($query$
        WITH ordered AS (
            SELECT %1$I AS id
            FROM %2$I.%3$I
            ORDER BY %1$I
        ),
        gaps AS (
            SELECT (id + 1) AS gap_start,
                   (lead(id) OVER (ORDER BY id) - 1) AS gap_end
            FROM ordered
        ),
        non_empty AS (
            SELECT gap_start, gap_end
            FROM gaps
            WHERE gap_end >= gap_start
        ),
        expanded AS (
            SELECT generate_series(gap_start, gap_end) AS missing_id
            FROM non_empty
        )
        SELECT missing_id
        FROM expanded
        ORDER BY missing_id
        LIMIT $1
    $query$, _pk, _schema, _table)
    USING _limit;
END
$function$;

CREATE OR REPLACE FUNCTION public.touch_market_listing()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION public.auctions_fix_end_time()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.end_time := NEW.start_time + INTERVAL '31 minutes';
    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION public.norm_username(t text)
RETURNS text
IMMUTABLE
LANGUAGE sql
AS $function$
    SELECT regexp_replace(lower(trim(coalesce(t, ''))), '^@', '')
$function$;

CREATE OR REPLACE FUNCTION public.norm_hero(t text)
RETURNS text
IMMUTABLE
LANGUAGE sql
AS $function$
    SELECT regexp_replace(
        regexp_replace(
            translate(lower(trim(coalesce(t, ''))), 'ё', 'е'),
            '[^0-9a-zа-я ]+',
            ' ',
            'g'
        ),
        '\s+',
        ' ',
        'g'
    )
$function$;

CREATE OR REPLACE FUNCTION public.uid_verif_sync_cols()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.verification_code := COALESCE(NEW.verification_code, NEW.challenge_code);
    NEW.challenge_code := COALESCE(NEW.challenge_code, NEW.verification_code);

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
$function$;

CREATE OR REPLACE FUNCTION public.touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at := CURRENT_TIMESTAMP;
    RETURN NEW;
END
$function$;

CREATE OR REPLACE VIEW public.v_user_uid_status
    (tg_user_id, tg_username, uid, uid_status, verified_at, verified_by,
     uid_is_banned, banned_until, ban_reason)
AS
SELECT u.user_id AS tg_user_id,
       u.username AS tg_username,
       uu.uid,
       uu.status AS uid_status,
       uu.verified_at,
       uu.verified_by,
       ub.uid IS NOT NULL
           AND (ub.banned_until IS NULL OR ub.banned_until > now()) AS uid_is_banned,
       ub.banned_until,
       ub.reason AS ban_reason
FROM public.users u
LEFT JOIN public.user_uids uu ON uu.user_id = u.user_id
LEFT JOIN public.uid_bans ub ON ub.uid = uu.uid;

DROP TRIGGER IF EXISTS trg_no_currency_flip ON public.auctions;
CREATE TRIGGER trg_no_currency_flip
    BEFORE UPDATE OF currency ON public.auctions
    FOR EACH ROW
    EXECUTE FUNCTION public.prevent_currency_change_if_bids();

DROP TRIGGER IF EXISTS trg_prevent_time_change ON public.auctions;
CREATE TRIGGER trg_prevent_time_change
    BEFORE UPDATE ON public.auctions
    FOR EACH ROW
    EXECUTE FUNCTION public.prevent_time_change_if_bids();

DROP TRIGGER IF EXISTS trg_auctions_fix_end_time ON public.auctions;
CREATE TRIGGER trg_auctions_fix_end_time
    BEFORE INSERT OR UPDATE OF start_time ON public.auctions
    FOR EACH ROW
    EXECUTE FUNCTION public.auctions_fix_end_time();

DROP TRIGGER IF EXISTS trg_user_appeals_touch ON public.user_appeals;
CREATE TRIGGER trg_user_appeals_touch
    BEFORE UPDATE ON public.user_appeals
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS trg_market_listings_touch ON public.market_listings;
CREATE TRIGGER trg_market_listings_touch
    BEFORE UPDATE ON public.market_listings
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS trg_uid_verif_sync_cols ON public.uid_verification_requests;
CREATE TRIGGER trg_uid_verif_sync_cols
    BEFORE INSERT OR UPDATE ON public.uid_verification_requests
    FOR EACH ROW
    EXECUTE FUNCTION public.uid_verif_sync_cols();
