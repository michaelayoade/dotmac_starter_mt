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

## One audit action, declared because it HAS a consumer

`entitlement_allocation.staged` is written by `service.stage_allocation` inside
its idempotent operation, so the declaration is live rather than aspirational —
which is the whole test ADR-0008's registries apply: a declared code with no
consumer is dead vocabulary that reads as a working gate.

Capabilities and permissions stay undeclared for exactly the same reason
inverted: this release ships no routers, so there is nothing for them to gate.
`dotmac-ticketing` proved the point the loud way — CI rejected a declared
capability code no mounted route enforced. They land with the routers, in the
same change as the guards that reference them.
"""

from __future__ import annotations

from dotmac_kernel.modules import ModuleManifest

module = ModuleManifest(
    code="entitlement_allocation",
    version="0.1.0a3",
    core=False,
    short_code="ealloc",
    migration_prefix="ea",
    migration_branch="entitlement_allocation",
    tables=(),
    platform_tables=("allocations", "allocation_entries"),
    supported_plane_sets=(),
    audit_actions=("entitlement_allocation.staged",),
)

__all__ = ["module"]
