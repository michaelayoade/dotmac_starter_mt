"""The versioned facts this module publishes, as values.

An adopting assembly reads these off the platform outbox and reacts —
`dotmac-entitlement-allocation` stages an allocation when an agreement
activates, `dotmac-licensing` issues against that allocation, billing raises the
first invoice. **This module calls none of them.** It records a decision and
emits the fact; the assembly routes it. ADR-0026 § 6 states the rule for
approvals and ADR-0024 states it generally: an owner emits, a consumer's own
authoritative service acts.

## Why the version is in the event type

`agreement.activated.v1`, not `agreement.activated`. A consumer pins a shape.
When the shape changes incompatibly, `v2` is emitted ALONGSIDE `v1` for a
migration window, and a consumer that never migrated keeps working instead of
silently mis-parsing. An unversioned type makes that impossible to do safely,
because there is no way to emit both.

The `v` is part of the string rather than a separate field for the same reason a
routing key is: a relay filters on the type, and a consumer that has to parse a
payload to discover it cannot subscribe to only what it understands.

## The payload carries references, never resolved documents

Every fact names `agreement_id`, `counterparty_ref` and `content_hash`, and
stops. It does not embed the counterparty's name, the release's manifest, or the
approval's policy document — those belong to their owners, and copying them into
an event makes this module a stale second source of each. A consumer that needs
the name asks the owner.

`content_hash` is in every payload deliberately: it is what lets a consumer
prove the fact it is acting on describes the same terms it last saw.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final
from uuid import UUID

from dotmac_commercial_agreements.ports import derive_end_exclusive

# ── Event types ─────────────────────────────────────────────────────────────

AGREEMENT_PROPOSED_V1: Final[str] = "agreement.proposed.v1"
AGREEMENT_APPROVED_V1: Final[str] = "agreement.approved.v1"
AGREEMENT_ACTIVATED_V1: Final[str] = "agreement.activated.v1"
AGREEMENT_AMENDED_V1: Final[str] = "agreement.amended.v1"
AGREEMENT_SUSPENDED_V1: Final[str] = "agreement.suspended.v1"
AGREEMENT_REINSTATED_V1: Final[str] = "agreement.reinstated.v1"
AGREEMENT_TERMINATED_V1: Final[str] = "agreement.terminated.v1"
AGREEMENT_EXPIRED_V1: Final[str] = "agreement.expired.v1"
AGREEMENT_REJECTED_V1: Final[str] = "agreement.rejected.v1"
AGREEMENT_CANCELLED_V1: Final[str] = "agreement.cancelled.v1"

#: Every type this module can emit. A consumer building a subscription set reads
#: this rather than a hand-kept list that drifts, and the module's own test
#: asserts the set matches what the service actually emits — a published
#: vocabulary nobody checks is the same defect ADR-0008 names for declarations.
PUBLISHED_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        AGREEMENT_PROPOSED_V1,
        AGREEMENT_APPROVED_V1,
        AGREEMENT_ACTIVATED_V1,
        AGREEMENT_AMENDED_V1,
        AGREEMENT_SUSPENDED_V1,
        AGREEMENT_REINSTATED_V1,
        AGREEMENT_TERMINATED_V1,
        AGREEMENT_EXPIRED_V1,
        AGREEMENT_REJECTED_V1,
        AGREEMENT_CANCELLED_V1,
    }
)


# ── Views ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PromisedLine:
    """One line of what an agreement promises, as a value.

    This is the shape `dotmac-entitlement-allocation` reads to build its
    `ContractSnapshot`. It carries `product_code` beside `capability_code` for
    the reason that module records: a capability code is only meaningful against
    the product that declares it.
    """

    line_no: int
    product_code: str
    capability_code: str
    quantity: int
    unit_amount: str
    unit_currency_code: str
    release_ref: str | None = None
    offer_ref: str | None = None


@dataclass(frozen=True, slots=True)
class AgreementView:
    """The agreement as a caller sees it. No ORM object leaves this module.

    Returning a detached ORM row would let a caller lazy-load into a session it
    does not own, and would make every column a public contract by accident.
    ``expiry_date`` preserves a1's inclusive end; ``end_exclusive`` is the
    explicit derived boundary for consumers that work with half-open ranges.
    """

    id: UUID
    reference: str
    agreement_family_id: UUID
    agreement_version: int
    counterparty_ref: str
    agreement_type: str
    status: str
    effective_date: date
    expiry_date: date
    content_hash: str | None
    record_version: int
    approval_policy_code: str | None = None
    approval_policy_version: int | None = None
    approval_decision_ref: str | None = None
    approved_at: datetime | None = None
    activation_rule: str | None = None
    activated_at: datetime | None = None
    supersedes_id: UUID | None = None
    superseded_by_id: UUID | None = None
    lines: tuple[PromisedLine, ...] = ()

    @property
    def end_exclusive(self) -> date:
        """The first date outside the inclusive agreement period."""
        return derive_end_exclusive(self.expiry_date)


@dataclass(frozen=True, slots=True)
class AgreementPage:
    """A bounded keyset page of detached agreement views.

    ``next_after`` is the last id in ``items`` only when another row exists.
    Pass it back unchanged; it is a stable UUID keyset, not an offset or a row
    count that moves when the estate changes.
    """

    items: tuple[AgreementView, ...]
    next_after: UUID | None


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """One entry of the append-only history, as a value."""

    sequence: int
    event_type: str
    from_status: str | None
    to_status: str
    occurred_at: datetime
    actor_ref: str | None
    reason: str | None
    command_id: str


__all__ = [
    "AGREEMENT_ACTIVATED_V1",
    "AGREEMENT_AMENDED_V1",
    "AGREEMENT_APPROVED_V1",
    "AGREEMENT_CANCELLED_V1",
    "AGREEMENT_EXPIRED_V1",
    "AGREEMENT_PROPOSED_V1",
    "AGREEMENT_REINSTATED_V1",
    "AGREEMENT_REJECTED_V1",
    "AGREEMENT_SUSPENDED_V1",
    "AGREEMENT_TERMINATED_V1",
    "PUBLISHED_EVENT_TYPES",
    "AgreementPage",
    "AgreementView",
    "PromisedLine",
    "TransitionRecord",
]
