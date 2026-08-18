"""Stable domain errors published by ``dotmac-orders``."""

from __future__ import annotations

from collections.abc import Mapping


class OrderError(ValueError):
    """A refused Orders command with a stable machine code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class OrderConflict(OrderError):
    """The command conflicts with an already accepted fact."""


class OrderNotFound(OrderError):
    """No order in the required tenant scope has the supplied identity."""


__all__ = [
    "OrderConflict",
    "OrderError",
    "OrderNotFound",
]
