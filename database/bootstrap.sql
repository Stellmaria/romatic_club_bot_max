-- Reproducible current schema for a new, empty PostgreSQL database.
--
-- Do not use this file to upgrade production. Existing databases are upgraded
-- only by db.migrations.apply_migrations and numbered migrations.
-- See database/README.md for provenance and private-data import policy.

BEGIN;

SET LOCAL search_path = public, pg_catalog;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

DO $bootstrap$
BEGIN
    IF to_regtype('public.obtain_type') IS NULL THEN
        EXECUTE 'CREATE TYPE public.obtain_type AS ENUM (''diamonds'', ''tea'')';
    END IF;
    IF to_regtype('public.deck_type') IS NULL THEN
        EXECUTE 'CREATE TYPE public.deck_type AS ENUM (''resource'', ''roulette'')';
    END IF;
    IF to_regtype('public.market_currency') IS NULL THEN
        EXECUTE 'CREATE TYPE public.market_currency AS ENUM (''cups'', ''diamonds'', ''treasures'', ''cash'')';
    END IF;
    IF to_regtype('public.listing_status') IS NULL THEN
        EXECUTE 'CREATE TYPE public.listing_status AS ENUM (''active'', ''hidden'', ''sold'', ''archived'', ''deleted'')';
    END IF;
    IF to_regtype('public.offer_kind') IS NULL THEN
        EXECUTE 'CREATE TYPE public.offer_kind AS ENUM (''cards'', ''cups'', ''diamonds'', ''treasures'', ''whole_deck'', ''service'')';
    END IF;
END
$bootstrap$;

CREATE OR REPLACE FUNCTION public.trg_set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END
$body$;

CREATE OR REPLACE FUNCTION public.is_valid_bid(
    p_auction_id integer,
    p_amount integer
)
RETURNS boolean
STABLE
LANGUAGE plpgsql
AS $body$
DECLARE
    cur text;
    start_p integer;
    bid_step integer;
    minimum integer;
    anchor integer;
    maximum_bid integer;
BEGIN
    IF p_amount IS NULL THEN
        RETURN false;
    END IF;

    SELECT lower(trim(a.currency)), COALESCE(a.start_price, 0)
      INTO cur, start_p
      FROM public.auctions AS a
     WHERE a.auction_id = p_auction_id;

    IF NOT FOUND OR cur IS NULL THEN
        RETURN false;
    END IF;

    IF cur IN ('💎', 'алмаз', 'алмазы', 'diamond', 'diamonds') THEN
        cur := 'алмазы';
    ELSIF cur IN ('🍵', 'чай', 'чашки', 'cups') THEN
        cur := 'чашки';
    ELSIF cur IN ('🪙', 'сокровища', 'treasure', 'treasures') THEN
        cur := 'сокровища';
    END IF;

    IF cur = 'алмазы' THEN
        bid_step := 10;
        minimum := 30;
    ELSIF cur = 'чашки' THEN
        bid_step := 2;
        minimum := 2;
    ELSIF cur = 'сокровища' THEN
        bid_step := 10;
        minimum := 0;
    ELSE
        RETURN false;
    END IF;

    anchor := GREATEST(start_p, minimum);
    IF p_amount < anchor OR mod(p_amount - anchor, bid_step) <> 0 THEN
        RETURN false;
    END IF;

    SELECT max(b.amount)
      INTO maximum_bid
      FROM public.bids AS b
     WHERE b.auction_id = p_auction_id;

    RETURN maximum_bid IS NULL OR p_amount >= maximum_bid + bid_step;
END
$body$;

CREATE OR REPLACE FUNCTION public.prevent_currency_change_if_bids()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    IF NEW.currency IS DISTINCT FROM OLD.currency
       AND EXISTS (
           SELECT 1
             FROM public.bids AS b
            WHERE b.auction_id = OLD.auction_id
       )
    THEN
        RAISE EXCEPTION 'Cannot change auction currency after bids exist';
    END IF;
    RETURN NEW;
END
$body$;

CREATE OR REPLACE FUNCTION public.prevent_time_change_if_bids()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    IF (
        NEW.start_time IS DISTINCT FROM OLD.start_time
        OR NEW.end_time IS DISTINCT FROM OLD.end_time
    )
       AND EXISTS (
           SELECT 1
             FROM public.bids AS b
            WHERE b.auction_id = OLD.auction_id
       )
    THEN
        RAISE EXCEPTION 'Cannot change auction time after bids exist';
    END IF;
    RETURN NEW;
END
$body$;

CREATE OR REPLACE FUNCTION public.list_missing_ids(
    _schema text,
    _table text,
    _pk text,
    _limit integer DEFAULT 1000
)
RETURNS TABLE(missing_id bigint)
LANGUAGE plpgsql
AS $body$
BEGIN
    RETURN QUERY EXECUTE format(
        $query$
        WITH ordered AS (
            SELECT %1$I AS id
              FROM %2$I.%3$I
             ORDER BY %1$I
        ),
        gaps AS (
            SELECT id + 1 AS gap_start,
                   lead(id) OVER (ORDER BY id) - 1 AS gap_end
              FROM ordered
        )
        SELECT candidate
          FROM (
              SELECT generate_series(gap_start, gap_end) AS candidate
                FROM gaps
               WHERE gap_end >= gap_start
          ) AS expanded
         ORDER BY candidate
         LIMIT $1
        $query$,
        _pk,
        _schema,
        _table
    )
    USING _limit;
END
$body$;

CREATE OR REPLACE FUNCTION public.touch_market_listing()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END
$body$;

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END
$body$;

CREATE OR REPLACE FUNCTION public.auctions_fix_end_time()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    NEW.end_time := date_trunc('minute', NEW.start_time + interval '30 minutes') + interval '59 seconds';
    RETURN NEW;
