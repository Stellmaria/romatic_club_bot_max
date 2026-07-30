"""Finite-state machine definitions used by Telegram handlers.

The class and state declaration order in this module is part of the bot's
runtime contract: aiogram derives persisted state identifiers from these
names.  Keep existing names and ordering stable when extending the module.
"""

from aiogram.fsm.state import State, StatesGroup

__all__ = [
    "UIDVerificationFSM",
    "EditLotFSM",
    "UIDVerificationFixFSM",
    "PublicWhoFSM",
    "UIDVerificationRevisionFSM",
    "RejectLotFSM",
    "EditCardFotoFSM",
    "ModActionFSM",
    "BroadcastFSM",
    "PreviewScheduleFSM",
    "LuxScheduleFSM",
    "AddCardFSM",
    "UserDeleteLotFSM",
    "RejectDeleteFSM",
    "PrintWinStates",
    "PrintExStates",
    "ExchangeFSM",
    "UserAddLotFSM",
    "ApproveLotFSM",
    "EditCardFSM",
    "AddLotFSM",
    "CardSubscribeFSM",
    "EconomyFSM",
    "AppealFSM",
    "UserEditLotFSM",
    "PostStatsFSM",
    "EditScheduleFSM",
    "AddDeckFSM",
    "PostStatsEditFSM",
]


class UIDVerificationFSM(StatesGroup):
    waiting_for_uid = State()
    waiting_for_profile_with_code = State()
    waiting_for_uid_proof = State()
    waiting_for_reg_date_proof = State()
    waiting_for_deal_screenshot = State()
    waiting_for_deal_username = State()
    waiting_for_extra_photos = State()


class EditLotFSM(StatesGroup):
    choosing_month = State()
    choosing_day = State()
    choosing_slot = State()
    choosing_field = State()
    entering_value = State()
    waiting_for_photo = State()
    waiting_for_date = State()


class UIDVerificationFixFSM(StatesGroup):
    choosing_item = State()
    waiting_media = State()
    waiting_username = State()
    collecting_extra = State()


class PublicWhoFSM(StatesGroup):
    waiting_for_who_target = State()


class UIDVerificationRevisionFSM(StatesGroup):
    choosing_flags = State()
    waiting_reason = State()


class RejectLotFSM(StatesGroup):
    waiting_reason = State()


class EditCardFotoFSM(StatesGroup):
    waiting_for_new_photo = State()


class ModActionFSM(StatesGroup):
    waiting_for_trusted_user = State()
    waiting_for_untrusted_user = State()
    waiting_for_admin_user = State()
    waiting_for_admin_remove_user = State()
    waiting_for_reject_pending_reason = State()
    waiting_for_reject_delete_reason = State()
    waiting_for_reject_exchange_reason = State()
    waiting_for_reject_uid_verification_reason = State()
    waiting_for_whois_target = State()
    waiting_for_uid_ban_target = State()
    waiting_for_uid_ban_reason = State()
    waiting_for_uid_unban_target = State()
    waiting_for_user_ban_target = State()
    waiting_for_user_ban_reason = State()
    waiting_for_user_unban_target = State()
    waiting_for_master_ban_target = State()
    waiting_for_master_ban_reason = State()
    waiting_for_master_unban_target = State()
    waiting_for_master_ban_user = State()
    waiting_for_master_ban_uid = State()
    waiting_for_master_unban_user = State()
    waiting_for_master_unban_uid = State()
    waiting_for_reject_exchange_reason_one = State()


class BroadcastFSM(StatesGroup):
    waiting_for_text = State()


class PreviewScheduleFSM(StatesGroup):
    choosing_month = State()
    choosing_day = State()


class LuxScheduleFSM(StatesGroup):
    choosing_month = State()
    choosing_day = State()


class AddCardFSM(StatesGroup):
    waiting_for_admin_password = State()
    waiting_for_deck = State()
    waiting_for_card_name = State()
    waiting_for_num = State()
    waiting_for_hero_name = State()
    waiting_for_image = State()
    waiting_for_rarity = State()
    waiting_for_story = State()
    waiting_for_quote = State()
    waiting_for_gift_type = State()
    waiting_for_gift_amount = State()
    waiting_for_confirmation = State()


# noinspection DuplicatedCode
class UserDeleteLotFSM(StatesGroup):
    waiting_for_delete_reason = State()


# noinspection DuplicatedCode
class RejectDeleteFSM(StatesGroup):
    waiting_for_reject_reason = State()


class PrintWinStates(StatesGroup):
    waiting_manual = State()


class PrintExStates(StatesGroup):
    waiting_manual = State()


class ExchangeFSM(StatesGroup):
    waiting_for_deck = State()
    waiting_for_mode = State()
    waiting_for_card = State()
    waiting_for_currency = State()
    waiting_for_copies = State()
    waiting_for_price = State()
    waiting_for_comment = State()
    waiting_for_proof = State()


# noinspection DuplicatedCode
class UserAddLotFSM(StatesGroup):
    waiting_for_auction_kind = State()
    waiting_for_own_variant = State()
    waiting_for_custom_card = State()
    waiting_for_subscription = State()
    waiting_for_proof_photo = State()
    waiting_for_deck = State()
    waiting_for_craft_uid = State()
    waiting_for_card = State()
    waiting_for_currency = State()
    waiting_for_custom_offer_terms = State()
    waiting_for_start_price = State()
    waiting_for_comment = State()
    waiting_for_confirmation = State()
    waiting_for_proof_photo_final = State()


class ApproveLotFSM(StatesGroup):
    choosing_month = State()
    choosing_day = State()
    choosing_time = State()
    confirming = State()
    editing_pending_lot = State()
    editing_pending_price = State()
    editing_pending_currency = State()
    editing_pending_comment = State()
    uploading_image = State()


class EditCardFSM(StatesGroup):
    choosing_field = State()
    entering_value = State()
    waiting_for_image = State()
    confirming_delete = State()


class AddLotFSM(StatesGroup):
    waiting_for_subscription = State()
    waiting_for_proof_photo = State()
    waiting_for_deck = State()
    waiting_for_card = State()
    waiting_for_currency = State()
    waiting_for_start_price = State()
    waiting_for_comment = State()
    waiting_for_confirmation = State()


class CardSubscribeFSM(StatesGroup):
    waiting_for_deck = State()
    waiting_for_card = State()


class EconomyFSM(StatesGroup):
    # deck type
    deck_id = State()
    deck_type = State()
    # card gift
    gift_card_id = State()
    gift_cups = State()
    gift_diamonds = State()
    # card obtain
    obtain_card_id = State()
    obtain_type = State()
    obtain_amount = State()


class AppealFSM(StatesGroup):
    waiting_for_topic = State()
    waiting_for_description = State()
    waiting_for_usernames = State()
    waiting_for_media = State()
    waiting_for_admin_reply = State()


class UserEditLotFSM(StatesGroup):
    choosing_field = State()
    waiting_for_price = State()
    waiting_for_currency = State()
    waiting_for_currency_price = State()
    waiting_for_comment = State()


class PostStatsFSM(StatesGroup):
    waiting_for_note = State()


class EditScheduleFSM(StatesGroup):
    choosing_month = State()
    choosing_day = State()
    choosing_lot = State()
    choosing_field = State()
    entering_value = State()
    editing_currency_price = State()
    confirming_delete = State()
    choosing_time = State()


class AddDeckFSM(StatesGroup):
    waiting_for_admin_password = State()
    waiting_for_deck_name = State()
    waiting_for_confirmation = State()


class PostStatsEditFSM(StatesGroup):
    waiting_for_value = State()
