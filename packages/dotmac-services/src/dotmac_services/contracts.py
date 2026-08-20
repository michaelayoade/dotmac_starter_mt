"""Service-lifecycle commands and states."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class ServicesError(Exception):
    """Base service-lifecycle refusal."""


class Conflict(ServicesError):
    """The requested lifecycle mutation is inadmissible."""


class ServiceStatus(enum.StrEnum):
    ORDERED = "ORDERED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"


@dataclass(frozen=True, slots=True)
class CreateService:
    customer_reference: str
    specification_reference: str
    qualification_reference: str | None = None


@dataclass(frozen=True, slots=True)
class TransitionService:
    service_id: UUID
    to_status: ServiceStatus
    reason: str
    occurred_at: datetime


__all__ = [
    "Conflict",
    "CreateService",
    "ServicesError",
    "ServiceStatus",
    "TransitionService",
]
