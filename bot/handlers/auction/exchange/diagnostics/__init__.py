from __future__ import annotations

from aiogram import Router

from .media import router as media_router
from .delivery import router as delivery_router
from .reports import router as reports_router
from .reconciliation import router as reconciliation_router

router = Router(name="auction_exchange_diagnostics")
router.include_router(media_router)
router.include_router(delivery_router)
router.include_router(reports_router)
router.include_router(reconciliation_router)

__all__ = ["router"]
