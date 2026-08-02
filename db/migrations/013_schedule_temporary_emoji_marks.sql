-- Track temporary Premium-emoji placeholders without weakening existing ID constraints.
SET search_path = public, pg_catalog;

CREATE TABLE IF NOT EXISTS public.schedule_temporary_emoji_marks (
    scope text NOT NULL CHECK (scope IN ('asset', 'deck', 'card')),
    entity_key text NOT NULL,
    placeholder_emoji_id bigint NOT NULL CHECK (placeholder_emoji_id > 0),
    fallback text NOT NULL DEFAULT '▫️',
    updated_by bigint,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, entity_key)
);

CREATE INDEX IF NOT EXISTS schedule_temporary_emoji_marks_scope_idx
    ON public.schedule_temporary_emoji_marks (scope, entity_key);

-- Keep a selected-deck review scope outside the ordinary setup session.  The
-- session changes stage while the administrator edits emoji/economy/fields,
-- whereas this row must survive until the chosen deck is completed or stopped.
CREATE TABLE IF NOT EXISTS public.schedule_setup_deck_scopes (
    user_id bigint PRIMARY KEY,
    deck_id bigint NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS schedule_setup_deck_scopes_deck_idx
    ON public.schedule_setup_deck_scopes (deck_id);
