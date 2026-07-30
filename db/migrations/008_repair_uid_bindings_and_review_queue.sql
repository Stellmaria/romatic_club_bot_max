-- 008_repair_uid_bindings_and_review_queue.sql
-- Restores verified UID bindings from approved verification requests when a
-- previous refactor left the request approved but the user_uids row missing or
-- revoked.  It also adds an index for the complete moderation queue statuses.

SET search_path = public, pg_catalog;

WITH latest_approved AS (
    SELECT DISTINCT ON (r.user_id)
           r.user_id,
           COALESCE(NULLIF(r.uid_hash, ''), NULLIF(r.uid, '')) AS stored_uid,
           NULLIF(r.uid_hash, '') AS uid_hash,
           NULLIF(r.uid_enc, '') AS uid_enc,
           NULLIF(r.uid_last4, '') AS uid_last4,
           COALESCE(r.decided_at, r.created_at, now()) AS verified_at,
           r.decided_by AS verified_by
    FROM public.uid_verification_requests AS r
    WHERE r.status = 'approved'
    ORDER BY r.user_id, r.decided_at DESC NULLS LAST, r.id DESC
), eligible AS (
    SELECT l.*
    FROM latest_approved AS l
    WHERE l.stored_uid IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM public.user_uids AS other
          WHERE other.user_id <> l.user_id
            AND (
                other.uid = l.stored_uid
                OR (l.uid_hash IS NOT NULL AND other.uid_hash = l.uid_hash)
            )
      )
)
INSERT INTO public.user_uids (
    uid,
    user_id,
    status,
    verified_at,
    verified_by,
    updated_at,
    uid_hash,
    uid_enc,
    uid_last4
)
SELECT e.stored_uid,
       e.user_id,
       'verified',
       e.verified_at,
       e.verified_by,
       now(),
       e.uid_hash,
       e.uid_enc,
       e.uid_last4
FROM eligible AS e
ON CONFLICT (user_id) DO UPDATE
SET uid         = EXCLUDED.uid,
    status      = 'verified',
    verified_at = COALESCE(public.user_uids.verified_at, EXCLUDED.verified_at),
    verified_by = COALESCE(public.user_uids.verified_by, EXCLUDED.verified_by),
    updated_at  = now(),
    uid_hash    = COALESCE(EXCLUDED.uid_hash, public.user_uids.uid_hash),
    uid_enc     = COALESCE(EXCLUDED.uid_enc, public.user_uids.uid_enc),
    uid_last4   = COALESCE(EXCLUDED.uid_last4, public.user_uids.uid_last4)
WHERE public.user_uids.status IS DISTINCT FROM 'verified'
   OR public.user_uids.uid_hash IS NULL
   OR public.user_uids.uid_enc IS NULL
   OR public.user_uids.uid_last4 IS NULL;

CREATE INDEX IF NOT EXISTS ix_auctions_moderation_queue_created
    ON public.auctions (status, created_at, auction_id)
    WHERE status IN ('draft', 'moderation', 'pending', 'approved');
