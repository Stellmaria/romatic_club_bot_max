BEGIN;

-- Deck labels were accidentally assigned to the neighbouring IDs.
-- Set both canonical values explicitly so the migration is idempotent.
UPDATE public.decks
SET name = CASE id
    WHEN 28 THEN '28 колода'
    WHEN 29 THEN '29 колода'
END
WHERE id IN (28, 29)
  AND name IS DISTINCT FROM CASE id
      WHEN 28 THEN '28 колода'
      WHEN 29 THEN '29 колода'
  END;

COMMIT;