END
$body$;

CREATE OR REPLACE FUNCTION public.norm_username(t text)
RETURNS text
IMMUTABLE
LANGUAGE sql
AS $body$
    SELECT regexp_replace(lower(trim(coalesce(t, ''))), '^@', '')
$body$;

CREATE OR REPLACE FUNCTION public.norm_hero(t text)
RETURNS text
IMMUTABLE
LANGUAGE sql
AS $body$
    SELECT regexp_replace(
        regexp_replace(
            translate(lower(trim(coalesce(t, ''))), 'ё', 'е'),
            '[^0-9a-zа-я ]+',
            ' ',
            'g'
        ),
        '\s+',
        ' ',
        'g'
    )
$body$;

CREATE OR REPLACE FUNCTION public.uid_verif_sync_cols()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    NEW.verification_code := COALESCE(
        NEW.verification_code,
        NEW.challenge_code
    );
    NEW.challenge_code := COALESCE(
        NEW.challenge_code,
        NEW.verification_code
    );
    NEW.profile_proof_file_id := COALESCE(
        NEW.profile_proof_file_id,
        NEW.profile_file_id
    );
    NEW.profile_file_id := COALESCE(
        NEW.profile_file_id,
        NEW.profile_proof_file_id
    );
    RETURN NEW;
END
$body$;

CREATE OR REPLACE FUNCTION public.touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    NEW.updated_at := CURRENT_TIMESTAMP;
    RETURN NEW;
END
$body$;

create table if not exists custom_emojis
(
    name     text not null
        constraint custom_emojis_pkey
            primary key,
    emoji_id text not null
        constraint custom_emojis_emoji_id_key
            unique
);

create table if not exists decks
(
    id        serial
        constraint decks_pkey
            primary key,
    name      varchar(255) not null,
    deck_type deck_type default 'resource'::deck_type
);

create table if not exists cards
(
    card_id         serial
        constraint cards_pkey
            primary key,
    deck_id         integer                                      not null
        constraint cards_deck_id_fkey
            references decks
            on update cascade on delete restrict
            deferrable initially deferred,
    num             integer                                      not null,
    hero_name       varchar(255)                                 not null,
    image_id        text,
    rarity          varchar(50)                                  not null,
    story           varchar(255)                                 not null,
    quote           text,
    card_name       varchar(255) default ''::character varying   not null,
    obtain_type     obtain_type  default 'diamonds'::obtain_type not null,
    obtain_amount   integer      default 0                       not null,
    media_type      text         default 'photo'::text           not null,
    media_file_id   text,
    media_unique_id text,
    thumb_file_id   text
);

create table if not exists auctions
(
    auction_id            serial
        constraint auctions_pkey
            primary key,
    card_name             varchar(255)                                      not null,
    hero_name             varchar(255),
    image_id              text,
    start_price           integer     default 0                             not null,
    start_time timestamp with time zone NOT NULL,
    end_time timestamp with time zone NOT NULL,
    status                varchar(20) default 'scheduled'::character varying
        constraint chk_auctions_status
            check ((status)::text = ANY
                   ((ARRAY ['draft'::character varying, 'moderation'::character varying,
                       'pending'::character varying, 'approved'::character varying,
                       'scheduled'::character varying, 'publishing'::character varying,
                       'publication_failed'::character varying, 'active'::character varying,
                       'finalizing'::character varying, 'finalization_failed'::character varying,
                       'finished'::character varying, 'rejected'::character varying,
                       'cancelled'::character varying, 'closed'::character varying])::text[])),
    created_at            timestamp with time zone default CURRENT_TIMESTAMP,
    currency              varchar(20) default 'чашки'::character varying
        constraint chk_auctions_currency
            check ((currency)::text = ANY
                   ((ARRAY ['алмазы'::character varying, 'чашки'::character varying, 'сокровища'::character varying])::text[])),
    accepted_currencies   text[] default ARRAY['чашки']::text[] not null,
    comment               text,
    message_id            bigint,
    notified_start        boolean     default false,
    notified_1min         boolean     default false,
    notified_end          boolean     default false,
    proof_photo_id        text,
    discussion_message_id bigint,
    notified_card_subs    boolean     default false,
    auction_kind          varchar(20) default 'standard'::character varying not null
        constraint auctions_auction_kind_chk
            check ((auction_kind)::text = ANY
                   ((ARRAY ['standard'::character varying, 'reverse'::character varying, 'fast'::character varying, 'free'::character varying, 'black'::character varying, 'exchange'::character varying])::text[])),
    craft_uid_possible    boolean,
    card_id               integer
        constraint auctions_card_id_fkey
            references cards
            on delete set null,
    finalization_started_at timestamp with time zone,
    finalization_finished_at timestamp with time zone,
    finalization_attempts integer default 0 not null,
    finalization_error text,
    publication_started_at timestamp with time zone,
    publication_finished_at timestamp with time zone,
    publication_next_attempt_at timestamp with time zone,
    publication_attempts integer default 0 not null,
    publication_error text,
    constraint chk_auctions_time_order
        check (
            CASE
                WHEN ((status)::text = ANY
                      ((ARRAY ['scheduled'::character varying, 'publishing'::character varying,
                          'publication_failed'::character varying, 'active'::character varying,
                          'finalizing'::character varying, 'finalization_failed'::character varying,
                          'finished'::character varying])::text[]))
                    THEN (end_time > start_time)
                ELSE true
                END)
);

create unique index if not exists ux_auctions_discussion_msg
    on auctions (discussion_message_id)
    where (discussion_message_id IS NOT NULL);

create unique index if not exists ux_auctions_message_id
    on auctions (message_id)
    where (message_id IS NOT NULL);

