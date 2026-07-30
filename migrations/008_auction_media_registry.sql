-- Configurable Telegram media for auction entities.
-- Replaces hard-coded deck/card/service media maps with database overrides.

-- Ensure card media metadata exists on installations created from older dumps.
ALTER TABLE public.cards
    ADD COLUMN IF NOT EXISTS media_type text NOT NULL DEFAULT 'photo',
    ADD COLUMN IF NOT EXISTS media_file_id text,
    ADD COLUMN IF NOT EXISTS media_unique_id text,
    ADD COLUMN IF NOT EXISTS thumb_file_id text;

UPDATE public.cards
SET media_file_id = image_id
WHERE media_file_id IS NULL
  AND image_id IS NOT NULL
  AND btrim(image_id) <> '';

CREATE TABLE IF NOT EXISTS public.auction_media_assets (
    asset_id bigserial PRIMARY KEY,
    target_kind text NOT NULL,
    target_key text NOT NULL,
    media_type text NOT NULL DEFAULT 'photo',
    file_id text NOT NULL,
    file_unique_id text,
    thumb_file_id text,
    updated_by bigint,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT uq_auction_media_assets_target UNIQUE (target_kind, target_key),
    CONSTRAINT chk_auction_media_assets_target_kind CHECK (
        target_kind IN ('deck', 'card', 'auction', 'rarity', 'service', 'spins', 'default')
    ),
    CONSTRAINT chk_auction_media_assets_media_type CHECK (
        media_type IN ('photo', 'video', 'animation', 'document')
    ),
    CONSTRAINT chk_auction_media_assets_nonempty_key CHECK (btrim(target_key) <> ''),
    CONSTRAINT chk_auction_media_assets_nonempty_file CHECK (btrim(file_id) <> '')
);

CREATE INDEX IF NOT EXISTS idx_auction_media_assets_kind
    ON public.auction_media_assets (target_kind, target_key);

CREATE OR REPLACE FUNCTION public.touch_auction_media_asset()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_auction_media_assets_updated_at
    ON public.auction_media_assets;
CREATE TRIGGER trg_auction_media_assets_updated_at
BEFORE UPDATE ON public.auction_media_assets
FOR EACH ROW
EXECUTE FUNCTION public.touch_auction_media_asset();
