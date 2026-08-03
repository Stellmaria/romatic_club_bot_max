"""One-time provisioning and maintenance CLI for the Telethon session."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from typing import Any, Callable

from telethon import TelegramClient

from bot.core.environment import load_project_environment, resolve_project_root
from bot.core.logging import configure_logging
from userbot.session import (
    UserbotSessionError,
    prepare_session_storage,
    remove_session_files,
    secure_session_files,
    validate_session_storage,
)

ClientFactory = Callable[[str, int, str], Any]
logger = logging.getLogger("userbot.provision")


@dataclass(frozen=True, slots=True)
class ProvisioningSettings:
    api_id: int
    api_hash: str
    session: str

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        project_root: str | Path | None = None,
    ) -> "ProvisioningSettings":
        environment = os.environ if environ is None else environ
        root = resolve_project_root(project_root)
        raw_api_id = str(environment.get("USERBOT_API_ID") or "").strip()
        api_hash = str(environment.get("USERBOT_API_HASH") or "").strip()
        raw_session = str(environment.get("USERBOT_SESSION") or "var/userbot_session").strip()
        errors: list[str] = []
        try:
            api_id = int(raw_api_id)
            if api_id <= 0:
                raise ValueError
        except ValueError:
            api_id = 0
            errors.append("USERBOT_API_ID must be a positive integer")
        if not api_hash:
            errors.append("USERBOT_API_HASH is required")
        if not raw_session:
            errors.append("USERBOT_SESSION is required")
        if errors:
            raise UserbotSessionError("; ".join(errors))
        session_path = Path(raw_session).expanduser()
        if not session_path.is_absolute():
            session_path = (root / session_path).resolve(strict=False)
        return cls(api_id=api_id, api_hash=api_hash, session=str(session_path))


def create_client(
    settings: ProvisioningSettings,
    *,
    client_factory: ClientFactory = TelegramClient,
) -> TelegramClient:
    return client_factory(settings.session, settings.api_id, settings.api_hash)


async def authorize_session(
    settings: ProvisioningSettings,
    *,
    client_factory: ClientFactory = TelegramClient,
) -> None:
    prepare_session_storage(settings.session)
    client = create_client(settings, client_factory=client_factory)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await client.start(
                phone=lambda: input("Телефон Telegram (+7...): ").strip(),
                code_callback=lambda: input("Код Telegram: ").strip(),
                password=lambda: getpass("Пароль 2FA: ").strip(),
            )
        if not await client.is_user_authorized():
            raise UserbotSessionError("Telegram did not authorize the userbot session")
        current_user = await client.get_me()
        logger.info(
            "Userbot session authorized",
            extra={"telegram_user_id": getattr(current_user, "id", None)},
        )
    finally:
        if client.is_connected():
            await client.disconnect()
        secure_session_files(settings.session)
    validate_session_storage(settings.session)


async def check_session(
    settings: ProvisioningSettings,
    *,
    client_factory: ClientFactory = TelegramClient,
) -> None:
    validate_session_storage(settings.session)
    client = create_client(settings, client_factory=client_factory)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise UserbotSessionError(
                "Userbot session is not authorized; run `auction-userbot-provision authorize`"
            )
        current_user = await client.get_me()
        logger.info(
            "Userbot session is valid",
            extra={"telegram_user_id": getattr(current_user, "id", None)},
        )
    finally:
        if client.is_connected():
            await client.disconnect()


async def revoke_session(
    settings: ProvisioningSettings,
    *,
    client_factory: ClientFactory = TelegramClient,
) -> None:
    try:
        validate_session_storage(settings.session)
    except UserbotSessionError:
        remove_session_files(settings.session)
        logger.info("No active local userbot session remained")
        return

    client = create_client(settings, client_factory=client_factory)
    try:
        await client.connect()
        if await client.is_user_authorized():
            await client.log_out()
    finally:
        if client.is_connected():
            await client.disconnect()
        remove_session_files(settings.session)
    logger.info("Userbot session revoked and local files removed")


async def rotate_session(
    settings: ProvisioningSettings,
    *,
    client_factory: ClientFactory = TelegramClient,
) -> None:
    await revoke_session(settings, client_factory=client_factory)
    await authorize_session(settings, client_factory=client_factory)


async def _run_action(
    action: str,
    settings: ProvisioningSettings,
    *,
    client_factory: ClientFactory = TelegramClient,
) -> None:
    actions = {
        "authorize": authorize_session,
        "check": check_session,
        "rotate": rotate_session,
        "revoke": revoke_session,
    }
    await actions[action](settings, client_factory=client_factory)


def run(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    configure_logging("INFO", structured=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("authorize", "check", "rotate", "revoke"))
    args = parser.parse_args(argv)

    project_root = resolve_project_root()
    load_project_environment(project_root)
    try:
        settings = ProvisioningSettings.from_env(project_root=project_root)
        asyncio.run(_run_action(args.action, settings))
    except KeyboardInterrupt:
        logger.warning("Userbot provisioning interrupted by operator")
        return 130
    except UserbotSessionError as error:
        logger.error("Userbot session error: %s", error)
        return 3
    except Exception:
        logger.exception("Userbot provisioning failed")
        return 1
    return 0


__all__ = ["ProvisioningSettings", "run"]


if __name__ == "__main__":
    raise SystemExit(run())
