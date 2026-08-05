from pathlib import Path


path = Path("bot/auction_notify.py")
source = path.read_text(encoding="utf-8")

import_needle = "    subscribers_for_rarity,\n)"
import_replacement = (
    "    subscribers_for_rarity,\n"
    "    subscribers_for_auction_presets,\n"
    ")"
)
if "    subscribers_for_auction_presets,\n" not in source:
    if import_needle not in source:
        raise SystemExit("auction preset import anchor not found")
    source = source.replace(import_needle, import_replacement, 1)

main_anchor = "            async def pref(name: str) -> Set[int]:\n"
main_block = """            try:
                uids_presets = await subscribers_for_auction_presets(
                    lot_title=lot_title,
                    card_id=cid_for_meta,
                    rarity=rarity_raw,
                    deck_id=deck_id,
                    deck_name=deck_name,
                )
                recipients.update(
                    int(user_id)
                    for user_id in (uids_presets or [])
                    if _to_int(user_id) is not None
                )
            except DBError as err:
                logger.warning(
                    "subscribers_for_auction_presets(%r) failed: %s",
                    lot_title,
                    err,
                )

"""
if source.count("await subscribers_for_auction_presets(") < 1:
    if main_anchor not in source:
        raise SystemExit("auction notification recipient anchor not found")
    source = source.replace(main_anchor, main_block + main_anchor, 1)

helper_anchor = """        except DBError as err:
            logger.warning("subscribers_for_rarity(%r) failed: %s", rarity_slug, err)

    return recipients
"""
helper_replacement = """        except DBError as err:
            logger.warning("subscribers_for_rarity(%r) failed: %s", rarity_slug, err)

    try:
        uids_presets = await subscribers_for_auction_presets(
            lot_title=lot_title,
            card_id=_to_int(auction.get("card_id")),
            rarity=rarity_slug or None,
            deck_id=_to_int(auction.get("deck_id")),
            deck_name=_to_str(auction.get("deck_name")).strip() or None,
        )
        recipients.update(
            int(user_id)
            for user_id in (uids_presets or [])
            if _to_int(user_id) is not None
        )
    except DBError as err:
        logger.warning(
            "subscribers_for_auction_presets(%r) failed: %s",
            lot_title,
            err,
        )

    return recipients
"""
if source.count("await subscribers_for_auction_presets(") < 2:
    if helper_anchor not in source:
        raise SystemExit("recipient helper anchor not found")
    source = source.replace(helper_anchor, helper_replacement, 1)

path.write_text(source, encoding="utf-8")
