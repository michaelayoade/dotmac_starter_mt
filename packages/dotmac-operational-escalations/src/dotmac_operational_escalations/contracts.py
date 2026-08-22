"""Operational escalation commands, vocabularies and outcomes."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class EscalationError(Exception):
    """Base refusal."""


class Conflict(EscalationError):
    """The policy, version or instance state is inadmissible."""


class EscalationStatus(enum.StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"


class PolicyVersionState(enum.StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


@dataclass(frozen=True, slots=True)
class RegisterPolicy:
    code: str
    name: str
    subject_type: str
    trigger: str


@dataclass(frozen=True, slots=True)
class DraftPolicyVersion:
    """The tunables of one immutable escalation level.

    `channels` names the delivery channels an escalation ASKS for. This module
    performs no delivery and stores no delivery outcome — Messaging/Integrator
    owns that, and Durable Timers owns the scheduling the intervals imply.
    """

    policy_id: UUID
    level: int
    channels: tuple[str, ...]
    minimum_severity: str | None = None
    unowned_after_seconds: int | None = None
    unresolved_after_seconds: int | None = None
    cooldown_seconds: int = 0


@dataclass(frozen=True, slots=True)
class RaiseEscalation:
    policy_id: UUID
    subject_reference: str
    dedup_key: str
    severity: str | None = None
    raised_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SettleEscalation:
    escalation_id: UUID
    actor_reference: str
    reason: str | None = None
    at: datetime | None = None


__all__ = [
    "Conflict",
    "DraftPolicyVersion",
    "EscalationError",
    "EscalationStatus",
    "PolicyVersionState",
    "RaiseEscalation",
    "RegisterPolicy",
    "SettleEscalation",
]
