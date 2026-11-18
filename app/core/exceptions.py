from typing import Any


class DomainError(Exception):
    code: str = "INTERNAL_ERROR"
    status: int = 500
    message: str = "internal error"
    details: dict[str, Any] | None = None

    def __init__(self, message: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message
        if details is not None:
            self.details = details


class ValidationError(DomainError):
    code = "VALIDATION_ERROR"
    status = 422


class UnsupportedDirectionError(DomainError):
    code = "UNSUPPORTED_DIRECTION"
    status = 400


class AmountBelowMinError(DomainError):
    code = "AMOUNT_BELOW_MIN"
    status = 400


class AmountAboveMaxError(DomainError):
    code = "AMOUNT_ABOVE_MAX"
    status = 400


class RateUnavailableError(DomainError):
    code = "RATE_UNAVAILABLE"
    status = 422


class QuoteNotFoundError(DomainError):
    code = "QUOTE_NOT_FOUND"
    status = 404


class QuoteExpiredError(DomainError):
    code = "QUOTE_EXPIRED"
    status = 410


class InsufficientFundsError(DomainError):
    code = "INSUFFICIENT_FUNDS"
    status = 409


class DailyLimitExceededError(DomainError):
    code = "DAILY_LIMIT_EXCEEDED"
    status = 409


class IdempotencyConflictError(DomainError):
    code = "IDEMPOTENCY_CONFLICT"
    status = 422


class ProviderUnavailableError(DomainError):
    code = "PROVIDER_UNAVAILABLE"
    status = 502


class TransferNotFoundError(DomainError):
    code = "TRANSFER_NOT_FOUND"
    status = 404
