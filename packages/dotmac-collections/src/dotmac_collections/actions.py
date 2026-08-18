"""Typed consequence requests and immutable product-owner receipts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypeAlias
from uuid import UUID

from dotmac_collections._validation import require_aware, require_text
from dotmac_collections.receivables import ReceivablePositionV1


@dataclass(frozen=True, slots=True)
class CollectionActionRequestedV1:
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
    action_code: str
    effect_scope: str
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
            "action_code",
            "effect_scope",
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
            raise ValueError("decision evidence does not match the action request")


@dataclass(frozen=True, slots=True)
class ActionApplied:
    request_id: UUID
    owner_code: str
    owner_receipt_id: str
    action_ref: str
    applied_at: datetime
    owner_state_fingerprint: str

    def __post_init__(self) -> None:
        for name in (
            "owner_code",
            "owner_receipt_id",
            "action_ref",
            "owner_state_fingerprint",
        ):
            require_text(name, getattr(self, name))
        require_aware("applied_at", self.applied_at)


@dataclass(frozen=True, slots=True)
class ActionRefused:
    request_id: UUID
    owner_code: str
    owner_receipt_id: str
    reason_code: str
    observed_at: datetime
    owner_state_fingerprint: str

    def __post_init__(self) -> None:
        for name in (
            "owner_code",
            "owner_receipt_id",
            "reason_code",
            "owner_state_fingerprint",
        ):
            require_text(name, getattr(self, name))
        require_aware("observed_at", self.observed_at)


@dataclass(frozen=True, slots=True)
class ActionDeferred:
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
class ActionFailed:
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


ActionReceipt: TypeAlias = ActionApplied | ActionRefused | ActionDeferred | ActionFailed


class ActionOwner(Protocol):
    def request_action(self, request: CollectionActionRequestedV1) -> ActionReceipt: ...


class ActionReceiptConflict(ValueError):
    """A request id was reused with different owner evidence."""


@dataclass(frozen=True, slots=True)
class ActionReceiptRecordResult:
    receipt_fingerprint: str
    replayed: bool


def _receipt_fingerprint(receipt: ActionReceipt) -> str:
    canonical = repr(receipt).encode()
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


class FakeActionReceiptRecorder:
    def __init__(self) -> None:
        self._receipts: dict[UUID, tuple[ActionReceipt, str]] = {}

    @property
    def receipts(self) -> tuple[ActionReceipt, ...]:
        return tuple(item[0] for item in self._receipts.values())

    def record(self, receipt: ActionReceipt) -> ActionReceiptRecordResult:
        fingerprint = _receipt_fingerprint(receipt)
        existing = self._receipts.get(receipt.request_id)
        if existing is not None:
            if existing[1] != fingerprint:
                raise ActionReceiptConflict(
                    "request id has different action receipt evidence"
                )
            return ActionReceiptRecordResult(fingerprint, True)
        self._receipts[receipt.request_id] = (receipt, fingerprint)
        return ActionReceiptRecordResult(fingerprint, False)


__all__ = [
    "ActionApplied",
    "ActionDeferred",
    "ActionFailed",
    "ActionOwner",
    "ActionReceipt",
    "ActionReceiptConflict",
    "ActionReceiptRecordResult",
    "ActionRefused",
    "CollectionActionRequestedV1",
    "FakeActionReceiptRecorder",
]
