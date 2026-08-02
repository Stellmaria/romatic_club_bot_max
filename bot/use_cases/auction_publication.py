from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from bot.use_cases.common import ApplicationInvalidState, ApplicationNotFound

Row = dict[str, Any]
Claim = Callable[[int], Awaitable[Row]]
BuildPayload = Callable[[Row], Awaitable[Any]]
Send = Callable[[Row, Any], Awaitable[int]]
MarkPublished = Callable[[int, int], Awaitable[bool]]
MarkFailed = Callable[[int, str], Awaitable[Any]]
PostCommit = Callable[[Row, int], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class PublishAuctionCommand:
    auction_id: int


@dataclass(frozen=True, slots=True)
class PublishedAuction:
    auction: Row
    message_id: int
    pending_confirmation: bool = False


class PublishAuctionUseCase:
    """Claim, deliver and finalize one auction publication atomically by state.

    Telegram delivery is an injected output port. A failed delivery marks the
    claim as failed; optional post-publication effects run only after the
    published state has been committed. Telegram can return ``message_id=0``
    while a large-chat media post is still being processed. That delivery must
    wait for a later channel-post confirmation instead of writing zero to the
    database or retrying the visible post.
    """

    def __init__(
        self,
        *,
        claim: Claim,
        build_payload: BuildPayload,
        send: Send,
        mark_published: MarkPublished,
        mark_failed: MarkFailed,
        after_published: PostCommit | None = None,
    ) -> None:
        self._claim = claim
        self._build_payload = build_payload
        self._send = send
        self._mark_published = mark_published
        self._mark_failed = mark_failed
        self._after_published = after_published

    async def execute(self, command: PublishAuctionCommand) -> PublishedAuction:
        auction_id = int(command.auction_id)
        try:
            auction = dict(await self._claim(auction_id))
        except Exception as exc:
            if exc.__class__.__name__ == "AuctionNotFound":
                raise ApplicationNotFound("auction not found") from exc
            if exc.__class__.__name__ == "InvalidAuctionTransition":
                current = getattr(exc, "current", None)
                raise ApplicationInvalidState(
                    "auction is not publishable",
                    details={"current": current},
                ) from exc
            raise

        try:
            payload = await self._build_payload(auction)
            message_id = int(await self._send(auction, payload))
        except Exception as exc:
            try:
                await self._mark_failed(auction_id, repr(exc))
            except Exception:
                pass
            raise

        if message_id <= 0:
            return PublishedAuction(
                auction=auction,
                message_id=0,
                pending_confirmation=True,
            )

        # Delivery already happened. Never mark the lot failed here because a
        # retry could duplicate a visible Telegram post. Lost/unknown commit
        # state requires operator review instead.
        try:
            committed = await self._mark_published(auction_id, message_id)
        except Exception as exc:
            raise ApplicationInvalidState(
                "auction was delivered but publication commit is unknown",
                details={"message_id": message_id},
            ) from exc
        if not committed:
            raise ApplicationInvalidState(
                "auction was delivered but publication claim was lost",
                details={"message_id": message_id},
            )

        if self._after_published is not None:
            await self._after_published(auction, message_id)
        return PublishedAuction(auction=auction, message_id=message_id)
