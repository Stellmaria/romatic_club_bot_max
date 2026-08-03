-- Query-performance continuation for administrative lists and hot workers.
--
-- The indexes are intentionally additive and idempotent. They match the
-- keyset predicates introduced with issue #36 and the existing auction,
-- exchange, schedule and outbox worker predicates.

CREATE INDEX IF NOT EXISTS ix_users_username_ci
    ON public.users (lower(username), user_id)
    WHERE username IS NOT NULL AND username <> '';

CREATE INDEX IF NOT EXISTS ix_users_trusted_username_ci
    ON public.users (lower(ltrim(username, '@')), user_id)
    WHERE is_trusted = TRUE
      AND username IS NOT NULL
      AND ltrim(username, '@') <> '';

CREATE INDEX IF NOT EXISTS ix_trusted_usernames_username_ci
    ON public.trusted_usernames (lower(ltrim(username, '@')));

CREATE INDEX IF NOT EXISTS ix_admins_added_at_user_id
    ON public.admins (added_at, user_id);

CREATE INDEX IF NOT EXISTS ix_auction_owners_user_auction
    ON public.auction_owners (user_id, auction_id);

CREATE INDEX IF NOT EXISTS ix_bids_auction_amount_bid
    ON public.bids (auction_id, amount DESC, bid_id DESC);

CREATE INDEX IF NOT EXISTS ix_delete_requests_pending_lot
    ON public.delete_requests (lot_id, created_at DESC)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS ix_cards_deck_num_card
    ON public.cards (deck_id, num, card_id);

CREATE INDEX IF NOT EXISTS ix_exchange_batches_status_created
    ON public.exchange_batches (status, created_at DESC, batch_id DESC);

CREATE INDEX IF NOT EXISTS ix_exchange_items_batch_item
    ON public.exchange_items (batch_id, item_id);

CREATE INDEX IF NOT EXISTS ix_market_listings_status_created
    ON public.market_listings (status, created_at DESC, listing_id DESC);

-- Preserve/repair the worker and schedule indexes on upgraded installations.
CREATE INDEX IF NOT EXISTS idx_auctions_start_time_date
    ON public.auctions (((start_time AT TIME ZONE 'Europe/Moscow')::date));

CREATE INDEX IF NOT EXISTS ix_auctions_publication_due
    ON public.auctions (start_time, auction_id)
    WHERE status = 'scheduled' AND message_id IS NULL;

CREATE INDEX IF NOT EXISTS ix_auctions_finalization_due
    ON public.auctions (end_time, auction_id)
    WHERE status IN ('scheduled', 'active');

CREATE INDEX IF NOT EXISTS ix_telegram_outbox_pending
    ON public.telegram_outbox (available_at, outbox_id)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS ix_telegram_outbox_processing
    ON public.telegram_outbox (locked_at, outbox_id)
    WHERE status = 'processing';
