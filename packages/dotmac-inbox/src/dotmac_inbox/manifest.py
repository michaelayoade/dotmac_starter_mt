"""Inbox's `ModuleManifest` — the third stateful module.

Four fields make it stateful, and they must match this module's row in
`dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER` (`INBOX_MIGRATION_OWNER`)
exactly, or `NamespaceRegistry.from_manifests` refuses the composition at boot:

- `short_code="ibx"` → the derived, read-only schema `mod_ibx`
- `migration_prefix="ib"` → revision ids `ib_0001_…`
- `migration_branch="inbox"` → how an `alembic_version` row is attributed
- `tables=(...)` → the composed gate rejects a migration creating anything
  outside this declaration, in both directions

**`tables` lists only this module's own tables.** A product's contact-resolution
or subject-link tables live in the product's schema and lineage; a module
claiming ownership of a table it does not create would make the live-catalog
gate wrong in the direction that matters.

## Why `core=False`

Most deployments have no inbox. ERP and the vendor control plane have none today
and no evident need for one — see `docs/inventories/inbox-sources.md` § "The
four products", where that thin cross-product demand is the reason the unit was
narrowed rather than lifted whole. Being non-core is the honest declaration.

## No capabilities, permissions or audit actions YET

This release ships no routers, and every one of those declarations exists to
gate or annotate a route. Ticketing's manifest records what happens otherwise:
`test_every_declared_capability_is_enforced_somewhere` fails with "capability
code(s) declared but enforced by no mounted route". A declared code with no
consumer is dead vocabulary that reads as a working gate — the exact failure
ADR-0008's registries exist to prevent.

`inbox.use`, the read/reply/administer permission split, and the transition
audit actions all land in the release that ships the routers, with the guards
that reference them in the same change.
"""

from __future__ import annotations

from dotmac_kernel.modules import ModuleManifest

module = ModuleManifest(
    code="inbox",
    version="0.1.0a1",
    core=False,
    # ── D1 database identity ────────────────────────────────────────────────
    short_code="ibx",
    migration_prefix="ib",
    migration_branch="inbox",
    tables=("inbox_conversations", "inbox_messages"),
)

__all__ = ["module"]
