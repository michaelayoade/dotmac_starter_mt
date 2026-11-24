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
class RepublishSubjectStateV1:
    """Re-emit the subject's current state under a fresh version.

    For an adopter that must apply the current state on its own (service activation
    or transfer): the last flip's version may be lower than what the service has
    seen, so the product would drop it as stale. A republish allocates the next
    version from the same sequence and emits the same event type.
    """

    command_id: UUID
    idempotency_key: str
    scope: Scope
    subject_ref: str
    requested_at: datetime
    reason_code: str = "state_republished"

    def __post_init__(self) -> None:
        for name in ("idempotency_key", "subject_ref", "reason_code"):
            require_text(name, getattr(self, name))
        require_aware("requested_at", self.requested_at)


@dataclass(frozen=True, slots=True)
class SubjectStateRepublished:
    subject_ref: str
    state: SubjectAccessState
    version: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class CollectionCaseViewV1:
    """Read-only identity and hold evidence of one case (no money)."""

    case_id: UUID
    subject_ref: str
    source_owner: str
    exposure_ref: str
    service_ref: str | None
    lifecycle: str
    source_version: int
    due_at: datetime | None
    access_hold_from: datetime | None
    access_hold_engaged_at: datetime | None


@dataclass(frozen=True, slots=True)
class CollectionSubjectStateV1:
    subject_ref: str
    state: SubjectAccessState
    version: int | None
    live_external_refs: tuple[str, ...]
    changed_at: datetime | None


__all__ = [
    "CollectionCaseViewV1",
    "CollectionSubjectStateV1",
    "EXTERNAL_REF_MAX_LENGTH",
    "HoldLifted",
    "HoldNotLive",
    "HoldPlaced",
    "LiftCollectionHoldV1",
    "PlaceCollectionHoldV1",
    "RepublishSubjectStateV1",
    "SUBJECT_ACCESS_HOLD_EVENT_TYPE",
    "SUBJECT_ACCESS_POLICY_VERSION",
    "SubjectAccessState",
    "SubjectStateRepublished",
    "decide_subject_access",
]
