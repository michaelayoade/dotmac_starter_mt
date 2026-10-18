"""Detached pacing decisions and the product-neutral forbidden exception."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PacingFact:
    readable: bool
    submittable: bool
    has_override: bool


class PacingForbidden(PermissionError):
    """Academy maps this denial to HTTP 403, retaining ``str(error)`` as detail."""


__all__ = ["PacingFact", "PacingForbidden"]
