-- compatibility: expand
-- rollback: code-only-safe
-- note: Add reviewed privacy request lifecycle with pseudonymous evidence and terminal-row immutability.

SET search_path = public, pg_catalog;

CREATE TABLE IF NOT EXISTS public.privacy_requests
(
    request_id          uuid                     NOT NULL PRIMARY KEY,
    request_kind        text                     NOT NULL DEFAULT 'anonymize'
        CHECK (request_kind IN ('anonymize')),
    subject_user_id     bigint
        REFERENCES public.users(user_id)
            ON DELETE SET NULL,
    subject_digest      text                     NOT NULL,
    status              text                     NOT NULL DEFAULT 'pending_review'
        CHECK (status IN (
            'pending_review',
            'approved',
            'completed',
            'completed_with_holds',
            'cancelled',
            'rejected',
            'failed'
        )),
    policy_sha256       text                     NOT NULL,
    approved_plan_sha256 text,
    approved_by_digest  text,
    blocking_holds      text[]                   NOT NULL DEFAULT '{}'::text[],
    retained_holds      text[]                   NOT NULL DEFAULT '{}'::text[],
    outcome_counts      jsonb                    NOT NULL DEFAULT '{}'::jsonb,
    requested_at        timestamp with time zone NOT NULL,
    updated_at          timestamp with time zone NOT NULL,
    approved_at         timestamp with time zone,
    completed_at        timestamp with time zone,
    cancelled_at        timestamp with time zone,
    version             integer                  NOT NULL DEFAULT 1
        CHECK (version > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_privacy_requests_active_subject
    ON public.privacy_requests (subject_digest)
    WHERE status IN ('pending_review', 'approved');

CREATE INDEX IF NOT EXISTS idx_privacy_requests_status_time
    ON public.privacy_requests (status, requested_at DESC);

CREATE INDEX IF NOT EXISTS idx_privacy_requests_subject_time
    ON public.privacy_requests (subject_digest, requested_at DESC);

CREATE OR REPLACE FUNCTION public.reject_terminal_privacy_request_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status IN ('completed', 'completed_with_holds', 'cancelled', 'rejected', 'failed') THEN
        RAISE EXCEPTION 'terminal privacy request evidence is immutable'
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'privacy request evidence cannot be deleted'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_privacy_requests_terminal_immutable
    ON public.privacy_requests;
CREATE TRIGGER trg_privacy_requests_terminal_immutable
BEFORE UPDATE OR DELETE ON public.privacy_requests
FOR EACH ROW
EXECUTE FUNCTION public.reject_terminal_privacy_request_mutation();
