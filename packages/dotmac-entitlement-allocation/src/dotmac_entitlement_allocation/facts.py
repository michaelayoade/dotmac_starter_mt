"""The allocation's published values — the vocabulary, and the READ contracts.

Everything here is a value: frozen, slotted, built from stdlib types, and
importing no ORM, no session and no persistence. That is what lets a browser
surface render an allocation without ever holding a session-bound row, which
would lazy-load on attribute access, mutate under the render, and expire when
the transaction closed.

## Why the status vocabulary lives here rather than beside the table

ONE definition, shared by persistence and by every owner that reads the
contract. `models` imports it from here rather than declaring it, because a
status is a value the module PUBLISHES and only incidentally a column — and a
read contract that had to import the ORM to name a status would drag persistence
into every consumer's type-checker.

## Sealed is the whole story

An allocation is staged, entries are written, and the row is SEALED in one
transaction; after that a database trigger refuses a late entry and refuses to
lift the seal. So an unsealed row on disk is not a state in the lifecycle — it
is an incomplete write, and its entries may be a partial set nobody finished
validating.

`AllocationIntegrity` says which of the two a caller is looking at, and
`AllocationRefusal` says why an unsealed one may not be issued against. Both are
derived here from rows this module owns, and neither appears on any input: an
allocation that claimed to be sealed because the request said so would be an
entitlement set nobody validated, wearing the word that means somebody did.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from dotmac_entitlement_allocation.ports import AllocationError

# ── The vocabulary ──────────────────────────────────────────────────────────


class AllocationStatus(StrEnum):
    """The allocation lifecycle, as a value object rather than a bare string.

    Only `STAGED` exists in this release. Delivery and acknowledgement states
    belong to licence issuance, a different owner; an allocation that tracked
    its own delivery would become a second delivery authority.

    Stored as text with no CHECK, for the reason ADR-0008 records against native
    enums: adding a member should cost a module release, not an `ALTER TYPE` on
    every deployment.
    """

    STAGED = "staged"


#: Back-compat alias for the value, so call sites read naturally.
STAGED = AllocationStatus.STAGED


@dataclass(frozen=True, slots=True)
class AllocatedCapability:
    """One entitled capability as it was staged.

    A named value object rather than a `tuple[str, int]`: a bare pair forces
    every consumer to remember which position is which, and the compiler cannot
    tell `(code, quantity)` from `(quantity, code)` when both are read
    positionally.
    """

    capability_code: str
    quantity: int


# ── Owner-derived verdicts ──────────────────────────────────────────────────


class AllocationIntegrity(StrEnum):
    """Whether what is on disk is a finished allocation.

    Derived from the `sealed` column, which only this module's staging path
    sets and which a trigger refuses to lift. Not a status: `STAGED` describes
    where the allocation is in its lifecycle, this describes whether the write
    that produced it completed.
    """

    #: Complete and immutable. Safe to issue a licence against.
    SEALED = "sealed"
    #: An incomplete write. Its entries may be a partial set nobody finished
    #: validating; repair or remove it rather than reading it as history.
    UNSEALED = "unsealed"


class AllocationAction(StrEnum):
    """What may still be done with an allocation.

    Owner-derived, and deliberately a very short list. There is no `AMEND`, no
    `RESTAGE` and no `UNSEAL` member at all: the seal is one-way, the online
    `platform_api` role may update no column but `sealed`, and a trigger refuses
    to lift it. An action a screen cannot be told about is an action a screen
    cannot render.
    """

    #: Issue a licence against this allocation. Permitted only when sealed.
    ISSUE = "issue"


class AllocationRefusal(StrEnum):
    """Why an activation cannot be issued against, in typed form.

    The read equivalent of what `allocation_product` raises, so a surface can
    explain the state instead of catching an exception to discover it.
    """

    #: Nothing has been staged for this `(contract_ref, content_hash)`.
    NOT_STAGED = "not_staged"
    #: A row exists but was never sealed — an incomplete write, not history.
    NOT_SEALED = "not_sealed"


class ReconciliationState(StrEnum):
    """Whether this exact activation has a usable allocation on file."""

    ALLOCATED = "allocated"
    MISSING = "missing"
    INCOMPLETE = "incomplete"


# ── Views ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AllocationRecord:
    """One allocation as it stands on file.

    Distinct from `AllocationView`, and the difference is the question each
    answers. `AllocationView` is the OUTCOME of a staging call and carries
    `replayed` — "did my delivery do work?" — which is meaningless to a reader
    who never staged anything. This is the RECORD: what is on file, whether the
    write completed, where it came from, and what may be done with it.

    `integrity` and `permitted_actions` are derived here from rows this module
    owns; there is nowhere on any input to state either.
    """

    id: UUID
    contract_ref: UUID
    product_code: str
    customer_ref: str
    content_hash: str
    status: AllocationStatus
    entries: tuple[AllocatedCapability, ...]
    integrity: AllocationIntegrity
    permitted_actions: tuple[AllocationAction, ...]
    #: Which delivery produced it. Idempotency provenance, kept so a replayed
    #: delivery is explainable rather than merely silent.
    source_event_id: str
    #: The digest of the CLAIM this allocation was staged from. What makes a
    #: second delivery of one activation distinguishable from a second, DIFFERENT
    #: claim about it.
    snapshot_fingerprint: str
    staged_at: datetime


@dataclass(frozen=True, slots=True)
class AllocationFilter:
    """What a caller may narrow the allocation estate by.

    A closed set of typed fields. No predicate, no sort column, no raw `where`:
    a consumer that could pass one would own every future query over
    `mod_ealloc`, and the module could no longer say what its own read surface
    is.

    There is deliberately nowhere here to state an integrity verdict or a
    permitted action. `sealed` SELECTS on the column this module wrote; it does
    not assert one.

    `page_size` is bounded by the TYPE rather than by the caller, because an
    unbounded list is how a control-plane screen becomes a full-table scan the
    day the fleet has staged its hundred-thousandth allocation.
    """

    contract_ref: UUID | None = None
    product_code: str | None = None
    customer_ref: str | None = None
    sealed: bool | None = None
    page: int = 1
    page_size: int = 50

    MAX_PAGE_SIZE: ClassVar[int] = 200

    def __post_init__(self) -> None:
        # `AllocationError` rather than a bare `ValueError`, so a caller
        # catching this module's refusals catches this too. It IS a `ValueError`
        # subclass, so nothing that caught the broader type stops working.
        if self.page < 1:
            raise AllocationError("page is 1-based")
        if not 1 <= self.page_size <= self.MAX_PAGE_SIZE:
            raise AllocationError(f"page_size must be 1..{self.MAX_PAGE_SIZE}")


@dataclass(frozen=True, slots=True)
class AllocationPage:
    """One page of allocations, and enough to render a pager honestly.

    `total` counts what matches the FILTER, not the page, so a surface can say
    "showing 50 of 412" without a second query and without guessing.
    """

    allocations: tuple[AllocationRecord, ...]
    total: int
    page: int
    page_size: int

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total


@dataclass(frozen=True, slots=True)
class AllocationReconciliation:
    """Whether one exact activation has a usable allocation, and if not, why.

    The caller supplies `(contract_ref, content_hash)` from ITS OWN authority —
    this module never reads a contract, which is the boundary that keeps
    contract invariants proven where they live rather than re-derived here from
    a foreign table. What the module answers is the half it owns: what it staged,
    whether that write completed, and whether a licence may be issued against it.

    `refusal` is the typed form of the exception `allocation_product` raises, so
    a surface explains the state instead of catching an error to discover it.
    """

    contract_ref: UUID
    content_hash: str
    state: ReconciliationState
    allocation: AllocationRecord | None = None
    refusal: AllocationRefusal | None = None
    detail: str | None = None

    @property
    def issuable(self) -> bool:
        return self.state is ReconciliationState.ALLOCATED


__all__ = [
    "STAGED",
    "AllocatedCapability",
    "AllocationAction",
    "AllocationFilter",
    "AllocationIntegrity",
    "AllocationPage",
    "AllocationRecord",
    "AllocationReconciliation",
    "AllocationRefusal",
    "AllocationStatus",
    "ReconciliationState",
]
