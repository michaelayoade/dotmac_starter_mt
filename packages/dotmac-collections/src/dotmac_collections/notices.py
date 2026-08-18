"""Owner-neutral notice requests and distinct delivery-owner receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypeAlias
from uuid import UUID

from dotmac_collections._validation import require_aware, require_text
from dotmac_collections.receivables import ReceivablePositionV1


@dataclass(frozen=True, slots=True)
class CollectionNoticeRequestedV1:
    request_id: UUID
    idempotency_key: str
    case_id: UUID
    policy_version_id: UUID
    policy_step_code: str
    step_attempt_ordinal: int
    source_owner: str
    exposure_ref: str
    source_version: int
    position_fingerprint: str
    subject_ref: str
    service_ref: str | None
    purpose_code: str
    decision_evidence: ReceivablePositionV1
    requested_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "idempotency_key",
            "policy_step_code",
            "source_owner",
            "exposure_ref",
            "position_fingerprint",
            "subject_ref",
            "purpose_code",
        ):
            require_text(name, getattr(self, name))
        if self.service_ref is not None:
            require_text("service_ref", self.service_ref)
        if self.step_attempt_ordinal < 1 or self.source_version < 1:
            raise ValueError("attempt ordinal and source version must be positive")
        require_aware("requested_at", self.requested_at)
        evidence = self.decision_evidence
        if (
            evidence.source_owner != self.source_owner
            or evidence.exposure_ref != self.exposure_ref
            or evidence.source_version != self.source_version
            or evidence.state_fingerprint != self.position_fingerprint
            or evidence.subject_ref != self.subject_ref
            or evidence.service_ref != self.service_ref
        ):
            raise ValueError("decision evidence does not match the notice request")


@dataclass(frozen=True, slots=True)
class NoticeAccepted:
    request_id: UUID
    owner_code: str
    owner_receipt_id: str
    accepted_at: datetime

    def __post_init__(self) -> None:
        require_text("owner_code", self.owner_code)
        require_text("owner_receipt_id", self.owner_receipt_id)
        require_aware("accepted_at", self.accepted_at)


@dataclass(frozen=True, slots=True)
class NoticeSuppressed:
    request_id: UUID
    owner_code: str
    owner_receipt_id: str
    reason_code: str
    observed_at: datetime

    def __post_init__(self) -> None:
        for name in ("owner_code", "owner_receipt_id", "reason_code"):
            require_text(name, getattr(self, name))
        require_aware("observed_at", self.observed_at)


@dataclass(frozen=True, slots=True)
class NoticeUnavailable:
    request_id: UUID
    owner_code: str
    owner_receipt_id: str
    reason_code: str
    observed_at: datetime
    retry_at: datetime

    def __post_init__(self) -> None:
        for name in ("owner_code", "owner_receipt_id", "reason_code"):
            require_text(name, getattr(self, name))
        require_aware("observed_at", self.observed_at)
        require_aware("retry_at", self.retry_at)


@dataclass(frozen=True, slots=True)
class NoticeFailed:
    request_id: UUID
    owner_code: str
    owner_receipt_id: str
    reason_code: str
    observed_at: datetime
    retryable: bool

    def __post_init__(self) -> None:
        for name in ("owner_code", "owner_receipt_id", "reason_code"):
            require_text(name, getattr(self, name))
        require_aware("observed_at", self.observed_at)


NoticeReceipt: TypeAlias = (
    NoticeAccepted | NoticeSuppressed | NoticeUnavailable | NoticeFailed
)


class NoticeOwner(Protocol):
    def request_notice(self, request: CollectionNoticeRequestedV1) -> NoticeReceipt: ...


__all__ = [
    "CollectionNoticeRequestedV1",
    "NoticeAccepted",
    "NoticeFailed",
    "NoticeOwner",
    "NoticeReceipt",
    "NoticeSuppressed",
    "NoticeUnavailable",
]
