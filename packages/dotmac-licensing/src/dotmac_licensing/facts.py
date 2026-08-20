"""The versioned facts this module publishes, and the views it returns.

An adopting assembly reads these off the platform outbox and reacts — the
Integrator delivers the envelope, deployment control records the intent, billing
starts the meter. **This module calls none of them** (ADR-0024): it records a
decision and emits the fact; the assembly routes it.

## The version is in the event type

`licence.issued.v1`, not `licence.issued`. A consumer pins a shape; when the
shape changes incompatibly, `v2` is emitted alongside `v1` for a migration
window and a consumer that never migrated keeps working instead of silently
mis-parsing. An unversioned type makes that impossible to do safely, because
there is no way to emit both.

## No fact carries a signed envelope

Every payload names `licence_id`, `licence_version` and `digest`, and stops. The
envelope is fetched from the issuance by whatever is going to deliver it. Putting
a signed document in an outbox row would copy it into every relay log, every
dead-letter dump and every consumer's own storage — and a signed licence is
exactly the artifact that grants authority when it lands somewhere.

`digest` is in every payload deliberately: it is what lets a consumer prove the
fact it is acting on describes the document it holds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final
from uuid import UUID

# ── Event types ─────────────────────────────────────────────────────────────

LICENCE_ISSUED_V1: Final[str] = "licence.issued.v1"
LICENCE_ACTIVATED_V1: Final[str] = "licence.activated.v1"
LICENCE_SUSPENDED_V1: Final[str] = "licence.suspended.v1"
LICENCE_REINSTATED_V1: Final[str] = "licence.reinstated.v1"
LICENCE_REVOKED_V1: Final[str] = "licence.revoked.v1"
LICENCE_EXPIRED_V1: Final[str] = "licence.expired.v1"
LICENCE_ACKNOWLEDGED_V1: Final[str] = "licence.acknowledged.v1"
REVOCATION_LIST_PUBLISHED_V1: Final[str] = "licence.revocation_list.published.v1"

#: Every type this module can emit. A consumer building a subscription set reads
#: this rather than a hand-kept list that drifts, and the module's own test
#: asserts the set matches what the service actually emits — a published
#: vocabulary nobody checks is the same defect ADR-0008 names for declarations.
PUBLISHED_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        LICENCE_ISSUED_V1,
        LICENCE_ACTIVATED_V1,
        LICENCE_SUSPENDED_V1,
        LICENCE_REINSTATED_V1,
        LICENCE_REVOKED_V1,
        LICENCE_EXPIRED_V1,
        LICENCE_ACKNOWLEDGED_V1,
        REVOCATION_LIST_PUBLISHED_V1,
    }
)


# ── Views ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IssuanceView:
    """One issued licence version as a caller sees it.

    Carries the `envelope` because the caller's next step is usually to hand it
    to a delivery transport, and making them re-read the row would be a second
    query for something they already asked for. No ORM object leaves this module:
    a detached row would let a caller lazy-load into a session it does not own,
    and would make every column a public contract by accident.
    """

    id: UUID
    licence_id: UUID
    version: int
    status: str
    digest: str
    key_id: str
    agreement_ref: str
    allocation_ref: str
    record_version: int
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    grace_days: int = 0
    deployment_ref: str | None = None
    activated_at: datetime | None = None
    replaced_by_version: int | None = None
    envelope: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LicenceView:
    """A lineage and every version in it."""

    id: UUID
    subject_ref: str
    product_code: str
    generation: int
    revoked: bool
    issuances: tuple[IssuanceView, ...] = ()


@dataclass(frozen=True, slots=True)
class AcknowledgementView:
    """One report from a deployment.

    `authenticated` is a BOOLEAN derived from whether the transport authenticated
    the reporter, deliberately separate from `reported_deployment_ref`. A caller
    reading this needs "can I trust who this came from?" to be one field it
    cannot accidentally conflate with "who did it say it was?".
    """

    issuance_id: UUID
    licence_version: int
    digest: str
    outcome: str
    reason: str | None
    reported_at: datetime
    reported_deployment_ref: str
    authenticated: bool


@dataclass(frozen=True, slots=True)
class RevocationListView:
    """One published revocation snapshot."""

    id: UUID
    list_version: int
    digest: str
    key_id: str
    entry_count: int
    envelope: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InspectionResult:
    """What a deployment would see when it verifies this envelope.

    `valid=False` is an ANSWER, not an error: the inspection API exists to
    diagnose "why is this customer's licence not working?", and raising would
    move the diagnosis into the caller's exception handler. `reason` is the
    kernel's own stable `LicenceError` subclass name, so the answer is the same
    word the receiver would report.
    """

    valid: bool
    reason: str | None = None
    detail: str | None = None
    validity: str | None = None
    licence_id: str | None = None
    licence_version: int | None = None
    digest: str | None = None
    product: str | None = None
    capabilities: tuple[str, ...] = ()


__all__ = [
    "LICENCE_ACKNOWLEDGED_V1",
    "LICENCE_ACTIVATED_V1",
    "LICENCE_EXPIRED_V1",
    "LICENCE_ISSUED_V1",
    "LICENCE_REINSTATED_V1",
    "LICENCE_REVOKED_V1",
    "LICENCE_SUSPENDED_V1",
    "PUBLISHED_EVENT_TYPES",
    "REVOCATION_LIST_PUBLISHED_V1",
    "AcknowledgementView",
    "InspectionResult",
    "IssuanceView",
    "LicenceView",
    "RevocationListView",
]
