"""The allocation tables, bound to the `mod_ealloc` schema (ADR-0006 D1).

Platform catalog tables: no `tenant_id`, no RLS, `app_user` REVOKEd. An
allocation is a VENDOR-side record of what a customer's contract entitles; the
product data plane never reads it. Ruling C4 is the reason — the control plane
allocates, and the data plane is the only writer of its own
`tenant_entitlement_grants`, learning what it may write from a signed envelope
rather than by querying the vendor.

## `product_code` is stored, and that is a correctness fix

The source implementation did not persist it. A capability code is only
meaningful against the product that declares it, so an allocation validated
against product A could later be issued as a licence for product B — the codes
would still resolve, just in a different catalogue. Persisting the product the
allocation was VALIDATED against closes that relabelling path: licence issuance
reads this column rather than accepting a fresh caller-supplied value.

## No foreign key to a contract

`contract_ref` is a bare UUID. An allocation is an immutable projection and must
outlive the row it was projected from — a contract archived, corrected, or moved
into whichever module ends up owning commercial contracts. A cross-module FK
would also splice two independently released migration lineages, which D1
forbids. `content_hash` and `source_event_id` carry the provenance the FK looked
like it was providing.

## Immutability, including against APPEND

`(contract_ref, content_hash)` is unique: one allocation per activated contract
version, so re-delivery of the same activation is a no-op rather than a second
row.

`platform_api` holds SELECT and INSERT, plus a COLUMN-LEVEL
`UPDATE (sealed, updated_at)` and nothing more. That stops the parent being
rewritten —
but INSERT is exactly what staging needs, so on the CHILD table that same grant
leaves the allocation **appendable**: raw SQL could add a capability to an
already-staged allocation and bypass catalogue validation entirely. Restricting
INSERT is not available, so the entries table carries a trigger
(`refuse_late_entry`) that rejects any entry once the parent is SEALED. The
service seals it after writing every entry, so an allocation is written once,
wholly, or not at all.

`quantity > 0` is a CHECK rather than only a service rule, for the same reason:
the service cannot police a path that never calls it.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from dotmac_kernel.models import Base, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

#: Derived from the allocated short code — never a literal here. The migration
#: uses a literal on purpose (a frozen historical artifact); runtime models
#: resolve through the ledger so drift between the two is a boot failure.
SCHEMA: str = module_schema("ealloc")

_ALLOCATIONS = "allocations"
_ENTRIES = "allocation_entries"


class AllocationStatus(StrEnum):
    """The allocation lifecycle, as a value object rather than a bare string.

    ONE definition, shared by persistence and by every owner that reads the
    contract — the typed-contracts standard's requirement, and the reason this
    is not two constants that drift.

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


class Allocation(Base, TimestampMixin):
    """An immutable projection of an activated contract version's entitlement."""

    __tablename__ = _ALLOCATIONS
    __table_args__ = (
        UniqueConstraint(
            "contract_ref",
            "content_hash",
            name="uq_allocations_contract_content",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()

    #: Provenance, not a reference. No FK — see the module docstring.
    contract_ref: Mapped[UUID] = mapped_column(nullable=False, index=True)

    #: The product whose manifest-declared catalogue every entry below was
    #: validated against. Read by licence issuance; never re-supplied by a
    #: caller at issuance time.
    product_code: Mapped[str] = mapped_column(String(120), nullable=False)

    customer_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AllocationStatus.STAGED.value
    )

    #: Which event produced this allocation. Idempotency provenance, kept so a
    #: replayed delivery is explainable rather than merely silent.
    source_event_id: Mapped[str] = mapped_column(String(200), nullable=False)

    #: A stable digest of the snapshot's CLAIM — product, customer, content hash
    #: and normalized entries, excluding the delivery id. Finding an existing
    #: `(contract_ref, content_hash)` is only a REPLAY if this matches;
    #: otherwise it is two different claims about one activation and staging
    #: raises rather than silently returning the first.
    #:
    #: Stored HERE rather than only in the idempotency record because
    #: idempotency records have a retention policy (ADR-0014 leaves it to the
    #: product) and allocations do not. A purge must not be able to turn a
    #: conflict back into a silent replay.
    snapshot_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Set once, by the service, after every entry is written. While False the
    #: entries table accepts inserts; once True the `refuse_late_entry` trigger
    #: refuses them, and `seal_is_one_way` refuses to lift it. This is the ONLY
    #: column `platform_api` may update, granted at column level.
    sealed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    entries: Mapped[list[AllocationEntry]] = relationship(
        lambda: AllocationEntry,
        back_populates="allocation",
        cascade="all, delete-orphan",
        order_by=lambda: AllocationEntry.capability_code,
    )


class AllocationEntry(Base, TimestampMixin):
    """One entitled capability and quantity within a staged allocation."""

    __tablename__ = _ENTRIES
    __table_args__ = (
        UniqueConstraint(
            "allocation_id",
            "capability_code",
            name="uq_allocation_entries_allocation_code",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    allocation_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.{_ALLOCATIONS}.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Declared by `Allocation.product_code`'s manifest, proven at staging time.
    #: A plain string, not an enum: the vocabulary belongs to the products, and
    #: this module is deliberately not where new codes are invented.
    capability_code: Mapped[str] = mapped_column(String(120), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    allocation: Mapped[Allocation] = relationship(
        lambda: Allocation,
        back_populates="entries",
    )


__all__ = [
    "SCHEMA",
    "STAGED",
    "Allocation",
    "AllocationEntry",
    "AllocationStatus",
]
