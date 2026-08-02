"""Persistence adapter functions used by Telegram delivery modules.

This transitional adapter keeps database modules out of Telegram handlers while
legacy query functions are migrated behind typed repositories and application
ports.  It contains no business rules; handlers should use application use
cases for mutations and use these exports only for delivery/read-model support.
"""

from db.admin import log_admin_action, log_audit_action
from db.auctions import (
    count_sold_by_card_id,
    count_sold_same_card,
    get_auctions_by_date,
    get_auctions_by_date_with_owners,
    get_delete_request,
    get_lot_by_id,
    get_lot_owners,
    get_lots_by_owner,
    has_pending_lot,
    update_delete_request_status,
)
from db.cards import get_all_decks, get_card_by_id, get_cards_by_deck
from db.legacy import (
    add_delete_request,
    auto_finish_old_lots_for_owner,
    get_cards_by_ids,
    get_cards_ids_by_deck,
    get_deck_by_id,
    get_lots_by_owner_view,
    get_settings,
    get_uid_profile_binding,
    get_user_basic_info_by_username,
    get_user_id_by_uid_any,
    get_user_verified_uid,
    get_whois_admin_payload,
    is_subscribed,
    list_auctions,
    mark_user_private_chat_closed,
    mark_user_private_chat_opened,
    set_owner_lot_folder,
    set_settings,
    set_subscription,
    sync_trusted_status,
    update_lot_field,
)
from db.users import get_user, is_luxury_user

__all__ = [
    "add_delete_request",
    "auto_finish_old_lots_for_owner",
    "count_sold_by_card_id",
    "count_sold_same_card",
    "get_all_decks",
    "get_auctions_by_date",
    "get_auctions_by_date_with_owners",
    "get_card_by_id",
    "get_cards_by_deck",
    "get_cards_by_ids",
    "get_cards_ids_by_deck",
    "get_deck_by_id",
    "get_delete_request",
    "get_lot_by_id",
    "get_lot_owners",
    "get_lots_by_owner",
    "get_lots_by_owner_view",
    "get_settings",
    "get_uid_profile_binding",
    "get_user",
    "get_user_basic_info_by_username",
    "get_user_id_by_uid_any",
    "get_user_verified_uid",
    "get_whois_admin_payload",
    "has_pending_lot",
    "is_luxury_user",
    "is_subscribed",
    "list_auctions",
    "log_admin_action",
    "log_audit_action",
    "mark_user_private_chat_closed",
    "mark_user_private_chat_opened",
    "set_owner_lot_folder",
    "set_settings",
    "set_subscription",
    "sync_trusted_status",
    "update_delete_request_status",
    "update_lot_field",
]
