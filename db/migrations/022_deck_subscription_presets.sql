-- compatibility: expand
-- rollback: code-only-safe
-- note: Backfill whole-deck presets and keep them synchronized with deck creation and renames.

SET search_path = public, pg_catalog;

INSERT INTO public.presets (key, title)
SELECT
    'deck_all_' || d.id::text,
    'Вся колода ' || d.id::text || ' — ' || d.name
FROM public.decks AS d
ON CONFLICT (key) DO UPDATE
SET title = EXCLUDED.title;

CREATE OR REPLACE FUNCTION public.sync_deck_subscription_preset()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO public.presets (key, title)
    VALUES (
        'deck_all_' || NEW.id::text,
        'Вся колода ' || NEW.id::text || ' — ' || NEW.name
    )
    ON CONFLICT (key) DO UPDATE
    SET title = EXCLUDED.title;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_decks_sync_subscription_preset ON public.decks;
CREATE TRIGGER trg_decks_sync_subscription_preset
AFTER INSERT OR UPDATE OF name ON public.decks
FOR EACH ROW
EXECUTE FUNCTION public.sync_deck_subscription_preset();
