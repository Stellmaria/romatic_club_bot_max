"""aiohttp application for the Telegram Mini App."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from aiohttp import web

from bot.core.time import serialize_timestamp
from db.auctions import list_auctions
from db.lifecycle import close_db, init_db
from db.pool import DatabaseRuntime
from db.profile_sync import sync_user_profile
from db.users import get_user
from webapi.settings import WebAppSettings
from webapi.telegram_auth import TelegramAuthError, ValidatedInitData, validate_init_data

logger = logging.getLogger("auction_bot.webapp")

SETTINGS_KEY = web.AppKey("webapp_settings", WebAppSettings)
DB_RUNTIME_KEY = web.AppKey("webapp_db_runtime", DatabaseRuntime)
PUBLIC_AUCTION_STATUSES = ["scheduled", "publishing", "active"]
NO_STORE_HEADERS = {"Cache-Control": "no-store"}


def create_app(
    settings: WebAppSettings,
    *,
    webapp_dir: str | Path | None = None,
) -> web.Application:
    app = web.Application(client_max_size=256 * 1024)
    app[SETTINGS_KEY] = settings

    root = (
        Path(webapp_dir).resolve()
        if webapp_dir is not None
        else Path(__file__).resolve().parents[1] / "webapp"
    )
    static_dir = root / "static"
    index_file = root / "index.html"

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/api/webapp/me", me)
    app.router.add_get("/api/webapp/auctions", auctions)
    app.router.add_get("/", _index_handler(index_file))
    app.router.add_static("/static/", static_dir, append_version=True)

    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    return app


async def _startup(app: web.Application) -> None:
    app[DB_RUNTIME_KEY] = await init_db(app[SETTINGS_KEY].database)
    logger.info("Mini App API started")


async def _cleanup(app: web.Application) -> None:
    runtime = app.get(DB_RUNTIME_KEY)
    if runtime is not None:
        await close_db(runtime)
    logger.info("Mini App API stopped")


def _index_handler(index_file: Path):
    async def index(_: web.Request) -> web.FileResponse:
        return web.FileResponse(index_file)

    return index


async def healthz(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def me(request: web.Request) -> web.Response:
    try:
        identity = _authenticate(request)
    except TelegramAuthError:
        return web.json_response(
            {"error": "telegram_auth_failed"},
            status=401,
            headers=NO_STORE_HEADERS,
        )

    tg_user = identity.user
    await sync_user_profile(tg_user.id, tg_user.username, tg_user.full_name)
    stored_user = await get_user(tg_user.id)
    if stored_user is None:
        logger.error("Telegram user %s disappeared after profile sync", tg_user.id)
        raise web.HTTPInternalServerError(text="user profile unavailable")

    return web.json_response(
        {
            "user": {
                "id": int(stored_user["user_id"]),
                "username": stored_user.get("username") or "",
                "full_name": stored_user.get("full_name") or tg_user.full_name,
                "first_name": tg_user.first_name,
                "last_name": tg_user.last_name,
                "language_code": tg_user.language_code,
                "is_premium": tg_user.is_premium,
                "photo_url": tg_user.photo_url,
            },
            "telegram": {
                "auth_date": identity.auth_date,
                "start_param": identity.start_param,
            },
        },
        headers=NO_STORE_HEADERS,
    )


async def auctions(request: web.Request) -> web.Response:
    try:
        _authenticate(request)
    except TelegramAuthError:
        return web.json_response(
            {"error": "telegram_auth_failed"},
            status=401,
            headers=NO_STORE_HEADERS,
        )

    rows = await list_auctions(PUBLIC_AUCTION_STATUSES)
    return web.json_response(
        {"auctions": [_serialize_auction(row) for row in rows]},
        headers=NO_STORE_HEADERS,
    )


def _serialize_auction(row: Mapping[str, Any]) -> dict[str, object]:
    start_time = row.get("start_time")
    end_time = row.get("end_time")
    card_id = row.get("card_id")
    return {
        "id": int(row["auction_id"]),
        "card_id": int(card_id) if card_id is not None else None,
        "card_name": str(row.get("card_name") or ""),
        "hero_name": str(row.get("hero_name") or ""),
        "start_price": int(row.get("start_price") or 0),
        "currency": str(row.get("currency") or ""),
        "status": str(row.get("status") or ""),
        "start_time": serialize_timestamp(start_time) if start_time is not None else None,
        "end_time": serialize_timestamp(end_time) if end_time is not None else None,
    }


def _authenticate(request: web.Request) -> ValidatedInitData:
    header = request.headers.get("Authorization", "")
    scheme, separator, payload = header.partition(" ")
    if not separator or scheme.casefold() != "tma" or not payload.strip():
        raise TelegramAuthError("Telegram authorization header is missing")

    settings = request.app[SETTINGS_KEY]
    return validate_init_data(
        payload.strip(),
        settings.bot_token,
        max_age_seconds=settings.auth_max_age_seconds,
    )


__all__ = ["create_app"]
