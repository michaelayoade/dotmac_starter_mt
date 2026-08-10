"""Domain exceptions raised by the service layer.

Routes let these bubble to app-level handlers (registered in app/main.py) for
consistent HTTP translation. Same hierarchy as dotmac_starter for portability.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for service-layer errors."""


class NotFoundError(DomainError):
    """Requested entity does not exist (or is hidden by RLS — same outcome)."""


class BadRequestError(DomainError):
    """Caller input invalid."""


class ConflictError(DomainError):
    """Operation conflicts with existing state (unique violation, etc.)."""


class UnauthorizedError(DomainError):
    """Caller's credentials are missing or invalid."""


class ForbiddenError(DomainError):
    """Caller is authenticated (or identified) but the action is not allowed
    by policy — e.g. self-registration while `auth.registration_policy` is
    `closed`. Maps to 403 in `dotmac_kernel.errors`."""


__all__ = [
    "BadRequestError",
    "ConflictError",
    "DomainError",
    "ForbiddenError",
    "NotFoundError",
    "UnauthorizedError",
]
