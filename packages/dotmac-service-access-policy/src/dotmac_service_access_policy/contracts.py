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
class ResolveDesiredAccess:
    service_reference: str
    decided_at: datetime


__all__ = [
    "AccessSignal",
    "DesiredAccess",
    "RecordAccessInput",
    "ResolveDesiredAccess",
]
