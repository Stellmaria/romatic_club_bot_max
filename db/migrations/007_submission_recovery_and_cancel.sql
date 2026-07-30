-- 007_submission_recovery_and_cancel.sql
-- Восстанавливает пользователей после зависшей публикации и разрешает
-- безопасно отзывать ещё не опубликованные заявки.

SET search_path = public, pg_catalog;

ALTER TABLE public.auctions
    DROP CONSTRAINT IF EXISTS chk_auctions_status;

ALTER TABLE public.auctions
    ADD CONSTRAINT chk_auctions_status
    CHECK (
        status IN (
            'draft',
            'moderation',
            'pending',
            'approved',
            'scheduled',
            'publishing',
            'publication_failed',
            'active',
            'finalizing',
            'finalization_failed',
            'finished',
            'rejected',
            'cancelled',
            'closed'
        )
    );

CREATE INDEX IF NOT EXISTS ix_auctions_unpublished_owner_recovery
    ON public.auctions (status, start_time)
    WHERE message_id IS NULL;
