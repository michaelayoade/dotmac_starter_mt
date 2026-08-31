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
from enum import StrEnum
from typing import Any, ClassVar, Final
from uuid import UUID

from dotmac_licensing.ports import LicensingError

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


# ── Read contracts ──────────────────────────────────────────────────────────
#
# What a browser surface renders, and the line it must not cross. Everything
# below is a value: no ORM row, no session, and — the rule that matters most in
# this module — NO KEY MATERIAL of any kind. A read contract names a `key_id`
# and stops. The public half is distributed through `build_keyring`, which is a
# protocol artefact rather than a screen's data, and the private half does not
# exist anywhere in this distribution.


class AcknowledgementState(StrEnum):
    """What the ISSUER has heard back about one issuance.

    Deliberately not called a delivery state, and the distinction is this
    module's boundary rather than a naming preference. Delivery belongs to the
    Integrator: `transport.py`, attempt counters, retry outcomes and connection
    refs all stayed there (ADR-0024, hard rule 28). This module ends at a signed
    envelope and resumes at an acknowledgement, so the only honest thing it can
    report is whether a receiver has told it anything — never whether a
    transport delivered.

    `AWAITING` therefore means "nothing has been reported", which is NOT
    "undelivered": a licence can be applied by a deployment that never reports.
    A surface that rendered it as a delivery failure would be inventing a fact
    the issuer does not have.
    """

    AWAITING = "awaiting"
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class SigningKeyView:
    """A signing key as a screen may see it: an IDENTIFIER and a status.

    No material, public or private. The private half has no column in this
    distribution at all — a wheel, a database dump, a replica and a stack trace
    are structurally incapable of leaking one. The PUBLIC half is real and
    distributed, but through `build_keyring`, which is a protocol artefact a
    deployment consumes; putting it on a screen's contract would make every
    future surface a place key material can appear by accident.

    A licence problem is diagnosed by key ID — "which key signed this, and is it
    still active?" — and that is what this answers.
    """

    key_id: str
    status: str
    registered_at: datetime


@dataclass(frozen=True, slots=True)
class RevocationView:
    """One revoked lineage, and who said so."""

    id: UUID
    licence_id: UUID
    reason: str
    actor_ref: str | None
    revoked_at: datetime


@dataclass(frozen=True, slots=True)
class LicenceSummary:
    """One lineage as a list row.

    Deliberately NOT `LicenceView`: that carries every issuance in the lineage,
    which is right for a detail screen and is a page of nested rows per entry on
    a list. The current version and status are what a list is for.
    """

    id: UUID
    subject_ref: str
    product_code: str
    generation: int
    revoked: bool
    issuance_count: int
    current_version: int | None
    current_status: str | None


@dataclass(frozen=True, slots=True)
class LicenceFilter:
    """What an operator may narrow the licence estate by.

    A closed set of typed fields. No predicate, no sort column, no raw `where`:
    a consumer that could pass one would own every future query over
    `mod_licensing`, and the module could no longer say what its own read
    surface is.

    There is deliberately nowhere here to state a digest, a key id as an
    assertion about signing, or an acknowledgement state. `key_id` and
    `issuance_status` SELECT on values this module wrote; they assert nothing.

    `page_size` is bounded by the TYPE rather than by the caller, because an
    unbounded list is how a licensing screen becomes a full-table scan on an
    estate that grows one row per issued version.
    """

    subject_ref: str | None = None
    product_code: str | None = None
    revoked: bool | None = None
    issuance_status: str | None = None
    key_id: str | None = None
    page: int = 1
    page_size: int = 50

    MAX_PAGE_SIZE: ClassVar[int] = 200

    def __post_init__(self) -> None:
        # `LicensingError` rather than a bare `ValueError`, so a caller catching
        # this module's refusals catches this too. It IS a `ValueError` subclass.
        if self.page < 1:
            raise LicensingError("page is 1-based")
        if not 1 <= self.page_size <= self.MAX_PAGE_SIZE:
            raise LicensingError(f"page_size must be 1..{self.MAX_PAGE_SIZE}")


@dataclass(frozen=True, slots=True)
class LicencePage:
    """One page of lineages, and enough to render a pager honestly.

    `total` counts what matches the FILTER, not the page, so a surface can say
    "showing 50 of 412" without a second query and without guessing.
    """

    licences: tuple[LicenceSummary, ...]
    total: int
    page: int
    page_size: int

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total


@dataclass(frozen=True, slots=True)
class IssuanceHandoff:
    """What the issuer hands a transport, and what it has heard back.

    The boundary made into a type. On the way OUT: the signed envelope and its
    digest, and the deployment it is bound to (or `None`, meaning deliberately
    portable). On the way BACK: acknowledgements, which are receipts a receiver
    reported — the `receipt_references` are the digests those reports named, so
    an operator can see whether the thing acknowledged is the thing issued.

    What is NOT here, and never will be: an attempt count, a retry outcome, a
    connection reference, a next-attempt time. Those are the Integrator's, and a
    field for one here would make this module a second delivery authority with
    no way to populate it honestly.
    """

    issuance_id: UUID
    licence_id: UUID
    licence_version: int
    subject_ref: str
    product_code: str
    digest: str
    key_id: str
    #: Bound licence when set; `None` means deliberately portable.
    deployment_ref: str | None
    envelope: Mapping[str, Any]
    acknowledgement_state: AcknowledgementState
    receipts: tuple[AcknowledgementView, ...] = ()
    #: The digests the receipts named. Equal to the issued digest when the
    #: deployment applied what was issued; a difference is the whole reason to
    #: show them side by side.
    receipt_references: tuple[str, ...] = ()


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
    "AcknowledgementState",
    "AcknowledgementView",
    "InspectionResult",
    "IssuanceHandoff",
    "IssuanceView",
    "LicenceFilter",
    "LicencePage",
    "LicenceSummary",
    "LicenceView",
    "RevocationListView",
    "RevocationView",
    "SigningKeyView",
]
