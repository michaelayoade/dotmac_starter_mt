"""The module's inbound contract: what a caller supplies, and what it must prove.

Two types and one protocol. Together they are the cut that makes this module
extractable at all — the source implementation read `vendor_cp.contracts`
directly (a foreign key, a `session.get(Contract, …)`, and a hard-coded
`"contract.activated"` literal), none of which can survive a module boundary.
See `docs/inventories/entitlement-allocation-sources.md` for the three couplings
and why each needed a different cut.

## The snapshot is provenance, not a reference

`ContractSnapshot.contract_ref` is a bare `UUID` with no foreign key, and that
is deliberate rather than a limitation. An allocation is an **immutable
projection of what an activated contract version entitled**; it has to stay
readable after the contract row is archived, corrected, or moved into whichever
module eventually owns commercial contracts. A live FK would make this module's
migration lineage depend on another module's — ADR-0006 D1 — and would make an
allocation deletable by deleting its contract, which is precisely what an
immutable record must not be.

## The catalogue is a port, not a dependency

The module VALIDATES; the caller supplies ACCESS to the authority. That split is
the kernel's established owner-validates pattern (`grant_entitlement` takes a
`CapabilityCatalogue` and raises rather than trusting its caller), and it is
what preserves the invariant across every adapter without making this module a
capability-code authority.

The protocol lives here rather than in the kernel on purpose: adding it to the
kernel would be a new kernel facility, and ADR-0017's moratorium holds until the
kernel lineage runs in a production product database. A module-owned port needs
no kernel change and no exception.

## Why `product_code` is on the snapshot and not inferred

A capability code is only meaningful against the product that declares it.
Without the product on the snapshot, an allocation validated for one product
could later be issued as a licence for another — the codes would still "exist",
just in a different catalogue. `product_code` is therefore required here, then
PERSISTED on the allocation, so licence issuance reads the product the
allocation was validated against rather than accepting a fresh one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class AllocationError(ValueError):
    """Base: this snapshot cannot become an allocation."""


class UnknownProductError(AllocationError):
    """The catalogue reader does not know this product.

    Fail closed. An unknown product is not an empty catalogue — it is a caller
    that cannot prove anything about the codes it is asking to allocate, and
    treating the two the same would let a typo in `product_code` allocate
    arbitrary capabilities against a product nobody has declared.
    """


class UndeclaredCapabilityError(AllocationError):
    """A capability code is not declared by the named product.

    Carries the full offending set, not just the first: a caller fixing a
    manifest wants every missing code at once, and reporting one at a time turns
    a single review into several.
    """

    def __init__(self, product_code: str, codes: tuple[str, ...]) -> None:
        self.product_code = product_code
        self.codes = codes
        super().__init__(
            f"product {product_code!r} declares no capability "
            f"{', '.join(repr(code) for code in codes)}; "
            "an allocation may never invent a capability code"
        )


class EmptyAllocationError(AllocationError):
    """A snapshot with no entries entitles nothing.

    Refused rather than stored, because an allocation that grants nothing is
    indistinguishable downstream from one whose entries were lost, and the
    difference matters when a customer asks why a capability stopped working.
    """


@dataclass(frozen=True, slots=True)
class ContractEntitlement:
    """One capability the contract entitles, and how much of it."""

    capability_code: str
    quantity: int = 1


@dataclass(frozen=True, slots=True)
class ContractSnapshot:
    """What an activated contract version entitles, frozen at activation.

    Constructed by the caller from its OWN authoritative state. This module
    never reads a contract, so contract invariants — that it is active, that its
    content hash matches the activation event — are proven where the authority
    lives rather than re-derived here from a foreign table.
    """

    contract_ref: UUID
    product_code: str
    customer_ref: str
    content_hash: str
    source_event_id: str
    entries: tuple[ContractEntitlement, ...]


class CapabilityCatalogueReader(Protocol):
    """Read access to the authoritative, manifest-derived capability catalogue.

    One method, and it RAISES rather than returning a boolean: a predicate
    invites `if not declared: log_and_continue`, and the whole point is that an
    undeclared code stops the write.

    An adapter wraps whatever holds the truth for the named product — in the
    vendor control plane, the kernel's `CapabilityCatalogue.require`. It must
    NOT wrap `active_capabilities()`, which describes the modules installed in
    the process doing the asking, not the ones declared by the target
    application. Those are different sets, and confusing them would validate an
    allocation against the wrong product's manifest.
    """

    def require_declared(
        self,
        *,
        product_code: str,
        capability_code: str,
    ) -> None:
        """Return normally if declared.

        Raise `UnknownProductError` if the product is unknown, and
        `UndeclaredCapabilityError` if the product declares no such code.
        """
        ...


__all__ = [
    "AllocationError",
    "CapabilityCatalogueReader",
    "ContractEntitlement",
    "ContractSnapshot",
    "EmptyAllocationError",
    "UndeclaredCapabilityError",
    "UnknownProductError",
]
