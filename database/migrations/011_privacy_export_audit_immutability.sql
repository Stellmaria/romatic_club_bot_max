CREATE OR REPLACE FUNCTION public.reject_privacy_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.action_type LIKE 'privacy.%' THEN
        RAISE EXCEPTION 'privacy audit evidence is append-only'
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.action_type LIKE 'privacy.%' THEN
        RAISE EXCEPTION 'privacy audit evidence cannot be created by update'
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_logs_privacy_immutable ON public.audit_logs;
CREATE TRIGGER trg_audit_logs_privacy_immutable
BEFORE UPDATE OR DELETE ON public.audit_logs
FOR EACH ROW
EXECUTE FUNCTION public.reject_privacy_audit_mutation();

CREATE INDEX IF NOT EXISTS idx_audit_logs_privacy_time
    ON public.audit_logs (created_at DESC, id DESC)
    WHERE action_type LIKE 'privacy.%';
