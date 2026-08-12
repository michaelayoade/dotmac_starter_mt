"""DotMac Entitlement Allocation — what a contract entitles, frozen and proven.

An **immutable projection of what an activated contract version entitles**,
staged once per `(contract_ref, content_hash)`. Not a grant, not a licence, not
a delivery record: ruling C4 makes the control plane the ALLOCATOR and the
product data plane the only writer of its own `tenant_entitlement_grants`.

## The module validates; the caller supplies the authority

```python
stage_allocation(db, snapshot, catalogues=reader)
```

`CapabilityCatalogueReader` is a one-method port the caller implements over
whatever holds the truth for the named product — in the vendor control plane, a
thin wrapper over the kernel's `CapabilityCatalogue.require`. The module then
performs the check itself.

That split is the kernel's established owner-validates pattern
(`grant_entitlement` takes a catalogue and raises rather than trusting its
caller). It preserves the invariant across every adapter — HTTP route, outbox
consumer, CLI backfill — instead of once per adapter, where the newest one is
always the one that forgot. There is no `validated=True`: an optional invariant
is a comment.

The port lives HERE rather than in the kernel deliberately. Adding it there
would be a new kernel facility, and ADR-0017's moratorium holds until the kernel
lineage runs in a production product database.

## An adapter TRANSLATES; the module does not guess

Your `require_declared` must raise this module's `UnknownProductError` or
`UndeclaredCapabilityError` — not the backing store's own exception type. The
service catches nothing broad, so an adapter defect surfaces as itself rather
than being reported as an undeclared capability.

## Two rules that are easy to get wrong

- **Never wrap `active_capabilities()`.** It describes the modules installed in
  the process doing the asking, not the ones declared by the target
  application. Validating against it checks the wrong product's manifest.
- **`product_code` is persisted, and licence issuance must read it** via
  `allocation_product()` rather than accepting a fresh caller-supplied value.
  Otherwise an allocation validated against product A can be issued as a licence
  for product B: every code still resolves, just in a different catalogue.

## Public surface

Everything importable from this top-level namespace is stable. Submodules are
not: import from here.
"""

from __future__ import annotations

from dotmac_entitlement_allocation.manifest import module
from dotmac_entitlement_allocation.models import (
    SCHEMA,
    STAGED,
    Allocation,
    AllocationEntry,
    AllocationStatus,
)
from dotmac_entitlement_allocation.ports import (
    AllocationConflictError,
    AllocationError,
    CapabilityCatalogueReader,
    ContractEntitlement,
    ContractSnapshot,
    DuplicateCapabilityError,
    EmptyAllocationError,
    IncompleteAllocationError,
    UndeclaredCapabilityError,
    UnknownProductError,
)
from dotmac_entitlement_allocation.service import (
    AUDIT_ACTION_STAGED,
    IDEMPOTENCY_SCOPE,
    AllocatedCapability,
    AllocationView,
    allocation_product,
    snapshot_fingerprint,
    stage_allocation,
)

__version__ = "0.1.0a1"

__all__ = [
    "AUDIT_ACTION_STAGED",
    "IDEMPOTENCY_SCOPE",
    "SCHEMA",
    "STAGED",
    "AllocatedCapability",
    "Allocation",
    "AllocationConflictError",
    "AllocationEntry",
    "AllocationError",
    "AllocationStatus",
    "AllocationView",
    "CapabilityCatalogueReader",
    "ContractEntitlement",
    "ContractSnapshot",
    "DuplicateCapabilityError",
    "EmptyAllocationError",
    "IncompleteAllocationError",
    "UndeclaredCapabilityError",
    "UnknownProductError",
    "__version__",
    "allocation_product",
    "module",
    "snapshot_fingerprint",
    "stage_allocation",
]
