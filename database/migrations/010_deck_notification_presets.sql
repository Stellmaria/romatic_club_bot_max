-- Keep deck notification presets aligned with the same public.decks catalog
-- used by the auction submission menu.

CREATE OR REPLACE FUNCTION public.upsert_deck_notification_preset(
    p_deck_id integer,
    p_deck_name text
)
RETURNS void
LANGUAGE plpgsql
AS $body$
DECLARE
    v_key text := 'deck_all_' || p_deck_id::text;
    v_title text;
    v_preset_id bigint;
    v_name text := btrim(COALESCE(p_deck_name, ''));
BEGIN
    v_title := 'Вся колода ' || p_deck_id::text;
    IF v_name <> '' THEN
        v_title := v_title || ' — ' || v_name;
    END IF;

    INSERT INTO public.presets(key, title)
    VALUES (v_key, v_title)
    ON CONFLICT (key) DO UPDATE
        SET title = EXCLUDED.title
    RETURNING id INTO v_preset_id;

    INSERT INTO public.preset_aliases(preset_id, alias)
    VALUES (v_preset_id, 'deck:' || p_deck_id::text)
    ON CONFLICT DO NOTHING;

    IF v_name <> '' THEN
        INSERT INTO public.preset_aliases(preset_id, alias)
        VALUES (v_preset_id, 'deck:' || lower(v_name))
        ON CONFLICT DO NOTHING;
    END IF;
END
$body$;

DO $body$
DECLARE
    deck_row record;
BEGIN
    FOR deck_row IN
        SELECT id, name
        FROM public.decks
        ORDER BY id
    LOOP
        PERFORM public.upsert_deck_notification_preset(deck_row.id, deck_row.name);
    END LOOP;
END
$body$;

CREATE OR REPLACE FUNCTION public.sync_deck_notification_preset_trigger()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
DECLARE
    v_preset_id bigint;
    v_old_name text;
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.name IS DISTINCT FROM NEW.name THEN
        v_old_name := btrim(COALESCE(OLD.name, ''));
        IF v_old_name <> '' THEN
            SELECT id
            INTO v_preset_id
            FROM public.presets
            WHERE key = 'deck_all_' || NEW.id::text;

            IF v_preset_id IS NOT NULL THEN
                DELETE FROM public.preset_aliases
                WHERE preset_id = v_preset_id
                  AND lower(alias) = lower('deck:' || v_old_name);
            END IF;
        END IF;
    END IF;

    PERFORM public.upsert_deck_notification_preset(NEW.id, NEW.name);
    RETURN NEW;
END
$body$;

DROP TRIGGER IF EXISTS trg_sync_deck_notification_preset ON public.decks;
CREATE TRIGGER trg_sync_deck_notification_preset
AFTER INSERT OR UPDATE OF name ON public.decks
FOR EACH ROW
EXECUTE FUNCTION public.sync_deck_notification_preset_trigger();
