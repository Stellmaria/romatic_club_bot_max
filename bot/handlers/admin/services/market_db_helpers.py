import json

from aiogram.fsm.context import FSMContext


async def _db_exec(sql: str, *args):
    import importlib
    dbmod = importlib.import_module("db.db")
    pool = getattr(dbmod, "db_pool", None)
    if pool is None:
        return
    try:
        return await pool.execute(sql, *args)
    except AttributeError:
        async with pool.acquire() as conn:
            return await conn.execute(sql, *args)


async def persist_proofs(listing_id: int, state: FSMContext):
    from db import db as dbmod
    pool = getattr(dbmod, "db_pool", None)
    if pool is None:
        return

    data = await state.get_data()
    proof_one = data.get("proof_file_id") or data.get("cover_file_id")
    proof_map: dict = dict(data.get("proof_by_card") or {})

    async with pool.acquire() as conn:
        async def has_col(tbl, col):
            row = await conn.fetchrow("""
                                      select exists(select 1
                                                    from information_schema.columns
                                                    where table_schema = 'public'
                                                      and table_name = $1
                                                      and column_name = $2) as has
                                      """, tbl, col)
            return bool(row and row["has"])

        has_ml_proof_one = await has_col("market_listings", "proof_file_id")
        has_cover = await has_col("market_listings", "cover_file_id")
        has_ml_proof_map = await has_col("market_listings", "proof_by_card")
        has_item_proof = await has_col("market_listing_items", "proof_file_id")

        if proof_one:
            if has_ml_proof_one:
                await conn.execute(
                    "update market_listings set proof_file_id=$2 where listing_id=$1",
                    listing_id, proof_one
                )
            elif has_cover:
                await conn.execute(
                    "update market_listings set cover_file_id=$2 where listing_id=$1",
                    listing_id, proof_one
                )

        if proof_map:
            if has_ml_proof_map:
                await conn.execute(
                    "update market_listings set proof_by_card=$2 where listing_id=$1",
                    listing_id, json.dumps(proof_map)
                )
            elif has_item_proof:
                for k, v in proof_map.items():
                    try:
                        cid = int(k)
                    except Exception:
                        continue
                    await conn.execute(
                        "update market_listing_items set proof_file_id=$3 where listing_id=$1 and card_id=$2",
                        listing_id, cid, v
                    )


async def fetch_card(card_id: int) -> dict:
    import importlib
    dbmod = importlib.import_module("db.db")
    pool = getattr(dbmod, "db_pool", None)
    if pool is None:
        return {}

    sql = """
          SELECT card_id, deck_id, hero_name, card_name, rarity, story, image_id
          FROM cards
          WHERE card_id = $1 \
          """
    try:
        row = await pool.fetchrow(sql, card_id)
    except AttributeError:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, card_id)

    return dict(row) if row else {}
