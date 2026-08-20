"""Immutable provider-neutral access projection and session contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class AccessState(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    SUSPENDED = "suspended"


class AuthenticationOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ERROR = "error"


class SessionState(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class RegisterNasAttachment:
    nas_ref: str
    access_server_ref: str
    capability_code: str
    source_ref: str


@dataclass(frozen=True, slots=True)
class ProjectAccessPolicy:
    subject_ref: str
    desired_state: AccessState
    policy_code: str
    policy_version: str
    attributes: tuple[tuple[str, str], ...]
    decision_ref: str
    valid_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class RecordAuthentication:
    subject_ref: str
    nas_ref: str
    session_ref: str | None
    outcome: AuthenticationOutcome
    reason_code: str | None
    source_ref: str
    observed_at: datetime
    fingerprint: str


@dataclass(frozen=True, slots=True)
class RecordAccounting:
    subject_ref: str
    nas_ref: str
    session_ref: str
    event_kind: str
    input_octets: int
    output_octets: int
    session_seconds: int
    source_ref: str
    observed_at: datetime
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ReconcileAccess:
    subject_ref: str
    observed_state: AccessState
    observed_fingerprint: str
    source_ref: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class CloseSession:
    session_id: UUID
    expected: SessionState
    reason_code: str
    source_ref: str
    closed_at: datetime


@dataclass(frozen=True, slots=True)
class AccessStateQuery:
    subject_ref: str


@dataclass(frozen=True, slots=True)
class SessionQuery:
    session_id: UUID | None = None
    subject_ref: str | None = None
    active_only: bool = False


@dataclass(frozen=True, slots=True)
class AuthenticationQuery:
    subject_ref: str
    since: datetime | None = None


@dataclass(frozen=True, slots=True)
class AccountingQuery:
    session_ref: str | None = None
    subject_ref: str | None = None
    since: datetime | None = None


@dataclass(frozen=True, slots=True)
class AccessProjection:
    id: UUID
    tenant_id: UUID
    subject_ref: str
    desired_state: AccessState
    policy_code: str
    policy_version: str
    attributes: tuple[tuple[str, str], ...]
    decision_ref: str
    desired_fingerprint: str
    observed_fingerprint: str | None
    valid_until: datetime | None
    projected_at: datetime


@dataclass(frozen=True, slots=True)
class AuthenticationReceipt:
    id: UUID
    tenant_id: UUID
    subject_ref: str
    nas_ref: str
    session_ref: str | None
    outcome: AuthenticationOutcome
    reason_code: str | None
    source_ref: str
    observed_at: datetime
    fingerprint: str
    duplicate: bool


@dataclass(frozen=True, slots=True)
class AccountingReceipt:
    id: UUID
    tenant_id: UUID
    subject_ref: str
    nas_ref: str
    session_ref: str
    event_kind: str
    input_octets: int
    output_octets: int
    session_seconds: int
    source_ref: str
    observed_at: datetime
    fingerprint: str
    duplicate: bool


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    id: UUID
    tenant_id: UUID
    subject_ref: str
    nas_ref: str
    session_ref: str
    state: SessionState
    started_at: datetime
    last_seen_at: datetime
    closed_at: datetime | None
    closed_reason_code: str | None
    close_source_ref: str | None
    input_octets: int
    output_octets: int


@dataclass(frozen=True, slots=True)
class AccessDriftReport:
    projection: AccessProjection
    drifted: bool
    expected_fingerprint: str
    observed_fingerprint: str
    reason_code: str | None
    reconciled_at: datetime


@dataclass(frozen=True, slots=True)
class AccessProjectionChanged:
    event_id: UUID
    tenant_id: UUID
    projection: AccessProjection
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AuthenticationObserved:
    event_id: UUID
    tenant_id: UUID
    receipt: AuthenticationReceipt
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SessionStarted:
    event_id: UUID
    tenant_id: UUID
    session: SessionSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SessionClosed:
    event_id: UUID
    tenant_id: UUID
    session: SessionSnapshot
    occurred_at: datetime


__all__ = [
    "AccessDriftReport",
    "AccessProjection",
    "AccessProjectionChanged",
    "AccessState",
    "AccessStateQuery",
    "AccountingQuery",
    "AccountingReceipt",
    "AuthenticationObserved",
    "AuthenticationOutcome",
    "AuthenticationQuery",
    "AuthenticationReceipt",
    "CloseSession",
    "ProjectAccessPolicy",
    "ReconcileAccess",
    "RecordAccounting",
    "RecordAuthentication",
    "RegisterNasAttachment",
    "SessionClosed",
    "SessionQuery",
    "SessionSnapshot",
    "SessionStarted",
    "SessionState",
]
