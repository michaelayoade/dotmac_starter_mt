"""Staging an allocation: validate everything, then write once, in that order.

## The ordering is the invariant

Shape, then replay, then catalogue, then write. Every entry is validated
against the catalogue **before** any row, audit record, or idempotency success
exists. Not "validate as we go" — a loop that added rows and validated the next
code would leave a partially written allocation when the fourth code turned out
undeclared, and the caller would see a failure while the database kept the
first three.

A snapshot is refused **atomically or accepted whole**.

## Why the module validates rather than trusting the caller

The caller supplies ACCESS to the authority; this module performs the check.
That is the kernel's `grant_entitlement` pattern, and it preserves the invariant
across every adapter — an HTTP route, an outbox consumer, a CLI backfill —
rather than once per adapter, where the newest one is always the one that
forgot.

There is deliberately no `validated=True`, no `skip_validation`, and no
`trusted`. An optional invariant is a comment.

An upstream offer or contract service enforcing the same rule at its own
boundary is not a second authority, provided both consult the same
manifest-derived catalogue rather than implementing separate legality rules.

## Two different identities, two different protections

`source_event_id` identifies a DELIVERY. `(contract_ref, content_hash)`
identifies an ACTIVATED CONTRACT VERSION. They fail differently and both need
guarding:

* **Delivery** goes through `dotmac_kernel.idempotency.execute_once_platform` —
  ADR-0014's one owner of at-most-once execution. It gives concurrent-insert
  resolution and, critically, means the staging audit event is written exactly
  once no matter how many times an at-least-once transport delivers.
* **Activation** carries a `snapshot_fingerprint` stored on the row. Finding an
  existing `(contract_ref, content_hash)` is only a replay if the incoming
  product, customer and normalized entries match it; otherwise it is two
  different claims about one activation, and returning the first silently would
  hide the disagreement forever.

The fingerprint lives on the ALLOCATION rather than only in the idempotency
record because idempotency records have a retention policy (ADR-0014 leaves it
to the product) and allocations do not. A purge must not be able to turn a
conflict back into a silent replay.

## Replay does not re-validate

An already-staged allocation is immutable history whose legality was decided
when it was staged. Re-checking it against a live catalogue would make a
delivered entitlement unreplayable the day a capability is retired — turning an
idempotent redelivery into an outage.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from dotmac_kernel.audit import write_platform_audit_event
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_entitlement_allocation.models import (
    Allocation,
    AllocationEntry,
    AllocationStatus,
)
from dotmac_entitlement_allocation.ports import (
    AllocationConflictError,
    CapabilityCatalogueReader,
    ContractSnapshot,
    DuplicateCapabilityError,
    EmptyAllocationError,
    IncompleteAllocationError,
    UndeclaredCapabilityError,
    UnknownProductError,
)

#: The idempotency scope, namespaced by the module code so two modules cannot
#: collide in a shared key space.
IDEMPOTENCY_SCOPE = "entitlement_allocation.stage"

#: Declared on the module manifest. One event per staging, written inside the
#: idempotent operation so an at-least-once transport cannot produce two.
AUDIT_ACTION_STAGED = "entitlement_allocation.staged"


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
    status: AllocationStatus
    entries: tuple[AllocatedCapability, ...]
    replayed: bool


def snapshot_fingerprint(snapshot: ContractSnapshot) -> str:
    """A stable digest of everything that makes up this snapshot's CLAIM.

    Deliberately excludes `source_event_id`: two deliveries of one activation
    are the same claim, and including the delivery id would make every
    redelivery look like a conflict. Entries are sorted, so the ordering a
    transport happened to produce cannot change the digest.
    """
    # Imported HERE, not at module scope. `dotmac_kernel.idempotency` reaches
    # `dotmac_kernel.db`, which BUILDS THE ENGINE at import — so a module-level
    # import would make this package unimportable without a DATABASE_URL, and
    # `models.py` would stop being the import-safe declaration a migration
    # gate, a type-checker and an offline test all rely on. The kernel's own
    # `settings_resolver` defers its outbox import for exactly this reason.
    from dotmac_kernel.idempotency import fingerprint_of

    return fingerprint_of(
        {
            "contract_ref": str(snapshot.contract_ref),
            "product_code": snapshot.product_code,
            "customer_ref": snapshot.customer_ref,
            "content_hash": snapshot.content_hash,
            "entries": sorted(
                [entry.capability_code, entry.quantity] for entry in snapshot.entries
            ),
        }
    )


def _view(allocation: Allocation, *, replayed: bool) -> AllocationView:
    return AllocationView(
        id=allocation.id,
        contract_ref=allocation.contract_ref,
        product_code=allocation.product_code,
        customer_ref=allocation.customer_ref,
        content_hash=allocation.content_hash,
        status=AllocationStatus(allocation.status),
        entries=tuple(
            AllocatedCapability(
                capability_code=entry.capability_code, quantity=entry.quantity
            )
            for entry in sorted(allocation.entries, key=lambda e: e.capability_code)
        ),
        replayed=replayed,
    )


def _existing(db: Session, snapshot: ContractSnapshot) -> Allocation | None:
    """The allocation for this activation, or None.

    An UNSEALED row raises rather than being returned. It is an incomplete
    write — a crash between the parent insert and the seal, or raw SQL — and
    adopting it as a replay would hand back an entitlement set nobody finished
    validating. The row cannot tell us whether its missing entries were rejected
    or merely never written, so the only safe reading is to refuse.
    """
    row = db.execute(
        select(Allocation).where(
            Allocation.contract_ref == snapshot.contract_ref,
            Allocation.content_hash == snapshot.content_hash,
        )
    ).scalar_one_or_none()
    if row is not None and not row.sealed:
        raise IncompleteAllocationError(
            f"allocation {row.id} for activation ({snapshot.contract_ref}, "
            f"{snapshot.content_hash}) was never sealed; it is an incomplete "
            "write, not history. Repair or remove it before staging again."
        )
    return row


def _replay_or_conflict(
    existing: Allocation, snapshot: ContractSnapshot, fingerprint: str
) -> AllocationView:
    if existing.snapshot_fingerprint != fingerprint:
        raise AllocationConflictError(
            f"activation ({snapshot.contract_ref}, {snapshot.content_hash}) was "
            "already staged from different inputs; this is not a replay. The "
            "stored allocation and the incoming snapshot disagree about the "
            "product, the customer, or the entitled capabilities."
        )
    return _view(existing, replayed=True)


def _check_shape(snapshot: ContractSnapshot) -> None:
    """Everything provable without asking anyone, before the catalogue is
    consulted."""
    if not snapshot.product_code:
        raise UnknownProductError(
            "product_code is required: a capability code is only meaningful "
            "against the product that declares it"
        )
    if not snapshot.entries:
        raise EmptyAllocationError(
            f"contract {snapshot.contract_ref} entitles nothing; "
            "an allocation with no entries is refused rather than stored"
        )

    seen: set[str] = set()
    duplicates: list[str] = []
    for entry in snapshot.entries:
        if entry.capability_code in seen and entry.capability_code not in duplicates:
            duplicates.append(entry.capability_code)
        seen.add(entry.capability_code)
    if duplicates:
        raise DuplicateCapabilityError(tuple(duplicates))

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

    Only `UndeclaredCapabilityError` is collected, so a caller repairing a
    manifest gets the whole list rather than one code per round-trip. Nothing
    else is caught: `UnknownProductError` means the caller cannot prove anything
    at all and propagates closed, and any OTHER exception is an adapter defect
    that must surface as itself. An earlier revision caught bare `KeyError` and
    reported it as an undeclared capability, which disguised three different
    failures — a broken adapter, a missing product, and a genuine undeclared
    code — as the cheapest of them.
    """
    undeclared: list[str] = []
    for entry in snapshot.entries:
        try:
            catalogues.require_declared(
                product_code=snapshot.product_code,
                capability_code=entry.capability_code,
            )
        except UndeclaredCapabilityError as exc:
            undeclared.extend(exc.codes or (entry.capability_code,))
    if undeclared:
        raise UndeclaredCapabilityError(snapshot.product_code, tuple(undeclared))


