"""Entitlement allocation's `ModuleManifest` — the fourth stateful module.

Must match `ENTITLEMENT_ALLOCATION_MIGRATION_OWNER` in the kernel ledger exactly
or `NamespaceRegistry.from_manifests` refuses the composition at boot:
`short_code="ealloc"` → `mod_ealloc`, `migration_prefix="ea"` → `ea_0001_…`,
`migration_branch="entitlement_allocation"`, and `tables` bounding what the
composed gate will accept the migration creating.

## `core=False`, and vendor-assembly-only beyond that

Ruling C4 splits allocation from granting: the control plane ALLOCATES, the
product data plane is the only writer of its own `tenant_entitlement_grants`.
A data plane installing this module would be acquiring the wrong half. Being
non-core is the first guard; the import-linter contract forbidding the assembly
from importing it is the second.

## No capabilities, permissions or audit actions YET

This release ships no routers, and each of those declarations exists to gate or
annotate a route. `dotmac-ticketing` proved the point the loud way — CI rejected
a declared capability code no mounted route enforced. They land with the routers,
in the same change as the guards that reference them.
"""

from __future__ import annotations

from dotmac_kernel.modules import ModuleManifest

module = ModuleManifest(
    code="entitlement_allocation",
    version="0.1.0a1",
    core=False,
    short_code="ealloc",
    migration_prefix="ea",
    migration_branch="entitlement_allocation",
    tables=("allocations", "allocation_entries"),
)

__all__ = ["module"]
