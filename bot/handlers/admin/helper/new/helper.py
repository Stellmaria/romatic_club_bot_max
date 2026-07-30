import asyncio
import re
from contextlib import suppress
from typing import List

from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest, TelegramAPIError

from db.legacy import logger, disable_all_notifications, clear_all_card_subscriptions, mark_user_unreachable

_UNREACHABLE_CACHE: set[int] = set()
BR_RE = re.compile(r"(?i)<br\s*/?>")
SAFE_SPLIT = 3500


def normalize_chat_id(raw: str | int | None) -> int | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("", "none", "null", "off", "false", "0"):
        return None
    if s.startswith("-100"):
        return int(s)
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return int(s)
    return None


def _looks_unreachable(err: Exception) -> bool:
    s = str(err).lower()
    return any(x in s for x in (
        "bot was blocked by the user",
        "user is deactivated",
        "chat not found",
        "forbidden",
    ))


async def _handle_unreachable(uid: int, reason: str) -> None:
    if uid in _UNREACHABLE_CACHE:
        return
    _UNREACHABLE_CACHE.add(uid)
    with suppress(Exception):
        await disable_all_notifications(uid)
    with suppress(Exception):
        await clear_all_card_subscriptions(uid)
    with suppress(Exception):
        await mark_user_unreachable(uid, reason)
    logger.info(f"[notify] user {uid} marked unreachable: {reason}")


#
def split_message(text: str, max_length: int = SAFE_SPLIT) -> List[str]:
    return [text[i: i + max_length] for i in range(0, len(text), max_length)]


def tg_clean(text: str) -> str:
    return BR_RE.sub("\n", text or "")


async def notify_users(bot, user_ids, text: str, silent: bool = False) -> None:
    cleaned = tg_clean(text)
    ids: list[int] = []
    for u in user_ids or []:
        try:
            ids.append(int(u))
        except (TypeError, ValueError):
            continue

    for uid in ids:
        for chunk in split_message(cleaned):
            try:
                await bot.send_message(
                    uid,
                    chunk,
                    parse_mode="HTML",
                    disable_notification=silent,
                    disable_web_page_preview=True,
                )

            except TelegramRetryAfter as e:
                await asyncio.sleep(int(getattr(e, "retry_after", 1)))
                with suppress(Exception):
                    await bot.send_message(
                        uid,
                        chunk,
                        parse_mode="HTML",
                        disable_notification=silent,
                        disable_web_page_preview=True,
                    )

            except TelegramForbiddenError as e:
                await _handle_unreachable(uid, f"forbidden: {e}")
                break

            except TelegramBadRequest as e:
                if _looks_unreachable(e):
                    await _handle_unreachable(uid, f"badrequest: {e}")
                    break
                logger.warning(f"[notify] bad request to {uid}: {e}")
                break

            except TelegramAPIError as e:
                logger.warning(f"[notify] api error to {uid}: {e}")
                break

            except Exception as e:
                logger.error(f"[notify] unknown error to {uid}: {e}")
                break
