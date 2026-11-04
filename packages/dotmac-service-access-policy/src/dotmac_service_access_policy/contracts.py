"""Service-access policy inputs and decisions."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime


class AccessSignal(enum.StrEnum):
    FUP_EXHAUSTED = "FUP_EXHAUSTED"
    PREPAID_DEPLETED = "PREPAID_DEPLETED"
    COLLECTIONS_HOLD = "COLLECTIONS_HOLD"
    ADMIN_HOLD = "ADMIN_HOLD"


class DesiredAccess(enum.StrEnum):
    ALLOW = "ALLOW"
    RESTRICT = "RESTRICT"
    DENY = "DENY"


@dataclass(frozen=True, slots=True)
class RecordAccessInput:
    service_reference: str
    signal: AccessSignal
    active: bool
    source_reference: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class RecordAccessInputV2:
    """Versioned access observation (V1 fields plus ordering and policy stamps).

    ``source_version`` is a non-negative integer assigned by the source of the
    observation, monotonically increasing per (service_reference, signal).
    ``policy_version`` names the policy revision the source applied; it is
    recorded and compared for payload identity but does not affect ordering.
    ``observed_at`` must be timezone-aware.
    """

    service_reference: str
    signal: AccessSignal
    active: bool
    source_reference: str
    observed_at: datetime
    source_version: int
    policy_version: str


class AccessInputDecision(enum.StrEnum):
    """Outcome of the pure ordering rule (see ``versioning``)."""

    APPLY = "APPLY"
    IDEMPOTENT = "IDEMPOTENT"
    REFUSE_STALE = "REFUSE_STALE"
    REFUSE_CONFLICT = "REFUSE_CONFLICT"
    REFUSE_UNVERSIONED = "REFUSE_UNVERSIONED"


@dataclass(frozen=True, slots=True)
class ResolveDesiredAccess:
    service_reference: str
    decided_at: datetime


__all__ = [
    "AccessInputDecision",
    "AccessSignal",
    "DesiredAccess",
    "RecordAccessInput",
    "RecordAccessInputV2",
    "ResolveDesiredAccess",
]
