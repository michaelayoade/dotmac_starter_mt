"""Staging an allocation: validate everything, then write, in that order.

## The ordering is the invariant

Every entry is validated against the catalogue **before** any row, any audit
record, or any idempotency success exists. Not "validate as we go" — a loop that
adds rows and validates the next code would leave a partially written
allocation when the fourth code turns out to be undeclared, and the caller would
see a failure while the database kept the first three.

So the sequence is fixed: shape, then catalogue, then read-back, then write.
A snapshot is refused **atomically or accepted whole**; there is no state in
which some of its entries were allocated.

## Why the module validates rather than trusting the caller

The caller supplies ACCESS to the authority; this module performs the check.
That is the kernel's `grant_entitlement` pattern, and it is what preserves the
invariant across every adapter — an HTTP route, an outbox consumer, a CLI
backfill — rather than once per adapter, where the newest one is always the one
that forgot.

There is deliberately no `validated=True`, no `skip_validation`, and no
`trusted` flag. Any of them would make the invariant optional, and an optional
invariant is a comment. The cost of a redundant check when an upstream offer or
contract service already enforced legality is one dict lookup; the cost of the
flag is that the guarantee stops being one.

An upstream service enforcing the same rule at its own boundary is not a second
authority, provided both consult the same manifest-derived catalogue rather than
implementing separate legality rules.

## Idempotency

`(contract_ref, content_hash)` identifies an activated contract version. A
replayed delivery of the same activation returns the existing allocation
unchanged and writes nothing — including no re-validation against a catalogue
that may since have changed. An already-staged allocation is immutable history;
its legality was decided when it was staged, and a capability retired afterwards
must not retroactively make a delivered entitlement unreplayable.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_entitlement_allocation.models import STAGED, Allocation, AllocationEntry
from dotmac_entitlement_allocation.ports import (
    CapabilityCatalogueReader,
    ContractSnapshot,
    EmptyAllocationError,
    UndeclaredCapabilityError,
    UnknownProductError,
)


@dataclass(frozen=True, slots=True)
class AllocationView:
    """What staging produced, as a value rather than a live ORM row.

    Returned instead of the model so a caller cannot mutate an allocation by
    holding what a read handed back — the same reason the online role has no
    UPDATE privilege. `replayed` distinguishes "staged now" from "already
    staged", which an idempotent operation must not hide: a consumer counting
    activations needs to know which of its deliveries did work.
    """

    id: UUID
    contract_ref: UUID
    product_code: str
    customer_ref: str
    content_hash: str
    status: str
    entries: tuple[tuple[str, int], ...]
    replayed: bool


def _view(allocation: Allocation, *, replayed: bool) -> AllocationView:
    return AllocationView(
        id=allocation.id,
        contract_ref=allocation.contract_ref,
        product_code=allocation.product_code,
        customer_ref=allocation.customer_ref,
        content_hash=allocation.content_hash,
        status=allocation.status,
        entries=tuple(
            (entry.capability_code, entry.quantity)
            for entry in sorted(allocation.entries, key=lambda e: e.capability_code)
        ),
        replayed=replayed,
    )


def _existing(db: Session, snapshot: ContractSnapshot) -> Allocation | None:
    return db.execute(
        select(Allocation).where(
            Allocation.contract_ref == snapshot.contract_ref,
            Allocation.content_hash == snapshot.content_hash,
        )
    ).scalar_one_or_none()


def _check_shape(snapshot: ContractSnapshot) -> None:
    if not snapshot.entries:
        raise EmptyAllocationError(
            f"contract {snapshot.contract_ref} entitles nothing; "
            "an allocation with no entries is refused rather than stored"
        )
    if not snapshot.product_code:
        raise UnknownProductError(
            "product_code is required: a capability code is only meaningful "
            "against the product that declares it"
        )
    negative = tuple(
        entry.capability_code for entry in snapshot.entries if entry.quantity < 1
    )
    if negative:
        raise EmptyAllocationError(
            f"quantities must be positive; got non-positive for {negative}"
        )


def _check_catalogue(
    snapshot: ContractSnapshot, catalogues: CapabilityCatalogueReader
) -> None:
    """Every entry, before anything is written.

    Undeclared codes are COLLECTED rather than raised on the first one: a caller
    repairing a manifest wants the whole list, and failing one at a time turns
    one review into several. `UnknownProductError` is NOT collected — it is not
    a fact about the codes, it means the caller cannot prove anything at all, so
    it propagates immediately and closed.
    """
    undeclared: list[str] = []
    for entry in snapshot.entries:
        try:
            catalogues.require_declared(
                product_code=snapshot.product_code,
                capability_code=entry.capability_code,
            )
        except UnknownProductError:
            raise
        except UndeclaredCapabilityError as exc:
            undeclared.extend(exc.codes or (entry.capability_code,))
        except KeyError:
            # An adapter over a catalogue that signals "no such code" with a
            # bare KeyError — the kernel's `UndeclaredCapabilityError` is one.
            # Treated as undeclared rather than propagating, so the atomic
            # rejection below still reports every offending code.
            undeclared.append(entry.capability_code)
    if undeclared:
        raise UndeclaredCapabilityError(snapshot.product_code, tuple(undeclared))


def stage_allocation(
    db: Session,
    snapshot: ContractSnapshot,
    *,
    catalogues: CapabilityCatalogueReader,
) -> AllocationView:
    """Stage the immutable allocation for an activated contract version.

    Idempotent on `(contract_ref, content_hash)`. Flush-only — `dotmac_kernel.db`
    is the one transaction authority (hard rule 8), so the caller's request or
    job boundary decides when this commits.

    Raises before writing anything:

    - `EmptyAllocationError` — no entries, or a non-positive quantity.
    - `UnknownProductError` — the catalogue does not know `product_code`.
    - `UndeclaredCapabilityError` — one or more codes are not declared by that
      product. The WHOLE snapshot is rejected; there is no partial allocation.
    """
    _check_shape(snapshot)

    # Replay check BEFORE validation: an allocation already staged is immutable
    # history whose legality was decided when it was staged. Re-validating it
    # against a live catalogue would make a delivered entitlement unreplayable
    # the day a capability is retired — turning an idempotent redelivery into an
    # outage.
    replayed = _existing(db, snapshot)
    if replayed is not None:
        return _view(replayed, replayed=True)

    _check_catalogue(snapshot, catalogues)

    allocation = Allocation(
        contract_ref=snapshot.contract_ref,
        product_code=snapshot.product_code,
        customer_ref=snapshot.customer_ref,
        content_hash=snapshot.content_hash,
        status=STAGED,
        source_event_id=snapshot.source_event_id,
        entries=[
            AllocationEntry(
                capability_code=entry.capability_code,
                quantity=entry.quantity,
            )
            for entry in snapshot.entries
        ],
    )
    db.add(allocation)
    db.flush()
    return _view(allocation, replayed=False)


def allocation_product(db: Session, allocation_id: UUID) -> str:
    """The product an allocation was VALIDATED against.

    Licence issuance calls this instead of accepting a caller-supplied product.
    Without it, an allocation validated against product A can be issued as a
    licence for product B: every code still resolves, just in a different
    catalogue, and nothing in the licence records that the swap happened.
    """
    product = db.execute(
        select(Allocation.product_code).where(Allocation.id == allocation_id)
    ).scalar_one_or_none()
    if product is None:
        raise LookupError(f"no allocation {allocation_id}")
    return product


__all__ = ["AllocationView", "allocation_product", "stage_allocation"]
