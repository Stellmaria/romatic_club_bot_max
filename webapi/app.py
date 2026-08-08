"""aiohttp application for the Telegram Mini App."""

from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiohttp import web

from bot.core.time import business_today
from db.cards import get_card_by_id
from db.lifecycle import close_db, init_db
from db.pool import DatabaseRuntime
from db.profile_sync import sync_user_profile
from db.users import get_user
from webapi.auction_home import build_auction_home, list_free_slots, parse_selected_date
from webapi.luxury import LuxuryLevelCache
from webapi.settings import WebAppSettings
from webapi.telegram_auth import (
    TelegramAuthError,
    ValidatedInitData,
    validate_init_data,
)

logger = logging.getLogger("auction_bot.webapp")

SETTINGS_KEY = web.AppKey("webapp_settings", WebAppSettings)
DB_RUNTIME_KEY = web.AppKey("webapp_db_runtime", DatabaseRuntime)
BOT_KEY = web.AppKey("webapp_bot", Bot)
LUXURY_CACHE_KEY = web.AppKey("webapp_luxury_cache", LuxuryLevelCache)
NO_STORE_HEADERS = {"Cache-Control": "no-store"}


def create_app(
    settings: WebAppSettings,
    *,
    webapp_dir: str | Path | None = None,
) -> web.Application:
    app = web.Application(client_max_size=256 * 1024)
    app[SETTINGS_KEY] = settings
    app[LUXURY_CACHE_KEY] = LuxuryLevelCache()

    root = (
        Path(webapp_dir).resolve()
        if webapp_dir is not None
        else Path(__file__).resolve().parents[1] / "webapp"
    )
    static_dir = root / "static"
    index_file = root / "index.html"

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/api/webapp/me", me)
    app.router.add_get("/api/webapp/auction-home", auction_home)
    app.router.add_get("/api/webapp/free-slots", free_slots)
    app.router.add_get(r"/api/webapp/cards/{card_id:\d+}/image", card_image)
    app.router.add_get("/", _index_handler(index_file))
    app.router.add_static("/static/", static_dir, append_version=True)

    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    return app


async def _startup(app: web.Application) -> None:
    app[DB_RUNTIME_KEY] = await init_db(app[SETTINGS_KEY].database)
    app[BOT_KEY] = Bot(token=app[SETTINGS_KEY].bot_token)
    logger.info("Mini App API started")


async def _cleanup(app: web.Application) -> None:
    bot = app.get(BOT_KEY)
    if bot is not None:
        await bot.session.close()

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
    identity = _authenticate_or_response(request)
    if isinstance(identity, web.Response):
        return identity

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


async def auction_home(request: web.Request) -> web.Response:
    identity = _authenticate_or_response(request)
    if isinstance(identity, web.Response):
        return identity

    settings = request.app[SETTINGS_KEY]
    today = business_today()
    try:
        selected_date = parse_selected_date(request.query.get("date"), today=today)
    except ValueError:
        return _api_error("invalid_date", 400)

    luxury_level = await request.app[LUXURY_CACHE_KEY].get(
        request.app[BOT_KEY],
        settings,
        identity.user.id,
    )
    if selected_date != today and luxury_level == 0:
        return _api_error("luxury_required", 403)

    snapshot = await build_auction_home(
        selected_date,
        channel_username=settings.auction_channel_username,
    )
    snapshot["is_today"] = selected_date == today
    snapshot["viewer"] = _viewer_payload(luxury_level, settings)
    return web.json_response(snapshot, headers=NO_STORE_HEADERS)


async def free_slots(request: web.Request) -> web.Response:
    identity = _authenticate_or_response(request)
    if isinstance(identity, web.Response):
        return identity

    settings = request.app[SETTINGS_KEY]
    luxury_level = await request.app[LUXURY_CACHE_KEY].get(
        request.app[BOT_KEY],
        settings,
        identity.user.id,
    )
    if luxury_level == 0:
        return _api_error("luxury_required", 403)

    today = business_today()
    try:
        selected_date = parse_selected_date(request.query.get("date"), today=today)
    except ValueError:
        return _api_error("invalid_date", 400)

    slots = await list_free_slots(selected_date)
    return web.json_response(
        {"date": selected_date.isoformat(), "slots": slots},
        headers=NO_STORE_HEADERS,
    )


async def card_image(request: web.Request) -> web.Response:
    identity = _authenticate_or_response(request)
    if isinstance(identity, web.Response):
        return identity

    card_id = int(request.match_info["card_id"])
    card = await get_card_by_id(card_id)
    if not card:
        return _api_error("card_not_found", 404)

    file_id = _card_file_id(card)
    if file_id is None:
        return _api_error("card_image_not_found", 404)

    try:
        downloaded = await request.app[BOT_KEY].download(file_id)
    except TelegramAPIError:
        logger.warning("Unable to download Telegram media for card %s", card_id)
        return _api_error("card_image_unavailable", 502)

    if downloaded is None:
        return _api_error("card_image_unavailable", 502)

    return web.Response(
        body=downloaded.read(),
        content_type="image/jpeg",
        headers=NO_STORE_HEADERS,
    )


def _viewer_payload(
    luxury_level: int,
    settings: WebAppSettings,
) -> dict[str, object]:
    return {
        "luxury_level": luxury_level,
        "luxury_label": f"Luxury {luxury_level}" if luxury_level else None,
        "can_use_calendar": luxury_level > 0,
        "can_use_free_slots": luxury_level > 0,
        "luxury_contact_url": settings.luxury_contact_url,
    }


def _card_file_id(card: dict) -> str | None:
    for key in ("image_id", "thumb_file_id"):
        value = str(card.get(key) or "").strip()
        if value:
            return value

    media_type = str(card.get("media_type") or "").strip().casefold()
    if media_type in {"", "photo", "image"}:
        value = str(card.get("media_file_id") or "").strip()
        if value:
            return value
    return None


def _authenticate_or_response(
    request: web.Request,
) -> ValidatedInitData | web.Response:
    try:
        return _authenticate(request)
    except TelegramAuthError:
        return _api_error("telegram_auth_failed", 401)


def _api_error(code: str, status: int) -> web.Response:
    return web.json_response(
        {"error": code},
        status=status,
        headers=NO_STORE_HEADERS,
    )


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
