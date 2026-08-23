"""Stable, transport-neutral errors for subscriptions' owning services."""

from __future__ import annotations

from collections.abc import Mapping


class SubscriptionError(ValueError):
    """Base fail-closed domain error with a stable machine code."""

    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = (
            code if code.startswith("subscriptions.") else f"subscriptions.{code}"
        )
        self.message = message
        self.details = dict(details or {})


class SubscriptionDataError(SubscriptionError):
    """Required or authoritative input is absent, malformed, or undeclared."""


class SubscriptionConflictError(SubscriptionError):
    """An identity was reused for different immutable content."""


class SubscriptionStateError(SubscriptionError):
    """A requested lifecycle transition is not permitted."""


class CadenceError(SubscriptionDataError):
    """Fail-closed cadence or calendar configuration error."""


__all__ = [
    "CadenceError",
    "SubscriptionConflictError",
    "SubscriptionDataError",
    "SubscriptionError",
    "SubscriptionStateError",
]