create index if not exists idx_auctions_status_endtime
    on auctions (status, end_time);

create index if not exists idx_auctions_status_starttime
    on auctions (status, start_time);

create index if not exists idx_auctions_start_time
    on auctions (start_time);

create index if not exists idx_auctions_card_name_trgm
    on auctions using gin (card_name gin_trgm_ops);

create index if not exists idx_auctions_hero_name_trgm
    on auctions using gin (hero_name gin_trgm_ops);

create index if not exists idx_auctions_sched_by_start
    on auctions (start_time)
    where ((status)::text = 'scheduled'::text);

create index if not exists idx_auctions_start_time_date
    on auctions (((start_time at time zone 'Europe/Moscow')::date));

create index if not exists idx_auctions_status_names
    on auctions (status, lower(card_name::text), lower(hero_name::text));

create index if not exists idx_auctions_active_end
    on auctions (end_time)
    where ((status)::text = ANY ((ARRAY ['scheduled'::character varying, 'active'::character varying])::text[]));

create index if not exists idx_auctions_kind
    on auctions (auction_kind);

create index if not exists idx_auctions_card_id
    on auctions (card_id);




create index if not exists idx_cards_hero_trgm
    on cards using gin (hero_name gin_trgm_ops);

create index if not exists idx_cards_name_trgm
    on cards using gin (card_name gin_trgm_ops);

create index if not exists idx_cards_hero
    on cards (hero_name);

create index if not exists idx_cards_name
    on cards (card_name);

create index if not exists idx_cards_deck_num
    on cards (deck_id, num);

create table if not exists trusted_usernames
(
    username varchar(32) not null
        constraint trusted_usernames_pkey
            primary key
);

create table if not exists user_appeals
(
    id                 bigserial
        constraint user_appeals_pkey
            primary key,
    user_id            bigint                                           not null,
    username           text,
    topic              text                                             not null,
    description        text                                             not null,
    participants       text,
    media_message_ids  integer[]                default '{}'::integer[],
    origin_chat_id     bigint                                           not null,
    status             text                     default 'pending'::text not null,
    moderator_id       bigint,
    moderator_username text,
    moderator_comment  text,
    created_at         timestamp with time zone default now()           not null,
    updated_at         timestamp with time zone default now()           not null
);


create table if not exists users
(
    user_id                     bigint              not null
        constraint users_pkey
            primary key,
    username                    varchar(32),
    full_name                   varchar(255),
    is_subscribed               boolean   default true,
    is_luxury                   boolean   default false,
    warnings_count              integer   default 0,
    created_at                  timestamp default CURRENT_TIMESTAMP,
    is_trusted                  boolean   default false,
    pm_opened                   boolean   default false,
    first_pm_at                 timestamp,
    last_pm_at                  timestamp,
    uid_verif_confirmed_count   integer   default 0 not null,
    uid_verif_rejected_count    integer   default 0 not null,
    uid_verif_last_confirmed_at timestamp with time zone,
    uid_verif_last_rejected_at  timestamp with time zone
);

create table if not exists admins
(
    user_id  bigint not null
        constraint admins_pkey
            primary key
        constraint admins_user_id_fkey
            references users
            on delete cascade,
    username text,
    added_by bigint,
    added_at timestamp default CURRENT_TIMESTAMP
);

create table if not exists auction_owners
(
    id           serial
        constraint auction_owners_pkey
            primary key,
    auction_id   integer                      not null
        constraint auction_owners_auction_id_fkey
            references auctions
            on delete cascade,
    user_id      bigint                       not null
        constraint auction_owners_user_id_fkey
            references users
            on delete cascade,
    folder       text default 'default'::text not null
        constraint auction_owners_folder_chk
            check (folder = ANY (ARRAY ['default'::text, 'archived'::text, 'payable'::text])),
    owner_folder text default 'default'::text not null,
    constraint ux_auction_owners
        unique (auction_id, user_id)
);

create index if not exists idx_auction_owners_auction_id
    on auction_owners (auction_id);

create index if not exists idx_auction_owners_user_id
    on auction_owners (user_id);

create index if not exists idx_auction_owners_user_folder
    on auction_owners (user_id, folder);

create table if not exists audit_logs
(
    id          serial
        constraint audit_logs_pkey
            primary key,
    user_id     bigint
        constraint fk_audit_user
            references users
            on delete set null,
    action_type varchar(50) not null,
    auction_id  bigint
        constraint fk_audit_auction
            references auctions
            on delete cascade,
    details     text,
    created_at  timestamp default CURRENT_TIMESTAMP
);

create index if not exists idx_audit_user_time
    on audit_logs (user_id asc, created_at desc);

create index if not exists idx_audit_auction_time
    on audit_logs (auction_id asc, created_at desc);

create table if not exists bids
(
    bid_id                serial
        constraint bids_pkey
            primary key,
    auction_id            integer not null
        constraint bids_auction_id_fkey
            references auctions
            on delete cascade,
    bidder_id             bigint  not null
        constraint bids_bidder_id_fkey
            references users
            on delete cascade,
    amount                integer not null
        constraint chk_bids_amount_positive
            check (amount > 0)
        constraint ck_bids_positive
            check (amount > 0),
    placed_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    discussion_message_id bigint,
    created_at            timestamp with time zone default CURRENT_TIMESTAMP,
    constraint chk_bids_step_and_min_by_currency
        check (is_valid_bid(auction_id, amount))
);

create index if not exists idx_bids_auction_sort
    on bids (auction_id asc, amount desc, placed_at asc);

create index if not exists idx_bids_discussion_msg
    on bids (discussion_message_id asc, placed_at desc);

create index if not exists idx_bids_auction
    on bids (auction_id);

create index if not exists idx_bids_bidder
    on bids (bidder_id);

