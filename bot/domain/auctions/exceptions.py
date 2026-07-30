from __future__ import annotations


class BidError(Exception):
    """Base class for expected auction bidding failures."""


class AuctionNotFound(BidError):
    pass


class AuctionNotActive(BidError):
    def __init__(self, status: str | None = None):
        self.status = status
        super().__init__(f"auction is not active: {status or 'unknown'}")


class AuctionEnded(BidError):
    pass


class UnsupportedCurrency(BidError):
    def __init__(self, currency: str):
        self.currency = currency
        super().__init__(f"unsupported auction currency: {currency}")


class BidFormatError(BidError):
    def __init__(self, message: str = "Ставка должна быть положительным числом."):
        self.user_message = message
        super().__init__(message)


class BidStepError(BidError):
    def __init__(self, *, amount: int, start_price: int, step: int):
        self.amount = int(amount)
        self.start_price = int(start_price)
        self.step = int(step)
        super().__init__(
            f"bid {self.amount} does not follow step {self.step} from start {self.start_price}"
        )


class BidTooLow(BidError):
    def __init__(self, *, minimum: int, current_max: int | None):
        self.minimum = int(minimum)
        self.current_max = int(current_max) if current_max is not None else None
        super().__init__(f"bid is below minimum {self.minimum}")


class BidTooHigh(BidError):
    def __init__(self, *, maximum: int, current_best: int | None):
        self.maximum = int(maximum)
        self.current_best = int(current_best) if current_best is not None else None
        super().__init__(f"reverse bid is above maximum {self.maximum}")


class BidderBanned(BidError):
    pass


class BidderNotEligible(BidError):
    pass


class AuctionKindNotBiddable(BidError):
    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(f"auction kind does not accept automatic bids: {kind}")


class BidAlreadyRecorded(BidError):
    def __init__(self, message_id: int):
        self.message_id = int(message_id)
        super().__init__(f"bid message {self.message_id} is already recorded")


class BidNotFound(BidError):
    pass


class BidOwnershipError(BidError):
    pass


class BidRevisionWindowExpired(BidError):
    def __init__(self, seconds: int):
        self.seconds = int(seconds)
        super().__init__(f"bid revision window of {seconds}s has expired")


class AutobidTargetNotFound(BidError):
    def __init__(self, username: str):
        self.username = username
        super().__init__(f"autobid target @{username} was not found")


class AutobidLimitTooLow(BidError):
    def __init__(self, *, minimum: int):
        self.minimum = int(minimum)
        super().__init__(f"autobid limit must be at least {self.minimum}")


class AuctionWorkflowError(Exception):
    """Base class for expected creation/moderation/publication failures."""


class AuctionAccessDenied(AuctionWorkflowError):
    def __init__(self, *, required_level: int, actual_level: int):
        self.required_level = int(required_level)
        self.actual_level = int(actual_level)
        super().__init__(
            f"auction requires luxury level {required_level}, got {actual_level}"
        )


class AuctionOwnerPermissionDenied(AuctionWorkflowError):
    pass


class InvalidAuctionTransition(AuctionWorkflowError):
    def __init__(self, *, current: str, target: str):
        self.current = current
        self.target = target
        super().__init__(f"invalid auction transition: {current} -> {target}")


class AuctionSlotConflict(AuctionWorkflowError):
    pass


class ExchangeWorkflowError(Exception):
    """Base class for expected exchange workflow failures."""


class ExchangeBatchNotFound(ExchangeWorkflowError):
    pass


class InvalidExchangeTransition(ExchangeWorkflowError):
    def __init__(self, *, current: str, target: str):
        self.current = current
        self.target = target
        super().__init__(f"invalid exchange transition: {current} -> {target}")