def stage_allocation(
    db: Session,
    snapshot: ContractSnapshot,
    *,
    catalogues: CapabilityCatalogueReader,
    actor_admin_id: UUID | None = None,
) -> AllocationView:
    """Stage the immutable allocation for an activated contract version.

    Idempotent on the delivery (`source_event_id`) through the kernel's
    at-most-once owner, and on the activation (`contract_ref`, `content_hash`)
    through a stored snapshot fingerprint. Flush-only — `dotmac_kernel.db` is
    the one transaction authority (hard rule 8), so the caller's request or job
    boundary decides when this commits.

    Raises before writing anything:

    - `EmptyAllocationError` — no entries, or a non-positive quantity.
    - `DuplicateCapabilityError` — a code appears twice; supply a normalized
      snapshot rather than expecting this module to aggregate.
    - `UnknownProductError` — the catalogue does not know `product_code`.
    - `UndeclaredCapabilityError` — one or more codes are not declared by that
      product. The WHOLE snapshot is rejected.
    - `AllocationConflictError` — this activation was already staged from
      different inputs.
    """
    _check_shape(snapshot)
    fingerprint = snapshot_fingerprint(snapshot)

    # Deferred for the same import-safety reason as `fingerprint_of` above.
    from dotmac_kernel.idempotency import execute_once_platform

    def _operation(session: Session) -> dict[str, object]:
        """Resolve an existing activation, or validate and stage a new one.

        This runs INSIDE the at-most-once owner, and that placement is the
        point. An earlier revision short-circuited an activation replay BEFORE
        reaching it, so a delivery key could be spent without ever being
        recorded: stage claim A under event-a and claim B under event-b, then
        replay claim A under event-b — A already existed, the call succeeded,
        and nothing discovered that event-b belonged to a different request.
        Every call now presents its delivery key first.
        """
        already = _existing(session, snapshot)
        if already is not None:
            view = _replay_or_conflict(already, snapshot, fingerprint)
            return {"allocation_id": str(view.id), "activation_replayed": True}

        _check_catalogue(snapshot, catalogues)

        allocation = Allocation(
            contract_ref=snapshot.contract_ref,
            product_code=snapshot.product_code,
            customer_ref=snapshot.customer_ref,
            content_hash=snapshot.content_hash,
            status=AllocationStatus.STAGED.value,
            source_event_id=snapshot.source_event_id,
            snapshot_fingerprint=fingerprint,
            entries=[
                AllocationEntry(
                    capability_code=entry.capability_code,
                    quantity=entry.quantity,
                )
                for entry in snapshot.entries
            ],
        )
        session.add(allocation)
        session.flush()
        # Seal AFTER every entry is written. From here the `refuse_late_entry`
        # trigger refuses any further entry, so an already-staged allocation
        # cannot acquire a capability that never met the catalogue — including
        # through raw SQL that never called this function.
        #
        # Through the ORM, not raw SQL: raw SQL loses the dialect's UUID
        # adaptation and the statement stops being portable across the two
        # databases this module is tested on. The UPDATE therefore also writes
        # `updated_at` (TimestampMixin's `onupdate`), which is why the
        # column-level grant covers that column too — it is metadata, not a
        # business column, and every business column stays unwritable.
        allocation.sealed = True
        session.flush()
        # Inside the idempotent operation, so an at-least-once transport
        # delivering the same event twice produces ONE audit event rather than
        # a trail that overstates how often this happened.
        write_platform_audit_event(
            session,
            actor_admin_id=actor_admin_id,
            action=AUDIT_ACTION_STAGED,
            entity_type="allocation",
            entity_id=str(allocation.id),
            details={
                "contract_ref": str(snapshot.contract_ref),
                "product_code": snapshot.product_code,
                "customer_ref": snapshot.customer_ref,
                "content_hash": snapshot.content_hash,
                "entries": len(snapshot.entries),
                "snapshot_fingerprint": fingerprint,
            },
        )
        return {"allocation_id": str(allocation.id), "activation_replayed": False}

    def _run() -> tuple[UUID, bool]:
        outcome = execute_once_platform(
            db,
            scope=IDEMPOTENCY_SCOPE,
            key=snapshot.source_event_id,
            operation=_operation,
            operation_name=IDEMPOTENCY_SCOPE,
            fingerprint=fingerprint,
        )
        allocation_id = UUID(str(outcome.result["allocation_id"]))
        # Either the DELIVERY was a replay (the kernel returned a stored
        # result) or the ACTIVATION was (this delivery is new, the allocation
        # was not). Both are replays to the caller; conflating them into
        # `False` would tell a consumer it did work it did not do.
        replayed = outcome.replayed or bool(outcome.result["activation_replayed"])
        return allocation_id, replayed

    try:
        allocation_id, replayed = _run()
    except IntegrityError:
        # A concurrent delivery of the SAME activation under a DIFFERENT
        # source_event_id: both operations found nothing, both inserted, and the
        # `(contract_ref, content_hash)` unique constraint decided. Retry
        # THROUGH the kernel rather than resolving here, so the losing delivery
        # key still receives its ledger row — otherwise the race is a second way
        # to spend a key without recording it.
        allocation_id, replayed = _run()

    staged = db.get(Allocation, allocation_id)
    if staged is None:  # pragma: no cover — written above, or raised above
        raise RuntimeError("allocation missing after staging")
    return _view(staged, replayed=replayed)


def allocation_product(db: Session, allocation_id: UUID) -> str:
    """The product an allocation was VALIDATED against.

    Licence issuance calls this instead of accepting a caller-supplied product.
    Without it, an allocation validated against product A can be issued as a
    licence for product B: every code still resolves, just in a different
    catalogue, and nothing in the licence records that the swap happened.
    """
    row = db.execute(
        select(Allocation.product_code, Allocation.sealed).where(
            Allocation.id == allocation_id
        )
    ).one_or_none()
    if row is None:
        raise LookupError(f"no allocation {allocation_id}")
    product, sealed = row
    if not sealed:
        # Fail closed. An unsealed row's entries may be a partial set nobody
        # finished validating, and answering here is how a licence gets issued
        # against entitlements that were never authorized.
        raise IncompleteAllocationError(
            f"allocation {allocation_id} was never sealed; it is an incomplete "
            "write and must not be issued against"
        )
    return str(product)


__all__ = [
    "AUDIT_ACTION_STAGED",
    "IDEMPOTENCY_SCOPE",
    "AllocatedCapability",
    "AllocationView",
    "allocation_product",
    "snapshot_fingerprint",
    "stage_allocation",
]