create table if not exists delete_requests
(
    id         serial
        constraint delete_requests_pkey
            primary key,
    lot_id     integer not null
        constraint delete_requests_lot_id_fkey
            references auctions
            on delete cascade,
    user_id    bigint  not null
        constraint delete_requests_user_id_fkey
            references users
            on delete cascade,
    reason     text,
    created_at timestamp   default CURRENT_TIMESTAMP,
    status     varchar(20) default 'pending'::character varying
);

create table if not exists notifications
(
    notification_id   serial
        constraint notifications_pkey
            primary key,
    user_id           bigint  not null
        constraint notifications_user_id_fkey
            references users
            on delete cascade,
    auction_id        integer not null
        constraint notifications_auction_id_fkey
            references auctions
            on delete cascade,
    notification_type varchar(50),
    sent_at           timestamp default CURRENT_TIMESTAMP
);

create index if not exists idx_notifications_user
    on notifications (user_id);

create table if not exists settings
(
    user_id              bigint not null
        constraint settings_pkey
            primary key
        constraint settings_user_id_fkey
            references users
            on delete cascade,
    notify_auction_start boolean default true,
    notify_bid_reminder  boolean default true,
    notify_auction_end   boolean default true,
    notify_daily_today   boolean default false
);

create table if not exists user_bans
(
    id           serial
        constraint user_bans_pkey
            primary key,
    user_id      bigint not null
        constraint user_bans_user_id_fkey
            references users
            on delete cascade,
    banned_until timestamp,
    reason       varchar(255),
    issued_at    timestamp default CURRENT_TIMESTAMP
);

create table if not exists user_subscriptions
(
    id                serial
        constraint user_subscriptions_pkey
            primary key,
    user_id           bigint  not null
        constraint user_subscriptions_user_id_fkey
            references users
            on delete cascade,
    card_id           integer not null
        constraint user_subscriptions_card_id_fkey
            references cards
            on delete cascade,
    created_at        timestamp default CURRENT_TIMESTAMP,
    last_confirmed_at timestamp with time zone,
    constraint ux_user_subscriptions
        unique (user_id, card_id)
);

create index if not exists idx_user_subscriptions_user
    on user_subscriptions (user_id);

create index if not exists idx_user_subscriptions_card
    on user_subscriptions (card_id);

create index if not exists idx_user_subs_last_confirmed
    on user_subscriptions (user_id asc, last_confirmed_at desc);

create table if not exists user_warnings
(
    id        serial
        constraint user_warnings_pkey
            primary key,
    user_id   bigint not null
        constraint user_warnings_user_id_fkey
            references users
            on delete cascade,
    reason    varchar(255),
    issued_at timestamp default CURRENT_TIMESTAMP,
    details   text
);

create unique index if not exists ux_users_username_ci
    on users (lower(username::text))
    where ((username IS NOT NULL) AND ((username)::text <> ''::text));

create index if not exists idx_users_is_luxury
    on users (is_luxury);

create table if not exists card_day_notifications
(
    id      bigserial
        constraint card_day_notifications_pkey
            primary key,
    user_id bigint                  not null,
    card_id bigint                  not null,
    day     date                    not null,
    sent_at timestamp default now() not null,
    constraint card_day_notifications_user_id_card_id_day_key
        unique (user_id, card_id, day)
);

create table if not exists unreachable_users
(
    user_id   bigint                  not null
        constraint unreachable_users_pkey
            primary key,
    reason    text                    not null,
    last_seen timestamp default now() not null
);

create table if not exists presets
(
    id    bigserial
        constraint presets_pkey
            primary key,
    key   text not null
        constraint presets_key_key
            unique,
    title text not null,
    constraint presets_key_uniq
        unique (key)
);

create table if not exists preset_aliases
(
    preset_id bigint not null
        constraint preset_aliases_preset_id_fkey
            references presets
            on delete cascade,
    alias     text   not null,
    constraint preset_aliases_pkey
        primary key (preset_id, alias)
);

create table if not exists user_preset_subscriptions
(
    id         bigserial
        constraint user_preset_subscriptions_pkey
            primary key,
    user_id    bigint                                 not null,
    preset_id  bigint                                 not null
        constraint user_preset_subscriptions_preset_id_fkey
            references presets
            on delete cascade,
    created_at timestamp with time zone default now() not null,
    constraint user_preset_subscriptions_user_id_preset_id_key
        unique (user_id, preset_id)
);

create table if not exists market_listings
(
    listing_id    serial
        constraint market_listings_pkey
            primary key,
    seller_id     bigint                                           not null
        constraint market_listings_seller_id_fkey
            references users
            on delete cascade,
    status        listing_status  default 'active'::listing_status not null,
    description   text,
    currency_type market_currency default 'cash'::market_currency  not null,
    cash_code     varchar(8),
    price_num     numeric(14, 2)  default 0                        not null
        constraint ck_ml_price_nonneg
            check (price_num >= (0)::numeric),
    created_at    timestamp       default now()                    not null,
    updated_at    timestamp       default now()                    not null,
    offer_kind    offer_kind      default 'cards'::offer_kind      not null,
    cover_file_id text,
    channel_id    bigint,
    message_id    bigint,
    deck_id       integer
        constraint market_listings_deck_id_fkey
            references decks
            on delete set null,
    proof_file_id text,
    proof_by_card jsonb           default '{}'::jsonb
);

create index if not exists idx_market_listings_seller_status
    on market_listings (seller_id, status);

create index if not exists idx_market_listings_currency
    on market_listings (currency_type, cash_code);

create index if not exists idx_market_listings_status_updated
    on market_listings (status asc, updated_at desc);

create index if not exists idx_market_listings_kind
    on market_listings (offer_kind);


