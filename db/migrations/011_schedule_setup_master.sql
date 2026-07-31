-- Premium schedule emoji setup, preview target and publication approval.
SET search_path = public, pg_catalog;

CREATE TABLE IF NOT EXISTS public.schedule_emoji_assets (
    asset_key text PRIMARY KEY,
    custom_emoji_id bigint NOT NULL CHECK (custom_emoji_id > 0),
    fallback text NOT NULL DEFAULT '▫️',
    updated_by bigint,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.schedule_deck_emojis (
    deck_id integer PRIMARY KEY REFERENCES public.decks(id) ON DELETE CASCADE,
    custom_emoji_id bigint NOT NULL CHECK (custom_emoji_id > 0),
    updated_by bigint,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.schedule_card_emojis (
    card_id integer PRIMARY KEY REFERENCES public.cards(card_id) ON DELETE CASCADE,
    custom_emoji_id bigint NOT NULL CHECK (custom_emoji_id > 0),
    verified boolean NOT NULL DEFAULT false,
    verified_by bigint,
    verified_at timestamptz,
    updated_by bigint,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.schedule_setup_sessions (
    user_id bigint PRIMARY KEY,
    stage text NOT NULL,
    asset_key text,
    deck_id integer REFERENCES public.decks(id) ON DELETE SET NULL,
    card_id integer REFERENCES public.cards(card_id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.schedule_preview_target (
    singleton_id smallint PRIMARY KEY DEFAULT 1 CHECK (singleton_id = 1),
    chat_id bigint NOT NULL,
    thread_id bigint,
    set_by bigint NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.schedule_publication_reviews (
    target_date date PRIMARY KEY,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'published')),
    preview_chat_id bigint,
    preview_thread_id bigint,
    preview_message_id bigint,
    reviewed_by bigint,
    reviewed_at timestamptz,
    channel_message_id bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS schedule_card_emojis_verified_idx
    ON public.schedule_card_emojis (verified, card_id);
CREATE INDEX IF NOT EXISTS schedule_publication_reviews_status_idx
    ON public.schedule_publication_reviews (status, target_date);
