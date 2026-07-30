import argparse
import asyncio
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import asyncpg
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]  # .../E:\python\main\1
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DATABASE_URL


def _to_dt(series: pd.Series) -> pd.Series:
    # принимает строки, NaN, уже-datetime
    return pd.to_datetime(series, errors="coerce")


BIGINT_MIN = Decimal("-9223372036854775808")
BIGINT_MAX = Decimal("9223372036854775807")


def _to_int(series: pd.Series | None, *, size: int) -> pd.Series:
    """
    Надёжно превращает значения в Int64 (с NA), НЕ через float64.
    Всё, что не влазит в BIGINT, превращаем в NA.
    """
    if series is None:
        return pd.Series([pd.NA] * size, dtype="Int64")

    out: list[object] = []
    for v in series.tolist():
        if v is None or (isinstance(v, float) and pd.isna(v)):
            out.append(pd.NA)
            continue

        s = str(v).strip()
        if not s or s.lower() in {"nan", "none", "null"}:
            out.append(pd.NA)
            continue

        try:
            d = Decimal(s).to_integral_value(rounding=ROUND_HALF_UP)

            # защитимся от 1e20 и прочего “я поставил стоимость вселенной”
            if d < BIGINT_MIN or d > BIGINT_MAX:
                out.append(pd.NA)
                continue

            out.append(int(d))
        except (InvalidOperation, ValueError, OverflowError):
            out.append(pd.NA)

    return pd.Series(out, dtype="Int64")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", help="Path to backfill_posts_*.csv")
    parser.add_argument("--truncate", action="store_true", help="TRUNCATE table before import")
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    df = pd.read_csv(csv_path)

    # Приведение типов
    df["post_date_msk"] = _to_dt(df.get("post_date_msk"))
    df["end_time_msk"] = _to_dt(df.get("end_time_msk"))
    df["deadline_msk"] = _to_dt(df.get("deadline_msk"))

    n = len(df)

    df["root_id"] = _to_int(df.get("root_id"), size=n)
    df["discussion_id"] = _to_int(df.get("discussion_id"), size=n)
    df["max_thread_valid"] = _to_int(df.get("max_thread_valid"), size=n)
    df["winner_id"] = _to_int(df.get("winner_id"), size=n)
    df["max_any_valid"] = _to_int(df.get("max_any_valid"), size=n)

    # и post_id тоже лучше так же:
    df["post_id"] = _to_int(df.get("post_id"), size=n)

    # выкидываем строки без post_id
    df = df[df["post_id"].notna()].copy()

    records = []
    for r in df.itertuples(index=False):
        records.append(
            (
                int(r.post_id),
                str(r.post_link),
                r.post_date_msk.to_pydatetime() if pd.notna(r.post_date_msk) else None,
                r.end_time_msk.to_pydatetime() if pd.notna(r.end_time_msk) else None,
                r.deadline_msk.to_pydatetime() if pd.notna(r.deadline_msk) else None,
                int(r.root_id) if pd.notna(r.root_id) else None,
                int(r.discussion_id) if pd.notna(r.discussion_id) else None,
                int(r.msgs_scanned) if pd.notna(r.msgs_scanned) else 0,
                int(r.numeric_msgs) if pd.notna(r.numeric_msgs) else 0,
                int(r.thread_bids) if pd.notna(r.thread_bids) else 0,
                int(r.thread_valid) if pd.notna(r.thread_valid) else 0,
                int(r.max_thread_valid) if pd.notna(r.max_thread_valid) else None,
                int(r.winner_id) if pd.notna(r.winner_id) else None,
                int(r.any_valid) if pd.notna(r.any_valid) else 0,
                int(r.max_any_valid) if pd.notna(r.max_any_valid) else None,
                str(r.note) if pd.notna(r.note) else None,
            )
        )

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        if args.truncate:
            await conn.execute("TRUNCATE public.auction_posts_backfill CASCADE;")

        # UPSERT (по post_id)
        await conn.executemany(
            """
            INSERT INTO public.auction_posts_backfill(post_id, post_link, post_date_msk, end_time_msk, deadline_msk,
                                                      root_id, discussion_id,
                                                      msgs_scanned, numeric_msgs, thread_bids, thread_valid,
                                                      max_thread_valid, winner_id, any_valid, max_any_valid, note)
            VALUES ($1, $2, $3, $4, $5,
                    $6, $7,
                    $8, $9, $10, $11,
                    $12, $13, $14, $15, $16)
            ON CONFLICT (post_id) DO UPDATE SET post_link=EXCLUDED.post_link,
                                                post_date_msk=EXCLUDED.post_date_msk,
                                                end_time_msk=EXCLUDED.end_time_msk,
                                                deadline_msk=EXCLUDED.deadline_msk,
                                                root_id=EXCLUDED.root_id,
                                                discussion_id=EXCLUDED.discussion_id,
                                                msgs_scanned=EXCLUDED.msgs_scanned,
                                                numeric_msgs=EXCLUDED.numeric_msgs,
                                                thread_bids=EXCLUDED.thread_bids,
                                                thread_valid=EXCLUDED.thread_valid,
                                                max_thread_valid=EXCLUDED.max_thread_valid,
                                                winner_id=EXCLUDED.winner_id,
                                                any_valid=EXCLUDED.any_valid,
                                                max_any_valid=EXCLUDED.max_any_valid,
                                                note=EXCLUDED.note
            ;
            """,
            records,
        )

        print(f"Imported {len(records)} rows into auction_posts_backfill")
    finally:
        await conn.close()
# E:\python\main\1\.venv\Scripts\python.exe -u E:\python\main\1\bot\tools\import_post_scans.py E:\python\main\1\backfill_posts_20260217_191006.csv --truncate
 # (.venv) PS E:\python\main\1> E:\python\main\1\.venv\Scripts\python.exe -u E:\python\main\1\backfill.py
if __name__ == "__main__":
    asyncio.run(main())
