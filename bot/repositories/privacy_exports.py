from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg


@dataclass(frozen=True, slots=True)
class ExportSource:
    dataset_id: str
    table: str
    subject_column: str
    allowed_columns: tuple[str, ...]


EXPORT_SOURCES = (
    ExportSource(
        "identity_profiles",
        "users",
        "user_id",
        (
            "user_id",
            "username",
            "full_name",
            "is_subscribed",
            "is_luxury",
            "warnings_count",
            "created_at",
            "is_trusted",
            "pm_opened",
            "first_pm_at",
            "last_pm_at",
            "uid_verif_confirmed_count",
            "uid_verif_rejected_count",
            "uid_verif_last_confirmed_at",
            "uid_verif_last_rejected_at",
        ),
    ),
    ExportSource(
        "preferences_and_delivery",
        "settings",
        "user_id",
        (
            "user_id",
            "notify_auction_start",
            "notify_bid_reminder",
            "notify_auction_end",
            "notify_daily_today",
        ),
    ),
    ExportSource(
        "preferences_and_delivery",
        "notifications",
        "user_id",
        ("notification_id", "user_id", "auction_id", "notification_type", "sent_at"),
    ),
    ExportSource(
        "preferences_and_delivery",
        "user_subscriptions",
        "user_id",
        ("id", "user_id", "card_id", "created_at", "last_confirmed_at"),
    ),
    ExportSource(
        "preferences_and_delivery",
        "user_preset_subscriptions",
        "user_id",
        ("id", "user_id", "preset_id", "created_at"),
    ),
    ExportSource(
        "preferences_and_delivery",
        "card_day_notifications",
        "user_id",
        ("id", "user_id", "card_id", "day", "sent_at"),
    ),
    ExportSource(
        "preferences_and_delivery",
        "unreachable_users",
        "user_id",
        ("user_id", "reason", "last_seen"),
    ),
    ExportSource(
        "appeals_bans_and_warnings",
        "user_appeals",
        "user_id",
        (
            "id",
            "user_id",
            "username",
            "topic",
            "description",
            "status",
            "created_at",
            "updated_at",
        ),
    ),
    ExportSource(
        "appeals_bans_and_warnings",
        "user_bans",
        "user_id",
        ("id", "user_id", "banned_until", "reason", "issued_at"),
    ),
    ExportSource(
        "appeals_bans_and_warnings",
        "user_warnings",
        "user_id",
        ("id", "user_id", "reason", "issued_at"),
    ),
    ExportSource(
        "appeals_bans_and_warnings",
        "delete_requests",
        "user_id",
        ("id", "lot_id", "user_id", "reason", "created_at", "status"),
    ),
    ExportSource(
        "market_and_media",
        "market_listings",
        "seller_id",
        (
            "listing_id",
            "seller_id",
            "status",
            "description",
            "currency_type",
            "cash_code",
            "price_num",
            "created_at",
            "updated_at",
            "offer_kind",
            "deck_id",
        ),
    ),
    ExportSource(
        "auction_and_bid_history",
        "auction_owners",
        "user_id",
        ("id", "auction_id", "user_id", "folder", "owner_folder"),
    ),
    ExportSource(
        "auction_and_bid_history",
        "bids",
        "bidder_id",
        (
            "bid_id",
            "auction_id",
            "bidder_id",
            "amount",
            "placed_at",
            "created_at",
        ),
    ),
    ExportSource(
        "exchange_history",
        "exchange_batches",
        "user_id",
        (
            "batch_id",
            "user_id",
            "deck_id",
            "mode",
            "currency",
            "price",
            "comment",
            "status",
            "created_at",
            "updated_at",
            "moderated_at",
            "deleted_at",
        ),
    ),
    ExportSource(
        "audit_history",
        "audit_logs",
        "user_id",
        ("id", "action_type", "auction_id", "created_at"),
    ),
)


class PrivacyExportRepository:
    """Read explicitly allowlisted subject data and append privacy audit evidence."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @staticmethod
    def _quote_identifier(value: str) -> str:
        if not value.replace("_", "").isalnum():
            raise ValueError(f"unsafe SQL identifier: {value!r}")
        return f'"{value}"'

    async def _available_columns(
        self,
        connection: asyncpg.Connection,
        source: ExportSource,
    ) -> tuple[str, ...]:
        rows = await connection.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = $1
            """,
            source.table,
        )
        available = {str(row["column_name"]) for row in rows}
        if source.subject_column not in available:
            return ()
        return tuple(column for column in source.allowed_columns if column in available)

    async def collect(
        self,
        connection: asyncpg.Connection,
        subject_user_id: int,
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        datasets: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for source in EXPORT_SOURCES:
            columns = await self._available_columns(connection, source)
            if not columns:
                continue
            selected = ", ".join(self._quote_identifier(column) for column in columns)
            table = self._quote_identifier(source.table)
            subject_column = self._quote_identifier(source.subject_column)
            rows = await connection.fetch(
                f"SELECT {selected} FROM public.{table} WHERE {subject_column} = $1",
                int(subject_user_id),
            )
            datasets.setdefault(source.dataset_id, {})[source.table] = [dict(row) for row in rows]
        return datasets

    async def append_audit(
        self,
        connection: asyncpg.Connection,
        *,
        action_type: str,
        details: str,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO public.audit_logs (user_id, action_type, auction_id, details)
            VALUES (NULL, $1, NULL, $2)
            """,
            action_type,
            details,
        )

    def acquire(self) -> Any:
        return self._pool.acquire()

    async def fetch_audit_by_correlation_id(self, correlation_id: UUID) -> dict[str, Any] | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT id, user_id, action_type, auction_id, details, created_at
                FROM public.audit_logs
                WHERE action_type = 'privacy.export.generated'
                  AND details::jsonb ->> 'correlation_id' = $1
                ORDER BY id DESC
                LIMIT 1
                """,
                str(correlation_id),
            )
        return dict(row) if row else None


__all__ = ["EXPORT_SOURCES", "ExportSource", "PrivacyExportRepository"]
