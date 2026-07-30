-- Phase 9: winner workflow persistence belongs to migrations, not Telegram handlers.

CREATE TABLE IF NOT EXISTS public.auction_win_mailings (
    id bigserial PRIMARY KEY,
    auction_id integer NOT NULL,
    target text NOT NULL,
    sent_by_user_id bigint,
    sent_by_username text,
    sent_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_auction_win_mailings_target
        CHECK (target IN ('owner', 'winner', 'both'))
);

CREATE INDEX IF NOT EXISTS idx_auction_win_mailings_auction_id
    ON public.auction_win_mailings (auction_id);

CREATE TABLE IF NOT EXISTS public.auction_manual_results (
    auction_id integer PRIMARY KEY,
    winner_user_id bigint,
    winner_username text,
    owner_user_id bigint,
    owner_username text,
    amount integer,
    updated_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by bigint,
    moderator_comment text,
    CONSTRAINT chk_auction_manual_results_amount
        CHECK (amount IS NULL OR amount >= 0)
);

ALTER TABLE public.auction_manual_results
    ADD COLUMN IF NOT EXISTS moderator_comment text;

CREATE TABLE IF NOT EXISTS public.admin_thanks_totals (
    author text PRIMARY KEY,
    thanks_total bigint NOT NULL DEFAULT 0,
    users_total bigint NOT NULL DEFAULT 0,
    updated_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS public.admin_thanks_users (
    author text NOT NULL,
    user_id bigint NOT NULL,
    created_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    thanks_count bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (author, user_id)
);

ALTER TABLE public.admin_thanks_users
    ADD COLUMN IF NOT EXISTS thanks_count bigint NOT NULL DEFAULT 0;

UPDATE public.admin_thanks_users
SET thanks_count = 1
WHERE thanks_count = 0;

ALTER TABLE public.auction_win_mailings
    DROP CONSTRAINT IF EXISTS chk_auction_win_mailings_target;
ALTER TABLE public.auction_win_mailings
    ADD CONSTRAINT chk_auction_win_mailings_target
    CHECK (target IN ('owner', 'winner', 'both'));

ALTER TABLE public.auction_manual_results
    DROP CONSTRAINT IF EXISTS chk_auction_manual_results_amount;
ALTER TABLE public.auction_manual_results
    ADD CONSTRAINT chk_auction_manual_results_amount
    CHECK (amount IS NULL OR amount >= 0);