create table if not exists market_listing_items
(
    id            serial
        constraint market_listing_items_pkey
            primary key,
    listing_id    integer           not null
        constraint market_listing_items_listing_id_fkey
            references market_listings
            on delete cascade,
    card_id       integer           not null
        constraint market_listing_items_card_id_fkey
            references cards
            on delete cascade,
    quantity      integer default 1 not null
        constraint ck_mli_qty_pos
            check (quantity > 0),
    proof_file_id text,
    constraint ux_mli
        unique (listing_id, card_id)
);

create index if not exists idx_market_listing_items_listing
    on market_listing_items (listing_id);

create index if not exists idx_market_listing_items_card
    on market_listing_items (card_id);

create unique index if not exists uq_market_listing_items
    on market_listing_items (listing_id, card_id);

create table if not exists market_rate_tiers
(
    id         serial
        constraint market_rate_tiers_pkey
            primary key,
    listing_id integer           not null
        constraint market_rate_tiers_listing_id_fkey
            references market_listings
            on delete cascade,
    label      text,
    qty        integer
        constraint ck_mrt_qty_nonneg
            check ((qty IS NULL) OR (qty >= 0)),
    pay_type   market_currency   not null,
    cash_code  varchar(8),
    price      numeric(14, 2)    not null
        constraint ck_mrt_price_nonneg
            check (price >= (0)::numeric),
    sort_order integer default 0 not null
);

create index if not exists idx_mrt_listing
    on market_rate_tiers (listing_id, sort_order);

create index if not exists idx_market_rate_tiers_listing
    on market_rate_tiers (listing_id);

create table if not exists exchange_batches
(
    batch_id               bigserial
        constraint exchange_batches_pkey
            primary key,
    user_id                bigint                                            not null
        constraint exchange_batches_user_id_fkey
            references users
            on delete cascade,
    deck_id                integer                                           not null
        constraint exchange_batches_deck_id_fkey
            references decks,
    mode                   text                                              not null
        constraint exchange_batches_mode_check
            check (mode = ANY (ARRAY ['card'::text, 'deck'::text, 'deck_split'::text])),
    currency               text                                              not null,
    price                  integer                                           not null,
    comment                text,
    proof_photo_id         text                     default 'NO_PROOF'::text not null,
    created_at             timestamp with time zone default now()            not null,
    status                 text                     default 'pending'::text  not null
        constraint chk_exchange_batches_status
            check (status = ANY (ARRAY [
                'pending'::text, 'approved'::text, 'rejected'::text,
                'publishing'::text, 'publication_failed'::text,
                'published'::text, 'deleted'::text
            ])),
    moderator_id           bigint,
    moderator_username     text,
    moderator_comment      text,
    moderated_at           timestamp with time zone,
    moderated_by           bigint,
    moderated_username     text,
    moderated_comment      text,
    manual_winner_id       bigint,
    manual_winner_username text,
    manual_price           integer,
    manual_link            text,
    manual_set_by          bigint,
    manual_set_at          timestamp with time zone,
    manual_sent_at         timestamp with time zone,
    posted_chat_id         bigint,
    posted_message_id      bigint,
    posted_at              timestamp with time zone,
    publication_started_at timestamp with time zone,
    publication_finished_at timestamp with time zone,
    publication_error      text,
    deleted_at             timestamp with time zone
);

create index if not exists idx_exchange_batches_status
    on exchange_batches (status);

create index if not exists idx_exchange_batches_deck
    on exchange_batches (deck_id);

create index if not exists idx_exchange_batches_status_created
    on exchange_batches (status asc, created_at desc);

create table if not exists exchange_items
(
    item_id    bigserial
        constraint exchange_items_pkey
            primary key,
    batch_id   bigint                                 not null
        constraint exchange_items_batch_id_fkey
            references exchange_batches
            on delete cascade,
    card_id    integer
        constraint exchange_items_card_id_fkey
            references cards,
    card_name  text,
    hero_name  text,
    created_at timestamp with time zone default now() not null
);

create index if not exists idx_exchange_items_batch
    on exchange_items (batch_id);

create index if not exists ix_exchange_items_batch_card
    on exchange_items (batch_id, card_id)
    where (card_id IS NOT NULL);

create index if not exists ix_auctions_publication_queue
    on auctions (status, publication_next_attempt_at, start_time, auction_id)
    where message_id is null
      and status in ('scheduled', 'publishing', 'publication_failed');

create index if not exists ix_auctions_schedule_conflicts
    on auctions (start_time, end_time, auction_id)
    where status in ('scheduled', 'publishing', 'active');

create index if not exists ix_bids_auction_lowest_winner_order
    on bids (auction_id, amount asc, placed_at asc, bid_id asc);

create unique index if not exists ux_exchange_batches_posted_message
    on exchange_batches (posted_chat_id, posted_message_id)
    where posted_chat_id is not null and posted_message_id is not null;

create index if not exists ix_exchange_batches_moderation_queue
    on exchange_batches (status, created_at, batch_id)
    where deleted_at is null;

create table if not exists guides_thanks
(
    user_id      bigint              not null
        constraint guides_thanks_pkey
            primary key,
    thanks_count integer   default 0 not null,
    last_at      timestamp default CURRENT_TIMESTAMP
);

create table if not exists admin_thanks_totals
(
    author       text                not null
        constraint admin_thanks_totals_pkey
            primary key,
    thanks_total bigint    default 0 not null,
    users_total  bigint    default 0 not null,
    updated_at   timestamp default CURRENT_TIMESTAMP
);

create table if not exists admin_thanks_users
(
    author       text                not null,
    user_id      bigint              not null,
    created_at   timestamp default CURRENT_TIMESTAMP,
    thanks_count bigint    default 0 not null,
    constraint admin_thanks_users_pkey
        primary key (author, user_id)
);

