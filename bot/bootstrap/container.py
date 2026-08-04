"""The only composition root for concrete bot application dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.application_ports import Clock, FileStoragePort, LocalFileStorage
from bot.core.time import SystemClock
from bot.repositories.auction_workflows import AuctionWorkflowRepository
from bot.repositories.exchanges import ExchangeRepository
from bot.repositories.privacy_exports import PrivacyExportRepository
from bot.services.auction_workflows import (
    AuctionCreationService,
    AuctionLifecycleService,
    AuctionModerationService,
    AuctionOwnerService,
    AuctionPublicationService,
)
from bot.services.exchanges import ExchangeService
from bot.services.privacy_exports import PrivacyExportService


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Process-scoped dependency graph.

    It owns no lifecycle resources. The process composition root starts the
    database and Telegram clients, then passes already-open adapters here.
    """

    auction_repository: AuctionWorkflowRepository
    exchange_repository: ExchangeRepository
    privacy_export_repository: PrivacyExportRepository
    auction_creation: AuctionCreationService
    auction_moderation: AuctionModerationService
    auction_owner: AuctionOwnerService
    auction_lifecycle: AuctionLifecycleService
    auction_publication: AuctionPublicationService
    exchange: ExchangeService
    privacy_export: PrivacyExportService
    clock: Clock
    file_storage: FileStoragePort

    @classmethod
    def build(
        cls,
        *,
        pool: Any,
        storage_root: Path,
        clock: Clock | None = None,
        file_storage: FileStoragePort | None = None,
    ) -> ApplicationContainer:
        """Build concrete adapters once from explicit lifecycle resources."""

        auction_repository = AuctionWorkflowRepository(pool)
        exchange_repository = ExchangeRepository(pool)
        privacy_export_repository = PrivacyExportRepository(pool)
        return cls(
            auction_repository=auction_repository,
            exchange_repository=exchange_repository,
            privacy_export_repository=privacy_export_repository,
            auction_creation=AuctionCreationService(auction_repository),
            auction_moderation=AuctionModerationService(auction_repository),
            auction_owner=AuctionOwnerService(auction_repository),
            auction_lifecycle=AuctionLifecycleService(auction_repository),
            auction_publication=AuctionPublicationService(auction_repository),
            exchange=ExchangeService(exchange_repository),
            privacy_export=PrivacyExportService(privacy_export_repository),
            clock=clock or SystemClock(),
            file_storage=file_storage or LocalFileStorage(storage_root),
        )


__all__ = ["ApplicationContainer", "SystemClock"]
