"""Argument validation shared by every inbox-operations command module.

These helpers were `service.py` privates until presence, transfer and
escalation commands needed the identical rules. They live here so there is one
definition of what a tenant scope, a required reference, an aware instant and
an eligible-agent cohort mean — a second copy would drift and let one command
accept what its sibling refuses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from dotmac_kernel.cache import TenantScope

from dotmac_inbox_operations.contracts import Conflict


def tenant_of(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-inbox-operations requires TenantScope")
    return scope.tenant_id


def required_text(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def utc_instant(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive round trip for portable unit canaries."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def eligible_references(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted({required_text(v, "agent reference") for v in values}))
    if not normalized:
        raise Conflict("queue eligibility must name at least one agent")
    return normalized


__all__ = [
    "aware",
    "eligible_references",
    "required_text",
    "tenant_of",
    "utc_instant",
]
