"""Router aggregate for marketplace flows.

The historical monolith was split into ordered fragments.  Keep this module
as the stable bootstrap import so the public router registration order does
not change.
"""

from aiogram import Router

from bot.handlers.admin.services import (
    market_create_flow,
    market_edit_flow,
    market_entry_flow,
    market_my_sales_flow,
    market_search_flow,
)


router = Router(name="market_flow")
router.include_routers(
    market_entry_flow.router,
    market_create_flow.create_router,
    market_edit_flow.router,
    market_search_flow.router,
    market_my_sales_flow.my_sales_router,
    market_create_flow.create_continuation_router,
    market_my_sales_flow.my_sales_continuation_router,
)
