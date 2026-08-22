"""Service-delivery order commands, vocabularies and outcomes."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class ServiceOrderError(Exception):
    """Base refusal."""


class Conflict(ServiceOrderError):
    """The order, decision or evidence state is inadmissible."""


class ServiceOrderType(enum.StrEnum):
    NEW_INSTALL = "NEW_INSTALL"
    UPGRADE = "UPGRADE"
    DOWNGRADE = "DOWNGRADE"
    DISCONNECT = "DISCONNECT"
    RECONNECT = "RECONNECT"
    CHANGE_SERVICE = "CHANGE_SERVICE"


class ServiceOrderStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    IN_DELIVERY = "IN_DELIVERY"
    ACTIVATED = "ACTIVATED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class ReadinessCheckKind(enum.StrEnum):
    """The named families of fact a readiness decision is made FROM.

    Deliberately provider-neutral: the module never reads a project, a work
    order or an address assignment. The caller normalizes whatever its own
    owners report into these kinds, and the module decides from them.
    """

    DELIVERY_RUN = "DELIVERY_RUN"
    DELIVERY_PLAN_BINDING = "DELIVERY_PLAN_BINDING"
    ACTIVATION_TASK = "ACTIVATION_TASK"
    FIELD_WORK = "FIELD_WORK"
    ACCESS_ASSIGNMENT = "ACCESS_ASSIGNMENT"


class ReadinessCheckResult(enum.StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReadinessDecisionStatus(enum.StrEnum):
    BLOCKED = "BLOCKED"
    ACTIVATION_REQUESTED = "ACTIVATION_REQUESTED"
    ACTIVATED = "ACTIVATED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class OpenServiceOrder:
    customer_reference: str
    order_type: ServiceOrderType
    request_key: str
    commercial_order_reference: str | None = None
    specification_reference: str | None = None
    service_reference: str | None = None


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    kind: ReadinessCheckKind
    result: ReadinessCheckResult
    reason_code: str
    source_type: str
    source_reference: str | None = None


@dataclass(frozen=True, slots=True)
class DecideReadiness:
    service_order_id: UUID
    command_id: UUID
    correlation_id: UUID
    actor: str
    checks: tuple[ReadinessCheck, ...]
    decided_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ConfirmActivation:
    service_order_id: UUID
    command_id: UUID
    correlation_id: UUID
    actor: str
    decided_at: datetime | None = None


__all__ = [
    "Conflict",
    "ConfirmActivation",
    "DecideReadiness",
    "OpenServiceOrder",
    "ReadinessCheck",
    "ReadinessCheckKind",
    "ReadinessCheckResult",
    "ReadinessDecisionStatus",
    "ServiceOrderError",
    "ServiceOrderStatus",
    "ServiceOrderType",
]