create table if not exists auction_win_mailings
(
    id               bigserial
        constraint auction_win_mailings_pkey
            primary key,
    auction_id       integer not null,
    target           text    not null,
    sent_by_user_id  bigint,
    sent_by_username text,
    sent_at          timestamp default CURRENT_TIMESTAMP
);

create index if not exists idx_auction_win_mailings_auction_id
    on auction_win_mailings (auction_id);

create table if not exists auction_manual_results
(
    auction_id      integer not null
        constraint auction_manual_results_pkey
            primary key,
    winner_user_id  bigint,
    winner_username text,
    owner_user_id   bigint,
    owner_username  text,
    amount          integer,
    updated_at      timestamp default CURRENT_TIMESTAMP,
    updated_by      bigint,
    moderator_comment text
);

create table if not exists auctions_image_id_backup
(
    auction_id   integer not null
        constraint auctions_image_id_backup_pkey
            primary key,
    old_image_id text,
    backed_up_at timestamp default now()
);

create table if not exists auction_posts_backfill
(
    post_id          bigint            not null
        constraint auction_posts_backfill_pkey
            primary key,
    post_link        text              not null,
    post_date_msk    timestamp,
    end_time_msk     timestamp,
    deadline_msk     timestamp,
    root_id          bigint,
    discussion_id    bigint,
    msgs_scanned     integer default 0 not null,
    numeric_msgs     integer default 0 not null,
    thread_bids      integer default 0 not null,
    thread_valid     integer default 0 not null,
    max_thread_valid bigint,
    winner_id        bigint,
    any_valid        integer default 0 not null,
    max_any_valid    bigint,
    note             text
);

create index if not exists idx_apb_post_date
    on auction_posts_backfill (post_date_msk);

create index if not exists idx_apb_post_day
    on auction_posts_backfill ((post_date_msk::date));

create table if not exists auction_posts_stats
(
    post_id           bigint                not null
        constraint auction_posts_stats_pkey
            primary key
        constraint auction_posts_stats_post_id_fkey
            references auction_posts_backfill
            on delete cascade,
    checked           boolean default false not null,
    checked_by        bigint,
    checked_at        timestamp,
    manual_winner_id  bigint,
    manual_max_bid    bigint,
    manual_valid_bids integer,
    manual_total_bids integer,
    manual_note       text,
    excluded          boolean default false not null,
    excluded_by       bigint,
    excluded_at       timestamp,
    excluded_reason   text,
    ordinal_no        integer,
    manual_date       date,
    manual_time       time,
    deck_no           integer,
    card_title        text,
    bidders_count     integer,
    min_bid           bigint,
    max_bid           bigint,
    owner_id          bigint,
    manual_link       text
);

create index if not exists idx_aps_checked
    on auction_posts_stats (checked);

create index if not exists idx_aps_excluded
    on auction_posts_stats (excluded);

create index if not exists idx_aps_deck_no
    on auction_posts_stats (deck_no);

create index if not exists idx_aps_card_title
    on auction_posts_stats (card_title);

create table if not exists exchange_print_stats
(
    batch_id           integer                                not null
        constraint exchange_print_stats_pkey
            primary key
        constraint exchange_print_stats_batch_id_fkey
            references exchange_batches
            on delete cascade,
    manual_winner_id   bigint,
    manual_winner_name text,
    manual_price       integer,
    manual_link        text,
    updated_by         bigint,
    updated_at         timestamp with time zone default now() not null
);

create index if not exists idx_exchange_print_stats_updated_at
    on exchange_print_stats (updated_at desc);

create table if not exists autobids
(
    autobid_id      serial
        constraint autobids_pkey
            primary key,
    auction_id      integer                                not null
        constraint autobids_auction_id_fkey
            references auctions
            on delete cascade,
    target_user_id  bigint                                 not null,
    target_username text,
    max_amount      integer                                not null
        constraint autobids_max_amount_check
            check (max_amount > 0),
    step            integer                  default 10    not null
        constraint autobids_step_check
            check (step > 0),
    is_active       boolean                  default true  not null,
    created_by      bigint                                 not null,
    created_at      timestamp with time zone default now() not null,
    updated_at      timestamp with time zone default now() not null,
    constraint autobids_auction_user_uniq
        unique (auction_id, target_user_id)
);

create table if not exists autobid_actions
(
    action_id             serial
        constraint autobid_actions_pkey
            primary key,
    autobid_id            integer                                not null
        constraint autobid_actions_autobid_id_fkey
            references autobids
            on delete cascade,
    auction_id            integer                                not null
        constraint autobid_actions_auction_id_fkey
            references auctions
            on delete cascade,
    target_user_id        bigint                                 not null,
    amount                integer                                not null
        constraint autobid_actions_amount_check
            check (amount > 0),
    discussion_message_id bigint                                 not null
        constraint autobid_actions_discussion_message_id_key
            unique,
    sent_at               timestamp with time zone default now() not null
);

create index if not exists autobid_actions_auction_id_idx
    on autobid_actions (auction_id);

create table if not exists user_uids
(
    uid         text                                              not null
        constraint user_uids_pkey
            primary key,
    user_id     bigint                                            not null
        constraint user_uids_user_id_key
            unique
        constraint user_uids_user_id_fkey
            references users
            on delete cascade,
    status      text                     default 'verified'::text not null
        constraint user_uids_status_check
            check (status = ANY (ARRAY ['verified'::text, 'revoked'::text])),
    verified_at timestamp with time zone default now()            not null,
    verified_by bigint,
    updated_at  timestamp with time zone default now()            not null,
    uid_hash    text,
    uid_enc     text,
    uid_last4   text
);

