-- Additive migration. Existing plaintext UID rows are scrubbed by
-- `python migrate_uid_encryption.py`; new application writes are encrypted.

ALTER TABLE IF EXISTS public.user_uids
    ADD COLUMN IF NOT EXISTS uid_hash text,
    ADD COLUMN IF NOT EXISTS uid_enc text,
    ADD COLUMN IF NOT EXISTS uid_last4 text;

ALTER TABLE IF EXISTS public.uid_verification_requests
    ADD COLUMN IF NOT EXISTS uid_hash text,
    ADD COLUMN IF NOT EXISTS uid_enc text,
    ADD COLUMN IF NOT EXISTS uid_last4 text;

ALTER TABLE IF EXISTS public.uid_bans
    ADD COLUMN IF NOT EXISTS uid_hash text,
    ADD COLUMN IF NOT EXISTS uid_enc text,
    ADD COLUMN IF NOT EXISTS uid_last4 text;

CREATE UNIQUE INDEX IF NOT EXISTS ux_user_uids_uid_hash
    ON public.user_uids(uid_hash)
    WHERE uid_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_uid_verification_requests_uid_hash
    ON public.uid_verification_requests(uid_hash)
    WHERE uid_hash IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_uid_bans_uid_hash
    ON public.uid_bans(uid_hash)
    WHERE uid_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.uid_verification_request_reminders (
    request_id bigint NOT NULL REFERENCES public.uid_verification_requests(id) ON DELETE CASCADE,
    stage_h smallint NOT NULL,
    sent_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (request_id, stage_h)
);

ALTER TABLE IF EXISTS public.uid_verification_requests
    DROP CONSTRAINT IF EXISTS uid_verification_requests_status_check;

ALTER TABLE IF EXISTS public.uid_verification_requests
    ADD CONSTRAINT uid_verification_requests_status_check
    CHECK (status = ANY (ARRAY[
        'pending'::text,
        'approved'::text,
        'rejected'::text,
        'conflict'::text,
        'expired'::text,
        'revision'::text
    ]));
