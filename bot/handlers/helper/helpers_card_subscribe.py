from db.legacy import add_user_subscription, get_card_by_id, list_user_card_subs, unsubscribe_subscription


async def subscribe_to_card(user_id: int, card_id: int):
    card = await get_card_by_id(card_id)
    if not card:
        return None
    await add_user_subscription(user_id, card_id, card['card_name'], card['hero_name'])
    return card


async def unsubscribe_from_card(sub_id: int, user_id: int) -> bool:
    return await unsubscribe_subscription(sub_id, user_id)


async def get_subscriptions(user_id: int) -> list[dict]:
    return await list_user_card_subs(user_id)