create table if not exists uid_verification_requests
(
    id                     bigserial
        constraint uid_verification_requests_pkey
            primary key,
    user_id                bigint                                           not null
        constraint uid_verification_requests_user_id_fkey
            references users
            on delete cascade,
    uid                    text                                             not null,
    challenge_code         text                                             not null,
    profile_file_id        text                                             not null,
    deal_file_ids          text[]                   default '{}'::text[]    not null,
    status                 text                     default 'pending'::text not null
        constraint uid_verification_requests_status_check
            check (status = ANY
                   (ARRAY ['pending'::text, 'approved'::text, 'rejected'::text, 'conflict'::text, 'expired'::text, 'revision'::text])),
    created_at             timestamp with time zone default now()           not null,
    decided_at             timestamp with time zone,
    decided_by             bigint,
    admin_comment          text,
    uid_proof_file_id      text,
    profile_proof_file_id  text,
    verification_code      text                                             not null,
    counterparty_usernames text[]                   default '{}'::text[]    not null,
    reg_date_proof_file_id text,
    extra_proof_file_ids   text[]                   default '{}'::text[]    not null,
    revision_flags         text[]                   default '{}'::text[]    not null,
    revision_reason        text,
    revision_requested_at  timestamp with time zone,
    revision_by            bigint,
    revision_by_username   text,
    revision_at            timestamp with time zone,
    revision_returned_at   timestamp with time zone,
    revision_completed_at  timestamp with time zone,
    uid_hash               text,
    uid_enc                text,
    uid_last4              text
);

create index if not exists idx_uid_verif_req_status_created
    on uid_verification_requests (status asc, created_at desc);

create index if not exists idx_uid_verif_req_uid
    on uid_verification_requests (uid);


create table if not exists uid_bans
(
    uid          text                                   not null
        constraint uid_bans_pkey
            primary key,
    reason       text,
    banned_by    bigint,
    banned_at    timestamp with time zone default now() not null,
    banned_until timestamp with time zone,
    uid_hash     text,
    uid_enc      text,
    uid_last4    text
);

create index if not exists idx_uid_bans_until
    on uid_bans (banned_until);

create table if not exists uid_verification_confirmations
(
    id                    bigserial
        constraint uid_verification_confirmations_pkey
            primary key,
    request_id            bigint                                           not null
        constraint uid_verification_confirmations_request_id_fkey
            references uid_verification_requests
            on delete cascade,
    counterparty_user_id  bigint
        constraint uid_verification_confirmations_counterparty_user_id_fkey
            references users
            on delete set null,
    counterparty_username text                                             not null,
    status                text                     default 'pending'::text not null
        constraint uid_verification_confirmations_status_check
            check (status = ANY (ARRAY ['pending'::text, 'confirmed'::text, 'rejected'::text, 'unreachable'::text])),
    created_at            timestamp with time zone default now()           not null,
    decided_at            timestamp with time zone,
    message_chat_id       bigint,
    message_id            bigint,
    constraint ux_uid_verif_conf_req_username
        unique (request_id, counterparty_username)
);

create table if not exists admin_thanks_clicks
(
    chat_id    bigint              not null,
    message_id integer             not null,
    user_id    bigint              not null,
    author     text                not null,
    item_id    bigint    default 0 not null,
    created_at timestamp default CURRENT_TIMESTAMP,
    constraint admin_thanks_clicks_pkey
        primary key (chat_id, message_id, user_id)
);

create table if not exists uid_verification_events
(
    id             bigserial
        constraint uid_verification_events_pkey
            primary key,
    request_id     bigint                                       not null
        constraint uid_verification_events_request_id_fkey
            references uid_verification_requests
            on delete cascade,
    actor_id       bigint,
    actor_username text,
    event_type     text                                         not null,
    details        jsonb                    default '{}'::jsonb not null,
    created_at     timestamp with time zone default now()       not null
);

create index if not exists idx_uidv_events_req_time
    on uid_verification_events (request_id asc, created_at desc);

create table if not exists uid_verification_confirmation_reminders
(
    conf_id bigint                                 not null
        constraint uid_verification_confirmation_reminders_conf_id_fkey
            references uid_verification_confirmations
            on delete cascade,
    stage_h integer                                not null,
    sent_at timestamp with time zone default now() not null,
    constraint uid_verification_confirmation_reminders_pkey
        primary key (conf_id, stage_h)
);

-- Objects introduced by immutable migrations 001, 003, 005 and 006. They are
-- materialized here so bootstrap represents the current empty-database shape;
-- the migration runner still records and safely replays every version.

CREATE TABLE IF NOT EXISTS public.bid_duplicate_archive (
    archive_id bigserial
        CONSTRAINT bid_duplicate_archive_pkey PRIMARY KEY,
    original_bid_id bigint NOT NULL,
    bid_payload jsonb NOT NULL,
    archived_at timestamptz NOT NULL DEFAULT now(),
    reason text NOT NULL DEFAULT 'duplicate discussion_message_id'
);

CREATE TABLE IF NOT EXISTS public.uid_verification_request_reminders (
    request_id bigint NOT NULL
        CONSTRAINT uid_verification_request_reminders_request_id_fkey
        REFERENCES public.uid_verification_requests(id) ON DELETE CASCADE,
    stage_h smallint NOT NULL,
    sent_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uid_verification_request_reminders_pkey
        PRIMARY KEY (request_id, stage_h)
);

