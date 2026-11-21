"""Per-account collection hold contracts and the pure subject-access decision.

A subject (one billing account) is ``held`` when a case demands the access hold
and no collection hold suppresses it. Only ``held`` turns service off; ``clear``
and ``suppressed`` both mean service on. Holds are insert-only facts: a hold is
live until a lift row names it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from dotmac_kernel.cache import Scope

from dotmac_collections._validation import require_aware, require_text

SUBJECT_ACCESS_HOLD_EVENT_TYPE = "collections.subject.access_hold.v1"
SUBJECT_ACCESS_POLICY_VERSION = "collections.subject_access.v1"
EXTERNAL_REF_MAX_LENGTH = 255

SubjectAccessState = Literal["clear", "held", "suppressed"]


def decide_subject_access(*, demanded: bool, suppressed: bool) -> SubjectAccessState:
    """The whole decision table: demanded and not suppressed is the only ``held``."""

    if not demanded:
        return "clear"
    return "suppressed" if suppressed else "held"


def _require_external_ref(value: str) -> None:
    require_text("external_ref", value)
    if len(value) > EXTERNAL_REF_MAX_LENGTH:
        raise ValueError("external_ref is too long")
    kind, separator, identity = value.partition(":")
    if not separator or not kind.strip() or not identity.strip():
        raise ValueError("external_ref must be <kind>:<id>")


@dataclass(frozen=True, slots=True)
class PlaceCollectionHoldV1:
    """Place one hold on a subject; ``external_ref`` is opaque to Collections."""

    command_id: UUID
    idempotency_key: str
    scope: Scope
    subject_ref: str
    external_ref: str
    reason_code: str
    evidence_ref: str
    actor_ref: str
    placed_at: datetime
    approval_ref: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "idempotency_key",
            "subject_ref",
            "reason_code",
            "evidence_ref",
            "actor_ref",
        ):
            require_text(name, getattr(self, name))
        _require_external_ref(self.external_ref)
        if self.approval_ref is not None:
            require_text("approval_ref", self.approval_ref)
        require_aware("placed_at", self.placed_at)


@dataclass(frozen=True, slots=True)
class LiftCollectionHoldV1:
    """Lift the live hold named by ``external_ref`` on a subject."""

    command_id: UUID
    idempotency_key: str
    scope: Scope
    subject_ref: str
    external_ref: str
    reason_code: str
    evidence_ref: str
    actor_ref: str
    lifted_at: datetime
    approval_ref: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "idempotency_key",
            "subject_ref",
            "reason_code",
            "evidence_ref",
            "actor_ref",
        ):
            require_text(name, getattr(self, name))
        _require_external_ref(self.external_ref)
        if self.approval_ref is not None:
            require_text("approval_ref", self.approval_ref)
        require_aware("lifted_at", self.lifted_at)


@dataclass(frozen=True, slots=True)
class HoldPlaced:
    hold_id: UUID
    subject_ref: str
    external_ref: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class HoldLifted:
    hold_id: UUID
    subject_ref: str
    external_ref: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class HoldNotLive:
    external_ref: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class CollectionSubjectStateV1:
    subject_ref: str
    state: SubjectAccessState
    version: int | None
    live_external_refs: tuple[str, ...]
    changed_at: datetime | None


__all__ = [
    "CollectionSubjectStateV1",
    "EXTERNAL_REF_MAX_LENGTH",
    "HoldLifted",
    "HoldNotLive",
    "HoldPlaced",
    "LiftCollectionHoldV1",
    "PlaceCollectionHoldV1",
    "SUBJECT_ACCESS_HOLD_EVENT_TYPE",
    "SUBJECT_ACCESS_POLICY_VERSION",
    "SubjectAccessState",
    "decide_subject_access",
]
