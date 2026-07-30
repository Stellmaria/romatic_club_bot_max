-- 002_initial_schema.sql
-- Полная прикладная схема. Без владельцев объектов и без внутренностей расширений PostgreSQL.
SET search_path = public, pg_catalog;

CREATE TABLE IF NOT EXISTS public.custom_emojis
(
    name     text not null
        primary key,
    emoji_id text not null
        unique
);

CREATE TABLE IF NOT EXISTS public.decks
(
    id        serial
        primary key,
    name      varchar(255) not null,
    deck_type deck_type default 'resource'::deck_type
);

CREATE TABLE IF NOT EXISTS public.cards
(
    card_id         serial
        primary key,
    deck_id         integer                                      not null
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

CREATE TABLE IF NOT EXISTS public.auctions
(
    auction_id            serial
        primary key,
    card_name             varchar(255)                                      not null,
    hero_name             varchar(255),
    image_id              text,
    start_price           integer     default 0                             not null,
    start_time            timestamp                                         not null,
    end_time              timestamp                                         not null,
    status                varchar(20) default 'scheduled'::character varying
        constraint chk_auctions_status
            check ((status)::text = ANY
                   ((ARRAY ['pending'::character varying, 'scheduled'::character varying, 'publishing'::character varying, 'active'::character varying, 'finished'::character varying, 'rejected'::character varying])::text[])),
    created_at            timestamp   default CURRENT_TIMESTAMP,
    currency              varchar(20) default 'чашки'::character varying
        constraint chk_auctions_currency
            check ((currency)::text = ANY
                   ((ARRAY ['алмазы'::character varying, 'чашки'::character varying, 'сокровища'::character varying])::text[])),
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
                                                                            references cards
                                                                                on delete set null,
    constraint chk_auctions_time_order
        check (
            CASE
                WHEN ((status)::text = ANY
                      ((ARRAY ['scheduled'::character varying, 'active'::character varying, 'finished'::character varying])::text[]))
                    THEN (end_time > start_time)
                ELSE true
                END),
    constraint auctions_end_eq_start_plus_31
        check (abs(EXTRACT(epoch FROM (end_time - (start_time + '00:31:00'::interval)))) <= (2)::numeric)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_auctions_discussion_msg
    on auctions (discussion_message_id)
    where (discussion_message_id IS NOT NULL);

CREATE UNIQUE INDEX IF NOT EXISTS ux_auctions_message_id
    on auctions (message_id)
    where (message_id IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_auctions_status_endtime
    on auctions (status, end_time);

CREATE INDEX IF NOT EXISTS idx_auctions_status_starttime
    on auctions (status, start_time);

CREATE INDEX IF NOT EXISTS idx_auctions_start_time
    on auctions (start_time);

CREATE INDEX IF NOT EXISTS idx_auctions_card_name_trgm
    on auctions using gin (card_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_auctions_hero_name_trgm
    on auctions using gin (hero_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_auctions_sched_by_start
    on auctions (start_time)
    where ((status)::text = 'scheduled'::text);

-- В старых установках start_time уже мог быть переведён в timestamptz.
-- Прямой cast timestamptz::date зависит от TimeZone сессии и поэтому не
-- является IMMUTABLE: PostgreSQL запрещает такой expression index.
-- Для timestamptz фиксируем прикладной часовой пояс, для legacy timestamp
-- сохраняем прежнюю семантику календарной даты.
DO $migration$
DECLARE
    start_time_is_timestamptz boolean;
BEGIN
    IF to_regclass('public.idx_auctions_start_time_date') IS NULL THEN
        SELECT a.atttypid = 'timestamp with time zone'::regtype
          INTO start_time_is_timestamptz
        FROM pg_catalog.pg_attribute AS a
        WHERE a.attrelid = 'public.auctions'::regclass
          AND a.attname = 'start_time'
          AND a.attnum > 0
          AND NOT a.attisdropped;

        IF start_time_is_timestamptz THEN
            EXECUTE $ddl$
                CREATE INDEX idx_auctions_start_time_date
                ON public.auctions (
                    ((start_time AT TIME ZONE 'Europe/Moscow')::date)
                )
            $ddl$;
        ELSE
            EXECUTE $ddl$
                CREATE INDEX idx_auctions_start_time_date
                ON public.auctions ((start_time::date))
            $ddl$;
        END IF;
    END IF;
END
$migration$;

CREATE INDEX IF NOT EXISTS idx_auctions_status_names
    on auctions (status, lower(card_name::text), lower(hero_name::text));

CREATE INDEX IF NOT EXISTS idx_auctions_active_end
    on auctions (end_time)
    where ((status)::text = ANY ((ARRAY ['scheduled'::character varying, 'active'::character varying])::text[]));

CREATE INDEX IF NOT EXISTS idx_auctions_kind
    on auctions (auction_kind);

CREATE INDEX IF NOT EXISTS idx_auctions_card_id
    on auctions (card_id);

CREATE INDEX IF NOT EXISTS idx_cards_hero_trgm
    on cards using gin (hero_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_cards_name_trgm
    on cards using gin (card_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_cards_hero
    on cards (hero_name);

CREATE INDEX IF NOT EXISTS idx_cards_name
    on cards (card_name);

CREATE INDEX IF NOT EXISTS idx_cards_deck_num
    on cards (deck_id, num);

CREATE TABLE IF NOT EXISTS public.trusted_usernames
(
    username varchar(32) not null
        primary key
);

CREATE TABLE IF NOT EXISTS public.user_appeals
(
    id                 bigserial
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

CREATE TABLE IF NOT EXISTS public.users
(
    user_id                     bigint              not null
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

CREATE TABLE IF NOT EXISTS public.admins
(
    user_id  bigint not null
        primary key
        references users
            on delete cascade,
    username text,
    added_by bigint,
    added_at timestamp default CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS public.auction_owners
(
    id           serial
        primary key,
    auction_id   integer                      not null
        references auctions
            on delete cascade,
    user_id      bigint                       not null
        references users
            on delete cascade,
    folder       text default 'default'::text not null
        constraint auction_owners_folder_chk
            check (folder = ANY (ARRAY ['default'::text, 'archived'::text, 'payable'::text])),
    owner_folder text default 'default'::text not null,
    constraint ux_auction_owners
        unique (auction_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_auction_owners_auction_id
    on auction_owners (auction_id);

CREATE INDEX IF NOT EXISTS idx_auction_owners_user_id
    on auction_owners (user_id);

CREATE INDEX IF NOT EXISTS idx_auction_owners_user_folder
    on auction_owners (user_id, folder);

CREATE TABLE IF NOT EXISTS public.audit_logs
(
    id          serial
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

CREATE INDEX IF NOT EXISTS idx_audit_user_time
    on audit_logs (user_id asc, created_at desc);

CREATE INDEX IF NOT EXISTS idx_audit_auction_time
    on audit_logs (auction_id asc, created_at desc);

CREATE TABLE IF NOT EXISTS public.delete_requests
(
    id         serial
        primary key,
    lot_id     integer not null
        references auctions
            on delete cascade,
    user_id    bigint  not null
        references users
            on delete cascade,
    reason     text,
    created_at timestamp   default CURRENT_TIMESTAMP,
    status     varchar(20) default 'pending'::character varying
);

CREATE TABLE IF NOT EXISTS public.notifications
(
    notification_id   serial
        primary key,
    user_id           bigint  not null
        references users
            on delete cascade,
    auction_id        integer not null
        references auctions
            on delete cascade,
    notification_type varchar(50),
    sent_at           timestamp default CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_notifications_user
    on notifications (user_id);

CREATE TABLE IF NOT EXISTS public.settings
(
    user_id              bigint not null
        primary key
        references users
            on delete cascade,
    notify_auction_start boolean default true,
    notify_bid_reminder  boolean default true,
    notify_auction_end   boolean default true,
    notify_daily_today   boolean default false
);

CREATE TABLE IF NOT EXISTS public.user_bans
(
    id           serial
        primary key,
    user_id      bigint not null
        references users
            on delete cascade,
    banned_until timestamp,
    reason       varchar(255),
    issued_at    timestamp default CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS public.user_subscriptions
(
    id                serial
        primary key,
    user_id           bigint  not null
        references users
            on delete cascade,
    card_id           integer not null
        references cards
            on delete cascade,
    created_at        timestamp default CURRENT_TIMESTAMP,
    last_confirmed_at timestamp with time zone,
    constraint ux_user_subscriptions
        unique (user_id, card_id)
);

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user
    on user_subscriptions (user_id);

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_card
    on user_subscriptions (card_id);

CREATE INDEX IF NOT EXISTS idx_user_subs_last_confirmed
    on user_subscriptions (user_id asc, last_confirmed_at desc);

CREATE TABLE IF NOT EXISTS public.user_warnings
(
    id        serial
        primary key,
    user_id   bigint not null
        references users
            on delete cascade,
    reason    varchar(255),
    issued_at timestamp default CURRENT_TIMESTAMP,
    details   text
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username_ci
    on users (lower(username::text))
    where ((username IS NOT NULL) AND ((username)::text <> ''::text));

CREATE INDEX IF NOT EXISTS idx_users_is_luxury
    on users (is_luxury);

CREATE TABLE IF NOT EXISTS public.card_day_notifications
(
    id      bigserial
        primary key,
    user_id bigint                  not null
        references users
            on delete cascade,
    card_id integer                 not null
        references cards
            on delete cascade,
    day     date                    not null,
    sent_at timestamp default now() not null,
    unique (user_id, card_id, day)
);

CREATE TABLE IF NOT EXISTS public.unreachable_users
(
    user_id   bigint                  not null
        primary key,
    reason    text                    not null,
    last_seen timestamp default now() not null
);

CREATE TABLE IF NOT EXISTS public.presets
(
    id    bigserial
        primary key,
    key   text not null
        constraint presets_key_uniq
            unique,
    title text not null
);

CREATE TABLE IF NOT EXISTS public.preset_aliases
(
    preset_id bigint not null
        references presets
            on delete cascade,
    alias     text   not null,
    primary key (preset_id, alias)
);

CREATE TABLE IF NOT EXISTS public.user_preset_subscriptions
(
    id         bigserial
        primary key,
    user_id    bigint                                 not null
        references users
            on delete cascade,
    preset_id  bigint                                 not null
        references presets
            on delete cascade,
    created_at timestamp with time zone default now() not null,
    unique (user_id, preset_id)
);

CREATE TABLE IF NOT EXISTS public.market_listings
(
    listing_id    serial
        primary key,
    seller_id     bigint                                           not null
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
                                                                   references decks
                                                                       on delete set null,
    proof_file_id text,
    proof_by_card jsonb           default '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_market_listings_seller_status
    on market_listings (seller_id, status);

CREATE INDEX IF NOT EXISTS idx_market_listings_currency
    on market_listings (currency_type, cash_code);

CREATE INDEX IF NOT EXISTS idx_market_listings_status_updated
    on market_listings (status asc, updated_at desc);

CREATE INDEX IF NOT EXISTS idx_market_listings_kind
    on market_listings (offer_kind);

CREATE TABLE IF NOT EXISTS public.market_listing_items
(
    id            serial
        primary key,
    listing_id    integer           not null
        references market_listings
            on delete cascade,
    card_id       integer           not null
        references cards
            on delete cascade,
    quantity      integer default 1 not null
        constraint ck_mli_qty_pos
            check (quantity > 0),
    proof_file_id text,
    constraint ux_mli
        unique (listing_id, card_id)
);

CREATE INDEX IF NOT EXISTS idx_market_listing_items_listing
    on market_listing_items (listing_id);

CREATE INDEX IF NOT EXISTS idx_market_listing_items_card
    on market_listing_items (card_id);

CREATE TABLE IF NOT EXISTS public.market_rate_tiers
(
    id         serial
        primary key,
    listing_id integer           not null
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

CREATE INDEX IF NOT EXISTS idx_mrt_listing
    on market_rate_tiers (listing_id, sort_order);

CREATE INDEX IF NOT EXISTS idx_market_rate_tiers_listing
    on market_rate_tiers (listing_id);

CREATE TABLE IF NOT EXISTS public.exchange_batches
(
    batch_id               bigserial
        primary key,
    user_id                bigint                                            not null
        references users
            on delete cascade,
    deck_id                integer                                           not null
        references decks,
    mode                   text                                              not null
        constraint exchange_batches_mode_check
            check (mode = ANY (ARRAY ['card'::text, 'deck'::text, 'deck_split'::text])),
    currency               text                                              not null,
    price                  integer                                           not null,
    comment                text,
    proof_photo_id         text                     default 'NO_PROOF'::text not null,
    created_at             timestamp with time zone default now()            not null,
    status                 text                     default 'pending'::text  not null,
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
    deleted_at             timestamp with time zone
);

CREATE INDEX IF NOT EXISTS idx_exchange_batches_status
    on exchange_batches (status);

CREATE INDEX IF NOT EXISTS idx_exchange_batches_deck
    on exchange_batches (deck_id);

CREATE INDEX IF NOT EXISTS idx_exchange_batches_status_created
    on exchange_batches (status asc, created_at desc);

CREATE TABLE IF NOT EXISTS public.exchange_items
(
    item_id    bigserial
        primary key,
    batch_id   bigint                                 not null
        references exchange_batches
            on delete cascade,
    card_id    integer
        references cards,
    card_name  text,
    hero_name  text,
    created_at timestamp with time zone default now() not null
);

CREATE INDEX IF NOT EXISTS idx_exchange_items_batch
    on exchange_items (batch_id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_exchange_items_batch_card
    on exchange_items (batch_id, card_id)
    where (card_id IS NOT NULL);

CREATE TABLE IF NOT EXISTS public.guides_thanks
(
    user_id      bigint              not null
        primary key,
    thanks_count integer   default 0 not null,
    last_at      timestamp default CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS public.admin_thanks_totals
(
    author       text                not null
        primary key,
    thanks_total bigint    default 0 not null,
    users_total  bigint    default 0 not null,
    updated_at   timestamp default CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS public.admin_thanks_users
(
    author       text                not null,
    user_id      bigint              not null,
    created_at   timestamp default CURRENT_TIMESTAMP,
    thanks_count bigint    default 0 not null,
    primary key (author, user_id)
);

CREATE TABLE IF NOT EXISTS public.auction_win_mailings
(
    id               bigserial
        primary key,
    auction_id       integer not null
        references auctions
            on delete cascade,
    target           text    not null,
    sent_by_user_id  bigint,
    sent_by_username text,
    sent_at          timestamp default CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_auction_win_mailings_auction_id
    on auction_win_mailings (auction_id);

CREATE TABLE IF NOT EXISTS public.auction_manual_results
(
    auction_id        integer not null
        primary key
        references auctions
            on delete cascade,
    winner_user_id    bigint,
    winner_username   text,
    owner_user_id     bigint,
    owner_username    text,
    amount            integer,
    updated_at        timestamp default CURRENT_TIMESTAMP,
    updated_by        bigint,
    moderator_comment text
);

CREATE TABLE IF NOT EXISTS public.auctions_image_id_backup
(
    auction_id   integer not null
        primary key
        references auctions
            on delete cascade,
    old_image_id text,
    backed_up_at timestamp default now()
);

CREATE TABLE IF NOT EXISTS public.auction_posts_backfill
(
    post_id          bigint            not null
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

CREATE INDEX IF NOT EXISTS idx_apb_post_date
    on auction_posts_backfill (post_date_msk);

-- Та же совместимость нужна для баз, где post_date_msk был вручную
-- изменён на timestamptz. Название столбца сохраняет МСК-семантику.
DO $migration$
DECLARE
    post_date_is_timestamptz boolean;
BEGIN
    IF to_regclass('public.idx_apb_post_day') IS NULL THEN
        SELECT a.atttypid = 'timestamp with time zone'::regtype
          INTO post_date_is_timestamptz
        FROM pg_catalog.pg_attribute AS a
        WHERE a.attrelid = 'public.auction_posts_backfill'::regclass
          AND a.attname = 'post_date_msk'
          AND a.attnum > 0
          AND NOT a.attisdropped;

        IF post_date_is_timestamptz THEN
            EXECUTE $ddl$
                CREATE INDEX idx_apb_post_day
                ON public.auction_posts_backfill (
                    ((post_date_msk AT TIME ZONE 'Europe/Moscow')::date)
                )
            $ddl$;
        ELSE
            EXECUTE $ddl$
                CREATE INDEX idx_apb_post_day
                ON public.auction_posts_backfill ((post_date_msk::date))
            $ddl$;
        END IF;
    END IF;
END
$migration$;

CREATE TABLE IF NOT EXISTS public.auction_posts_stats
(
    post_id           bigint                not null
        primary key
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

CREATE INDEX IF NOT EXISTS idx_aps_checked
    on auction_posts_stats (checked);

CREATE INDEX IF NOT EXISTS idx_aps_excluded
    on auction_posts_stats (excluded);

CREATE INDEX IF NOT EXISTS idx_aps_deck_no
    on auction_posts_stats (deck_no);

CREATE INDEX IF NOT EXISTS idx_aps_card_title
    on auction_posts_stats (card_title);

CREATE TABLE IF NOT EXISTS public.exchange_print_stats
(
    batch_id           bigint                                 not null
        primary key
        references exchange_batches
            on delete cascade,
    manual_winner_id   bigint,
    manual_winner_name text,
    manual_price       integer,
    manual_link        text,
    updated_by         bigint,
    updated_at         timestamp with time zone default now() not null
);

CREATE INDEX IF NOT EXISTS idx_exchange_print_stats_updated_at
    on exchange_print_stats (updated_at desc);

CREATE TABLE IF NOT EXISTS public.autobids
(
    autobid_id      serial
        primary key,
    auction_id      integer                                not null
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

CREATE TABLE IF NOT EXISTS public.autobid_actions
(
    action_id             serial
        primary key,
    autobid_id            integer                                not null
        references autobids
            on delete cascade,
    auction_id            integer                                not null
        references auctions
            on delete cascade,
    target_user_id        bigint                                 not null,
    amount                integer                                not null
        constraint autobid_actions_amount_check
            check (amount > 0),
    discussion_message_id bigint                                 not null
        unique,
    sent_at               timestamp with time zone default now() not null
);

CREATE INDEX IF NOT EXISTS autobid_actions_auction_id_idx
    on autobid_actions (auction_id);

CREATE TABLE IF NOT EXISTS public.user_uids
(
    uid         text                                              not null
        primary key,
    user_id     bigint                                            not null
        unique
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

CREATE UNIQUE INDEX IF NOT EXISTS ux_user_uids_uid_hash
    on user_uids (uid_hash);

CREATE TABLE IF NOT EXISTS public.uid_verification_requests
(
    id                     bigserial
        primary key,
    user_id                bigint                                           not null
        references users
            on delete cascade,
    uid                    text                                             not null,
    challenge_code         text                                             not null,
    profile_file_id        text                                             not null,
    deal_file_ids          text[]                   default '{}'::text[]    not null,
    status                 text                     default 'pending'::text not null
        constraint uid_verification_requests_status_check
            check (status = ANY
                   (ARRAY ['pending'::text, 'approved'::text, 'rejected'::text, 'conflict'::text, 'revision'::text])),
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

CREATE INDEX IF NOT EXISTS idx_uid_verif_req_status_created
    on uid_verification_requests (status asc, created_at desc);

CREATE INDEX IF NOT EXISTS idx_uid_verif_req_uid
    on uid_verification_requests (uid);

CREATE INDEX IF NOT EXISTS idx_uid_verif_req_uid_hash
    on uid_verification_requests (uid_hash);

CREATE TABLE IF NOT EXISTS public.uid_bans
(
    uid          text                                   not null
        primary key,
    reason       text,
    banned_by    bigint,
    banned_at    timestamp with time zone default now() not null,
    banned_until timestamp with time zone,
    uid_hash     text,
    uid_enc      text,
    uid_last4    text
);

CREATE INDEX IF NOT EXISTS idx_uid_bans_until
    on uid_bans (banned_until);

CREATE UNIQUE INDEX IF NOT EXISTS ux_uid_bans_uid_hash
    on uid_bans (uid_hash);

CREATE TABLE IF NOT EXISTS public.uid_verification_confirmations
(
    id                    bigserial
        primary key,
    request_id            bigint                                           not null
        references uid_verification_requests
            on delete cascade,
    counterparty_user_id  bigint
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

CREATE TABLE IF NOT EXISTS public.admin_thanks_clicks
(
    chat_id    bigint              not null,
    message_id integer             not null,
    user_id    bigint              not null,
    author     text                not null,
    item_id    bigint    default 0 not null,
    created_at timestamp default CURRENT_TIMESTAMP,
    primary key (chat_id, message_id, user_id)
);

CREATE TABLE IF NOT EXISTS public.uid_verification_events
(
    id             bigserial
        primary key,
    request_id     bigint                                       not null
        references uid_verification_requests
            on delete cascade,
    actor_id       bigint,
    actor_username text,
    event_type     text                                         not null,
    details        jsonb                    default '{}'::jsonb not null,
    created_at     timestamp with time zone default now()       not null
);

CREATE INDEX IF NOT EXISTS idx_uidv_events_req_time
    on uid_verification_events (request_id asc, created_at desc);

CREATE TABLE IF NOT EXISTS public.uid_verification_confirmation_reminders
(
    conf_id bigint                                 not null
        references uid_verification_confirmations
            on delete cascade,
    stage_h integer                                not null,
    sent_at timestamp with time zone default now() not null,
    primary key (conf_id, stage_h)
);

-- Функция должна существовать до создания CHECK-ограничения таблицы bids.
CREATE OR REPLACE FUNCTION public.is_valid_bid(p_auction_id integer, p_amount integer) returns boolean
    stable
    language plpgsql
as
$$
DECLARE
  cur     text;
  start_p integer;
  step    integer;
  min_cur integer;
  anchor  integer;
  max_b   integer;
BEGIN
  IF p_amount IS NULL THEN
    RETURN false;
  END IF;

  SELECT lower(trim(a.currency)), COALESCE(a.start_price, 0)
    INTO cur, start_p
  FROM public.auctions a
  WHERE a.auction_id = p_auction_id;

  IF NOT FOUND OR cur IS NULL THEN
    RETURN false;
  END IF;

  -- нормализация валют
  IF cur IN ('💎','алмаз','алмазы','diamond','diamonds') THEN cur := 'алмазы'; END IF;
  IF cur IN ('🍵','чай','чашки','cups') THEN cur := 'чашки'; END IF;
  IF cur IN ('🪙','сокровища','treasure','treasures') THEN cur := 'сокровища'; END IF;

  -- правила шага/минималки
  IF cur = 'алмазы' THEN
    step := 10;  min_cur := 30;
  ELSIF cur = 'чашки' THEN
    step := 2;   min_cur := 2;
  ELSIF cur = 'сокровища' THEN
    step := 10;  min_cur := 0;   -- если хочешь минимум, поставь 10/50/150 и т.д.
  ELSE
    RETURN false;
  END IF;

  anchor := GREATEST(start_p, min_cur);

  IF p_amount < anchor THEN
    RETURN false;
  END IF;

  -- шаг считаем относительно anchor (чтобы стартовая цена могла быть не кратна step)
  IF step > 1 AND ((p_amount - anchor) % step) <> 0 THEN
    RETURN false;
  END IF;

  SELECT max(b.amount) INTO max_b
  FROM public.bids b
  WHERE b.auction_id = p_auction_id;

  IF max_b IS NULL THEN
    RETURN true; -- первая ставка, уже прошла проверки
  END IF;

  RETURN p_amount >= (max_b + step);
END;
$$;

CREATE TABLE IF NOT EXISTS public.bids
(
    bid_id                serial
        primary key,
    auction_id            integer not null
        references auctions
            on delete cascade,
    bidder_id             bigint  not null
        references users
            on delete cascade,
    amount                integer not null
        constraint chk_bids_amount_positive
            check (amount > 0),
    placed_at             timestamp default CURRENT_TIMESTAMP,
    discussion_message_id bigint,
    created_at            timestamp default CURRENT_TIMESTAMP,
    constraint chk_bids_step_and_min_by_currency
        check (is_valid_bid(auction_id, amount))
);

CREATE INDEX IF NOT EXISTS idx_bids_auction_sort
    on bids (auction_id asc, amount desc, placed_at asc);

CREATE INDEX IF NOT EXISTS idx_bids_discussion_msg
    on bids (discussion_message_id asc, placed_at desc);

CREATE INDEX IF NOT EXISTS idx_bids_auction
    on bids (auction_id);

CREATE INDEX IF NOT EXISTS idx_bids_bidder
    on bids (bidder_id);