CREATE TABLE IF NOT EXISTS public.telegram_outbox (
    outbox_id bigserial
        CONSTRAINT telegram_outbox_pkey PRIMARY KEY,
    dedupe_key text NOT NULL
        CONSTRAINT telegram_outbox_dedupe_key_key UNIQUE,
    method text NOT NULL DEFAULT 'send_message',
    chat_id bigint NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    available_at timestamptz NOT NULL DEFAULT now(),
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 5,
    locked_at timestamptz,
    sent_at timestamptz,
    telegram_message_id bigint,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    topic text NOT NULL DEFAULT 'legacy',
    delivery_state text NOT NULL DEFAULT 'not_attempted',
    reviewed_at timestamptz,
    reviewed_by bigint,
    review_note text,
    CONSTRAINT chk_telegram_outbox_method
        CHECK (method IN ('send_message', 'copy_message')),
    CONSTRAINT chk_telegram_outbox_status
        CHECK (status IN ('pending', 'processing', 'sent', 'failed')),
    CONSTRAINT chk_telegram_outbox_payload
        CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT chk_telegram_outbox_attempts
        CHECK (attempts >= 0 AND max_attempts > 0),
    CONSTRAINT chk_telegram_outbox_delivery_state
        CHECK (delivery_state IN (
            'not_attempted',
            'unknown',
            'confirmed_sent',
            'confirmed_not_sent'
        ))
);

CREATE TABLE IF NOT EXISTS public.schema_migrations (
    version text
        CONSTRAINT schema_migrations_pkey PRIMARY KEY,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_user_uids_uid_hash
    ON public.user_uids(uid_hash);

CREATE INDEX IF NOT EXISTS ix_uid_verification_requests_uid_hash
    ON public.uid_verification_requests(uid_hash)
    WHERE uid_hash IS NOT NULL;

-- Preserve the confirmed historical index name as well. Migration 001 uses a
-- newer partial-index name; production may legitimately contain both and the
-- alignment policy never drops an existing object.
CREATE INDEX IF NOT EXISTS idx_uid_verif_req_uid_hash
    ON public.uid_verification_requests(uid_hash);

CREATE UNIQUE INDEX IF NOT EXISTS ux_uid_bans_uid_hash
    ON public.uid_bans(uid_hash);

CREATE INDEX IF NOT EXISTS ix_auctions_due_finalization
    ON public.auctions(status, end_time)
    WHERE status IN ('active', 'finalizing', 'finalization_failed');

CREATE UNIQUE INDEX IF NOT EXISTS ux_bids_discussion_message_id
    ON public.bids(discussion_message_id)
    WHERE discussion_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_bids_auction_winner_order
    ON public.bids(auction_id, amount DESC, placed_at ASC, bid_id ASC);

CREATE INDEX IF NOT EXISTS ix_auctions_discussion_active
    ON public.auctions(discussion_message_id, status)
    WHERE discussion_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_telegram_outbox_pending
    ON public.telegram_outbox(available_at, outbox_id)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS ix_telegram_outbox_processing
    ON public.telegram_outbox(locked_at, outbox_id)
    WHERE status = 'processing';

CREATE INDEX IF NOT EXISTS ix_telegram_outbox_failed_review
    ON public.telegram_outbox(delivery_state, updated_at DESC, outbox_id DESC)
    WHERE status = 'failed';

CREATE INDEX IF NOT EXISTS ix_telegram_outbox_topic_created
    ON public.telegram_outbox(topic, created_at DESC);

CREATE OR REPLACE VIEW public.v_user_uid_status (
    tg_user_id,
    tg_username,
    uid,
    uid_status,
    verified_at,
    verified_by,
    uid_is_banned,
    banned_until,
    ban_reason
) AS
SELECT u.user_id,
       u.username,
       uu.uid,
       uu.status,
       uu.verified_at,
       uu.verified_by,
       ub.uid IS NOT NULL
           AND (ub.banned_until IS NULL OR ub.banned_until > now()),
       ub.banned_until,
       ub.reason
  FROM public.users AS u
  LEFT JOIN public.user_uids AS uu ON uu.user_id = u.user_id
  LEFT JOIN public.uid_bans AS ub ON ub.uid = uu.uid;

DO $bootstrap$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_trigger
         WHERE tgrelid = 'public.auctions'::regclass
           AND tgname = 'trg_no_currency_flip'
           AND NOT tgisinternal
    ) THEN
        EXECUTE $trigger$
            CREATE TRIGGER trg_no_currency_flip
            BEFORE UPDATE OF currency ON public.auctions
            FOR EACH ROW
            EXECUTE FUNCTION public.prevent_currency_change_if_bids()
        $trigger$;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_trigger
         WHERE tgrelid = 'public.user_appeals'::regclass
           AND tgname = 'trg_user_appeals_touch'
           AND NOT tgisinternal
    ) THEN
        EXECUTE $trigger$
            CREATE TRIGGER trg_user_appeals_touch
            BEFORE UPDATE ON public.user_appeals
            FOR EACH ROW
            EXECUTE FUNCTION public.touch_updated_at()
        $trigger$;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_trigger
         WHERE tgrelid = 'public.market_listings'::regclass
           AND tgname = 'trg_market_listings_touch'
           AND NOT tgisinternal
    ) THEN
        EXECUTE $trigger$
            CREATE TRIGGER trg_market_listings_touch
            BEFORE UPDATE ON public.market_listings
            FOR EACH ROW
            EXECUTE FUNCTION public.touch_market_listing()
        $trigger$;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_trigger
         WHERE tgrelid = 'public.uid_verification_requests'::regclass
           AND tgname = 'trg_uid_verif_sync_cols'
           AND NOT tgisinternal
    ) THEN
        EXECUTE $trigger$
            CREATE TRIGGER trg_uid_verif_sync_cols
            BEFORE INSERT OR UPDATE ON public.uid_verification_requests
            FOR EACH ROW
            EXECUTE FUNCTION public.uid_verif_sync_cols()
        $trigger$;
    END IF;
END
$bootstrap$;

-- Deliberately no trg_auctions_fix_end_time or trg_prevent_time_change:
-- migration 004 permits variable duration and moderator restart/extension.

COMMIT;
