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
